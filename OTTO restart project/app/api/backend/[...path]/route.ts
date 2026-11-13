import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/features/auth/session";
import { createInternalPrincipal } from "@/features/auth/internal-principal";
import { verifyCsrf } from "@/features/auth/csrf";
import { buildProxyHeaders, readProxyBody } from "@/features/auth/proxy-transport";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const BACKEND_READ_TIMEOUT_MS = 15_000;

type Context = { params: Promise<{ path: string[] }> };

function apiError(status: number, code: string, message: string, requestId = randomUUID()) {
  return NextResponse.json(
    { code, message, requestId, retryable: status === 429 || status >= 500, details: {} },
    { status, headers: { "X-Request-ID": requestId, "Cache-Control": "no-store" } },
  );
}

function backendBaseUrl(): URL {
  const configured = process.env.OTTORESTART_BACKEND_URL?.trim();
  const value = configured || (process.env.NODE_ENV === "production" ? "" : "http://otto_restart_backend:3301");
  if (!value) throw new Error("OttoRestart backend URL is not configured.");
  const url = new URL(value);
  if (!new Set(["http:", "https:"]).has(url.protocol) || url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) {
    throw new Error("OttoRestart backend URL configuration is invalid.");
  }
  return url;
}

export async function GET(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "GET");
}

export async function POST(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "POST");
}

export async function PUT(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "PUT");
}

export async function PATCH(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "PATCH");
}

export async function DELETE(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "DELETE");
}

async function proxyRequest(request: NextRequest, context: Context, method: string) {
  const { path } = await context.params;
  if (!path.length || path.some((part) => !part || part === "." || part === ".." || part.includes("/") || part.includes("\\"))) {
    return apiError(404, "NOT_FOUND", "The requested OttoRestart API path does not exist.");
  }
  const targetPath = path.map((part) => encodeURIComponent(part)).join("/");
  const requestId = randomUUID();
  let session: { user: string } | null = null;
  if (targetPath !== "health") {
    try {
      session = await verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value);
    } catch {
      return apiError(503, "AUTH_CONFIGURATION_ERROR", "OttoRestart authentication is not configured.", requestId);
    }
    if (!session) return apiError(401, "UNAUTHORIZED", "Требуется авторизация в OttoRestart.", requestId);
  }

  const csrfVerified = method === "GET" || method === "HEAD" ? false : verifyCsrf(request);
  if (method !== "GET" && method !== "HEAD" && !csrfVerified) {
    return apiError(403, "CSRF_REQUIRED", "Проверьте источник запроса и CSRF-токен OttoRestart.", requestId);
  }

  let targetUrl: URL;
  let internalToken: string | undefined;
  try {
    const base = backendBaseUrl();
    targetUrl = new URL(`/api/restart/${targetPath}`, base);
    targetUrl.search = request.nextUrl.search;
    if (session) internalToken = createInternalPrincipal(session, csrfVerified, requestId);
  } catch {
    return apiError(503, "BACKEND_CONFIGURATION_ERROR", "Сервис OttoRestart не настроен.", requestId);
  }

  const headers = buildProxyHeaders(request.headers, internalToken, requestId);
  const body = await readProxyBody(method, request);
  const controller = new AbortController();
  let readTimedOut = false;
  const abortOnClientDisconnect = () => controller.abort(request.signal.reason);
  if (request.signal.aborted) {
    abortOnClientDisconnect();
  } else {
    request.signal.addEventListener("abort", abortOnClientDisconnect, { once: true });
  }
  const timeout = method === "GET"
    ? setTimeout(() => {
        readTimedOut = true;
        controller.abort();
      }, BACKEND_READ_TIMEOUT_MS)
    : undefined;

  try {
    const backendResponse = await fetch(targetUrl, {
      method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
    const responseHeaders = new Headers({
      "Content-Type": backendResponse.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store",
      "X-Request-ID": backendResponse.headers.get("x-request-id") || requestId,
    });
    const responseBody = method === "HEAD" ? null : new Uint8Array(await backendResponse.arrayBuffer());
    return new NextResponse(responseBody, { status: backendResponse.status, headers: responseHeaders });
  } catch {
    if (readTimedOut) {
      return apiError(504, "BACKEND_TIMEOUT", "Backend OttoRestart не ответил за 15 секунд. Повторите запрос позже.", requestId);
    }
    return apiError(503, "BACKEND_UNAVAILABLE", "Не удалось соединиться с backend OttoRestart.", requestId);
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
    request.signal.removeEventListener("abort", abortOnClientDisconnect);
  }
}

import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/features/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_INTERNAL_URL =
  process.env.OTTORESTART_BACKEND_URL || "http://otto_restart_backend:3301";

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "GET");
}

export async function POST(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "POST");
}

export async function PUT(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "PUT");
}

export async function DELETE(request: NextRequest, context: Context) {
  return proxyRequest(request, context, "DELETE");
}

async function proxyRequest(
  request: NextRequest,
  context: Context,
  method: string
) {
  const { path } = await context.params;
  const targetPath = path.join("/");

  // Defense in depth: Verify session cookie (except for health check)
  if (targetPath !== "health") {
    const token = request.cookies.get(SESSION_COOKIE)?.value;
    const session = await verifySessionToken(token);
    if (!session) {
      return NextResponse.json(
        { error: "UNAUTHORIZED", message: "Требуется авторизация в OttoRestart." },
        { status: 401 }
      );
    }
  }

  const searchParams = request.nextUrl.search;
  const targetUrl = `${BACKEND_INTERNAL_URL}/api/restart/${targetPath}${searchParams}`;

  try {
    const headers: Record<string, string> = {
      "Content-Type": request.headers.get("content-type") || "application/json",
    };

    let body: string | undefined = undefined;
    if (method !== "GET" && method !== "HEAD") {
      body = await request.text();
    }

    const backendRes = await fetch(targetUrl, {
      method,
      headers,
      body,
      cache: "no-store",
    });

    const data = await backendRes.text();

    return new NextResponse(data, {
      status: backendRes.status,
      headers: {
        "Content-Type":
          backendRes.headers.get("content-type") || "application/json",
      },
    });
  } catch (error) {
    console.error(`[backend-proxy] Failed to connect to ${targetUrl}:`, error);
    return NextResponse.json(
      {
        error: "BACKEND_UNAVAILABLE",
        message: "Не удалось соединиться с сервисом базы данных OttoRestart (порт 3301).",
        targetUrl,
      },
      { status: 503 }
    );
  }
}


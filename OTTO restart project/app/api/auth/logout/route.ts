import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/features/auth/session";
import { CSRF_COOKIE, verifyCsrf } from "@/features/auth/csrf";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  const requestId = crypto.randomUUID();
  let session: { user: string } | null;
  try {
    session = await verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value);
  } catch {
    return NextResponse.json(
      { code: "AUTH_CONFIGURATION_ERROR", message: "Авторизация OttoRestart не настроена.", requestId, retryable: false, details: {} },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!session) {
    return NextResponse.json(
      { code: "UNAUTHORIZED", message: "Сессия OttoRestart отсутствует или истекла.", requestId, retryable: false, details: {} },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!verifyCsrf(request)) {
    return NextResponse.json(
      { code: "CSRF_REQUIRED", message: "Проверьте источник запроса и CSRF-токен OttoRestart.", requestId, retryable: false, details: {} },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }

  const response = NextResponse.json({
    ok: true,
    message: "Сессия завершена",
    requestId,
  });

  response.cookies.set({
    name: SESSION_COOKIE,
    value: "",
    httpOnly: true,
    path: "/restart",
    sameSite: "lax",
    maxAge: 0,
    expires: new Date(0),
  });
  response.cookies.set({
    name: CSRF_COOKIE,
    value: "",
    httpOnly: true,
    path: "/restart",
    sameSite: "strict",
    secure: request.nextUrl.protocol === "https:",
    maxAge: 0,
    expires: new Date(0),
  });

  return response;
}

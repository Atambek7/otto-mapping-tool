import { NextRequest, NextResponse } from "next/server";
import { verifySessionToken, SESSION_COOKIE, SESSION_TTL_SECONDS } from "@/features/auth/session";
import { CSRF_COOKIE, createCsrfToken } from "@/features/auth/csrf";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  let session: { user: string } | null;
  try {
    session = await verifySessionToken(request.cookies.get(SESSION_COOKIE)?.value);
  } catch {
    return NextResponse.json(
      { code: "AUTH_CONFIGURATION_ERROR", message: "Авторизация OttoRestart не настроена.", requestId: crypto.randomUUID(), retryable: false, details: {} },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
  if (!session) {
    return NextResponse.json(
      { code: "UNAUTHORIZED", message: "Для доступа требуется авторизация в OttoRestart.", requestId: crypto.randomUUID(), retryable: false, details: {} },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }

  const token = createCsrfToken();
  const response = NextResponse.json({ token }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set({
    name: CSRF_COOKIE,
    value: token,
    httpOnly: true,
    path: "/restart",
    sameSite: "strict",
    secure: request.nextUrl.protocol === "https:",
    maxAge: SESSION_TTL_SECONDS,
  });
  return response;
}

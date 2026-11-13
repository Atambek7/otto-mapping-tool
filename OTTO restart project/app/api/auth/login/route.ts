import { NextRequest, NextResponse } from "next/server";
import {
  type AuthConfig,
  createSessionToken,
  getAuthConfig,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  verifyLoginCredentials,
} from "@/features/auth/session";
import { hasAllowedOrigin } from "@/features/auth/csrf";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    if (!hasAllowedOrigin(request)) {
      return NextResponse.json(
        { code: "FORBIDDEN", message: "Источник запроса не разрешён.", requestId: crypto.randomUUID(), retryable: false, details: {} },
        { status: 403, headers: { "Cache-Control": "no-store" } },
      );
    }
    const body = await request.json().catch(() => null);

    if (!body || typeof body !== "object") {
      return NextResponse.json(
        { code: "VALIDATION_ERROR", message: "Некорректный запрос", requestId: crypto.randomUUID(), retryable: false, details: {} },
        { status: 400 }
      );
    }

    const username = typeof body.username === "string" ? body.username.trim() : "";
    const password = typeof body.password === "string" ? body.password : "";

    let config: AuthConfig;
    try {
      config = getAuthConfig();
    } catch {
      return NextResponse.json(
        { code: "AUTH_CONFIGURATION_ERROR", message: "Авторизация OttoRestart не настроена.", requestId: crypto.randomUUID(), retryable: false, details: {} },
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }

    const credentialsMatch = await verifyLoginCredentials(username, password, config);

    if (!credentialsMatch) {
      // Artificial delay of 500ms to slow down brute force
      await new Promise((resolve) => setTimeout(resolve, 500));
      return NextResponse.json(
        { code: "INVALID_CREDENTIALS", message: "Неверный логин или пароль", requestId: crypto.randomUUID(), retryable: false, details: {} },
        { status: 401 }
      );
    }

    const token = await createSessionToken(username, SESSION_TTL_SECONDS);

    const isSecure = request.nextUrl.protocol === "https:";

    const response = NextResponse.json({
      ok: true,
      user: username,
      message: "Успешный вход в OttoRestart",
    });

    response.cookies.set({
      name: SESSION_COOKIE,
      value: token,
      httpOnly: true,
      path: "/restart",
      sameSite: "lax",
      maxAge: SESSION_TTL_SECONDS,
      secure: isSecure,
    });

    return response;
  } catch {
    return NextResponse.json(
      { code: "INTERNAL_ERROR", message: "Ошибка сервера при авторизации.", requestId: crypto.randomUUID(), retryable: false, details: {} },
      { status: 500, headers: { "Cache-Control": "no-store" } }
    );
  }
}

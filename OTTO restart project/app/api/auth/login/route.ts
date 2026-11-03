import { NextRequest, NextResponse } from "next/server";
import {
  createSessionToken,
  getAuthConfig,
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  timingSafeEqualString,
} from "@/features/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => null);

    if (!body || typeof body !== "object") {
      return NextResponse.json(
        { error: "VALIDATION_ERROR", message: "Некорректный запрос" },
        { status: 400 }
      );
    }

    const username = typeof body.username === "string" ? body.username.trim() : "";
    const password = typeof body.password === "string" ? body.password : "";

    const config = getAuthConfig();

    const userMatches = timingSafeEqualString(username, config.user);
    const passMatches = timingSafeEqualString(password, config.password);

    if (!userMatches || !passMatches) {
      // Artificial delay of 500ms to slow down brute force
      await new Promise((resolve) => setTimeout(resolve, 500));
      return NextResponse.json(
        { error: "INVALID_CREDENTIALS", message: "Неверный логин или пароль" },
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
  } catch (error) {
    console.error("[auth-login] Error:", error);
    return NextResponse.json(
      { error: "SERVER_ERROR", message: "Ошибка сервера при авторизации" },
      { status: 500 }
    );
  }
}


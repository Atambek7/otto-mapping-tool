import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/features/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  const response = NextResponse.json({
    ok: true,
    message: "Сессия завершена",
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

  return response;
}


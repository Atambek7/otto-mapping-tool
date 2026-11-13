import { randomBytes, timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";

export const CSRF_COOKIE = "ottorestart_csrf";

export function createCsrfToken(): string {
  return randomBytes(32).toString("base64url");
}

export function allowedOrigins(request: NextRequest): Set<string> {
  const configured = process.env.OTTORESTART_ALLOWED_ORIGINS?.split(",").map((value) => value.trim()).filter(Boolean) ?? [];
  if (configured.length) return new Set(configured);
  if (process.env.NODE_ENV === "production") throw new Error("OttoRestart allowed origins are not configured.");
  return new Set([request.nextUrl.origin]);
}

export function hasAllowedOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    const parsed = new URL(origin);
    return parsed.origin === origin && allowedOrigins(request).has(origin);
  } catch {
    return false;
  }
}

export function verifyCsrf(request: NextRequest): boolean {
  if (!hasAllowedOrigin(request)) return false;
  const cookie = request.cookies.get(CSRF_COOKIE)?.value;
  const header = request.headers.get("x-csrf-token");
  if (!cookie || !header || cookie.length !== header.length) return false;
  return timingSafeEqual(Buffer.from(cookie), Buffer.from(header));
}

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "./features/auth/session";

const PUBLIC_PREFIXES = [
  "/login",
  "/api/auth/",
  "/_next/",
  "/favicon",
  "/icon",
  "/apple-icon",
];

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 1. Allow public routes & assets
  for (const prefix of PUBLIC_PREFIXES) {
    if (pathname === prefix || pathname.startsWith(prefix)) {
      return NextResponse.next();
    }
  }

  // Allow static files
  if (
    pathname.endsWith(".ico") ||
    pathname.endsWith(".png") ||
    pathname.endsWith(".svg") ||
    pathname.endsWith(".jpg") ||
    pathname.endsWith(".css") ||
    pathname.endsWith(".js")
  ) {
    return NextResponse.next();
  }

  // 2. Check session token from cookie
  const sessionToken = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await verifySessionToken(sessionToken);

  if (session) {
    return NextResponse.next();
  }

  // 3. Unauthorized handling
  // For API requests: return 401 JSON
  if (pathname.startsWith("/api/")) {
    return NextResponse.json(
      {
        error: "UNAUTHORIZED",
        message: "Для доступа к API требуется авторизация в OttoRestart.",
      },
      { status: 401 }
    );
  }

  // For pages: redirect to /login with return destination
  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  const destination = pathname + request.nextUrl.search;
  if (destination && destination !== "/" && destination !== "/restart") {
    loginUrl.searchParams.set("next", destination);
  } else {
    loginUrl.search = "";
  }

  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico, icon.png, apple-icon.png
     */
    "/((?!_next/static|_next/image|favicon.ico|icon.png|apple-icon.png).*)",
  ],
};


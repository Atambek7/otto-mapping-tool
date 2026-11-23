import { NextRequest, NextResponse } from "next/server";
import { PUBLIC_MEDIA_FILENAME_RE, resolveBackendBaseUrl } from "@/features/auth/proxy-transport";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ filename: string }> };

export async function GET(request: NextRequest, context: Context) {
  const { filename } = await context.params;
  if (!filename || !PUBLIC_MEDIA_FILENAME_RE.test(filename)) {
    return NextResponse.json({ code: "MEDIA_NOT_FOUND", message: "Media file not found." }, { status: 404 });
  }
  let targetUrl: URL;
  try {
    targetUrl = new URL(`/api/restart/public-media/${encodeURIComponent(filename)}`, resolveBackendBaseUrl());
  } catch {
    return NextResponse.json({ code: "BACKEND_CONFIGURATION_ERROR", message: "Backend URL not configured." }, { status: 503 });
  }
  try {
    const upstream = await fetch(targetUrl, {
      method: "GET",
      cache: "no-store",
      redirect: "manual",
      signal: request.signal,
    });
    if (!upstream.ok) {
      return NextResponse.json({ code: "MEDIA_NOT_FOUND", message: "Media file not found." }, { status: upstream.status });
    }
    const bytes = new Uint8Array(await upstream.arrayBuffer());
    return new NextResponse(bytes, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "image/jpeg",
        "Cache-Control": upstream.headers.get("cache-control") || "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return NextResponse.json({ code: "BACKEND_UNAVAILABLE", message: "Media service unavailable." }, { status: 503 });
  }
}

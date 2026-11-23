const FORWARDED_HEADERS = ["accept", "content-type", "idempotency-key", "origin", "x-csrf-token"];

export const PUBLIC_MEDIA_FILENAME_RE = /^upscaled_(JV|XL)_[a-f0-9]{12}_[a-f0-9]{12}\.jpg$/;

export function resolveBackendBaseUrl(): URL {
  const configured = process.env.OTTORESTART_BACKEND_URL?.trim();
  const value = configured || (process.env.NODE_ENV === "production" ? "" : "http://otto_restart_backend:3301");
  if (!value) throw new Error("OttoRestart backend URL is not configured.");
  const url = new URL(value);
  if (!new Set(["http:", "https:"]).has(url.protocol) || url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) {
    throw new Error("OttoRestart backend URL configuration is invalid.");
  }
  return url;
}

export function buildProxyHeaders(source: Headers, internalToken: string | undefined, requestId: string): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = source.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("X-Request-ID", requestId);
  if (internalToken) headers.set("Authorization", `Bearer ${internalToken}`);
  return headers;
}

export async function readProxyBody(method: string, request: Pick<Request, "arrayBuffer">): Promise<ArrayBuffer | undefined> {
  if (method === "GET" || method === "HEAD") return undefined;
  return request.arrayBuffer();
}

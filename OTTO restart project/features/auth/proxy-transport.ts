const FORWARDED_HEADERS = ["accept", "content-type", "idempotency-key", "origin", "x-csrf-token"];

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

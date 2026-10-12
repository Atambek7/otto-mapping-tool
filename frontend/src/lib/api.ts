export const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api";

function mergeSignals(
  userSignal?: AbortSignal | null,
  timeoutMs = 30000
): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);

  let onUserAbort: (() => void) | null = null;

  if (userSignal) {
    if (userSignal.aborted) {
      clearTimeout(timer);
      controller.abort(userSignal.reason);
      return { signal: controller.signal, cleanup: () => {} };
    }
    onUserAbort = () => {
      clearTimeout(timer);
      controller.abort(userSignal.reason);
    };
    userSignal.addEventListener("abort", onUserAbort, { once: true });
  }

  const cleanup = () => {
    clearTimeout(timer);
    if (userSignal && onUserAbort) {
      userSignal.removeEventListener("abort", onUserAbort);
    }
  };

  return { signal: controller.signal, cleanup };
}

export async function fetchWithAuth(
  url: string,
  options: RequestInit = {},
  retries = 2
): Promise<Response> {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const headers = {
    ...options.headers,
  } as Record<string, string>;

  // Only set application/json if we are not sending FormData
  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  // Include Bearer header if token exists in localStorage (backwards-compatibility),
  // but credentials: "include" sends HttpOnly cookie automatically.
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const requestTimeout = (options as any).timeout ?? 30000;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const { signal: mergedSignal, cleanup } = mergeSignals(options.signal, requestTimeout);
    const fetchOptions: RequestInit = {
      ...options,
      headers,
      credentials: options.credentials || "include",
      signal: mergedSignal,
    };

    try {
      const response = await fetch(`${API_URL}${url}`, fetchOptions);

      if (response.status === 401) {
        if (typeof window !== "undefined" && window.location.pathname !== "/login") {
          localStorage.removeItem("token");
          window.location.href = "/login";
        }
      }

      return response;
    } catch (err: any) {
      // Do not retry if caller explicitly aborted or AbortError occurred
      if (
        (err instanceof DOMException && err.name === "AbortError") ||
        err?.name === "AbortError" ||
        options.signal?.aborted
      ) {
        throw err;
      }

      if (attempt < retries) {
        // Wait before retry: 800ms, 1600ms
        await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
        continue;
      }
      throw err;
    } finally {
      cleanup();
    }
  }
  throw new Error("fetchWithAuth: exhausted retries");
}


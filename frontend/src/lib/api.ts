export const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api";

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

  const fetchOptions: RequestInit = {
    ...options,
    headers,
    credentials: options.credentials || "include",
  };

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetch(`${API_URL}${url}`, fetchOptions);

      if (response.status === 401) {
        if (typeof window !== "undefined" && window.location.pathname !== "/login") {
          localStorage.removeItem("token");
          window.location.href = "/login";
        }
      }

      return response;
    } catch (err) {
      if (attempt < retries) {
        // Wait before retry: 800ms, 1600ms
        await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
        continue;
      }
      throw err;
    }
  }
  throw new Error("fetchWithAuth: exhausted retries");
}

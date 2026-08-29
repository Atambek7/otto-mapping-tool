export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

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

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }



  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await fetch(`${API_URL}${url}`, { ...options, headers });

      if (response.status === 401) {
        if (typeof window !== "undefined") {
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
  // Never reached, but TS needs it
  throw new Error("fetchWithAuth: exhausted retries");
}

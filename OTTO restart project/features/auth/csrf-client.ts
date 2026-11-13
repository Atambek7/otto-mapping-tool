let cachedToken: string | undefined;

export async function getCsrfToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  const response = await fetch("/restart/api/auth/csrf", {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || typeof payload.token !== "string") {
    throw new Error(payload?.message ?? "Не удалось получить CSRF-токен OttoRestart.");
  }
  cachedToken = payload.token;
  return payload.token;
}

export function clearCsrfToken(): void {
  cachedToken = undefined;
}

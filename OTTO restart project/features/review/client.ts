import type { RestartAccount } from "../api/types";
import type { ReviewAction, ReviewItem, ReviewList, ReviewPatch } from "./types";

export class ReviewClientError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

/** Same-origin DEMO API only. Never falls back to production or Phase 3's API URL. */
export class DemoReviewClient {
  private async request<T>(account: RestartAccount, path: string, method = "GET", body?: unknown, query: Record<string, string | number> = {}): Promise<T> {
    const params = new URLSearchParams({ account });
    for (const [key, value] of Object.entries(query)) if (value !== "") params.set(key, String(value));
    let response: Response;
    try {
      response = await fetch(`/restart/api/demo/${path}?${params}`, { method, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-OttoRestart-Demo": "1" }, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch { throw new ReviewClientError(0, "NETWORK_ERROR", "Нет соединения с демо-API. Повторите загрузку."); }
    const data = await response.json().catch(() => null);
    if (!response.ok || !data) throw new ReviewClientError(response.status, data?.code ?? "INVALID_RESPONSE", data?.message ?? "Не удалось прочитать демо-ответ.");
    return data as T;
  }
  list(account: RestartAccount, query = "", status = "", page = 1) { return this.request<ReviewList>(account, "items", "GET", undefined, { q: query, status, page, pageSize: 20 }); }
  get(account: RestartAccount, id: string) { return this.request<ReviewItem>(account, `items/${encodeURIComponent(id)}`); }
  act(item: ReviewItem, action: ReviewAction) { return this.request<ReviewItem>(item.account, `items/${encodeURIComponent(item.id)}/${action}`, "POST", { expectedVersion: item.version }); }
  patch(item: ReviewItem, patch: ReviewPatch) { return this.request<ReviewItem>(item.account, `items/${encodeURIComponent(item.id)}`, "PATCH", { expectedVersion: item.version, patch }); }
  reset(account: RestartAccount) { return this.request<ReviewList>(account, "reset", "POST", {}); }
  approveMany(account: RestartAccount, items: ReviewItem[]) { return this.request<{ id: string; item?: ReviewItem; error?: string }[]>(account, "approve", "POST", { items: items.map((item) => ({ id: item.id, expectedVersion: item.version })) }); }
}

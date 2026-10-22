import type { RestartAccount } from "../api/types";
import type { DemoQueueEntry, DemoQueueSnapshot, DemoQueueTask } from "./types";

export class DemoQueueClientError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

/** Same-origin demo queue client. It cannot target the Phase 3 or OTTO APIs. */
export class DemoQueueClient {
  private async request<T>(account: RestartAccount, path: string, method = "GET", body?: unknown, query: Record<string, string> = {}): Promise<T> {
    const params = new URLSearchParams({ account });
    for (const [key, value] of Object.entries(query)) params.set(key, value);
    let response: Response;
    try {
      response = await fetch(`/restart/api/demo/${path}?${params}`, {
        method, credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-OttoRestart-Demo": "1" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new DemoQueueClientError(0, "NETWORK_ERROR", "Нет соединения с демо-очередью. Повторите запрос.");
    }
    const data = await response.json().catch(() => null);
    if (!response.ok || !data) throw new DemoQueueClientError(response.status, data?.code ?? "INVALID_RESPONSE", data?.message ?? "Не удалось прочитать ответ демо-очереди.");
    return data as T;
  }

  snapshot(account: RestartAccount) { return this.request<DemoQueueSnapshot>(account, "queue"); }
  approvedItems(account: RestartAccount) { return this.request<{ items: { id: string; version: number; old: { title: string } }[] }>(account, "items", "GET", undefined, { status: "APPROVED" }); }
  enqueueDryRun(account: RestartAccount, items: DemoQueueEntry[]) { return this.request<{ queue: DemoQueueSnapshot; results: { id: string; task?: DemoQueueTask; error?: string }[] }>(account, "queue/enqueue-dry-run", "POST", { items }); }
  control(account: RestartAccount, action: "pause" | "resume" | "stop-accepting" | "start-accepting") { return this.request<DemoQueueSnapshot>(account, `queue/${action}`, "POST", {}); }
  runDryRun(account: RestartAccount, id: string) { return this.request<DemoQueueTask>(account, `queue/tasks/${encodeURIComponent(id)}/dry-run`, "POST", {}); }
  cancel(account: RestartAccount, id: string) { return this.request<DemoQueueTask>(account, `queue/tasks/${encodeURIComponent(id)}/cancel`, "POST", {}); }
}

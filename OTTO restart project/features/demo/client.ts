import type { RestartAccount, RestartPeriod } from "../api/types";
import type { DemoBatch, DemoBatchDetails, DemoBatchItem, DemoBatchPreview, DemoEanEntry, DemoEvent, DemoOverview, DemoPage, DemoSettings } from "./types";

export class DemoWorkspaceError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

/** Same-origin client for the isolated, session-only mock workspace. */
export class DemoWorkspaceClient {
  private async request<T>(account: RestartAccount, path: string, method = "GET", body?: unknown, query: Record<string, string | number> = {}): Promise<T> {
    const params = new URLSearchParams({ account });
    for (const [key, value] of Object.entries(query)) if (String(value) !== "") params.set(key, String(value));
    let response: Response;
    try {
      response = await fetch(`/restart/api/demo/${path}?${params}`, { method, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", "X-OttoRestart-Demo": "1" }, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch { throw new DemoWorkspaceError(0, "NETWORK_ERROR", "Демо-сервис недоступен. Повторите загрузку."); }
    const data = await response.json().catch(() => null);
    if (!response.ok || !data) throw new DemoWorkspaceError(response.status, data?.code ?? "INVALID_RESPONSE", data?.message ?? "Не удалось прочитать демо-ответ.");
    return data as T;
  }
  overview(account: RestartAccount) { return this.request<DemoOverview>(account, "overview"); }
  batches(account: RestartAccount, query = "", status = "", period = "", page = 1, pageSize = 20) { return this.request<DemoPage<DemoBatch>>(account, "batches", "GET", undefined, { q: query, status, period, page, pageSize }); }
  batch(account: RestartAccount, id: string) { return this.request<DemoBatch>(account, `batches/${encodeURIComponent(id)}`); }
  batchItems(account: RestartAccount, id: string, page = 1, pageSize = 50, query = "") { return this.request<DemoPage<DemoBatchItem>>(account, `batches/${encodeURIComponent(id)}/items`, "GET", undefined, { page, pageSize, q: query }); }
  previewBatch(account: RestartAccount, input: { period: RestartPeriod; name: string; itemCount: number }) { return this.request<DemoBatchPreview>(account, "batches/preview", "POST", input); }
  createBatch(account: RestartAccount, input: { period: RestartPeriod; name: string; itemCount: number }) { return this.request<DemoBatchDetails>(account, "batches", "POST", input); }
  eans(account: RestartAccount, status = "", query = "") { return this.request<DemoPage<DemoEanEntry>>(account, "ean-pool", "GET", undefined, { status, q: query }); }
  addDemoEans(account: RestartAccount, count = 4) { return this.request<{ mode: "DEMO"; importedCount: number; entries: DemoEanEntry[] }>(account, "ean-pool/demo-import", "POST", { count }); }
  events(account: RestartAccount, query = "") { return this.request<DemoPage<DemoEvent>>(account, "events", "GET", undefined, { q: query }); }
  settings(account: RestartAccount) { return this.request<DemoSettings>(account, "settings"); }
  saveSettings(account: RestartAccount, patch: Partial<Pick<DemoSettings, "defaultPeriod" | "maximumDemoBatchItems" | "strictAttributeChecks">>) { return this.request<DemoSettings>(account, "settings", "PATCH", patch); }
  reset(account: RestartAccount) { return this.request<{ mode: "DEMO"; account: RestartAccount; message: string }>(account, "reset", "POST", {}); }
}

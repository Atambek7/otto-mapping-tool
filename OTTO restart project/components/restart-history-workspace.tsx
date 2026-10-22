"use client";

import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAccount, RestartEvent } from "@/features/api/types";

export function RestartHistoryWorkspace() {
  const [account, setAccount] = useState<RestartAccount | "">("");
  const [events, setEvents] = useState<RestartEvent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const client = useMemo(() => { try { return new RestartApiClient(); } catch { return null; } }, []);

  async function load(selected: RestartAccount) {
    setLoading(true); setError("");
    try {
      const api = client ?? new RestartApiClient();
      const result = await api.listEvents(selected, { page: 1, pageSize: 50 });
      setEvents(result.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить историю.");
    } finally { setLoading(false); }
  }

  useEffect(() => { if (account) void load(account); else setEvents([]); /* account drives this request */ }, [account]);

  return <div className="workspace-stack">
    <section className="surface-card batches-list-card">
      <div className="card-heading"><div><h2>События OttoRestart</h2><p>История импорта партий и загрузки EAN, отдельно по аккаунту.</p></div>
        {account ? <button className="icon-button" aria-label="Обновить историю" onClick={() => void load(account)} type="button"><RefreshCw size={15} /></button> : null}
      </div>
      <label className="field-label detail-account">Аккаунт OTTO
        <select value={account} onChange={(event) => setAccount(event.target.value as RestartAccount | "")}>
          <option value="">Выберите аккаунт</option><option value="JV">JV</option><option value="XL">XL</option>
        </select>
      </label>
      {loading ? <ApiState kind="loading" label="Загружаем историю…" /> : null}
      {error ? <ApiState kind="error" description={error} onRetry={() => { if (account) void load(account); }} /> : null}
      {account && !loading && !error ? events.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>Событие</th><th>Объект</th><th>Детали</th><th>Время</th></tr></thead>
        <tbody>{events.map((event) => <tr key={event.id}><td><span className="status-pill">{event.eventType}</span></td><td>{event.entityType} · <span className="mono-value">{event.entityId.slice(0, 12)}</span></td><td>{event.summary}</td><td>{new Date(event.createdAt).toLocaleString("ru-RU")}</td></tr>)}</tbody>
      </table></div> : <div className="table-empty">Событий пока нет.</div> : null}
    </section>
  </div>;
}

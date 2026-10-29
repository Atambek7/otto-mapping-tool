"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Clock3, RefreshCw, Search } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { DemoWorkspaceClient } from "@/features/demo/client";
import type { DemoEvent } from "@/features/demo/types";
import { useRestartAccount } from "@/features/state/restart-account";

const client = new DemoWorkspaceClient();
export function DemoHistoryWorkspace() {
  const { account, ready } = useRestartAccount(); const [events, setEvents] = useState<DemoEvent[]>([]); const [search, setSearch] = useState(""); const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true); const [error, setError] = useState(""); const [reload, setReload] = useState(0);
  const load = useCallback(async (active: () => boolean) => { setLoading(true); setError(""); try { const result = await client.events(account, query); if (active()) setEvents(result.items); } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Не удалось загрузить demo-историю."); } finally { if (active()) setLoading(false); } }, [account, query]);
  useEffect(() => { if (!ready) return; let active = true; void load(() => active); return () => { active = false; }; }, [ready, load, reload]);
  function searchEvents(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setQuery(search.trim()); }
  return <div className="workspace-stack"><div className="demo-mode-banner"><Clock3 size={19} /><div><strong>Сессионный журнал</strong><span>Здесь записываются только синтетические данные и действия в интерфейсе для аккаунта {account}. Записи не являются историей OTTO.</span></div></div>
    <section className="surface-card batches-list-card"><div className="card-heading"><div><h2>История demo-операций</h2><p>Изменения карточек, тестовые партии, demo-пул и настройки.</p></div><button className="icon-button" aria-label="Обновить demo-историю" disabled={loading} onClick={() => setReload((value) => value + 1)} type="button"><RefreshCw size={15} /></button></div>
      <form className="item-search demo-list-search" onSubmit={searchEvents}><input aria-label="Поиск demo-событий" placeholder="EAN, событие, описание или ID" value={search} onChange={(event) => setSearch(event.target.value)} /><button className="demo-button" type="submit"><Search size={14} /> Найти</button></form>
      {loading ? <ApiState kind="loading" label="Загружаем demo-историю…" /> : null}{error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
      {!loading && !error && events.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>Событие</th><th>Объект</th><th>Подробности</th><th>Время</th></tr></thead><tbody>{events.map((event) => <tr key={event.id}><td><span className="status-pill">{event.eventType}</span></td><td>{event.entityType} · <span className="mono-value">{event.entityId.slice(0, 24)}</span></td><td>{event.summary}</td><td>{new Date(event.createdAt).toLocaleString("ru-RU")}</td></tr>)}</tbody></table></div> : null}
      {!loading && !error && !events.length ? <ApiState kind="empty" title="Нет событий по этому фильтру" description="Измените запрос или выполните действие в demo-проверке." /> : null}
    </section></div>;
}

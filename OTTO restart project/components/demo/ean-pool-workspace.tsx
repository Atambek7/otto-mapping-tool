"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Database, FlaskConical, Plus, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { DemoWorkspaceClient } from "@/features/demo/client";
import type { DemoEanEntry, DemoEanStatus } from "@/features/demo/types";
import { useRestartAccount } from "@/features/state/restart-account";

const client = new DemoWorkspaceClient();
const labels: Record<DemoEanStatus, string> = { AVAILABLE: "Свободен · demo", RESERVED: "Условный резерв", USED: "Использован · demo", QUARANTINED: "Карантин · demo" };
export function DemoEanPoolWorkspace() {
  const { account, ready } = useRestartAccount();
  const [entries, setEntries] = useState<DemoEanEntry[]>([]); const [status, setStatus] = useState(""); const [search, setSearch] = useState(""); const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [reload, setReload] = useState(0);
  const load = useCallback(async (active: () => boolean) => { setLoading(true); setError(""); try { const result = await client.eans(account); if (active()) setEntries(result.items); } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Не удалось загрузить demo-пул."); } finally { if (active()) setLoading(false); } }, [account]);
  useEffect(() => { if (!ready) return; let active = true; void load(() => active); return () => { active = false; }; }, [ready, load, reload]);
  async function importDemo() { if (busy) return; setBusy(true); setError(""); setNotice(""); try { const result = await client.addDemoEans(account, 4); setNotice(`Добавлено ${result.importedCount} условных DEMO-кодов в сессионный пул.`); setReload((value) => value + 1); } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось расширить demo-пул."); } finally { setBusy(false); } }
  function searchPool(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setQuery(search.trim()); }
  const filteredEntries = entries.filter((entry) => (!status || entry.status === status) && entry.ean.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const counts = { AVAILABLE: entries.filter((entry) => entry.status === "AVAILABLE").length, RESERVED: entries.filter((entry) => entry.status === "RESERVED").length, USED: entries.filter((entry) => entry.status === "USED").length, QUARANTINED: entries.filter((entry) => entry.status === "QUARANTINED").length };
  return <div className="workspace-stack">
    <div className="demo-mode-banner"><FlaskConical size={19} /><div><strong>Рабочий GTIN пул не подключён</strong><span>На этой странице только DEMO-коды вида DEMO-POOL-JV-A. Они не являются EAN/GTIN и не резервируются для реальных операций.</span></div><button className="demo-button" type="button" disabled={busy || loading} onClick={() => void importDemo()}><Plus size={14} /> Добавить 4 demo-кода</button></div>
    <div className="demo-pool-metrics">{(["AVAILABLE", "RESERVED", "USED", "QUARANTINED"] as const).map((key) => <article className="metric-card" key={key}><div className="metric-topline"><span>{labels[key]}</span><Database size={15} /></div><strong className="metric-value">{counts[key]}</strong></article>)}</div>
    {notice ? <p className="success-banner" role="status">{notice}</p> : null}{error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    <section className="surface-card batches-list-card"><div className="card-heading"><div><h2>Демо-пул · {account}</h2><p>Сессионные строки, исчезнут после перезапуска приложения.</p></div><button className="icon-button" aria-label="Обновить demo-пул" disabled={loading} onClick={() => setReload((value) => value + 1)} type="button"><RefreshCw size={15} /></button></div>
      <form className="item-search demo-list-search" onSubmit={searchPool}><input aria-label="Поиск demo-кода" placeholder="Поиск условного кода" value={search} onChange={(event) => setSearch(event.target.value)} /><button className="demo-button" type="submit"><Search size={14} /> Найти</button><select aria-label="Статус demo-кода" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Все статусы</option>{Object.entries(labels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></form>
      {loading ? <ApiState kind="loading" label="Загружаем demo-пул…" /> : null}{!loading && filteredEntries.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>Условный EAN</th><th>Статус</th><th>Партия</th><th>Сеанс</th></tr></thead><tbody>{filteredEntries.map((entry) => <tr key={entry.ean}><td className="mono-value">{entry.ean}</td><td><span className="status-pill">{labels[entry.status]}</span></td><td>{entry.batchId ?? "—"}</td><td>{new Date(entry.importedAt).toLocaleString("ru-RU")}</td></tr>)}</tbody></table></div> : null}
      {!loading && !filteredEntries.length ? <ApiState kind="empty" title="Нет demo-кодов по этому фильтру" description="Измените поиск или статус, либо добавьте синтетические коды." /> : null}
      <div className="safe-note"><ShieldCheck size={14} /> Импорт не вызывает API и не меняет никаких данных OttoFlow.</div>
    </section>
  </div>;
}

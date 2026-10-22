"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Archive, ArrowRight, BookOpenText, Clock3, Search, Tag } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { useRestartAccount } from "@/features/state/restart-account";
import { DemoWorkspaceClient } from "@/features/demo/client";
import { DemoReviewClient } from "@/features/review/client";
import { REVIEW_STATUS_LABELS } from "@/features/review/types";
import type { DemoEanEntry, DemoEvent } from "@/features/demo/types";
import type { ReviewItem } from "@/features/review/types";

const workspace = new DemoWorkspaceClient();
const review = new DemoReviewClient();
type LibraryTab = "links" | "pool" | "history";
const tabs: { id: LibraryTab; label: string }[] = [{ id: "links", label: "EAN-связи" }, { id: "pool", label: "Демо-пул" }, { id: "history", label: "История связей" }];
const poolStatus = { AVAILABLE: "Свободен · demo", RESERVED: "Условный резерв", USED: "Использован · demo", QUARANTINED: "Карантин · demo" } as const;

export function OttoLibraryWorkspace() {
  const { account, ready } = useRestartAccount();
  const [tab, setTab] = useState<LibraryTab>("links");
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [eans, setEans] = useState<DemoEanEntry[]>([]);
  const [events, setEvents] = useState<DemoEvent[]>([]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  const load = useCallback(async (active: () => boolean) => {
    setLoading(true); setError("");
    try {
      // Load sequentially so the first response establishes the session cookie
      // before the other requests read the same in-memory demo workspace.
      const pool = await workspace.eans(account, "", query);
      if (!active()) return;
      const candidates = await review.list(account, query, "", 1);
      if (!active()) return;
      const audit = await workspace.events(account, query);
      if (active()) { setEans(pool.items); setItems(candidates.items); setEvents(audit.items); }
    } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Не удалось загрузить OTTO Library."); }
    finally { if (active()) setLoading(false); }
  }, [account, query]);
  useEffect(() => { if (!ready) return; let active = true; void load(() => active); return () => { active = false; }; }, [ready, load, reload]);

  const linked = items.filter((item) => item.identity !== null).length;
  function submitSearch(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setQuery(search.trim()); }

  return <section className="page-section library-page">
    <div className="page-heading"><div><div className="eyebrow">ДАННЫЕ RESTART</div><h1>OTTO Library</h1><p>Синтетические EAN-связи, пул идентификаторов и история сессии {account}.</p></div><span className="phase-chip"><span /> DEMO · session only</span></div>
    <div className="library-summary-grid">
      <article className="library-summary-card"><span><BookOpenText size={17} /> EAN-связи</span><strong>{linked}</strong><small>связано в демо-карточках</small></article>
      <article className="library-summary-card"><span><Archive size={17} /> Демо-пул</span><strong>{eans.length}</strong><small>сессионные идентификаторы</small></article>
      <article className="library-summary-card"><span><Clock3 size={17} /> События</span><strong>{events.length}</strong><small>доступно в локальной истории</small></article>
    </div>
    <section className="surface-card library-card"><div className="library-toolbar"><div><div className="card-heading"><div><h2>Связи и история идентификаторов</h2><p>Данные разделены по аккаунту {account} и не покидают demo-сессию.</p></div></div></div><div className="library-toolbar-actions"><form className="item-search" onSubmit={submitSearch}><input aria-label="Поиск EAN или карточки" placeholder="Поиск EAN, товара или ID" value={search} onChange={(event) => setSearch(event.target.value)} /><button className="demo-button" type="submit"><Search size={14} /> Найти</button></form><button type="button" className="icon-button" aria-label="Обновить OTTO Library" disabled={loading} onClick={() => setReload((value) => value + 1)}><Tag size={15} /></button></div></div>
      <div className="library-tabs" role="tablist" aria-label="Содержимое OTTO Library">{tabs.map((value) => <button type="button" role="tab" aria-selected={tab === value.id} key={value.id} onClick={() => setTab(value.id)}>{value.label}{value.id === "links" ? <span>{items.length}</span> : null}</button>)}</div>
      {loading ? <ApiState kind="loading" label="Загружаем данные библиотеки…" /> : null}
      {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
      {!loading && !error && tab === "links" ? <><p className="library-lineage-note"><span className="library-note-dot" /> Aftercool source EAN в текущем API отсутствует. Ниже показана только существующая synthetic-связь исходного demo-ID с новым demo-EAN.</p>{items.length ? <div className="restart-table-wrap library-table-wrap"><table className="restart-table library-table"><thead><tr><th>Товар / партия</th><th>Aftercool source</th><th>Исходный ID · DEMO</th><th>Новый OTTO EAN · DEMO</th><th>Состояние</th><th>Последнее событие</th><th /></tr></thead><tbody>{items.map((item) => { const lastEvent = [...item.events].sort((a, b) => b.at.localeCompare(a.at))[0]; return <tr key={item.id}><td><strong>{item.prepared?.title ?? item.old.title}</strong><small className="demo-subline">{item.batchId} · {item.id}</small></td><td><span className="library-unavailable">Не подключён</span></td><td className="mono-value">{item.old.ean}</td><td className="mono-value">{item.identity?.newEan ?? "Не назначен"}</td><td><span className={`review-status review-status-${item.status.toLowerCase()}`}>{REVIEW_STATUS_LABELS[item.status]}</span></td><td>{lastEvent ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(lastEvent.at)) : "—"}</td><td><Link className="text-link" href={`/quality-control?item=${encodeURIComponent(item.id)}`} aria-label={`Открыть QC для ${item.old.title}`}><ArrowRight size={15} /></Link></td></tr>; })}</tbody></table></div> : <ApiState kind="empty" title="Связей по поиску нет" description="Подготовьте demo-карточку в разделе Candidates, чтобы сгенерировать условный новый EAN." />}</> : null}
      {!loading && !error && tab === "pool" ? eans.length ? <div className="restart-table-wrap library-table-wrap"><table className="restart-table library-table"><thead><tr><th>Условный EAN</th><th>Статус</th><th>Партия</th><th>Добавлен в сессию</th></tr></thead><tbody>{eans.map((entry) => <tr key={entry.ean}><td className="mono-value">{entry.ean}</td><td><span className="status-pill">{poolStatus[entry.status]}</span></td><td>{entry.batchId ?? "—"}</td><td>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeZone: "UTC" }).format(new Date(entry.importedAt))}</td></tr>)}</tbody></table></div> : <ApiState kind="empty" title="В demo-пуле нет записей по этому запросу" description="Пул хранит только синтетические значения, которые не являются GTIN." /> : null}
      {!loading && !error && tab === "history" ? events.length ? <div className="library-event-list">{events.map((event) => <article key={event.id}><span className="library-event-icon"><Clock3 size={14} /></span><div><strong>{event.summary}</strong><small>{event.eventType} · {event.entityId}</small></div><time>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(event.createdAt))}</time></article>)}</div> : <ApiState kind="empty" title="История пока пуста" description="События появятся после действий в демо-процессе." /> : null}
      <div className="library-legacy-links"><span>Полные прежние инструменты:</span><Link href="/ean-pool">Пул новых EAN <ArrowRight size={13} /></Link><Link href="/history">История OttoRestart <ArrowRight size={13} /></Link></div>
    </section>
  </section>;
}

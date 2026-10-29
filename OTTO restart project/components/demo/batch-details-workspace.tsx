"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ChevronLeft, ChevronRight, Download, FlaskConical, Search, ShieldCheck } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { DemoWorkspaceClient } from "@/features/demo/client";
import type { DemoBatch, DemoBatchItem, DemoPage } from "@/features/demo/types";
import { useRestartAccount } from "@/features/state/restart-account";

const client = new DemoWorkspaceClient();
const tabs = ["Кандидаты", "Подготовка", "Утверждение", "Публикация", "История"] as const;
export function DemoBatchDetailsWorkspace({ batchId }: { batchId: string }) {
  const { account, ready, choose } = useRestartAccount();
  const batchAccount = batchId.includes("-xl") ? "XL" : batchId.includes("-jv") ? "JV" : account;
  const [batch, setBatch] = useState<DemoBatch | null>(null); const [itemsPage, setItemsPage] = useState<DemoPage<DemoBatchItem> | null>(null); const [page, setPage] = useState(1); const [search, setSearch] = useState(""); const [query, setQuery] = useState("");
  const [tab, setTab] = useState<(typeof tabs)[number]>("Кандидаты"); const [loading, setLoading] = useState(true); const [error, setError] = useState(""); const [reload, setReload] = useState(0);
  const load = useCallback(async (active: () => boolean) => { setLoading(true); setError(""); try { const result = await client.batch(batchAccount, batchId); if (active()) setBatch(result); const rows = await client.batchItems(batchAccount, batchId, page, 50, query); if (active()) setItemsPage(rows); } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Не удалось открыть demo-партию."); } finally { if (active()) setLoading(false); } }, [batchAccount, batchId, page, query]);
  useEffect(() => { if (ready && batchAccount !== account) choose(batchAccount); }, [ready, batchAccount]);
  useEffect(() => { if (!ready) return; let active = true; void load(() => active); return () => { active = false; }; }, [ready, load, reload]);
  function exportRows() {
    if (!batch) return;
    const rows = [["row", "source_demo_id", "title", "status", "issue"], ...(itemsPage?.items ?? []).map((item) => [String(item.rowNumber), item.sourceEan, item.title, item.status, item.issue ?? ""])];
    const csv = `\uFEFF${rows.map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(",")).join("\r\n")}`;
    const anchor = document.createElement("a"); anchor.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); anchor.download = `${batch.id}-demo-report.csv`; anchor.click(); URL.revokeObjectURL(anchor.href);
  }
  function searchItems(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setPage(1); setQuery(search.trim()); }
  return <section className="page-section"><div className="detail-toolbar"><Link className="text-link" href="/batches"><ArrowLeft size={14} /> Все демо-партии</Link></div>
    {loading ? <ApiState kind="loading" label="Загружаем demo-партию…" /> : null}{error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {batch ? <>
      <div className="page-heading"><div><div className="eyebrow">ПАРТИЯ RESTART · {batch.account} · {batch.period}</div><h1>{batch.name}</h1><p>{batch.fileName} · создана {new Date(batch.createdAt).toLocaleString("ru-RU")}. Строки хранятся только в памяти demo-сессии.</p></div><span className="phase-chip"><span /> DEMO · локально</span></div>
      <div className="demo-mode-banner"><FlaskConical size={19} /><div><strong>{batch.itemCount} синтетических строк</strong><span>Данные не являются EAN/GTIN и не вызывают внешние сервисы.</span></div><button className="demo-button" type="button" onClick={exportRows}><Download size={14} /> Demo-отчёт</button></div>
      <WorkflowStepper active="batches" />
      <section className="batch-progress-card" aria-label="Прогресс проверки партии"><div><strong>Проверка строк</strong><span>{batch.validatedCount} из {batch.itemCount} проверено</span></div><div className="batch-progress-track"><span style={{ width: `${batch.itemCount ? Math.min(100, Math.round(batch.validatedCount / batch.itemCount * 100)) : 0}%` }} /></div><small>Ошибки: {batch.errorCount} · утверждено в демо: {batch.approvedCount}</small></section>
      <div className="demo-batch-stats"><span>Строк <strong>{batch.itemCount}</strong></span><span>Проверено <strong>{batch.validatedCount}</strong></span><span>С замечаниями <strong>{batch.errorCount}</strong></span><span>Утверждено в демо <strong>{batch.approvedCount}</strong></span><span className="demo-lock-stat"><ShieldCheck size={14} /> Publish выключен</span></div>
      <div className="review-tabs" role="tablist" aria-label="Этапы demo-партии">{tabs.map((value) => <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{value}</button>)}</div>
      <section className="surface-card demo-batch-tab-panel">
        {tab === "Кандидаты" ? <><div className="card-heading"><div><h2>Синтетические кандидаты</h2><p>Серверная пагинация и поиск; значения DEMO не являются GTIN/EAN.</p></div></div><form className="item-search demo-list-search" onSubmit={searchItems}><input aria-label="Поиск синтетического товара" placeholder="Demo-ID или название" value={search} onChange={(event) => setSearch(event.target.value)} /><button className="demo-button" type="submit"><Search size={14} /> Найти</button></form><div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>Строка</th><th>Тестовый ID</th><th>Название</th><th>Состояние</th><th>Цена · demo</th><th>Замечание</th></tr></thead><tbody>{(itemsPage?.items ?? []).map((item) => <tr key={item.id}><td>{item.rowNumber}</td><td className="mono-value">{item.sourceEan}</td><td>{item.title}</td><td><span className="status-pill">{item.status}</span></td><td>{item.standardPriceCents === null ? "—" : `${(item.standardPriceCents / 100).toFixed(2)} EUR`}</td><td>{item.issue ?? "—"}</td></tr>)}</tbody></table></div><div className="pagination-bar"><span>Страница {itemsPage?.page.page ?? 1} из {Math.max(1, itemsPage?.page.totalPages ?? 1)} · строк: {itemsPage?.page.totalItems ?? 0} · экспортирует текущую страницу</span><div><button type="button" aria-label="Предыдущая страница" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></button><button type="button" aria-label="Следующая страница" disabled={!itemsPage || page >= itemsPage.page.totalPages || loading} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></button></div></div></> : null}
        {tab === "Подготовка" ? <div className="demo-tab-empty"><h2>Подготовка демонстрируется на тестовой партии</h2><p>Откройте встроенную demo-проверку {batch.account}, чтобы попробовать подготовку, предложение тестового названия, проверку и сценарий цены.</p><Link className="primary-action" href={`/review`}>Открыть Candidates</Link></div> : null}
        {tab === "Утверждение" ? <div className="demo-tab-empty"><h2>Утверждение доступно только для проверенной demo-карточки</h2><p>Approval записывает только локальный снимок synthetic payload. Для этой созданной партии данные источника отсутствуют.</p><Link className="primary-action" href={`/review?tab=approval`}>Перейти к demo-проверке</Link></div> : null}
        {tab === "Публикация" ? <div className="demo-tab-empty"><ShieldCheck size={34} /><h2>Публикация выключена</h2><p>Очередь доступна только для локального dry-run одобренных demo-карточек. Запросы к OTTO не выполняются.</p><Link className="text-link" href="/queue">Открыть demo-очередь →</Link></div> : null}
        {tab === "История" ? <div className="demo-tab-empty"><h2>События демо-партии</h2><p>События создания и локальных действий собраны в общей истории OttoRestart.</p><Link className="text-link" href={`/history?q=${encodeURIComponent(batch.id)}`}>Посмотреть события →</Link></div> : null}
      </section>
    </> : null}
  </section>;
}

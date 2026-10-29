"use client";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { ArrowRight, CheckCheck, ChevronLeft, ChevronRight, FlaskConical, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { useRestartAccount } from "@/features/state/restart-account";
import { DemoReviewClient } from "@/features/review/client";
import { REVIEW_STATUS_LABELS } from "@/features/review/types";
import type { ReviewAction, ReviewItem, ReviewList, ReviewPatch, ReviewTab } from "@/features/review/types";
import type { RestartAccount } from "@/features/api/types";
import { ItemDrawer, formatPrice, formatTime } from "./item-drawer";
import "./review-workspace.css";

const client = new DemoReviewClient();
const tabs: { id: ReviewTab; label: string }[] = [{ id: "candidates", label: "Кандидаты" }, { id: "preparation", label: "Подготовка" }, { id: "approval", label: "Утверждение" }, { id: "publication", label: "Публикация" }, { id: "history", label: "История" }];

export function ReviewWorkspace({ initialAccount }: { initialAccount?: RestartAccount }) {
  const { account, ready, choose } = useRestartAccount();
  const [tab, setTab] = useState<ReviewTab>("candidates");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [filtersReady, setFiltersReady] = useState(false);
  const [data, setData] = useState<ReviewList | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drawer, setDrawer] = useState<ReviewItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [drawerError, setDrawerError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const scope = useRef(account);
  const openRequest = useRef(0);
  scope.current = account;

  useEffect(() => {
    const params = new URL(window.location.href).searchParams;
    const savedTab = params.get("tab");
    const savedQuery = params.get("q") ?? "";
    if (tabs.some((value) => value.id === savedTab)) setTab(savedTab as ReviewTab);
    setSearch(savedQuery); setQuery(savedQuery); setStatus(params.get("status") ?? "");
    setPage(Math.max(1, Number(params.get("page")) || 1));
    setFiltersReady(true);
  }, []);
  useEffect(() => { if (ready && initialAccount) choose(initialAccount); }, [ready, initialAccount]); // Only initial demo-batch scope overrides stored selection.
  useEffect(() => {
    if (!filtersReady || !ready) return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab); url.searchParams.set("page", String(page));
    if (query) url.searchParams.set("q", query); else url.searchParams.delete("q");
    if (status) url.searchParams.set("status", status); else url.searchParams.delete("status");
    window.history.replaceState(null, "", url);
  }, [tab, query, status, page, filtersReady, ready]);
  useEffect(() => {
    openRequest.current += 1;
    setSelected(new Set()); setDrawer(null); setNotice("");
  }, [account, query, status, page]);
  useEffect(() => {
    if (!ready || !filtersReady) return;
    let active = true;
    setLoading(true); setError(""); setData(null);
    client.list(account, query, status, page).then((result) => { if (active) setData(result); }).catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : "Ошибка демо-API."); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [account, query, status, page, reload, ready, filtersReady]);

  async function openItem(item: ReviewItem) {
    const requestId = ++openRequest.current;
    setDrawerError(""); setDrawer(item);
    try { const fresh = await client.get(item.account, item.id); if (scope.current === fresh.account && openRequest.current === requestId) setDrawer(fresh); }
    catch (cause) { if (openRequest.current === requestId) setDrawerError(cause instanceof Error ? cause.message : "Не удалось открыть карточку."); }
  }
  function replaceInList(item: ReviewItem) {
    if (scope.current !== item.account) return;
    setData((current) => current ? { ...current, items: current.items.map((row) => row.id === item.id ? item : row) } : null);
  }
  function update(item: ReviewItem) {
    if (scope.current !== item.account) return;
    setDrawer(item);
    replaceInList(item);
  }
  async function action(action: ReviewAction) {
    if (!drawer || busy) return;
    setBusy(true); setDrawerError("");
    try {
      let result = await client.act(drawer, action);
      if (action === "simulate-price-change") result = await client.act(result, "refresh-price");
      update(result);
    } catch (cause) { setDrawerError(cause instanceof Error ? cause.message : "Действие не выполнено."); }
    finally { setBusy(false); }
  }
  async function save(patch: ReviewPatch) {
    if (!drawer || busy) return;
    setBusy(true); setDrawerError("");
    try { update(await client.patch(drawer, patch)); }
    catch (cause) { setDrawerError(cause instanceof Error ? cause.message : "Правки не сохранены."); }
    finally { setBusy(false); }
  }
  async function prepare(item: ReviewItem) {
    setBusy(true); setError("");
    try { update(await client.act(item, "prepare")); setReload((value) => value + 1); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Подготовка не выполнена."); }
    finally { setBusy(false); }
  }
  async function bulkApprove() {
    const items = data?.items.filter((item) => selected.has(item.id) && item.status === "READY_FOR_APPROVAL") ?? [];
    if (!items.length || !window.confirm(`Утвердить ${items.length} выбранных демо-карточек? Сервер заново проверит цену и версию каждой записи.`)) return;
    setBusy(true); setError("");
    try {
      const results = await client.approveMany(account, items);
      const approved = results.filter((result) => result.item?.status === "APPROVED").length;
      setNotice(`Утверждено в демо: ${approved}. Требуют проверки: ${results.length - approved}.`);
      setSelected(new Set()); setReload((value) => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Массовое утверждение не выполнено."); }
    finally { setBusy(false); }
  }
  async function prepareSelectedWithAi() {
    const items = data?.items.filter((item) => selected.has(item.id)) ?? [];
    if (!items.length || busy) return;
    if (items.some((item) => item.status === "READY_FOR_APPROVAL" || item.status === "APPROVED") && !window.confirm("Создание нового AI-заголовка сбросит прежнее демо-утверждение выбранных карточек. Продолжить?")) return;
    setBusy(true); setError(""); setNotice("");
    let assignedEans = 0;
    let generatedTitles = 0;
    const skipped: string[] = [];
    try {
      for (const selectedItem of items) {
        try {
          let item = await client.get(account, selectedItem.id);
          const hadIdentity = Boolean(item.identity);
          if (!item.identity || !item.prepared) item = await client.act(item, "prepare");
          replaceInList(item);
          if (!item.identity || !item.prepared || item.old.marketplaceStatus === "RESTRICTED" || item.status === "PRICE_UNAVAILABLE" || item.status === "PRICE_REVIEW_REQUIRED") {
            skipped.push(`${item.old.ean}: ${item.issues[0]?.message ?? REVIEW_STATUS_LABELS[item.status]}`);
            continue;
          }
          item = await client.act(item, "suggest");
          replaceInList(item);
          if (!hadIdentity) assignedEans += 1;
          generatedTitles += 1;
        } catch (cause) {
          skipped.push(`${selectedItem.old.ean}: ${cause instanceof Error ? cause.message : "действие не выполнено"}`);
        }
      }
      const skippedText = skipped.length
        ? ` Пропущено ${skipped.length}: ${skipped.slice(0, 2).join("; ")}${skipped.length > 2 ? "; …" : ""}`
        : "";
      setNotice(`DEMO: назначено новых EAN — ${assignedEans}; создано AI-заголовков — ${generatedTitles}.${skippedText}`);
      setSelected(new Set());
      setReload((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Массовая подготовка не выполнена.");
    } finally { setBusy(false); }
  }
  async function reset() {
    if (!window.confirm("Сбросить весь демонстрационный набор JV и XL? Условные правки, резервы и утверждения будут удалены.")) return;
    setBusy(true);
    try { await client.reset(account); setDrawer(null); setSelected(new Set()); setPage(1); setReload((value) => value + 1); setNotice("Демо-набор сброшен."); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Сброс не выполнен."); }
    finally { setBusy(false); }
  }
  function searchItems(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setPage(1); setQuery(search.trim()); }
  const pageItems = data?.items ?? [];
  const selectedItems = pageItems.filter((item) => selected.has(item.id));
  const selectedReadyCount = selectedItems.filter((item) => item.status === "READY_FOR_APPROVAL").length;
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selectedItems.length > 0 && selectedItems.length < pageItems.length;
  }, [selectedItems.length, pageItems.length]);

  return <section className="page-section demo-review">
    <div className="page-heading"><div><div className="eyebrow">РАБОЧИЙ ПРОЦЕСС · {account}</div><h1>{initialAccount ? "Демо-партия" : "Кандидаты 3/12M"}</h1><p>Поиск, подготовка и demo-утверждение карточек Restart для выбранного аккаунта.</p></div><span className="review-demo-label"><FlaskConical size={14} /> DEMO · локально</span></div>
    <WorkflowStepper active={initialAccount ? "batches" : "candidates"} />
    {initialAccount ? <div className="review-reset-row"><span>Карточки, цены и AI-заголовки синтетические; рабочий EAN-пул не используется.</span><button type="button" className="review-secondary" onClick={() => void reset()} disabled={busy}>Сбросить демо</button></div> : null}
    <div className="review-batch-bar"><div><strong>Демо-партия {account}</strong><span>5 сценариев · Restart 3/12M · публикация закрыта</span></div><div className="review-account-tabs" aria-label="Аккаунт демонстрационной партии">{(["JV", "XL"] as const).map((value) => <button type="button" aria-pressed={account === value} key={value} disabled={busy} onClick={() => { choose(value); setPage(1); }}>{value} · −{value === "JV" ? "10" : "15"} €</button>)}</div><Link className="text-link" href={`/batches/demo-${account.toLowerCase()}`}>Открыть демо-партию <ArrowRight size={14} /></Link></div>
    <div className="review-tabs" role="tablist" aria-label="Шаги партии">{tabs.map((value) => <button type="button" id={`review-tab-${value.id}`} role="tab" aria-selected={tab === value.id} aria-controls="review-tab-panel" tabIndex={tab === value.id ? 0 : -1} key={value.id} onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); const index = tabs.findIndex((entry) => entry.id === tab); const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length; setTab(tabs[next].id); document.getElementById(`review-tab-${tabs[next].id}`)?.focus(); } }} onClick={() => setTab(value.id)}>{value.label}</button>)}</div>
    <div role="tabpanel" id="review-tab-panel" aria-labelledby={`review-tab-${tab}`} className="review-tab-content">
      {tab === "publication" ? <div className="review-publication"><ShieldCheck size={35} /><h2>Публикация отключена</h2><p>Утверждение доступно только для demo-снимка. Реальные карточки не создаются в OTTO, ONLINE не присваивается. Можно проверить только локальную очередь dry-run.</p><Link className="review-link-button" href="/queue">Открыть demo-очередь →</Link></div> : <>
        <div className="review-toolbar"><form className="item-search" onSubmit={searchItems}><input aria-label="Поиск по EAN или названию" placeholder="EAN, название или сценарий" value={search} onChange={(event) => setSearch(event.target.value)} /><button className="review-secondary" type="submit">Найти</button></form><select aria-label="Фильтр статуса Restart" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="">Все статусы</option>{Object.entries(REVIEW_STATUS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select><button className="review-secondary" type="button" disabled={busy} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} /> Обновить</button></div>
        {loading ? <ApiState kind="loading" /> : null}
        {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
        {notice ? <p className="success-banner" role="status">{notice}</p> : null}
        {data ? <div className="review-pool-summary"><span>Демо-EAN: свободно <strong>{data.pool.available}</strong> · зарезервировано <strong>{data.pool.reserved}</strong></span><small>JV и XL имеют независимые условные пулы.</small></div> : null}
        {!loading && !error && !pageItems.length ? <ApiState kind="empty" title="Нет карточек по выбранному фильтру" description="Измените поиск или статус." /> : null}
        {tab === "history" ? <div className="review-timeline">{pageItems.flatMap((item) => item.events.map((event) => ({ ...event, item }))).sort((a, b) => b.at.localeCompare(a.at)).map((event) => <article key={`${event.item.id}-${event.version}`}><time>{formatTime(event.at)}</time><strong>{event.description}</strong><span>{event.item.old.ean} · v{event.version}</span></article>)}{data && !pageItems.some((item) => item.events.length) ? <ApiState kind="empty" title="История демо-партии пуста" description="Подготовьте или измените карточку, чтобы увидеть события." /> : null}</div> : pageItems.length ? <>
          <div className="review-selection-bar">
            <span>Выбрано {selectedItems.length} · готово к утверждению {selectedReadyCount}</span>
            <div className="review-selection-actions">
              {selectedItems.length ? <button type="button" className="primary-action" disabled={busy} onClick={() => void prepareSelectedWithAi()} title="Используются только условные EAN и локальная AI-заглушка."><Sparkles size={14} /> Назначить EAN + AI-заголовок · DEMO</button> : null}
              {selectedReadyCount ? <button type="button" className="review-secondary" disabled={busy} onClick={() => void bulkApprove()}><CheckCheck size={14} /> Утвердить выбранные в демо</button> : null}
            </div>
          </div>
          <div className="restart-table-wrap review-table-wrap"><table className="restart-table review-table"><thead><tr><th><input ref={selectAllRef} type="checkbox" aria-label="Выбрать все товары на этой странице" aria-checked={selectedItems.length > 0 && selectedItems.length < pageItems.length ? "mixed" : selectedItems.length === pageItems.length && pageItems.length > 0} checked={pageItems.length > 0 && selectedItems.length === pageItems.length} disabled={busy || !pageItems.length} onChange={(event) => setSelected((current) => { const next = new Set(current); for (const item of pageItems) { if (event.target.checked) next.add(item.id); else next.delete(item.id); } return next; })} /></th><th>Товар</th><th>OLD → NEW</th><th>StandardPrice · демо</th><th>Проверка</th><th>OTTO · демо</th><th>Restart</th><th>Действия</th></tr></thead><tbody>{pageItems.map((item) => <tr key={item.id}><td><input type="checkbox" disabled={busy} aria-label={`Выбрать ${item.old.ean}`} checked={selected.has(item.id)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next; })} /></td><td><div className="review-product-cell"><img src="/restart/demo-chair.svg" alt="" /><div><strong>{item.prepared?.title ?? item.old.title}</strong><span>{item.scenario}</span></div></div></td><td className="review-id-cell"><span>{item.old.ean}</span><span>→ {item.identity?.newEan ?? "Не зарезервирован"}</span></td><td>{item.price ? <div className="review-price-cell"><strong>{formatPrice(item.price.newCents)}</strong><span>{formatPrice(item.price.baseCents)} − {formatPrice(item.price.discountCents)}</span><small>{formatTime(item.price.fetchedAt)}</small></div> : <span className="review-muted">Не прочитана</span>}</td><td><span className={item.issues.length ? "review-danger-text" : "review-muted"}>{item.issues.length ? `${item.issues.length} блокировок` : item.status === "READY_FOR_APPROVAL" || item.status === "APPROVED" ? "Проверено" : "Ожидает проверки"}</span></td><td><div className="review-otto-status"><span>active: {item.old.activeStatus}</span><span>marketplace: {item.old.marketplaceStatus}</span></div></td><td><span className={`review-status review-status-${item.status.toLowerCase()}`}>{REVIEW_STATUS_LABELS[item.status]}</span></td><td><button className="review-secondary" type="button" disabled={busy} onClick={() => void openItem(item)}>Сравнить</button>{tab === "preparation" && !item.prepared ? <button className="review-link-button" type="button" disabled={busy} onClick={() => void prepare(item)}>Подготовить</button> : null}</td></tr>)}</tbody></table></div>
          {data ? <div className="pagination-bar"><span>Страница {data.page.page} · найдено {data.page.totalItems} · выбор охватывает только эту страницу</span><div><button type="button" aria-label="Предыдущая страница" disabled={page <= 1 || busy} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></button><button type="button" aria-label="Следующая страница" disabled={page >= data.page.totalPages || busy} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></button></div></div> : null}
        </> : null}
      </>}
    </div>
    {drawer ? <ItemDrawer key={`${drawer.account}-${drawer.id}`} item={drawer} busy={busy} error={drawerError} onClose={() => { openRequest.current += 1; setDrawer(null); setReload((value) => value + 1); }} onAction={(value) => void action(value)} onSave={(value) => void save(value)} onReload={() => void openItem(drawer)} /> : null}
  </section>;
}

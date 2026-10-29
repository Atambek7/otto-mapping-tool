"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, CheckCheck, CircleAlert, FlaskConical, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { useRestartAccount } from "@/features/state/restart-account";
import { DemoReviewClient } from "@/features/review/client";
import { REVIEW_STATUS_LABELS } from "@/features/review/types";
import type { ReviewAction, ReviewItem, ReviewList } from "@/features/review/types";

const client = new DemoReviewClient();

function titleOf(item: ReviewItem) { return item.prepared?.title ?? item.old.title; }
function fieldsOf(item: ReviewItem) {
  const fields = new Set([...Object.keys(item.old.attributes), ...Object.keys(item.prepared?.attributes ?? {})]);
  return [...fields].map((key) => ({ key, before: item.old.attributes[key] ?? "—", after: item.prepared?.attributes[key] ?? "—" }));
}

export function QualityControlWorkspace() {
  const { account, ready } = useRestartAccount();
  const [data, setData] = useState<ReviewList | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    setLoading(true); setError(""); setData(null); setSelectedId("");
    client.list(account, query, "", 1).then((result) => {
      if (!active) return;
      setData(result);
      const requestedId = new URL(window.location.href).searchParams.get("item");
      setSelectedId((current) => result.items.some((item) => item.id === current) ? current : result.items.find((item) => item.id === requestedId)?.id ?? result.items[0]?.id ?? "");
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : "Не удалось загрузить карточки для проверки.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [account, query, ready, reload]);

  const item = data?.items.find((candidate) => candidate.id === selectedId) ?? data?.items[0] ?? null;
  async function act(action: ReviewAction) {
    if (!item || busy) return;
    if (action === "approve" && !window.confirm(`Утвердить подготовленную версию в демо для ${account}? Публикация отключена.`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await client.act(item, action);
      setData((current) => current ? { ...current, items: current.items.map((row) => row.id === updated.id ? updated : row) } : current);
      setNotice(({ prepare: "Карточка подготовлена в демо.", suggest: "AI-заголовок создан демонстрационной заглушкой.", validate: "Проверка подготовленной карточки завершена.", approve: "Версия утверждена в демо-сессии.", "refresh-price": "Цена обновлена в демо-снимке.", "simulate-price-change": "Демо-сценарий изменения цены выполнен." } satisfies Record<ReviewAction, string>)[action]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Действие проверки не выполнено."); }
    finally { setBusy(false); }
  }
  function submitSearch(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setQuery(search.trim()); }

  return <section className="page-section qc-page">
    <div className="page-heading"><div><div className="eyebrow">КОНТРОЛЬ КАЧЕСТВА</div><h1>Quality Control</h1><p>Сравнение исходной и подготовленной карточки перед demo-утверждением.</p></div><span className="phase-chip"><span /> DEMO · локальные fixtures</span></div>
    <WorkflowStepper active="quality" />
    <div className="qc-toolbar"><form className="item-search" onSubmit={submitSearch}><input aria-label="Поиск карточки для проверки" placeholder="Название, EAN или сценарий" value={search} onChange={(event) => setSearch(event.target.value)} /><button type="submit" className="demo-button">Найти</button></form><label className="qc-item-select"><span>Карточка · {account}</span><select aria-label="Выбрать demo-карточку" value={item?.id ?? ""} onChange={(event) => setSelectedId(event.target.value)} disabled={!data?.items.length}>{(data?.items ?? []).map((candidate) => <option value={candidate.id} key={candidate.id}>{titleOf(candidate)} · {candidate.id}</option>)}</select></label><button type="button" className="icon-button" aria-label="Обновить карточки" disabled={loading || busy} onClick={() => setReload((value) => value + 1)}><RefreshCw size={16} /></button></div>
    {loading ? <ApiState kind="loading" label="Загружаем карточки для QC…" /> : null}
    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {notice ? <p className="success-banner qc-notice" role="status">{notice}</p> : null}
    {!loading && !error && !item ? <ApiState kind="empty" title="Карточки не найдены" description="Измените поиск или переключите аккаунт." /> : null}
    {item ? <>
      <div className="qc-product-summary"><div><span className="qc-product-avatar">{item.old.title.slice(0, 1)}</span><div><strong>{titleOf(item)}</strong><small>{item.scenario} · {item.old.sku} · {item.old.ean}</small></div></div><span className={`review-status review-status-${item.status.toLowerCase()}`}>{REVIEW_STATUS_LABELS[item.status]}</span></div>
      <div className="qc-comparison-grid">
        <article className="qc-side-card"><div className="qc-side-heading"><div><span>01 · ИСХОДНЫЕ ДАННЫЕ</span><h2>Старая карточка</h2></div><span className="qc-state-label">Источник · DEMO</span></div><div className="qc-image-placeholder"><img src="/restart/demo-chair.svg" alt="Синтетическое изображение товара" /></div><dl className="qc-details"><div><dt>Название</dt><dd>{item.old.title}</dd></div><div><dt>Категория</dt><dd>{item.old.category}</dd></div><div><dt>Старый EAN · DEMO</dt><dd className="mono-value">{item.old.ean}</dd></div><div><dt>Артикул</dt><dd className="mono-value">{item.old.sku}</dd></div>{Object.entries(item.old.attributes).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value || "—"}</dd></div>)}</dl></article>
        <article className="qc-side-card qc-after-card"><div className="qc-side-heading"><div><span>02 · ПОДГОТОВЛЕННАЯ ВЕРСИЯ</span><h2>Новая карточка</h2></div><span className={`qc-state-label${item.prepared ? " ready" : ""}`}>{item.prepared ? "Подготовлено · DEMO" : "Ожидает подготовки"}</span></div>
          {item.prepared ? <><div className="qc-image-placeholder"><img src="/restart/demo-chair.svg" alt="Синтетическое изображение подготовленной карточки" /></div><dl className="qc-details"><div><dt>Новый title</dt><dd>{item.prepared.title}</dd></div><div><dt>Категория</dt><dd>{item.prepared.category}</dd></div><div><dt>Новый EAN · DEMO</dt><dd className="mono-value">{item.identity?.newEan ?? "Не назначен"}</dd></div>{fieldsOf(item).map(({ key, before, after }) => <div key={key} className={before !== after ? "changed" : ""}><dt>{key}{before !== after ? <small>{before} →</small> : null}</dt><dd>{after || "—"}</dd></div>)}</dl></> : <div className="qc-not-prepared"><ShieldCheck size={30} /><strong>Версия ещё не подготовлена</strong><span>Подготовка использует только синтетические данные и отдельный DEMO-пул.</span><button className="primary-action" type="button" disabled={busy} onClick={() => void act("prepare")}>Подготовить карточку · DEMO</button></div>}
          {item.issues.length ? <div className="qc-warnings"><strong><CircleAlert size={15} /> Замечания проверки</strong>{item.issues.map((issue) => <span key={`${issue.code}-${issue.field}`}>{issue.field ? `${issue.field}: ` : ""}{issue.message}</span>)}</div> : item.prepared ? <div className="qc-clean"><ShieldCheck size={15} /> Нет сохранённых замечаний demo-валидации</div> : null}
        </article>
      </div>
      <div className="qc-actions"><div><strong>Действия для этой демо-версии</strong><span>Внешние сервисы не вызываются; публикация отключена.</span></div><div>{item.prepared ? <><button type="button" className="review-secondary" disabled={busy} onClick={() => void act("suggest")}><Sparkles size={14} /> AI-title · демо</button><button type="button" className="review-secondary" disabled={busy} onClick={() => void act("validate")}>Проверить</button><button type="button" className="primary-action" disabled={busy || item.status !== "READY_FOR_APPROVAL"} onClick={() => void act("approve")}><CheckCheck size={15} /> Утвердить в демо</button></> : null}<Link className="text-link" href={`/review?q=${encodeURIComponent(item.id)}`}>Полный редактор <ArrowRight size={14} /></Link></div></div>
    </> : null}
    <p className="qc-disclaimer"><FlaskConical size={14} /> Все значения на странице синтетические. EAN, цена и заголовки не подтверждают данные каталога OTTO.</p>
  </section>;
}

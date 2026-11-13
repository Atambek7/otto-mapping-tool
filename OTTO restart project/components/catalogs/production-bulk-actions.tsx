"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import type { RestartAccount } from "@/features/api/types";
import type { CatalogProductItem } from "@/features/catalogs/types";
import { assignSelectedEan, buildBulkProposal, saveBulkProposal, type BulkContentProposal, type CatalogBulkAction } from "@/features/catalogs/bulk-actions";
import "./production-bulk-actions.css";

const titles: Record<CatalogBulkAction, string> = {
  "assign-ean": "Назначить новые EAN", defaults: "Стандартные значения",
  "ai-title": "ИИ-названия", "ai-category": "ИИ-категории", "ai-attributes": "ИИ-характеристики по фото",
};
interface ResultRow {
  id: string; label: string; key: string; checked: boolean;
  proposal?: BulkContentProposal; ean?: string; error?: string; applied?: boolean;
}

export function ProductionBulkActions({ action, products, account, onClose, onSaved }: {
  action: CatalogBulkAction; products: CatalogProductItem[]; account: RestartAccount;
  onClose: () => void; onSaved: () => void;
}) {
  const api = useMemo(() => new RestartApiClient(), []);
  const dialog = useRef<HTMLDialogElement>(null);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const [rows, setRows] = useState<ResultRow[]>(() => products.map((product) => ({
    id: product.id, label: `${product.sourceEan || product.ean} · ${product.title}`,
    key: crypto.randomUUID(), checked: true,
  })));
  const [phase, setPhase] = useState<"idle" | "running" | "review" | "saving" | "done">("idle");
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const busy = phase === "running" || phase === "saving";
  const isEan = action === "assign-ean";
  const pending = rows.filter((row) => row.proposal?.changes.length && row.checked && !row.applied);

  useEffect(() => {
    mounted.current = true;
    dialog.current?.showModal();
    return () => { mounted.current = false; controller.current?.abort(); dialog.current?.close(); };
  }, []);

  async function requestProposals() {
    if (busy) return;
    const targets = rows.filter((row) => !row.applied && !row.ean && (!row.proposal || row.error));
    if (!targets.length) return;
    const abort = new AbortController(); controller.current = abort;
    setPhase("running"); setProgress({ done: 0, total: targets.length });
    let cursor = 0, completed = 0;
    async function worker() {
      while (!abort.signal.aborted) {
        const row = targets[cursor++]; if (!row) return;
        try {
          const result = isEan
            ? { ean: await assignSelectedEan(api, row.id, account, row.key, abort.signal) }
            : { proposal: await buildBulkProposal(api, action as Exclude<CatalogBulkAction, "assign-ean">, row.id, account, abort.signal) };
          if (mounted.current) setRows((current) => current.map((item) => item.id === row.id ? { ...item, ...result, error: undefined } : item));
        } catch (error) {
          if (mounted.current) setRows((current) => current.map((item) => item.id === row.id ? { ...item, error: abort.signal.aborted ? "Запрос отменён" : error instanceof Error ? error.message : String(error) } : item));
        } finally {
          completed += 1;
          if (mounted.current) setProgress({ done: completed, total: targets.length });
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(isEan ? 1 : 2, targets.length) }, () => worker()));
    if (mounted.current) { setPhase(isEan ? "done" : "review"); if (isEan) onSaved(); }
  }

  async function applySelected() {
    if (busy || !pending.length) return;
    setPhase("saving"); setProgress({ done: 0, total: pending.length });
    let done = 0;
    for (const row of pending) {
      try {
        await saveBulkProposal(api, row.proposal!, `${row.key}-save`);
        if (mounted.current) setRows((current) => current.map((item) => item.id === row.id ? { ...item, applied: true, error: undefined } : item));
      } catch (error) {
        const message = error instanceof RestartApiError && error.code === "VERSION_CONFLICT"
          ? "Карточка уже изменена. Закройте окно и обновите данные; чужие изменения не перезаписаны."
          : error instanceof Error ? error.message : String(error);
        if (mounted.current) setRows((current) => current.map((item) => item.id === row.id ? { ...item, error: message } : item));
      } finally { done += 1; if (mounted.current) setProgress({ done, total: pending.length }); }
    }
    if (mounted.current) { setPhase("review"); onSaved(); }
  }

  return <dialog ref={dialog} className="production-bulk-dialog" aria-labelledby="production-bulk-title" onCancel={(event) => {
    event.preventDefault(); if (phase !== "saving") { controller.current?.abort(); onClose(); }
  }}>
    <header><div><h2 id="production-bulk-title">{titles[action]}</h2><p>{account} · выбрано {products.length} товаров</p></div><button type="button" aria-label="Закрыть" disabled={phase === "saving"} onClick={() => { controller.current?.abort(); onClose(); }}><X size={20} /></button></header>
    <div className="production-bulk-body">
      <p className="production-bulk-note">{isEan
        ? "Будет назначен один свободный EAN из пула каждому выбранному товару. Уже назначенные EAN сохранятся. Публикация не запускается."
        : "Получите предложения, проверьте изменения и сохраните выбранные карточки на сервере. Цена и EAN не меняются. После сохранения потребуется повторная проверка карточки."}</p>
      {busy ? <div className="production-bulk-progress" role="status"><Loader2 size={16} className="spin" />{phase === "saving" ? "Сохранение" : isEan ? "Назначение EAN" : "Подготовка предложений"}: {progress.done} / {progress.total}<progress value={progress.done} max={progress.total || 1} /></div> : null}
      {rows.map((row) => <article className="production-bulk-product" key={row.id}>
        <div className="production-bulk-product-heading">{!isEan && row.proposal?.changes.length && !row.applied ? <input type="checkbox" checked={row.checked} disabled={busy} aria-label={`Сохранить ${row.label}`} onChange={(event) => setRows((current) => current.map((item) => item.id === row.id ? { ...item, checked: event.target.checked } : item))} /> : null}<strong>{row.label}</strong>{row.applied ? <span className="production-bulk-success"><Check size={14} />Сохранено</span> : null}</div>
        {row.ean ? <p className="production-bulk-success">Назначен EAN: <code>{row.ean}</code></p> : null}
        {row.error ? <p role="alert" className="production-bulk-error">{row.error}</p> : null}
        {row.proposal ? row.proposal.changes.length ? <div className="production-bulk-diff"><div className="production-bulk-diff-head"><span>Поле</span><span>Сейчас</span><span>Предлагается</span></div>{row.proposal.changes.map((change) => <div key={change.field}><strong>{change.field}</strong><span>{change.before || "—"}</span><span>{change.after || "—"}</span></div>)}</div> : <p className="production-bulk-note">Изменений не требуется.</p> : null}
      </article>)}
    </div>
    <footer><span>{isEan ? `EAN назначены: ${rows.filter((row) => row.ean).length}` : `Сохранено: ${rows.filter((row) => row.applied).length} · выбрано к сохранению: ${pending.length}`}</span><div>
      <button type="button" className="catalog-action-btn" disabled={phase === "saving"} onClick={() => { controller.current?.abort(); onClose(); }}>Закрыть</button>
      {!busy && rows.some((row) => !row.applied && !row.ean && (!row.proposal || row.error)) ? <button type="button" className="catalog-action-btn" onClick={() => void requestProposals()}>{phase === "idle" ? isEan ? "Назначить EAN" : "Получить предложения" : "Повторить ошибки"}</button> : null}
      {!isEan && pending.length ? <button type="button" className="primary-action" disabled={busy} onClick={() => void applySelected()}>Сохранить изменения ({pending.length})</button> : null}
    </div></footer>
  </dialog>;
}

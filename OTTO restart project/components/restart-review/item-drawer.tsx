"use client";
import { useEffect, useRef, useState } from "react";
import { CheckCheck, RefreshCw, ShieldCheck, Sparkles, X } from "lucide-react";
import { DEMO_CATEGORIES, DEMO_TITLE_LIMIT, REVIEW_STATUS_LABELS } from "@/features/review/types";
import type { ReviewAction, ReviewItem, ReviewPatch } from "@/features/review/types";

export const formatPrice = (cents: number) => new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(cents / 100);
export const formatTime = (iso: string) => new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "medium", timeZone: "UTC" }).format(new Date(iso)) + " UTC";
const fields = [{ key: "material", label: "Материал" }, { key: "color", label: "Цвет" }, { key: "width", label: "Ширина, см" }, { key: "height", label: "Высота, см" }];
const draftOf = (item: ReviewItem): ReviewPatch | null => item.prepared ? { title: item.prepared.title, category: item.prepared.category, attributes: { ...item.prepared.attributes }, images: [...item.prepared.images] } : null;

export function ItemDrawer({ item, busy, error, onClose, onAction, onSave, onReload }: {
  item: ReviewItem; busy: boolean; error: string;
  onClose: () => void; onAction: (action: ReviewAction) => void; onSave: (patch: ReviewPatch) => void; onReload: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [draft, setDraft] = useState<ReviewPatch | null>(() => draftOf(item));
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(item));
  const differences = draft ? [draft.title !== item.old.title ? "Название" : "", draft.category !== item.old.category ? "Категория" : "", ...fields.filter(({ key }) => draft.attributes[key] !== item.old.attributes[key]).map(({ label }) => label), JSON.stringify(draft.images) !== JSON.stringify(item.old.images) ? "Изображения" : ""].filter(Boolean) : [];
  useEffect(() => { setDraft(draftOf(item)); }, [item.id, item.version]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  function close() { if (!busy && (!dirty || window.confirm("Закрыть карточку и отбросить несохранённые правки?"))) onClose(); }
  function change<K extends keyof ReviewPatch>(key: K, value: ReviewPatch[K]) { setDraft((current) => current ? { ...current, [key]: value } : null); }
  function approve() { if (window.confirm(`Утвердить демо-версию ${item.payloadVersion} для ${item.account}? Это фиксирует карточку и цену в демо-сессии.`)) onAction("approve"); }

  return <dialog ref={dialog} className="review-drawer" aria-labelledby="review-drawer-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <header className="review-drawer-header"><div><span className="review-demo-label">DEMO · {item.account} · v{item.version}</span><h2 id="review-drawer-title">Старый товар ↔ Новый товар</h2><p>{item.scenario}</p></div><button type="button" className="icon-button" aria-label="Закрыть карточку" disabled={busy} onClick={close}><X size={20} /></button></header>
    <div className="review-drawer-body">
      <div className="review-comparison">
        <article><h3>Исходная карточка <span>условные данные</span></h3>
          <img className="review-product-image" src="/restart/demo-chair.svg" alt="Демонстрационная иллюстрация кресла" />
          <dl className="review-identity"><dt>Old EAN / SKU</dt><dd>{item.old.ean}</dd><dt>Product Reference</dt><dd>{item.old.productReference}</dd></dl>
          <label className="review-field">Название<div className="review-readonly">{item.old.title}</div></label>
          <label className="review-field">Категория<div className="review-readonly">{DEMO_CATEGORIES.find((category) => category.id === item.old.category)?.label}</div></label>
          <div className="review-attributes">{fields.map(({ key, label }) => <label className="review-field" key={key}>{label}<div className="review-readonly">{item.old.attributes[key] || "Не заполнено"}</div></label>)}</div>
          <div className="review-price-panel"><span>Демо-ответ: standardPrice старого SKU</span><strong>{item.price ? formatPrice(item.price.baseCents) : "Цена не прочитана"}</strong><small>activeStatus: {item.old.activeStatus}<br />marketplaceStatus: {item.old.marketplaceStatus}</small></div>
        </article>
        <article><h3>Подготовленная карточка <span>{REVIEW_STATUS_LABELS[item.status]}</span></h3>
          {draft ? <>
            {draft.images.length ? <img className="review-product-image" src="/restart/demo-chair.svg" alt="Иллюстрация подготовленной демо-карточки" /> : <div className="review-product-image review-empty-image">Нет подготовленного изображения</div>}
            <dl className="review-identity"><dt>New EAN / SKU</dt><dd>{item.identity?.newEan}</dd><dt>Product Reference</dt><dd>{item.identity?.newProductReference}</dd></dl>
            <label className="review-field">Название<textarea value={draft.title} maxLength={500} disabled={busy} onChange={(event) => change("title", event.target.value)} /><small className={[...draft.title].length > DEMO_TITLE_LIMIT ? "review-danger-text" : ""}>{[...draft.title].length}/{DEMO_TITLE_LIMIT} · ограничение демо-схемы</small></label>
            <label className="review-field">Категория<select value={draft.category} disabled={busy} onChange={(event) => change("category", event.target.value)}>{DEMO_CATEGORIES.map((category) => <option value={category.id} key={category.id}>{category.label}</option>)}</select></label>
            <div className="review-attributes">{fields.map(({ key, label }) => <label className="review-field" key={key}>{label}<input value={draft.attributes[key] ?? ""} maxLength={200} disabled={busy} onChange={(event) => change("attributes", { ...draft.attributes, [key]: event.target.value })} className={draft.attributes[key] !== item.old.attributes[key] ? "review-field-changed" : ""} /></label>)}</div>
            <label className="review-image-toggle"><input type="checkbox" checked={draft.images.includes("chair")} disabled={busy} onChange={(event) => change("images", event.target.checked ? ["chair"] : [])} /> Включить локальную демо-иллюстрацию</label>
            <div className="review-price-panel"><span>Новая standardPrice · расчёт в целых центах</span><strong>{item.price ? `${formatPrice(item.price.baseCents)} − ${formatPrice(item.price.discountCents)} = ${formatPrice(item.price.newCents)}` : "Цена недоступна"}</strong><small>{item.price ? `Источник: DEMO_OTTO_STANDARD_PRICE · снимок ${item.price.snapshotVersion}\n${formatTime(item.price.fetchedAt)}` : "Без цены утверждение невозможно."}</small></div>
          </> : <div className="review-not-prepared"><ShieldCheck size={28} /><strong>Карточка ещё не подготовлена</strong><p>При подготовке сервер проверит демо-цену и зарезервирует условный идентификатор из отдельного демо-пула.</p><button className="primary-action button-action" type="button" disabled={busy} onClick={() => onAction("prepare")}>Подготовить карточку</button></div>}
        </article>
      </div>
      <div className="review-mode-note"><strong>NEW_PRODUCT_REFERENCE</strong><span>Новые идентификаторы совпадают. Повторное использование старого Product Reference отключено. Все значения DEMO-* не являются настоящими GTIN.</span></div>
      {draft ? <div className="review-diff"><strong>Отличия Old → New</strong><span>Идентификаторы · цена{differences.length ? ` · ${differences.join(" · ")}` : " · содержание скопировано из источника"}</span></div> : null}
      {item.prepared?.titleOrigin === "DEMO_AI_STUB" ? <p className="review-ai-note"><Sparkles size={15} /> Название предложено локальной AI-заглушкой; внешняя модель не вызывалась.</p> : null}
      {dirty ? <p className="review-ai-note">Есть несохранённые правки. Сохранение сбросит прежнее утверждение.</p> : null}
      {error ? <p role="alert" className="review-inline-error">{error} <button type="button" disabled={busy} onClick={onReload}>Обновить карточку</button></p> : null}
      {item.issues.length ? <div className="review-issues" role="status"><h3>Причины блокировки</h3><ul>{item.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><strong>{issue.code}</strong><span>{issue.message}</span></li>)}</ul></div> : null}
      {item.approval ? <div className="review-approval"><CheckCheck size={21} /><div><strong>Утверждена демо-версия {item.approval.payloadVersion}</strong><span>Снимок цены {item.approval.priceSnapshotVersion} · {formatTime(item.approval.approvedAt)}</span><code title={item.approval.payloadHash}>SHA-256: {item.approval.payloadHash.slice(0, 24)}…</code></div></div> : null}
      <details className="review-audit"><summary>История карточки · {item.events.length} событий</summary><ol>{[...item.events].reverse().map((event) => <li key={event.version}><time>{formatTime(event.at)}</time><strong>{event.action} · v{event.version}</strong><span>{event.description}</span></li>)}</ol></details>
    </div>
    <footer className="review-drawer-footer">
      <div className="review-footer-actions">
        {draft ? <><button className="review-secondary" type="button" disabled={busy || !dirty} onClick={() => onSave(draft)}>Сохранить правки</button><button className="review-secondary" type="button" disabled={busy || dirty} onClick={() => onAction("suggest")}><Sparkles size={14} /> AI-название · демо</button></> : null}
        <button className="review-secondary" type="button" disabled={busy || dirty} onClick={() => onAction("refresh-price")}><RefreshCw size={14} /> Обновить цену</button>
        <button className="review-secondary" type="button" disabled={busy || dirty || !draft} onClick={() => onAction("validate")}>Проверить</button>
        <button className="primary-action button-action" type="button" disabled={busy || dirty || item.status !== "READY_FOR_APPROVAL"} onClick={approve}>Утвердить демо-версию</button>
      </div>
      <div className="review-footer-hint"><span>{busy ? "Сервер обрабатывает действие…" : "Утверждение сохраняет версию. Публикация отключена."}</span><button type="button" disabled={busy || dirty} onClick={() => onAction("simulate-price-change")}>Демо-сценарий: standardPrice +10 €</button></div>
    </footer>
  </dialog>;
}

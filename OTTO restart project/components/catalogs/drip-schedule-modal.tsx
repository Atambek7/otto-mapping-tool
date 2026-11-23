"use client";

import React, { useState, useMemo, useEffect, useRef } from "react";
import {
  Calendar,
  Check,
  Clock,
  Layers,
  Loader2,
  Play,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import type { CatalogProductItem } from "@/features/catalogs/types";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAccount, RestartDripPlanningPreview } from "@/features/api/types";
import { isOttoRestartDemoMode } from "@/features/runtime-mode";
import { useTranslation } from "@/features/i18n";

const restartApi = new RestartApiClient();

interface DripScheduleModalProps {
  isOpen: boolean;
  onClose: () => void;
  factoryName?: string;
  totalProductsCount: number;
  products?: CatalogProductItem[];
  selectionCount?: number;
  scopeFilters?: Record<string, unknown>;
  onScheduleCreated?: (result: { account: RestartAccount; requestId: string | null; batchIds: string[]; itemCount: number; status: string; demo: boolean }) => void;
}

const PRESET_DAYS = [3, 7, 14, 30];

function durationUnit(value: number, units: { one: string; few: string; many: string }): string {
  const lastTwo = value % 100;
  const lastOne = value % 10;
  if (lastOne === 1 && lastTwo !== 11) return units.one;
  if (lastOne >= 2 && lastOne <= 4 && (lastTwo < 12 || lastTwo > 14)) return units.few;
  return units.many;
}

export function isDripCandidate(p: CatalogProductItem): boolean {
  return (p.status === "READY" || p.status === "ERROR") && !!p.category.trim()
    && (!p.operationStatus || ["DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"].includes(p.operationStatus));
}

export function DripScheduleModal({
  isOpen,
  onClose,
  factoryName,
  totalProductsCount,
  products = [],
  selectionCount,
  scopeFilters,
  onScheduleCreated,
}: DripScheduleModalProps) {
  const { t } = useTranslation();
  const resolvedFactoryName = factoryName || t("drip.defaultFactoryName");
  const { account } = useRestartAccount();
  const [daysInput, setDaysInput] = useState("7");
  const [dailyTime, setDailyTime] = useState<string>("08:00");
  const [excludePublished, setExcludePublished] = useState<boolean>(true);
  const [antiCollision, setAntiCollision] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [allMatching, setAllMatching] = useState(false);
  const [serverPreview, setServerPreview] = useState<RestartDripPlanningPreview | null>(null);
  const [serverPreviewKey, setServerPreviewKey] = useState("");
  const [previewRefresh, setPreviewRefresh] = useState(0);
  const [previewError, setPreviewError] = useState("");
  const demo = isOttoRestartDemoMode("afterbuy");
  const submissionKey = useRef<{ signature: string; key: string } | null>(null);
  const candidateIds = products.filter(isDripCandidate).map((p) => p.id);
  const selectionKey = JSON.stringify(allMatching && scopeFilters ? { filters: scopeFilters } : { candidateIds });
  const parsedDays = /^\d+$/.test(daysInput) ? Number(daysInput) : Number.NaN;
  const isDaysValid = Number.isInteger(parsedDays) && parsedDays >= 1 && parsedDays <= 30;
  const days = isDaysValid ? parsedDays : 0;
  const safeDays = isDaysValid ? parsedDays : 1;
  const periodLabel = `${daysInput || "—"} ${durationUnit(days, {
    one: t("drip.daysCount1"), few: t("drip.daysCount3"), many: t("drip.daysCountN"),
  })}`;
  const body = useMemo(() => {
    const now = new Date();
    const localTime = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Berlin", hour: "2-digit", minute: "2-digit", hour12: false }).format(now);
    let date = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
    if (localTime >= dailyTime) date = new Date(Date.parse(`${date}T12:00:00Z`) + 86400000).toISOString().slice(0, 10);
    return { ...JSON.parse(selectionKey), name: resolvedFactoryName, startDate: date, dailyTime: `${dailyTime}:00`, timezone: "Europe/Berlin", totalDays: days };
  }, [selectionKey, dailyTime, days, resolvedFactoryName]);
  const previewRequestKey = JSON.stringify({ account, days, body });
  const currentServerPreview = serverPreviewKey === previewRequestKey ? serverPreview : null;
  useEffect(() => {
    if (!isOpen || demo) return;
    const controller = new AbortController();
    setServerPreview(null);
    setServerPreviewKey("");
    if (!isDaysValid) {
      setPreviewError(t("drip.invalidCustomDuration"));
      return () => controller.abort();
    }
    setPreviewError("");
    const requestKey = previewRequestKey;
    restartApi.previewDripPlanning(account, body, controller.signal).then((value) => {
      if (!controller.signal.aborted) {
        setServerPreview(value);
        setServerPreviewKey(requestKey);
      }
    }).catch((error) => { if (!controller.signal.aborted) setPreviewError(String(error)); });
    return () => controller.abort();
  }, [isOpen, demo, account, body, isDaysValid, daysInput, previewRequestKey, previewRefresh, t]);

  const count = demo ? totalProductsCount : currentServerPreview?.itemCount ?? 0;
  const averagePerDay = currentServerPreview?.averagePerDay ?? (isDaysValid ? count / days : 0);
  const firstRunAtLabel = currentServerPreview?.firstRunAt
    ? new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Berlin", dateStyle: "medium", timeStyle: "short" }).format(new Date(currentServerPreview.firstRunAt))
    : "—";
  const distribution = useMemo(() => {
    if (!isDaysValid) return [];
    if (!demo) return (currentServerPreview?.distribution ?? []).map((d) => ({ dayNum: d.dayNumber, count: d.count, dateFormatted: new Date(d.scheduledAt).toLocaleDateString("ru-RU", { timeZone: "Europe/Berlin", month: "short", day: "numeric" }) }));
    return Array.from({ length: safeDays }, (_, i) => ({ dayNum: i + 1, count: Math.floor(count / safeDays) + (i < count % safeDays ? 1 : 0), dateFormatted: "" }));
  }, [demo, currentServerPreview, isDaysValid, safeDays, count]);

  const maxDayCount = useMemo(() => {
    return Math.max(...distribution.map((d) => d.count), 1);
  }, [distribution]);

  if (!isOpen) return null;

  async function handleSubmit() {
    if (!isDaysValid) {
      setPreviewError(t("drip.invalidCustomDuration"));
      return;
    }
    setIsSubmitting(true);
    const isDemo = isOttoRestartDemoMode("afterbuy");

    if (isDemo) {
      setTimeout(() => {
        setIsSubmitting(false);
        onScheduleCreated?.({ account, requestId: null, batchIds: [], itemCount: count, status: "DEMO", demo: true });
        onClose();
      }, 450);
      return;
    }

    try {
      if (!currentServerPreview || !currentServerPreview.itemCount) throw new Error("Сначала дождитесь расчёта плана.");
      const createBody = { ...body, previewHash: currentServerPreview.selectionHash };
      const signature = JSON.stringify({ account, createBody });
      if (submissionKey.current?.signature !== signature) submissionKey.current = { signature, key: `drip-plan-${crypto.randomUUID()}` };
      const result = await restartApi.createDripPlanning(account, createBody, submissionKey.current.key);
      onScheduleCreated?.({ account, requestId: result.id, batchIds: result.batchIds ?? [], itemCount: result.itemCount, status: result.status, demo: false });
      onClose();
      setIsSubmitting(false);
    } catch (err) {
      setPreviewError(`Не удалось создать расписание: ${err instanceof Error ? err.message : String(err)}`);
      setServerPreview(null);
      setServerPreviewKey("");
      setPreviewRefresh((value) => value + 1);
      setIsSubmitting(false);
    }
  }

  return (
    <div className="drip-modal-backdrop" onClick={() => { if (!isSubmitting) onClose(); }} role="dialog" aria-modal="true">
      <div
        className="drip-modal-window"
        onClick={(e) => e.stopPropagation()}
        aria-label={t("drip.scheduleTitle")}
      >
        {/* Header */}
        <div className="drip-modal-header">
          <div className="drip-header-left">
            <div className="drip-icon-box">
              <Clock size={20} className="drip-icon-clock" />
            </div>
            <div>
              <div className="drip-title-row">
                <h2 className="drip-title-h2">{t("drip.scheduleTitle")}</h2>
                <span className="drip-account-chip">{account} Möbel</span>
              </div>
              <p className="drip-subtitle">
                {t("drip.subline", { factoryName: resolvedFactoryName })}
              </p>
            </div>
          </div>

          <button
            type="button"
            className="drip-close-btn"
            onClick={onClose}
            disabled={isSubmitting}
            aria-label={t("common.close")}
          >
            <X size={16} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="drip-modal-body">
          {!demo && scopeFilters && selectionCount === undefined ? <label className="drip-toggle-card"><input type="checkbox" checked={allMatching} disabled={isSubmitting} onChange={(event) => setAllMatching(event.target.checked)} /> Все подходящие товары по текущим фильтрам, включая другие страницы</label> : null}
          {previewError ? <p role="alert">{previewError}</p> : null}
          {/* Section 1: Parameters (Days + Time) */}
          <div className="drip-card-block">
            <div className="drip-card-header">
              <SlidersHorizontal size={15} className="text-orange-400" />
              <span>{t("drip.paramsTitle")}</span>
            </div>

            <div className="drip-params-grid">
              {/* Days Selector */}
              <div className="drip-param-col">
                <label className="drip-label">
                  <Calendar size={13} className="text-muted" />
                  <span>{t("drip.durationDays")}</span>
                </label>
                <div className="drip-preset-chips">
                  {PRESET_DAYS.map((d) => {
                    const isActive = days === d;
                    return (
                      <button
                        key={d}
                        type="button"
                        disabled={isSubmitting}
                        onClick={() => setDaysInput(String(d))}
                        className={`drip-preset-btn ${isActive ? "active" : ""}`}
                      >
                        <span className="preset-number">{d}</span>
                        <span className="preset-unit">{d === 3 ? t("drip.daysCount3") : t("drip.daysCountN")}</span>
                      </button>
                    );
                  })}
                </div>
                <label className="drip-custom-days-label" htmlFor="drip-custom-days">{t("drip.customDuration")}</label>
                <input
                  id="drip-custom-days"
                  className="drip-custom-days-field"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={30}
                  step={1}
                  value={daysInput}
                  aria-invalid={!isDaysValid}
                  aria-describedby="drip-custom-days-help"
                  disabled={isSubmitting}
                  onChange={(event) => setDaysInput(event.target.value)}
                />
                <span id="drip-custom-days-help" className={`drip-custom-days-help ${isDaysValid ? "" : "invalid"}`}>
                  {isDaysValid ? t("drip.customDurationHint") : t("drip.invalidCustomDuration")}
                </span>
              </div>

              {/* Time Selector */}
              <div className="drip-param-col">
                <label className="drip-label">
                  <Clock size={13} className="text-muted" />
                  <span>{t("drip.dailyStartTime")}</span>
                </label>
                <div className="drip-time-input-box">
                  <input
                    type="time"
                    value={dailyTime}
                    disabled={isSubmitting}
                    onChange={(e) => setDailyTime(e.target.value)}
                    className="drip-time-field"
                  />
                  <span className="drip-tz-tag">Europe/Berlin (CET)</span>
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: Safeguards & Options */}
          {isOttoRestartDemoMode("afterbuy") ? <div className="drip-card-block">
            <div className="drip-card-header">
              <ShieldCheck size={15} className="text-emerald-400" />
              <span>{t("drip.safetyRules")}</span>
            </div>

            <div className="drip-toggles-stack">
              {/* Toggle 1: Exclude Published */}
              <div
                className={`drip-toggle-card ${excludePublished ? "toggle-active" : ""}`}
                onClick={() => setExcludePublished((v) => !v)}
              >
                <div className="drip-toggle-checkbox">
                  <div className={`custom-checkbox ${excludePublished ? "checked" : ""}`}>
                    {excludePublished && <Check size={12} strokeWidth={3} />}
                  </div>
                </div>
                <div className="drip-toggle-content">
                  <span className="drip-toggle-title">
                    {t("drip.excludePublished")}
                  </span>
                  <p className="drip-toggle-desc">
                    {t("drip.excludePublishedDesc")}
                  </p>
                </div>
              </div>

              {/* Toggle 2: Anti-Collision */}
              <div
                className={`drip-toggle-card ${antiCollision ? "toggle-active" : ""}`}
                onClick={() => setAntiCollision((v) => !v)}
              >
                <div className="drip-toggle-checkbox">
                  <div className={`custom-checkbox ${antiCollision ? "checked" : ""}`}>
                    {antiCollision && <Check size={12} strokeWidth={3} />}
                  </div>
                </div>
                <div className="drip-toggle-content">
                  <div className="drip-toggle-title-row">
                    <span className="drip-toggle-title">
                      {t("drip.antiCollision")}
                    </span>
                    <span className="drip-rec-badge">
                      <ShieldCheck size={11} style={{ marginRight: 4, verticalAlign: "middle" }} />
                      {t("drip.recommended")}
                    </span>
                  </div>
                  <p className="drip-toggle-desc">
                    {t("drip.antiCollisionDesc")}
                  </p>
                </div>
              </div>
            </div>
          </div> : <p className="drip-subtitle">{selectionCount !== undefined ? `Выбрано товаров: ${selectionCount}. Подходят для плана: ${count}. Остальные исключены по статусу, категории или состоянию операции.` : "Учитываются подходящие товары текущей страницы."} Категории распределяются по дням; для совпадающего исходного EAN в другом аккаунте применяется интервал безопасности.</p>}

          {/* Section 3: Distribution Timeline & Rate */}
          <div className="drip-card-block">
            <div className="drip-card-header">
              <Layers size={15} className="text-amber-400" />
              <span>Ежедневное распределение и разбивка по категориям</span>
            </div>

            {/* Metrics Strip */}
            <div className="drip-kpi-strip">
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">{t("drip.totalProducts")}</span>
                <strong className="drip-kpi-value text-orange">{count}</strong>
                {!demo && currentServerPreview ? <small>Карточка: {currentServerPreview.readyCount} готово · {currentServerPreview.needsCorrectionCount} требует исправления; это не результат QC</small> : null}
              </div>
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">{t("drip.dailyAverage")}</span>
                <strong className="drip-kpi-value text-emerald">
                  {isDaysValid ? `~${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(averagePerDay)} ${t("drip.itemsPerDayUnit")}` : "—"}
                </strong>
              </div>
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">{t("drip.dischargePeriod")}</span>
                <strong className="drip-kpi-value text-amber">{isDaysValid ? periodLabel : t("drip.invalidDurationShort")}</strong>
                {!demo && currentServerPreview ? <small>Первый запуск: {firstRunAtLabel}</small> : null}
              </div>
            </div>

            {!demo && currentServerPreview ? <div className="drip-chart-container" style={{overflowX: "auto", maxHeight: 260}}>
              <table style={{width: "100%", textAlign: "left", borderCollapse: "collapse"}}>
                <thead><tr><th>День</th><th>Товары</th><th>Распределение по категориям</th></tr></thead>
                <tbody>{currentServerPreview.distribution.map((item) => <tr key={item.scheduledAt}>
                  <td style={{padding: "10px"}}>День {item.dayNumber}<br /><small>{new Date(item.scheduledAt).toLocaleString("ru-RU", {timeZone: "Europe/Berlin", dateStyle: "medium", timeStyle: "short"})}</small></td>
                  <td style={{padding: "10px"}}>{item.count}</td>
                  <td style={{padding: "10px"}}>{Object.entries(item.categories ?? {}).map(([category, amount]) => <span key={category} style={{display: "inline-block", margin: "3px", padding: "3px 7px", borderRadius: 6, background: "var(--surface-hover, #202b3a)"}}>{category || "Без категории"} ×{amount}</span>)}</td>
                </tr>)}</tbody>
              </table>
              <small>Дата и категория каждого товара сохраняются при создании. Если выбор или параметры изменились, создание потребует обновить расчёт.</small>
            </div> : <div className="drip-chart-container"><div className="drip-chart-bars">{distribution.map((item) => <div key={item.dayNum} className="drip-chart-col"><span className="drip-bar-count">{item.count}</span><div className="drip-bar-track"><div className="drip-bar-fill" style={{height: `${Math.max(30, Math.round((item.count / maxDayCount) * 100))}%`}} /></div><span className="drip-bar-day">{t("drip.dayN", {day: item.dayNum})}</span></div>)}</div></div>}

          </div>
        </div>

        {/* Footer */}
        <div className="drip-modal-footer">
          <div className="drip-footer-summary">
            <span>{t("drip.totalSummary", { total: count, period: periodLabel })}</span>
          </div>

          <div className="drip-footer-actions">
            <button
              type="button"
              className="drip-btn-secondary"
              onClick={onClose}
              disabled={isSubmitting || (!demo && !currentServerPreview)}
            >
              {t("common.cancel")}
            </button>

            <button
              type="button"
              className="drip-btn-primary"
              onClick={handleSubmit}
              disabled={isSubmitting || !isDaysValid || count === 0 || (!demo && !currentServerPreview)}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={15} className="spin" />
                  <span>{t("drip.startingSchedule")}</span>
                </>
              ) : (
                <>
                  <Play size={14} fill="currentColor" />
                  <span>Создать расписание Drip Feed</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import React, { useState, useMemo } from "react";
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
  onScheduleCreated?: (days: number, batchName?: string) => void;
}

const PRESET_DAYS = [3, 7, 14, 30];

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
  onScheduleCreated,
}: DripScheduleModalProps) {
  const { t } = useTranslation();
  const resolvedFactoryName = factoryName || t("drip.defaultFactoryName");
  const { account } = useRestartAccount();
  const [days, setDays] = useState<number>(7);
  const [dailyTime, setDailyTime] = useState<string>("08:00");
  const [excludePublished, setExcludePublished] = useState<boolean>(true);
  const [antiCollision, setAntiCollision] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Distribution calculation
  const distribution = useMemo(() => {
    const total = Math.max(0, totalProductsCount);
    const basePerDay = Math.floor(total / days);
    const remainder = total % days;

    return Array.from({ length: days }, (_, idx) => {
      const count = idx < remainder ? basePerDay + 1 : basePerDay;
      const d = new Date();
      d.setDate(d.getDate() + idx + 1);
      const dayLabel = d.toLocaleDateString("ru-RU", {
        weekday: "short",
        day: "numeric",
        month: "short",
      });
      return {
        dayNum: idx + 1,
        dateFormatted: dayLabel,
        count,
      };
    });
  }, [totalProductsCount, days]);

  const maxDayCount = useMemo(() => {
    return Math.max(...distribution.map((d) => d.count), 1);
  }, [distribution]);

  if (!isOpen) return null;

  async function handleSubmit() {
    setIsSubmitting(true);
    const isDemo = isOttoRestartDemoMode("afterbuy");

    if (isDemo) {
      setTimeout(() => {
        setIsSubmitting(false);
        onScheduleCreated?.(days);
        onClose();
      }, 450);
      return;
    }

    try {
      const readyItems = products.filter(isDripCandidate);
      if (readyItems.length === 0) {
        alert(t("drip.noReadyItemsError"));
        setIsSubmitting(false);
        return;
      }

      const operationIds: string[] = [];
      for (const item of readyItems) {
        if (item.operationId) operationIds.push(item.operationId);
        else operationIds.push((await restartApi.createOperation(account, item.id, `drip-operation-${crypto.randomUUID()}`)).id);
      }
      const tomorrow = new Date(Date.now() + 86400000);
      const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin", year: "numeric", month: "2-digit", day: "2-digit" }).format(tomorrow);
      const batchName = `${resolvedFactoryName} · ${account} · ${days}д (${readyItems.length} тов.)`;
      const timeVal = dailyTime.length === 5 ? `${dailyTime}:00` : dailyTime;

      const idempotencyKey = `drip-create-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const batch = await restartApi.createDripBatch(
        {
          account,
          name: batchName,
          operationIds,
          startDate: today,
          dailyTime: timeVal,
          timezone: "Europe/Berlin",
          totalDays: days,
        },
        idempotencyKey
      );

      if (readyItems.some((item) => item.status === "ERROR" || item.operationStatus !== "READY")) {
        setIsSubmitting(false);
        alert("План сохранён как черновик. Подготовьте товары, исправьте ошибки и пройдите QC, затем запустите план в очереди.");
        onClose();
        return;
      }

      const startKey = `drip-start-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      await restartApi.controlDripBatch(
        batch.id,
        account,
        "start",
        batch.version,
        startKey
      );

      setIsSubmitting(false);
      onScheduleCreated?.(days, batch.name);
      onClose();
    } catch (err) {
      console.error("Drip schedule start failed:", err);
      alert(
        `Ошибка запуска Drip Feed: ${err instanceof Error ? err.message : String(err)}`
      );
      setIsSubmitting(false);
    }
  }

  return (
    <div className="drip-modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
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
            aria-label={t("common.close")}
          >
            <X size={16} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="drip-modal-body">
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
                        onClick={() => setDays(d)}
                        className={`drip-preset-btn ${isActive ? "active" : ""}`}
                      >
                        <span className="preset-number">{d}</span>
                        <span className="preset-unit">{d === 3 ? t("drip.daysCount3") : t("drip.daysCountN")}</span>
                      </button>
                    );
                  })}
                </div>
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
          </div> : <p className="drip-subtitle">{selectionCount !== undefined ? `Выбрано товаров: ${selectionCount}. Подходят для плана: ${totalProductsCount}. Остальные исключены по статусу, категории или состоянию операции.` : "Учитываются подходящие товары текущей страницы."} Защита от пересечений категорий пока не подключена.</p>}

          {/* Section 3: Distribution Timeline & Rate */}
          <div className="drip-card-block">
            <div className="drip-card-header">
              <Layers size={15} className="text-amber-400" />
              <span>{t("drip.distributionPlan")}</span>
            </div>

            {/* Metrics Strip */}
            <div className="drip-kpi-strip">
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">{t("drip.totalProducts")}</span>
                <strong className="drip-kpi-value text-orange">{totalProductsCount}</strong>
              </div>
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">{t("drip.dailyAverage")}</span>
                <strong className="drip-kpi-value text-emerald">
                  ~{Math.round(totalProductsCount / days)} {t("drip.itemsPerDayUnit")}
                </strong>
              </div>
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">{t("drip.dischargePeriod")}</span>
                <strong className="drip-kpi-value text-amber">{days} {days === 3 ? t("drip.daysCount3") : t("drip.daysCountN")}</strong>
              </div>
            </div>

            {/* Visual Bar Chart */}
            <div className="drip-chart-container">
              <div className="drip-chart-bars">
                {distribution.map((item) => {
                  const heightPercent = Math.max(30, Math.round((item.count / maxDayCount) * 100));
                  return (
                    <div key={item.dayNum} className="drip-chart-col">
                      <span className="drip-bar-count">{item.count}</span>
                      <div className="drip-bar-track">
                        <div
                          className="drip-bar-fill"
                          style={{ height: `${heightPercent}%` }}
                        />
                      </div>
                      <span className="drip-bar-day">{t("drip.dayN", { day: item.dayNum })}</span>
                      <span className="drip-bar-date">{item.dateFormatted.split(",")[0]}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="drip-modal-footer">
          <div className="drip-footer-summary">
            <span>{t("drip.totalSummary", { total: totalProductsCount, days })}</span>
          </div>

          <div className="drip-footer-actions">
            <button
              type="button"
              className="drip-btn-secondary"
              onClick={onClose}
              disabled={isSubmitting}
            >
              {t("common.cancel")}
            </button>

            <button
              type="button"
              className="drip-btn-primary"
              onClick={handleSubmit}
              disabled={isSubmitting || totalProductsCount === 0}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={15} className="spin" />
                  <span>{t("drip.startingSchedule")}</span>
                </>
              ) : (
                <>
                  <Play size={14} fill="currentColor" />
                  <span>{t("drip.startDripFeedDays", { days })}</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Clock, Layers, Pause, Play, RefreshCw, X } from "lucide-react";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import type { RestartAccount, RestartSettings } from "@/features/api/types";
import {
  buildBatchBannerViewModel,
  buildPlanningBannerViewModel,
  pickBannerBatch,
  pickBannerPlanningRequest,
  resolveDripApiFactoryId,
  type DripBannerViewModel,
} from "@/features/catalogs/drip-banner-utils";
import { dripFeedService } from "@/features/catalogs/drip-feed-service";

const api = new RestartApiClient();
const POLL_INTERVAL_MS = 10_000;

export interface ActiveDripFeedBannerProps {
  demoMode?: boolean;
  account: RestartAccount;
  factoryId?: string;
  sourceType: "AFTERBUY" | "DIRECT_OTTO";
  refreshSignal?: number;
  onOpenQueue: (target?: { batchId?: string; requestId?: string }) => void;
  onPlanMutated?: (message?: string) => void;
}

function idempotencyKey(action: string): string {
  if (!globalThis.crypto?.randomUUID) {
    throw new Error("Браузер не поддерживает безопасные ключи повторного запроса.");
  }
  return `drip-banner-${action}-${globalThis.crypto.randomUUID()}`;
}

function readableBannerError(error: unknown): string {
  if (error instanceof RestartApiError) {
    return `${error.message} (${error.code})`;
  }
  return error instanceof Error ? error.message : "Не удалось обновить состояние Drip Feed.";
}

export function ActiveDripFeedBanner({
  demoMode = true,
  account,
  factoryId,
  sourceType,
  refreshSignal = 0,
  onOpenQueue,
  onPlanMutated,
}: ActiveDripFeedBannerProps) {
  const [banner, setBanner] = useState<DripBannerViewModel | null>(null);
  const [settings, setSettings] = useState<RestartSettings | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [localRefreshTick, setLocalRefreshTick] = useState(0);

  const currentAccountRef = useRef(account);
  currentAccountRef.current = account;
  const apiSourceType = sourceType === "DIRECT_OTTO" ? "OTTO_DIRECT" : "AFTERBUY";
  const apiFactoryId = resolveDripApiFactoryId(sourceType, factoryId);
  const demoFactoryId = factoryId || "direct-otto";

  const loadBannerState = useCallback(
    async (signal?: AbortSignal) => {
      if (demoMode) {
        const plan = dripFeedService.getActivePlan(demoFactoryId, account);
        if (!plan) {
          setBanner(null);
          return;
        }
        const total = Math.max(0, plan.total_products);
        const published = plan.progress?.published ?? 0;
        const processing = plan.progress?.processing ?? 0;
        const errors = plan.progress?.failed ?? 0;
        const percentage =
          plan.progress?.percentage ??
          (total > 0 ? Math.min(100, Math.round((published / total) * 100)) : 0);
        const isPaused = plan.status === "paused";
        setBanner({
          kind: "batch",
          id: String(plan.id),
          title: `Активный Drip Feed: День ${plan.current_day} из ${plan.total_days}`,
          statusText: isPaused ? "НА ПАУЗЕ" : "ВЫПОЛНЯЕТСЯ",
          statusTone: isPaused ? "paused" : "running",
          isPaused,
          isAnimated: !isPaused,
          currentDay: plan.current_day,
          totalDays: plan.total_days,
          dailyTime: plan.daily_time || "09:00",
          timezoneLabel: "Берлин",
          totalItems: total,
          publishedCount: published,
          processingCount: processing,
          errorsCount: errors,
          preparedCount: total,
          percentage,
        });
        return;
      }

      if (signal?.aborted) return;

      try {
        const [batchesRes, planningRes] = await Promise.all([
          api.listDripBatches(account, {
            factoryId: apiFactoryId,
            sourceType: apiSourceType,
            pageSize: 20,
            signal,
          }),
          api.listDripPlanningRequests(account, {
            factoryId: apiFactoryId,
            sourceType: apiSourceType,
            pageSize: 10,
            signal,
          }),
        ]);
        if (signal?.aborted || currentAccountRef.current !== account) return;

        const scopedBatches = batchesRes.items.filter((item) => item.account === account);
        const pickedBatch = pickBannerBatch(scopedBatches);

        if (pickedBatch) {
          const fullBatch = await api.getDripBatch(pickedBatch.id, account, signal);
          if (signal?.aborted || currentAccountRef.current !== account) return;
          if (fullBatch.account !== account) return;
          setBanner(buildBatchBannerViewModel(fullBatch));
          setActionError("");
          return;
        }

        const scopedPlanning = planningRes.items.filter((item) => item.account === account);
        const pendingPlan = pickBannerPlanningRequest(scopedPlanning);
        if (pendingPlan) {
          setBanner(buildPlanningBannerViewModel(pendingPlan));
          setActionError("");
          return;
        }

        setBanner(null);
      } catch {
        // Keep last visible state cleanly on transient network/polling hiccups
      }
    },
    [account, apiFactoryId, apiSourceType, demoFactoryId, demoMode],
  );

  // Reset banner on account or factory switch to avoid cross-account flicker
  useEffect(() => {
    setBanner(null);
    setActionError("");
    if (settings?.account !== account) {
      setSettings(null);
    }
  }, [account, factoryId, settings?.account]);

  // Initial load + periodic background polling while tab is visible
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();

    const poll = async () => {
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        await loadBannerState(controller.signal);
      }
      if (active && !controller.signal.aborted) {
        timer = setTimeout(() => {
          void poll();
        }, POLL_INTERVAL_MS);
      }
    };

    void poll();

    return () => {
      active = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [loadBannerState, refreshSignal, localRefreshTick]);

  const handleManualRefresh = async () => {
    if (refreshing || busyAction) return;
    setRefreshing(true);
    setActionError("");
    try {
      await loadBannerState();
    } finally {
      setRefreshing(false);
    }
  };

  const handleBatchAction = async (action: "pause" | "resume" | "cancel") => {
    if (!banner || busyAction) return;

    if (demoMode) {
      const planId = Number(banner.id);
      if (action === "pause" || action === "resume") {
        dripFeedService.togglePlanPause(planId);
      } else {
        if (!window.confirm("Отменить активный Drip Feed план (DEMO)?")) return;
        dripFeedService.cancelPlan(planId);
      }
      setLocalRefreshTick((t) => t + 1);
      onPlanMutated?.();
      return;
    }

    const batch = banner.batch;
    if (!batch || batch.account !== account) return;

    setActionError("");

    if (action === "resume") {
      let currentSettings = settings?.account === account ? settings : null;
      if (!currentSettings) {
        try {
          currentSettings = await api.getSettings(account);
          if (currentAccountRef.current !== account) return;
          setSettings(currentSettings);
        } catch (err) {
          setActionError(readableBannerError(err));
          return;
        }
      }
      const writesEnabled = Boolean(
        currentSettings.mode === "PRODUCTION" &&
          currentSettings.otto.readEnabled &&
          currentSettings.publication.productWriteEnabled,
      );
      const confirmed = window.confirm(
        `Подтвердить действие: продолжить расписание «${batch.name}» (${account})? Worker повторно проверит поколение, QC и серверные флаги.${writesEnabled ? " По текущим флагам отправка товара в OTTO может быть разрешена после этих проверок." : " Отправка в OTTO сейчас заблокирована серверными флагами."}`,
      );
      if (!confirmed) return;
    }

    if (action === "cancel") {
      const confirmed = window.confirm(
        `Отменить будущие задания расписания «${batch.name}»? Уже начатые внешние операции не затрагиваются.`,
      );
      if (!confirmed) return;
    }

    setBusyAction(action);
    try {
      await api.controlDripBatch(
        batch.id,
        account,
        action,
        batch.version,
        idempotencyKey(action),
      );
      setLocalRefreshTick((t) => t + 1);
      onPlanMutated?.(
        action === "pause"
          ? "Drip Feed поставлен на паузу."
          : action === "resume"
            ? "Drip Feed возобновлён."
            : "План Drip Feed отменён.",
      );
    } catch (err) {
      setActionError(readableBannerError(err));
    } finally {
      setBusyAction(null);
    }
  };

  if (!banner) return null;

  const isPlanning = banner.kind === "planning";

  return (
    <section
      className={`otto-drip-banner is-${banner.statusTone}`}
      aria-label="Активное состояние Drip Feed"
    >
      <div className="otto-drip-banner-main">
        <div
          className={`otto-drip-banner-icon ${banner.isAnimated ? "is-pulsing" : ""} ${banner.isPaused ? "is-paused" : ""}`}
          aria-hidden="true"
        >
          <Clock size={18} />
        </div>

        <div className="otto-drip-banner-content">
          <div className="otto-drip-banner-title-row">
            <span className="otto-drip-banner-title">{banner.title}</span>
            <span className={`otto-drip-banner-badge is-${banner.statusTone}`}>
              {banner.statusText}
            </span>
          </div>

          <div className="otto-drip-banner-meta">
            <span>
              Ежедневно в {banner.dailyTime} ({banner.timezoneLabel})
            </span>
            <span className="otto-drip-banner-dot">·</span>
            {isPlanning ? (
              <span>
                Подготовлено:{" "}
                <strong className="otto-drip-banner-published">
                  {banner.preparedCount}
                </strong>{" "}
                / {banner.totalItems} товаров
              </span>
            ) : (
              <span>
                Опубликовано:{" "}
                <strong className="otto-drip-banner-published">
                  {banner.publishedCount}
                </strong>{" "}
                / {banner.totalItems} товаров
              </span>
            )}
            {banner.processingCount > 0 && (
              <>
                <span className="otto-drip-banner-dot">·</span>
                <span className="otto-drip-banner-processing">
                  В обработке: <strong>{banner.processingCount}</strong>
                </span>
              </>
            )}
            {banner.errorsCount > 0 && (
              <>
                <span className="otto-drip-banner-dot">·</span>
                <span className="otto-drip-banner-errors">
                  Ошибок: {banner.errorsCount}
                </span>
              </>
            )}
          </div>

          <div
            className="otto-drip-banner-progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={banner.percentage}
          >
            <div
              className={`otto-drip-banner-progress-fill ${banner.isPaused ? "is-paused" : ""} ${banner.isAnimated && banner.percentage === 0 ? "is-indeterminate" : ""}`}
              style={{
                width:
                  banner.isAnimated && banner.percentage === 0
                    ? "18%"
                    : `${banner.percentage}%`,
              }}
            />
          </div>

          {actionError && (
            <div className="otto-drip-banner-inline-error" role="alert">
              {actionError}
            </div>
          )}
        </div>
      </div>

      <div className="otto-drip-banner-actions">
        <button
          type="button"
          className="otto-drip-banner-btn is-queue"
          onClick={() =>
            onOpenQueue(
              banner.kind === "planning"
                ? { requestId: banner.id }
                : { batchId: banner.id },
            )
          }
        >
          <Layers size={14} />
          <span>Список товаров</span>
        </button>

        {!isPlanning && (banner.statusTone === "running" || banner.statusTone === "scheduled") && (
          <button
            type="button"
            className="otto-drip-banner-btn is-pause"
            disabled={busyAction !== null}
            onClick={() => void handleBatchAction("pause")}
          >
            <Pause size={14} />
            <span>{busyAction === "pause" ? "Пауза…" : "Пауза"}</span>
          </button>
        )}

        {!isPlanning && banner.statusTone === "paused" && (
          <button
            type="button"
            className="otto-drip-banner-btn is-resume"
            disabled={busyAction !== null}
            onClick={() => void handleBatchAction("resume")}
          >
            <Play size={14} />
            <span>{busyAction === "resume" ? "Запуск…" : "Продолжить"}</span>
          </button>
        )}

        {!isPlanning && (
          <button
            type="button"
            className="otto-drip-banner-btn is-cancel"
            disabled={busyAction !== null}
            onClick={() => void handleBatchAction("cancel")}
          >
            <X size={14} />
            <span>{busyAction === "cancel" ? "Отмена…" : "Отменить"}</span>
          </button>
        )}

        <button
          type="button"
          className="otto-drip-banner-refresh"
          onClick={() => void handleManualRefresh()}
          disabled={refreshing || busyAction !== null}
          title="Обновить состояние Drip Feed"
          aria-label="Обновить состояние Drip Feed"
        >
          <RefreshCw size={15} className={refreshing ? "spin-icon" : ""} />
        </button>
      </div>
    </section>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, FileSpreadsheet, Loader2, X } from "lucide-react";
import { RestartApiClient } from "@/features/api/client";
import type { RestartImportRun } from "@/features/api/types";
import { getOttoRestartMode } from "@/features/runtime-mode";
import { useRestartAccount } from "@/features/state/restart-account";

const ACTIVE_STATES = new Set(["PENDING", "RUNNING", "STAGED", "APPLYING"]);
const TERMINAL_STATES = new Set(["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]);
const POLL_INTERVAL_MS = 5000;
const MINIMIZED_STORAGE_KEY = "ottoRestart:importProgressMinimized";
const DISMISSED_PREFIX = "ottoRestart:importProgressDismissed:";

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "< 1 мин";
  const mins = Math.ceil(seconds / 60);
  if (mins < 60) return `~${mins} мин`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins > 0 ? `~${hours} ч ${remMins} мин` : `~${hours} ч`;
}

export function GlobalImportProgressBar() {
  const { account, ready } = useRestartAccount();
  const mode = getOttoRestartMode();
  const api = useMemo(() => new RestartApiClient(), []);

  const [runs, setRuns] = useState<RestartImportRun[]>([]);
  const [minimized, setMinimized] = useState(false);
  const [dismissedIds, setDismissedIds] = useState<Record<string, boolean>>({});
  const rateSampleRef = useRef<{ runId: string; processed: number; atMs: number } | null>(null);
  const lastBroadcastRef = useRef<Map<string, { processed: number; state: string }>>(new Map());
  const [rowsPerMin, setRowsPerMin] = useState<number | null>(null);

  useEffect(() => {
    try {
      setMinimized(window.localStorage.getItem(MINIMIZED_STORAGE_KEY) === "true");
    } catch {
      // Ignore storage errors
    }
  }, []);

  const toggleMinimized = useCallback(() => {
    setMinimized((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(MINIMIZED_STORAGE_KEY, String(next));
      } catch {
        // Ignore storage errors
      }
      return next;
    });
  }, []);

  const dismissRun = useCallback((runId: string) => {
    setDismissedIds((prev) => ({ ...prev, [runId]: true }));
    try {
      window.localStorage.setItem(`${DISMISSED_PREFIX}${runId}`, "1");
    } catch {
      // Ignore storage errors
    }
  }, []);

  useEffect(() => {
    if (!ready || mode !== "PRODUCTION") {
      setRuns([]);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let activeAbort: AbortController | null = null;

    const clearTimer = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const scheduleNext = (hasActive: boolean) => {
      clearTimer();
      if (cancelled || !hasActive || document.visibilityState === "hidden") return;
      timer = setTimeout(() => {
        void pollOnce();
      }, POLL_INTERVAL_MS);
    };

    const pollOnce = async () => {
      if (cancelled) return;
      activeAbort?.abort();
      const controller = new AbortController();
      activeAbort = controller;
      try {
        const res = await api.getActiveImportRuns(account, controller.signal);
        if (cancelled || controller.signal.aborted) return;
        const items = Array.isArray(res?.items) ? res.items : [];
        setRuns(items);

        const broadcastTarget =
          items.find((item) => ACTIVE_STATES.has(item.runState)) ??
          items.find((item) => TERMINAL_STATES.has(item.runState));
        if (broadcastTarget) {
          const prev = lastBroadcastRef.current.get(broadcastTarget.id);
          const nextProcessed = Number(broadcastTarget.processedCount || 0);
          const nextState = String(broadcastTarget.runState || "");
          const progressed =
            prev !== undefined
              ? nextProcessed > prev.processed || nextState !== prev.state
              : ACTIVE_STATES.has(nextState) && nextProcessed > 0;
          lastBroadcastRef.current.set(broadcastTarget.id, { processed: nextProcessed, state: nextState });
          if (progressed) {
            window.dispatchEvent(
              new CustomEvent("ottorestart:import-progress", {
                detail: {
                  account,
                  runId: broadcastTarget.id,
                  processedCount: nextProcessed,
                  runState: nextState,
                },
              })
            );
          }
        }

        const activeRun = items.find((item) => ACTIVE_STATES.has(item.runState));
        if (activeRun) {
          const prev = rateSampleRef.current;
          const now = Date.now();
          if (!prev || prev.runId !== activeRun.id || activeRun.processedCount < prev.processed) {
            rateSampleRef.current = {
              runId: activeRun.id,
              processed: activeRun.processedCount,
              atMs: now,
            };
            setRowsPerMin(null);
          } else if (activeRun.processedCount > prev.processed && now - prev.atMs >= 4000) {
            const deltaRows = activeRun.processedCount - prev.processed;
            const deltaMin = (now - prev.atMs) / 60000;
            if (deltaMin > 0) {
              setRowsPerMin(Math.max(1, Math.round(deltaRows / deltaMin)));
            }
          }
        } else {
          rateSampleRef.current = null;
          setRowsPerMin(null);
        }

        scheduleNext(Boolean(activeRun));
      } catch {
        if (!cancelled && !controller.signal.aborted) {
          // Retry slowly only if we already were tracking an active import
          const hadActive = runsRef.current.some((item) => ACTIVE_STATES.has(item.runState));
          if (hadActive) {
            clearTimer();
            timer = setTimeout(() => void pollOnce(), POLL_INTERVAL_MS * 2);
          }
        }
      }
    };

    const handleStarted = () => {
      if (document.visibilityState !== "hidden") {
        void pollOnce();
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        void pollOnce();
      } else {
        clearTimer();
      }
    };

    window.addEventListener("ottorestart:import-started", handleStarted);
    document.addEventListener("visibilitychange", handleVisibility);
    if (document.visibilityState !== "hidden") {
      void pollOnce();
    }

    return () => {
      cancelled = true;
      clearTimer();
      activeAbort?.abort();
      window.removeEventListener("ottorestart:import-started", handleStarted);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [account, api, mode, ready]);

  const runsRef = useRef<RestartImportRun[]>([]);
  runsRef.current = runs;

  const visibleRun = useMemo(() => {
    const isDismissed = (runId: string) => {
      if (dismissedIds[runId]) return true;
      try {
        return typeof window !== "undefined" && window.localStorage.getItem(`${DISMISSED_PREFIX}${runId}`) === "1";
      } catch {
        return false;
      }
    };
    const active = runs.find((item) => ACTIVE_STATES.has(item.runState));
    if (active) return active;
    return runs.find((item) => TERMINAL_STATES.has(item.runState) && !isDismissed(item.id)) ?? null;
  }, [dismissedIds, runs]);

  if (!visibleRun || mode !== "PRODUCTION") return null;

  const isActive = ACTIVE_STATES.has(visibleRun.runState);
  const total = Math.max(0, Number(visibleRun.totalRows || 0));
  const processed = Math.max(0, Math.min(total || Number.MAX_SAFE_INTEGER, Number(visibleRun.processedCount || 0)));
  const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
  const remainingRows = Math.max(0, total - processed);
  const etaSeconds = isActive && rowsPerMin && rowsPerMin > 0 && remainingRows > 0
    ? (remainingRows / rowsPerMin) * 60
    : null;

  const summaryObj = (visibleRun.summary && typeof visibleRun.summary === "object")
    ? (visibleRun.summary as Record<string, unknown>)
    : null;
  const protectedFactoryCount = Math.max(0, Number(summaryObj?.protectedFactoryCount ?? 0) || 0);
  const duplicateIdentifierCount = Math.max(0, Number(summaryObj?.duplicateIdentifierCount ?? 0) || 0);
  const alreadyInCatalogCount = Math.max(0, Number(summaryObj?.alreadyInCatalogCount ?? 0) || 0);
  const duplicateInFileCount = Math.max(0, Number(summaryObj?.duplicateInFileCount ?? 0) || 0);
  const technicalErrorCount = summaryObj && summaryObj.technicalErrorCount !== undefined
    ? Math.max(0, Number(summaryObj.technicalErrorCount) || 0)
    : Math.max(0, Number(visibleRun.errorCount || 0) - protectedFactoryCount - duplicateIdentifierCount - alreadyInCatalogCount - duplicateInFileCount);
  const hasRealErrors = visibleRun.runState === "FAILED" || technicalErrorCount > 0;
  const isErrorState = hasRealErrors;
  const isSuccess = visibleRun.runState === "SUCCEEDED" || (visibleRun.runState === "PARTIAL" && !hasRealErrors);

  if (minimized) {
    return (
      <aside className="global-import-pill" aria-live="polite" aria-label="Прогресс импорта 3/12M">
        <button type="button" className="global-import-pill-btn" onClick={toggleMinimized} title="Развернуть прогресс импорта">
          {isActive ? (
            <Loader2 size={14} className="spin global-import-icon-accent" />
          ) : isSuccess ? (
            <CheckCircle2 size={14} className="global-import-icon-ok" />
          ) : (
            <AlertTriangle size={14} className="global-import-icon-warn" />
          )}
          <span className="global-import-pill-text">
            Импорт {visibleRun.account} · {total > 0 ? `${pct}%` : visibleRun.runState}
          </span>
          <span className="global-import-pill-meter" aria-hidden="true">
            <i style={{ width: `${pct}%` }} />
          </span>
          <ChevronUp size={13} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="global-import-widget" aria-live="polite" aria-label="Прогресс массового импорта 3/12M">
      <div className="global-import-head">
        <div className="global-import-title-wrap">
          {isActive ? (
            <Loader2 size={15} className="spin global-import-icon-accent" />
          ) : isSuccess ? (
            <CheckCircle2 size={15} className="global-import-icon-ok" />
          ) : (
            <AlertTriangle size={15} className="global-import-icon-warn" />
          )}
          <FileSpreadsheet size={14} style={{ opacity: 0.75, flexShrink: 0 }} />
          <strong className="global-import-filename" title={visibleRun.filename ?? "Импорт"}>
            {visibleRun.filename || "Импорт списка"}
          </strong>
          <span className="global-import-account">{visibleRun.account}</span>
        </div>

        <div className="global-import-controls">
          <button
            type="button"
            className="global-import-icon-btn"
            onClick={toggleMinimized}
            title="Свернуть"
            aria-label="Свернуть индикатор импорта"
          >
            <ChevronDown size={14} />
          </button>
          {!isActive ? (
            <button
              type="button"
              className="global-import-icon-btn"
              onClick={() => dismissRun(visibleRun.id)}
              title="Скрыть"
              aria-label="Скрыть уведомление об импорте"
            >
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>

      <div className="global-import-bar-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={`global-import-bar-fill ${isSuccess ? "is-done" : isErrorState ? "is-warn" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="global-import-stats-row">
        <span className="tabular-nums">
          <strong>{processed.toLocaleString("ru-RU")}</strong> / {total.toLocaleString("ru-RU")} ({pct}%)
        </span>
        <span className="global-import-eta tabular-nums">
          {isActive
            ? rowsPerMin
              ? `${rowsPerMin} стр/мин${etaSeconds ? ` · ${formatDuration(etaSeconds)}` : ""}`
              : visibleRun.runState === "PENDING"
                ? "В очереди..."
                : "Расчёт скорости..."
            : isSuccess
              ? "Импорт завершён"
              : visibleRun.runState === "PARTIAL"
                ? "Завершён с ошибками"
                : `Статус: ${visibleRun.runState}`}
        </span>
      </div>

      <div className="global-import-footer">
        <div className="global-import-counters tabular-nums">
          <span>Afterbuy: <strong>{visibleRun.afterbuyCount}</strong></span>
          <span>OTTO Direct: <strong>{visibleRun.directOttoCount}</strong></span>
          {alreadyInCatalogCount > 0 ? (
            <span title="Эти товары уже есть в каталоге — пропущены без повторного импорта">
              Уже в каталоге (пропущено): <strong>{alreadyInCatalogCount}</strong>
            </span>
          ) : null}
          {duplicateInFileCount > 0 ? (
            <span title="Повторяющиеся строки внутри самого файла пропущены">
              Дубли в файле: <strong>{duplicateInFileCount}</strong>
            </span>
          ) : null}
          {protectedFactoryCount > 0 ? (
            <span title="Товары из запрещённых/защищённых фабрик пропущены автоматически">
              Запрещ. фабрики (пропущено): <strong>{protectedFactoryCount}</strong>
            </span>
          ) : null}
          {duplicateIdentifierCount > 0 ? (
            <span title="В Aftercool найдено несколько фабрик на один EAN — пропущено">
              Дубли EAN (пропущено): <strong>{duplicateIdentifierCount}</strong>
            </span>
          ) : null}
          {technicalErrorCount > 0 ? (
            <span className="global-import-err-count">
              {protectedFactoryCount === 0 && duplicateIdentifierCount === 0 && summaryObj?.technicalErrorCount === undefined
                ? "Пропущено / ошибки"
                : "Ошибки"}: <strong>{technicalErrorCount}</strong>
            </span>
          ) : null}
        </div>
        <Link href="/discover" className="global-import-link">
          Подробнее
        </Link>
      </div>
    </aside>
  );
}

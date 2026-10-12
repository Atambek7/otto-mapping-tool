"use client";

import React, { useState, useEffect, useRef } from "react";
import { useAccount } from "@/lib/useAccount";
import { Button } from "@/components/ui/button";
import {
  Clock,
  Calendar,
  Layers,
  Sparkles,
  AlertCircle,
  Loader2,
  X,
  Send,
  SlidersHorizontal,
  TableProperties
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

import { fetchWithAuth } from "@/lib/api";

interface DripScheduleModalProps {
  factoryId: string;
  factoryName?: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  selectedEans?: string[];
}

export default function DripScheduleModal({
  factoryId,
  factoryName,
  isOpen,
  onClose,
  onSuccess,
  selectedEans,
}: DripScheduleModalProps) {
  const { t } = useTranslation();
  const { account } = useAccount();
  const [days, setDays] = useState<number>(7);
  const [dailyTime, setDailyTime] = useState<string>("08:00");
  const [executorType, setExecutorType] = useState<string>("otto_api");
  const [excludePublished, setExcludePublished] = useState<boolean>(false);
  const [antiCollisionEnabled, setAntiCollisionEnabled] = useState<boolean>(true);
  const [staggerDays, setStaggerDays] = useState<number>(2);

  const [loadingPreview, setLoadingPreview] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [previewData, setPreviewData] = useState<any | null>(null);

  const previewAbortControllerRef = useRef<AbortController | null>(null);
  const previewRequestIdRef = useRef<number>(0);

  useEffect(() => {
    if (!isOpen) {
      if (previewAbortControllerRef.current) {
        previewAbortControllerRef.current.abort();
        previewAbortControllerRef.current = null;
      }
      setPreviewData(null);
      setError(null);
      return;
    }

    if (previewAbortControllerRef.current) {
      previewAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    previewAbortControllerRef.current = controller;
    const currentRequestId = ++previewRequestIdRef.current;

    const timer = setTimeout(() => {
      fetchPreview(days, dailyTime, controller.signal, currentRequestId);
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [isOpen, days, dailyTime, account, excludePublished, antiCollisionEnabled, staggerDays]);

  const fetchPreview = async (d: number, t: string, signal: AbortSignal, reqId: number) => {
    setLoadingPreview(true);
    setError(null);
    try {
      const res = await fetchWithAuth(`/pipeline/preview/${factoryId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal,
        body: JSON.stringify({
          account_name: account,
          days: d,
          daily_time: t,
          timezone: "Europe/Berlin",
          product_eans: selectedEans && selectedEans.length > 0 ? selectedEans : null,
          exclude_published: excludePublished,
          anti_collision_enabled: antiCollisionEnabled,
          stagger_days: staggerDays,
        }),
      });

      if (reqId !== previewRequestIdRef.current) {
        return;
      }

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Error ${res.status}: Failed to calculate schedule preview.`);
      }

      const data = await res.json();
      if (reqId === previewRequestIdRef.current) {
        setPreviewData(data);
      }
    } catch (err: any) {
      if (err?.name === "AbortError" || signal.aborted) {
        return;
      }
      if (reqId === previewRequestIdRef.current) {
        setError(err.message || "Connection error while generating schedule preview");
      }
    } finally {
      if (reqId === previewRequestIdRef.current) {
        setLoadingPreview(false);
      }
    }
  };

  const handleCreatePlan = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetchWithAuth(`/pipeline/create/${factoryId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_name: account,
          days: days,
          daily_time: dailyTime,
          timezone: "Europe/Berlin",
          executor_type: executorType,
          product_eans: selectedEans && selectedEans.length > 0 ? selectedEans : null,
          exclude_published: excludePublished,
          anti_collision_enabled: antiCollisionEnabled,
          stagger_days: staggerDays,
        }),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Error ${res.status}: Failed to create publication plan.`);
      }

      onSuccess();
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to initialize drip feed publication plan");
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-xs p-4 overflow-y-auto">
      <div className="bg-white dark:bg-card border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-3xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-card">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 flex items-center justify-center shrink-0">
              <Clock className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-slate-900 dark:text-white tracking-tight">
                  {t("drip_modal.title")}
                </h3>
                <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200/80 dark:border-indigo-900/60">
                  {account} Möbel
                </span>
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {t("drip_modal.target_catalog")} <span className="font-medium text-slate-700 dark:text-slate-300">{factoryName || factoryId}</span>
                {selectedEans && selectedEans.length > 0 && (
                  <span className="ml-2 text-indigo-600 dark:text-indigo-400 font-medium">{t("drip_modal.selected_items", { count: selectedEans.length })}</span>
                )}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable Body */}
        <div className="p-6 space-y-6 overflow-y-auto flex-1 text-sm text-slate-700 dark:text-slate-300">
          {error && (
            <div className="p-3.5 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900/60 text-rose-700 dark:text-rose-300 text-xs flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                <div className="font-semibold">{t("common.error")}</div>
                <div className="mt-0.5">{error}</div>
              </div>
            </div>
          )}

          {/* Section 1: Schedule Parameters */}
          <div>
            <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
              <SlidersHorizontal className="w-3.5 h-3.5" />
              <span>{t("drip_modal.strategy_schedule")}</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
              {/* Duration Days */}
              <div className="bg-slate-50/70 dark:bg-slate-900/40 p-3.5 rounded-xl border border-slate-200/70 dark:border-slate-800">
                <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">
                  {t("drip_modal.duration_days")}
                </label>
                <div className="grid grid-cols-4 gap-1.5 mb-2">
                  {[3, 5, 7, 14].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDays(d)}
                      className={cn(
                        "py-1.5 rounded-lg text-xs font-semibold transition-colors",
                        days === d
                          ? "bg-indigo-600 text-white shadow-xs"
                          : "bg-white dark:bg-card border border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-100"
                      )}
                    >
                      {d}d
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  min="1"
                  max="30"
                  value={days}
                  onChange={(e) => setDays(Math.max(1, Math.min(30, parseInt(e.target.value) || 1)))}
                  className="w-full bg-white dark:bg-card border border-slate-200 dark:border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  placeholder={t("drip_modal.custom_days")}
                />
              </div>

              {/* Daily Scheduled Time */}
              <div className="bg-slate-50/70 dark:bg-slate-900/40 p-3.5 rounded-xl border border-slate-200/70 dark:border-slate-800">
                <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">
                  {t("drip_modal.daily_time")}
                </label>
                <input
                  type="time"
                  value={dailyTime}
                  onChange={(e) => setDailyTime(e.target.value)}
                  className="w-full bg-white dark:bg-card border border-slate-200 dark:border-slate-800 rounded-lg px-3 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <span className="text-[11px] text-slate-400 mt-2 block">
                  {t("drip_modal.timezone_note")}
                </span>
              </div>

              {/* Pipeline Target / Channel */}
              <div className="bg-slate-50/70 dark:bg-slate-900/40 p-3.5 rounded-xl border border-slate-200/70 dark:border-slate-800">
                <label className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-2">
                  {t("drip_modal.delivery_channel")}
                </label>
                <select
                  value={executorType}
                  onChange={(e) => setExecutorType(e.target.value)}
                  className="w-full bg-white dark:bg-card border border-slate-200 dark:border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
                >
                  <option value="otto_api">{t("drip_modal.channel_otto_api")}</option>
                  <option value="batch_file">{t("drip_modal.channel_batch_file")}</option>
                </select>
                <span className="text-[11px] text-slate-400 mt-2 block">
                  {t("drip_modal.channel_desc")}
                </span>
              </div>
            </div>

            {/* Exclude already published toggle */}
            <div className="mt-3 pt-3 border-t border-slate-100 dark:border-slate-800/80 flex items-center justify-between">
              <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-600 dark:text-slate-300 font-medium select-none">
                <input
                  type="checkbox"
                  checked={excludePublished}
                  onChange={(e) => setExcludePublished(e.target.checked)}
                  className="w-4 h-4 rounded text-indigo-600 border-slate-300 focus:ring-indigo-500 cursor-pointer"
                />
                <span>{t("drip_modal.exclude_published")}</span>
              </label>
              {previewData?.already_published > 0 && (
                <span className="text-[11px] text-slate-400">
                  {t("drip_modal.already_published_count", { count: previewData.already_published })}
                </span>
              )}
            </div>

            {/* Anti-Collision Protection Setting */}
            <div className="mt-3.5 p-3 rounded-xl bg-amber-50/60 dark:bg-amber-950/30 border border-amber-200/80 dark:border-amber-900/60 space-y-2.5">
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer text-xs font-semibold text-amber-950 dark:text-amber-200 select-none">
                  <input
                    type="checkbox"
                    checked={antiCollisionEnabled}
                    onChange={(e) => setAntiCollisionEnabled(e.target.checked)}
                    className="w-4 h-4 rounded text-amber-600 border-amber-300 focus:ring-amber-500 cursor-pointer"
                  />
                  <span>🛡️ {t("drip_modal.anti_collision_title", { oppAccount: account === "JV" ? "XL" : "JV" })}</span>
                </label>
                {previewData?.stagger_delayed_total > 0 && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-200 dark:bg-amber-900 text-amber-900 dark:text-amber-100">
                    {t("drip_modal.stagger_delayed_badge", { count: previewData.stagger_delayed_total })}
                  </span>
                )}
              </div>

              {antiCollisionEnabled && (
                <div className="flex items-center justify-between pt-2 border-t border-amber-200/60 dark:border-amber-900/40 text-xs text-amber-900 dark:text-amber-300">
                  <div className="text-[11px] leading-tight max-w-[65%]">
                    {t("drip_modal.anti_collision_desc", { oppAccount: account === "JV" ? "XL" : "JV" })}:
                  </div>
                  <div className="flex items-center gap-1.5">
                    {[1, 2, 3, 5, 7].map((d) => (
                      <button
                        key={d}
                        type="button"
                        onClick={() => setStaggerDays(d)}
                        className={cn(
                          "px-2 py-1 rounded text-xs font-semibold transition-colors",
                          staggerDays === d
                            ? "bg-amber-600 text-white shadow-xs"
                            : "bg-white dark:bg-card border border-amber-300 dark:border-amber-800 text-amber-800 dark:text-amber-300 hover:bg-amber-100"
                        )}
                      >
                        {d}д
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Section 2: Distribution Preview */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
                <TableProperties className="w-3.5 h-3.5" />
                <span>{t("drip_modal.daily_distribution")}</span>
              </div>
              {loadingPreview && (
                <span className="text-xs text-indigo-600 dark:text-indigo-400 flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" /> {t("drip_modal.calculating")}
                </span>
              )}
            </div>

            {previewData ? (
              <div className="space-y-3">
                {/* Metric Strip */}
                <div className="grid grid-cols-4 gap-2 bg-slate-50/70 dark:bg-slate-900/40 p-3 rounded-xl border border-slate-200/70 dark:border-slate-800 text-center">
                  <div>
                    <span className="text-[11px] text-slate-400 block font-medium">{t("drip_modal.total_products")}</span>
                    <span className="text-base font-bold text-slate-900 dark:text-white mt-0.5 block">
                      {previewData.total_products}
                      {previewData.total_factory_products && previewData.total_factory_products !== previewData.total_products && (
                        <span className="text-xs font-normal text-slate-400 ml-1">/ {previewData.total_factory_products}</span>
                      )}
                    </span>
                  </div>
                  <div>
                    <span className="text-[11px] text-slate-400 block font-medium">{t("drip_modal.ready_products")}</span>
                    <span className="text-base font-bold text-emerald-600 dark:text-emerald-400 mt-0.5 block">
                      {previewData.ready_products ?? previewData.total_products}
                    </span>
                  </div>
                  <div>
                    <span className="text-[11px] text-slate-400 block font-medium">{t("drip_modal.daily_average")}</span>
                    <span className="text-base font-bold text-indigo-600 dark:text-indigo-400 mt-0.5 block">
                      {t("drip_modal.items_per_day", { count: Math.round(previewData.total_products / previewData.days) })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[11px] text-slate-400 block font-medium">{t("drip_modal.first_run_date")}</span>
                    <span className="text-sm font-bold text-slate-700 dark:text-slate-300 mt-1 block truncate">
                      {previewData.start_date} {t("drip_modal.at_time")} {previewData.daily_time}
                    </span>
                  </div>
                </div>

                {/* Day Breakdown Table */}
                <div className="border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden max-h-60 overflow-y-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead className="bg-slate-50 dark:bg-slate-900/80 sticky top-0 border-b border-slate-200 dark:border-slate-800 text-slate-500 font-semibold">
                      <tr>
                        <th className="py-2.5 px-3.5 w-20">{t("drip_modal.th_day")}</th>
                        <th className="py-2.5 px-3.5 w-28">{t("drip_modal.th_products")}</th>
                        <th className="py-2.5 px-3.5">{t("drip_modal.th_category_allocation")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 bg-white dark:bg-card">
                      {previewData.days_summary?.map((row: any) => (
                        <tr key={row.day} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition-colors">
                          <td className="py-2.5 px-3.5 font-bold text-indigo-600 dark:text-indigo-400">
                            {t("drip_modal.day_label", { day: row.day })}
                          </td>
                          <td className="py-2.5 px-3.5 font-semibold text-slate-900 dark:text-white">
                            <div>{t("drip_modal.items_count", { count: row.total_items })}</div>
                            {row.stagger_delayed_count > 0 && (
                              <div className="text-[10px] font-semibold text-amber-600 dark:text-amber-400 mt-0.5 flex items-center gap-1">
                                <span>🛡️</span>
                                <span>{row.stagger_delayed_count} с задержкой</span>
                              </div>
                            )}
                          </td>
                          <td className="py-2.5 px-3.5">
                            <div className="flex flex-wrap gap-1.5">
                              {Object.entries(row.categories || {}).map(([cname, count]) => (
                                <span
                                  key={cname}
                                  className="inline-flex items-center px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 border border-slate-200/60 dark:border-slate-700/60 text-[11px] text-slate-700 dark:text-slate-300"
                                >
                                  <span className="truncate max-w-[140px]">{cname}</span>
                                  <span className="ml-1 text-indigo-600 dark:text-indigo-400 font-bold">×{String(count)}</span>
                                </span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="py-10 text-center text-xs text-slate-400 bg-slate-50/50 dark:bg-slate-900/20 rounded-xl border border-dashed border-slate-200 dark:border-slate-800">
                {t("drip_modal.loading_simulation")}
              </div>
            )}
          </div>
        </div>

        {/* Sticky Footer */}
        <div className="px-6 py-4 border-t border-slate-100 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-slate-50/50 dark:bg-card">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            {t("drip_modal.worker_note", { time: dailyTime })}
          </div>
          <div className="flex items-center gap-2 self-end sm:self-auto">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onClose}
              className="border-slate-200 dark:border-slate-800"
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleCreatePlan}
              disabled={submitting || !previewData || previewData.total_products === 0}
              className="bg-indigo-600 hover:bg-indigo-700 text-white gap-2 font-semibold shadow-xs"
            >
              {submitting ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>{t("drip_modal.creating")}</span>
                </>
              ) : (
                <>
                  <Send className="w-3.5 h-3.5" />
                  <span>{t("drip_modal.create_plan")}</span>
                </>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

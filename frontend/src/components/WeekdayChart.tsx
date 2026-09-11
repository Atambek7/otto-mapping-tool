"use client";

import React, { useEffect, useState, useCallback } from "react";
import { fetchWithAuth } from "@/lib/api";
import { useAccount } from "@/lib/useAccount";
import {
  RefreshCw,
  TrendingUp,
  ShoppingCart,
  Euro,
  Sparkles,
  Calendar,
  CalendarDays,
  ChevronDown,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import WeekdayOrdersModal from "@/components/WeekdayOrdersModal";

interface WeekdayData {
  weekday: number;
  label: string;
  orders: number;
  items: number;
  revenue: number;
  avg_per_week: number;
  intensity: number;
}

interface WeekdayStatsResponse {
  account_name: string;
  weeks: number;
  period_from: string;
  period_to: string;
  weekdays: WeekdayData[];
  peak_day: { weekday: number; label: string } | null;
  total_orders: number;
  total_revenue: number;
  last_synced_at: string | null;
  is_mock: boolean;
}

interface WeekdayChartProps {
  weeks?: number;
}

const PRESETS = [
  { weeks: 4, labelKey: "analytics.preset_4w", fallback: "4 недели (1 мес)" },
  { weeks: 8, labelKey: "analytics.preset_8w", fallback: "8 недель (2 мес)" },
  { weeks: 12, labelKey: "analytics.preset_12w", fallback: "12 недель (3 мес)" },
  { weeks: 26, labelKey: "analytics.preset_26w", fallback: "26 недель (6 мес)" },
  { weeks: 52, labelKey: "analytics.preset_52w", fallback: "52 недели (1 год)" },
];

export default function WeekdayChart({ weeks = 12 }: WeekdayChartProps) {
  const { account } = useAccount();
  const { t, i18n } = useTranslation();

  const [selectedWeeks, setSelectedWeeks] = useState<number>(weeks);
  const [customRange, setCustomRange] = useState<{ from: string; to: string } | null>(null);
  const [tempFrom, setTempFrom] = useState<string>("");
  const [tempTo, setTempTo] = useState<string>("");
  const [pickerOpen, setPickerOpen] = useState(false);

  const localizeDay = (label: string) => {
    if (i18n.language !== "ru") return label;
    const map: Record<string, string> = {
      Monday: "Понедельник", Mon: "Пн",
      Tuesday: "Вторник", Tue: "Вт",
      Wednesday: "Среда", Wed: "Ср",
      Thursday: "Четверг", Thu: "Чт",
      Friday: "Пятница", Fri: "Пт",
      Saturday: "Суббота", Sat: "Сб",
      Sunday: "Воскресенье", Sun: "Вс",
    };
    return map[label] || label;
  };

  // Instant SWR: initialize from sessionStorage to eliminate skeleton blocking on revisit
  const [data, setData] = useState<WeekdayStatsResponse | null>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = sessionStorage.getItem(`otto_weekday_stats_${account}_${weeks}`);
        if (saved) return JSON.parse(saved);
      } catch {}
    }
    return null;
  });

  const [loading, setLoading] = useState<boolean>(() => {
    if (typeof window !== "undefined") {
      try {
        return !sessionStorage.getItem(`otto_weekday_stats_${account}_${weeks}`);
      } catch {}
    }
    return true;
  });

  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hoveredDay, setHoveredDay] = useState<number | null>(null);
  const [modalDay, setModalDay] = useState<{ weekday: number; label: string } | null>(null);

  const fetchStats = useCallback(
    async (forceRefresh = false) => {
      const key = customRange
        ? `otto_weekday_stats_${account}_${customRange.from}_${customRange.to}`
        : `otto_weekday_stats_${account}_${selectedWeeks}`;

      if (!sessionStorage.getItem(key)) {
        setLoading(true);
      }
      setError(null);
      try {
        let url = `/analytics/weekday-stats?account_name=${account}`;
        if (customRange?.from && customRange?.to) {
          url += `&from_date=${customRange.from}&to_date=${customRange.to}`;
        } else {
          url += `&weeks=${selectedWeeks}`;
        }
        if (forceRefresh) {
          url += `&refresh=true`;
        }

        const res = await fetchWithAuth(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        setData(json);
        try {
          sessionStorage.setItem(key, JSON.stringify(json));
        } catch {}
      } catch (e: any) {
        setError(e.message || t("analytics.load_error"));
      } finally {
        setLoading(false);
      }
    },
    [account, selectedWeeks, customRange, t]
  );

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      let syncUrl = `/analytics/sync?account_name=${account}`;
      if (customRange?.from && customRange?.to) {
        syncUrl += `&from_date=${customRange.from}&to_date=${customRange.to}`;
      } else {
        syncUrl += `&days_back=${selectedWeeks * 7}`;
      }
      await fetchWithAuth(syncUrl, { method: "POST" });
      setTimeout(() => fetchStats(true), 3000);
    } catch {
      /* ignore */
    } finally {
      setTimeout(() => setSyncing(false), 3000);
    }
  };

  const handleSelectPreset = (w: number) => {
    setSelectedWeeks(w);
    setCustomRange(null);
    setTempFrom("");
    setTempTo("");
    setPickerOpen(false);
  };

  const handleApplyCustom = () => {
    if (!tempFrom || !tempTo) return;
    setCustomRange({ from: tempFrom, to: tempTo });
    setPickerOpen(false);
  };

  const handleResetToDefault = () => {
    setSelectedWeeks(12);
    setCustomRange(null);
    setTempFrom("");
    setTempTo("");
    setPickerOpen(false);
  };

  const getPeriodLabel = () => {
    if (customRange) {
      return `${customRange.from} — ${customRange.to}`;
    }
    const preset = PRESETS.find((p) => p.weeks === selectedWeeks);
    return preset ? t(preset.labelKey, preset.fallback) : `${selectedWeeks} нед.`;
  };

  if (loading) {
    return (
      <div className="bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-2xl p-6 shadow-xs">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-8 h-8 rounded-lg bg-indigo-50 dark:bg-indigo-950/40 flex items-center justify-center">
            <TrendingUp className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
          </div>
          <div className="h-4 bg-slate-100 dark:bg-slate-800 rounded w-48 animate-pulse" />
        </div>
        <div className="flex items-end gap-3 h-32">
          {[0.4, 0.6, 0.8, 0.7, 1.0, 0.9, 0.5].map((h, i) => (
            <div key={i} className="flex-1 bg-slate-100 dark:bg-slate-800/60 rounded-t animate-pulse" style={{ height: `${h * 100}%` }} />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white dark:bg-[#111827] border border-rose-200 dark:border-rose-900/40 rounded-2xl p-6 text-rose-600 dark:text-rose-400 text-sm">
        {t("analytics.load_error")}: {error}
      </div>
    );
  }

  if (!data) return null;

  const peakDayName = data.peak_day ? localizeDay(data.peak_day.label) : "—";

  return (
    <div className="bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-2xl p-6 shadow-xs">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-indigo-50 dark:bg-indigo-950/50 flex items-center justify-center shrink-0 border border-indigo-100/80 dark:border-indigo-900/40">
            <TrendingUp className="w-4.5 h-4.5 text-indigo-600 dark:text-indigo-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-bold text-slate-900 dark:text-white text-base leading-tight">
                {t("analytics.title")}
              </h3>
              {data.is_mock && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-50 dark:bg-amber-950/50 text-amber-700 dark:text-amber-400 border border-amber-200/80 dark:border-amber-900/50 font-semibold tracking-wide uppercase">
                  {t("analytics.demo_badge")}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {t("analytics.period_desc", { weeks: data.weeks, from: data.period_from, to: data.period_to })}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          {/* Calendar & Period Popover */}
          <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
            <PopoverTrigger
              render={
                <button
                  type="button"
                  className="h-8 px-2.5 sm:px-3 rounded-lg flex items-center gap-1.5 sm:gap-2 text-xs font-medium text-slate-700 dark:text-slate-200 border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/60 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors cursor-pointer shadow-2xs"
                />
              }
            >
              <Calendar className="w-3.5 h-3.5 text-indigo-500 shrink-0" />
              <span className="font-semibold">{getPeriodLabel()}</span>
              <ChevronDown className="w-3.5 h-3.5 text-slate-400 opacity-70 ml-0.5" />
            </PopoverTrigger>
            <PopoverContent className="w-[300px] p-3 bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-xl z-50">
              {/* Presets */}
              <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2 px-1">
                {t("analytics.period_btn", "Период")}
              </div>
              <div className="grid grid-cols-1 gap-1 mb-3">
                {PRESETS.map((p) => {
                  const isActive = !customRange && selectedWeeks === p.weeks;
                  return (
                    <button
                      key={p.weeks}
                      onClick={() => handleSelectPreset(p.weeks)}
                      className={cn(
                        "w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center justify-between cursor-pointer",
                        isActive
                          ? "bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400 font-semibold"
                          : "text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800/60"
                      )}
                    >
                      <span>{t(p.labelKey, p.fallback)}</span>
                      {isActive && <Check className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />}
                    </button>
                  );
                })}
              </div>

              <div className="border-t border-slate-100 dark:border-slate-800/80 my-2" />

              {/* Custom Date Range */}
              <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2 px-1 flex items-center gap-1.5">
                <CalendarDays className="w-3.5 h-3.5 text-slate-400" />
                <span>{t("analytics.custom_range", "Календарь / Свой период")}</span>
              </div>
              <div className="space-y-2 px-1">
                <div>
                  <label className="text-[10px] text-slate-400 mb-1 block">{t("analytics.date_from", "Начало")}</label>
                  <input
                    type="date"
                    value={tempFrom}
                    max={tempTo || new Date().toISOString().split("T")[0]}
                    onChange={(e) => setTempFrom(e.target.value)}
                    className="w-full h-8 px-2.5 text-xs bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-slate-400 mb-1 block">{t("analytics.date_to", "Конец")}</label>
                  <input
                    type="date"
                    value={tempTo}
                    min={tempFrom}
                    max={new Date().toISOString().split("T")[0]}
                    onChange={(e) => setTempTo(e.target.value)}
                    className="w-full h-8 px-2.5 text-xs bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  />
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <button
                    onClick={handleApplyCustom}
                    disabled={!tempFrom || !tempTo}
                    className="flex-1 h-8 px-3 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold disabled:opacity-40 transition-colors shadow-2xs cursor-pointer"
                  >
                    {t("analytics.apply", "Применить")}
                  </button>
                  {customRange && (
                    <button
                      onClick={handleResetToDefault}
                      className="h-8 px-2.5 rounded-lg border border-slate-200 dark:border-slate-700 text-xs text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors cursor-pointer"
                      title={t("analytics.reset", "Сброс")}
                    >
                      {t("analytics.reset", "Сброс")}
                    </button>
                  )}
                </div>
              </div>
            </PopoverContent>
          </Popover>

          {/* Sync Button */}
          <button
            onClick={handleSync}
            disabled={syncing}
            title={t("analytics.sync")}
            className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors disabled:opacity-50 cursor-pointer"
          >
            <RefreshCw className={cn("w-3.5 h-3.5 text-slate-500", syncing && "animate-spin text-indigo-600")} />
            <span>{syncing ? t("analytics.syncing") : t("analytics.sync")}</span>
          </button>
        </div>
      </div>

      {/* Summary KPI Strip */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
        <div className="bg-slate-50/80 dark:bg-slate-900/40 rounded-xl p-3.5 border border-slate-200/60 dark:border-slate-800/60">
          <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1 flex items-center gap-1.5">
            <ShoppingCart className="w-3.5 h-3.5 text-slate-400" /> {t("analytics.total_orders")}
          </div>
          <div className="text-xl font-bold text-slate-900 dark:text-white tracking-tight">
            {data.total_orders.toLocaleString()}
          </div>
        </div>

        <div className="bg-slate-50/80 dark:bg-slate-900/40 rounded-xl p-3.5 border border-slate-200/60 dark:border-slate-800/60">
          <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1 flex items-center gap-1.5">
            <Euro className="w-3.5 h-3.5 text-emerald-500" /> {t("analytics.gross_volume")}
          </div>
          <div className="text-xl font-bold text-emerald-600 dark:text-emerald-400 tracking-tight">
            {data.total_revenue.toLocaleString("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 })}
          </div>
        </div>

        <div className="bg-slate-50/80 dark:bg-slate-900/40 rounded-xl p-3.5 border border-slate-200/60 dark:border-slate-800/60">
          <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1 flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-indigo-500" /> {t("analytics.peak_day")}
          </div>
          <div className="text-xl font-bold text-indigo-600 dark:text-indigo-400 tracking-tight">
            {peakDayName}
          </div>
        </div>
      </div>

      {/* Bar Chart Canvas */}
      <div className="pt-2 pb-1">
        <div className="flex items-end gap-2.5 h-40">
          {data.weekdays.map((day) => {
            const isPeak = day.weekday === data.peak_day?.weekday;
            const isHovered = hoveredDay === day.weekday;
            const barHeight = Math.max(day.intensity * 100, 6);
            const localizedDayLabel = localizeDay(day.label);

            return (
              <div
                key={day.weekday}
                className="flex-1 flex flex-col items-center gap-2 cursor-pointer relative group"
                onClick={() => setModalDay({ weekday: day.weekday, label: day.label })}
                onMouseEnter={() => setHoveredDay(day.weekday)}
                onMouseLeave={() => setHoveredDay(null)}
                role="button"
                tabIndex={0}
                title="Нажмите для просмотра купленных товаров и заказов"
              >
                {/* Floating Tooltip */}
                {isHovered && (
                  <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 z-30 pointer-events-none bg-slate-900 dark:bg-white text-white dark:text-slate-900 rounded-lg px-3 py-2 shadow-lg text-[11px] whitespace-nowrap animate-in fade-in zoom-in-95 duration-100">
                    <div className="font-bold border-b border-slate-700 dark:border-slate-200 pb-1 mb-1 flex items-center justify-between gap-2">
                      <span>{localizedDayLabel}</span>
                      <span className="text-[10px] text-indigo-300 dark:text-indigo-600 font-normal">кликните для деталей</span>
                    </div>
                    <div><span className="font-semibold">{day.orders}</span> {t("analytics.orders")}</div>
                    <div><span className="font-semibold">{day.items}</span> {t("analytics.products")}</div>
                    <div className="text-emerald-400 dark:text-emerald-600 font-semibold">
                      {day.revenue.toLocaleString("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 })}
                    </div>
                    <div className="text-slate-400 dark:text-slate-500 text-[10px]">~{day.avg_per_week} {t("analytics.per_week")}</div>
                  </div>
                )}

                {/* Bar */}
                <div className="w-full flex items-end justify-center" style={{ height: "130px" }}>
                  <div
                    className={cn(
                      "w-full max-w-[56px] rounded-t-lg transition-all duration-200 group-hover:brightness-110 group-active:scale-95",
                      isPeak
                        ? "bg-indigo-600 dark:bg-indigo-500 shadow-sm shadow-indigo-500/30"
                        : isHovered
                        ? "bg-slate-400 dark:bg-slate-500"
                        : "bg-slate-200 dark:bg-slate-700/60 hover:bg-slate-300"
                    )}
                    style={{ height: `${barHeight}%` }}
                  />
                </div>

                {/* Day Label */}
                <div
                  className={cn(
                    "text-xs font-semibold transition-colors group-hover:text-indigo-600 dark:group-hover:text-indigo-400",
                    isPeak ? "text-indigo-600 dark:text-indigo-400" : "text-slate-500 dark:text-slate-400"
                  )}
                >
                  {localizedDayLabel}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Intelligence Callout */}
      {data.peak_day && (
        <div className="mt-5 pt-4 border-t border-slate-100 dark:border-slate-800 flex items-start sm:items-center gap-2.5 text-xs text-slate-600 dark:text-slate-400">
          <div className="w-5 h-5 rounded-full bg-indigo-50 dark:bg-indigo-950/60 flex items-center justify-center shrink-0 text-indigo-600 dark:text-indigo-400 mt-0.5 sm:mt-0">
            <Sparkles className="w-3 h-3" />
          </div>
          <span>
            <strong className="text-slate-900 dark:text-white font-semibold">{peakDayName}</strong> {t("analytics.strongest_day")}{" "}
            {t("analytics.drip_advice")}
          </span>
        </div>
      )}

      {/* Weekday Orders Modal */}
      <WeekdayOrdersModal
        isOpen={modalDay !== null}
        onClose={() => setModalDay(null)}
        account={account}
        weekday={modalDay?.weekday ?? null}
        weekdayLabel={modalDay ? localizeDay(modalDay.label) : ""}
        weeks={selectedWeeks}
        customRange={customRange}
      />
    </div>
  );
}

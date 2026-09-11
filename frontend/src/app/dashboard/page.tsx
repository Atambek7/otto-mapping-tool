"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Package,
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  UploadCloud,
  Clock,
  ArrowRight,
  RefreshCw,
  Layers,
  ShieldCheck,
  ChevronRight
} from "lucide-react";
import Link from "next/link";
import { fetchWithAuth } from "@/lib/api";
import { useAccount } from "@/lib/useAccount";
import WeekdayChart from "@/components/WeekdayChart";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

interface Stats {
  total_products: number;
  catalog_total?: number;
  imported_products?: number;
  mapped: number;
  need_attributes: number;
  error: number;
  ready: number;
  unmapped: number;
}

export default function DashboardPage() {
  const { account } = useAccount();
  const { t } = useTranslation();

  // Instant SWR: initialize from sessionStorage to eliminate skeleton blocking on revisit
  const [stats, setStats] = useState<Stats | null>(() => {
    if (typeof window !== "undefined") {
      try {
        const saved = sessionStorage.getItem(`otto_dashboard_stats_${account}`);
        if (saved) return JSON.parse(saved);
      } catch {}
    }
    return null;
  });

  const [loading, setLoading] = useState<boolean>(() => {
    if (typeof window !== "undefined") {
      try {
        return !sessionStorage.getItem(`otto_dashboard_stats_${account}`);
      } catch {}
    }
    return true;
  });

  const [refreshing, setRefreshing] = useState(false);

  // Sync state with account switcher
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(`otto_dashboard_stats_${account}`);
      if (saved) {
        setStats(JSON.parse(saved));
        setLoading(false);
      } else {
        setStats(null);
        setLoading(true);
      }
    } catch {}
  }, [account]);

  const loadStats = useCallback(async (isManual = false) => {
    if (isManual) {
      setRefreshing(true);
    } else if (!sessionStorage.getItem(`otto_dashboard_stats_${account}`)) {
      setLoading(true);
    }

    try {
      const url = isManual ? `/stats?account=${account}&refresh=true` : `/stats?account=${account}`;
      const res = await fetchWithAuth(url);
      if (res.ok) {
        const data = await res.json();
        setStats(data);
        try {
          sessionStorage.setItem(`otto_dashboard_stats_${account}`, JSON.stringify(data));
        } catch {}
      }
    } catch (err) {
      console.error("Failed to load dashboard stats", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [account]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        {/* Header skeleton */}
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <div className="h-7 w-40 bg-slate-200 dark:bg-slate-800 rounded-lg" />
            <div className="h-4 w-72 bg-slate-100 dark:bg-slate-800/60 rounded-md" />
          </div>
          <div className="h-9 w-24 bg-slate-200 dark:bg-slate-800 rounded-lg" />
        </div>

        {/* KPI Strip skeleton */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-24 bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-xl p-4 space-y-2" />
          ))}
        </div>

        {/* Main section skeleton */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 h-72 bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-2xl" />
          <div className="h-72 bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-2xl" />
        </div>
      </div>
    );
  }

  if (!stats) return null;

  const totalInPipeline = stats.imported_products !== undefined ? stats.imported_products : stats.total_products;
  const readyPercent = totalInPipeline > 0 ? Math.round((stats.ready / totalInPipeline) * 100) : 0;
  const errorPercent = totalInPipeline > 0 ? Math.round((stats.error / totalInPipeline) * 100) : 0;
  const needAttrPercent = totalInPipeline > 0 ? Math.round((stats.need_attributes / totalInPipeline) * 100) : 0;
  const unmappedPercent = totalInPipeline > 0 ? Math.max(0, 100 - readyPercent - errorPercent - needAttrPercent) : 0;

  const kpis = [
    {
      id: "total",
      label: t("dashboard.total_products"),
      value: stats.total_products.toLocaleString(),
      subtext: stats.imported_products !== undefined ? `${stats.imported_products.toLocaleString()} ${t("dashboard.in_pipeline")}` : t("dashboard.in_catalog"),
      icon: Package,
      color: "text-slate-700 dark:text-slate-300",
      bgColor: "bg-slate-100 dark:bg-slate-800",
      href: "/dashboard/factories",
    },
    {
      id: "unmapped",
      label: t("dashboard.unmapped"),
      value: stats.unmapped.toLocaleString(),
      subtext: t("dashboard.unmapped_sub"),
      icon: Clock,
      color: "text-slate-600 dark:text-slate-400",
      bgColor: "bg-slate-100 dark:bg-slate-800",
      href: "/dashboard/factories",
    },
    {
      id: "need_attrs",
      label: t("dashboard.need_attributes"),
      value: stats.need_attributes.toLocaleString(),
      subtext: t("dashboard.need_attributes_sub"),
      icon: AlertCircle,
      color: "text-amber-600 dark:text-amber-400",
      bgColor: "bg-amber-50 dark:bg-amber-950/40",
      href: "/dashboard/validation",
    },
    {
      id: "errors",
      label: t("dashboard.validation_errors"),
      value: stats.error.toLocaleString(),
      subtext: t("dashboard.validation_errors_sub"),
      icon: AlertTriangle,
      color: "text-rose-600 dark:text-rose-400",
      bgColor: "bg-rose-50 dark:bg-rose-950/40",
      href: "/dashboard/validation",
    },
    {
      id: "ready",
      label: t("dashboard.ready_export"),
      value: stats.ready.toLocaleString(),
      subtext: `${readyPercent}% ${t("dashboard.ready_export_sub")}`,
      icon: UploadCloud,
      color: "text-indigo-600 dark:text-indigo-400",
      bgColor: "bg-indigo-50 dark:bg-indigo-950/40",
      href: "/dashboard/export",
    },
  ];

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
              {t("dashboard.overview_title")}
            </h1>
            <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
              {account === "JV" ? "JV Möbel" : "XL Möbel"}
            </span>
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            {t("dashboard.overview_subtitle")}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => loadStats(true)}
            disabled={refreshing}
            className="h-9 px-3 text-xs font-medium rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-[#111827] text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 shadow-xs flex items-center gap-2 cursor-pointer transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn("w-3.5 h-3.5 text-slate-500", refreshing && "animate-spin text-indigo-600")} />
            <span>{refreshing ? t("common.refreshing") : t("common.refresh")}</span>
          </button>
        </div>
      </div>

      {/* KPI Metric Strip */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {kpis.map((kpi) => (
          <Link
            key={kpi.id}
            href={kpi.href}
            className="bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-xl p-4 shadow-xs hover:border-slate-300 dark:hover:border-slate-700 transition-all group flex flex-col justify-between"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 truncate">
                {kpi.label}
              </span>
              <div className={cn("w-7 h-7 rounded-lg flex items-center justify-center shrink-0", kpi.bgColor)}>
                <kpi.icon className={cn("w-3.5 h-3.5", kpi.color)} />
              </div>
            </div>
            <div>
              <div className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
                {kpi.value}
              </div>
              <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5 truncate">
                {kpi.subtext}
              </p>
            </div>
          </Link>
        ))}
      </div>

      {/* Main Grid: Pipeline Readiness (2/3) + Operational Inbox (1/3) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pipeline Readiness Stage Breakdown */}
        <div className="lg:col-span-2 bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-2xl p-6 shadow-xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base font-bold text-slate-900 dark:text-white flex items-center gap-2">
                  <Layers className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                  {t("dashboard.pipeline_title")}
                </h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                  {t("dashboard.pipeline_desc")} ({totalInPipeline.toLocaleString()})
                </p>
              </div>
              <span className="text-sm font-bold text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-950/50 px-2.5 py-1 rounded-lg border border-indigo-100 dark:border-indigo-900/50">
                {readyPercent}%
              </span>
            </div>

            {/* Segmented Stacked Progress Bar */}
            <div className="h-3.5 w-full rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden flex my-5">
              <div
                style={{ width: `${readyPercent}%` }}
                className="bg-indigo-600 h-full transition-all duration-500"
                title={`${t("dashboard.ready_stage")}: ${readyPercent}%`}
              />
              <div
                style={{ width: `${needAttrPercent}%` }}
                className="bg-amber-500 h-full transition-all duration-500"
                title={`${t("dashboard.missing_attrs_stage")}: ${needAttrPercent}%`}
              />
              <div
                style={{ width: `${errorPercent}%` }}
                className="bg-rose-500 h-full transition-all duration-500"
                title={`${t("dashboard.validation_issues_stage")}: ${errorPercent}%`}
              />
              <div
                style={{ width: `${unmappedPercent}%` }}
                className="bg-slate-300 dark:bg-slate-700 h-full transition-all duration-500"
                title={`${t("dashboard.unmapped_stage")}: ${unmappedPercent}%`}
              />
            </div>

            {/* Stage Cards Grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2">
              <div className="p-3 rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/30">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
                  <span className="w-2.5 h-2.5 rounded-full bg-indigo-600" />
                  <span>{t("dashboard.ready_stage")}</span>
                </div>
                <div className="text-lg font-bold text-slate-900 dark:text-white">
                  {stats.ready.toLocaleString()}
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">{readyPercent}% {t("dashboard.completed")}</div>
              </div>

              <div className="p-3 rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/30">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
                  <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                  <span>{t("dashboard.missing_attrs_stage")}</span>
                </div>
                <div className="text-lg font-bold text-slate-900 dark:text-white">
                  {stats.need_attributes.toLocaleString()}
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">{needAttrPercent}% {t("dashboard.blocked")}</div>
              </div>

              <div className="p-3 rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/30">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
                  <span className="w-2.5 h-2.5 rounded-full bg-rose-500" />
                  <span>{t("dashboard.validation_issues_stage")}</span>
                </div>
                <div className="text-lg font-bold text-slate-900 dark:text-white">
                  {stats.error.toLocaleString()}
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">{errorPercent}% {t("dashboard.failed")}</div>
              </div>

              <div className="p-3 rounded-xl border border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/30">
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
                  <span className="w-2.5 h-2.5 rounded-full bg-slate-400" />
                  <span>{t("dashboard.unmapped_stage")}</span>
                </div>
                <div className="text-lg font-bold text-slate-900 dark:text-white">
                  {stats.unmapped.toLocaleString()}
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">{unmappedPercent}% {t("dashboard.in_queue")}</div>
              </div>
            </div>
          </div>

          <div className="mt-6 pt-4 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between text-xs">
            <span className="text-slate-500 dark:text-slate-400">
              {t("dashboard.need_process")}
            </span>
            <Link
              href="/dashboard/factories"
              className="font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 flex items-center gap-1 group"
            >
              <span>{t("dashboard.open_workspaces")}</span>
              <ArrowRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5" />
            </Link>
          </div>
        </div>

        {/* Operational Attention Inbox */}
        <div className="bg-white dark:bg-[#111827] border border-slate-200/80 dark:border-slate-800 rounded-2xl p-6 shadow-xs flex flex-col justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900 dark:text-white flex items-center gap-2 mb-1">
              <ShieldCheck className="w-4 h-4 text-slate-700 dark:text-slate-300" />
              {t("dashboard.action_required")}
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
              {t("dashboard.action_required_desc")}
            </p>

            <div className="space-y-3">
              <Link
                href="/dashboard/validation"
                className="flex items-center justify-between p-3 rounded-xl border border-slate-200/70 dark:border-slate-800 hover:border-slate-300 hover:bg-slate-50/60 dark:hover:bg-slate-900/40 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-rose-50 dark:bg-rose-950/50 flex items-center justify-center shrink-0">
                    <AlertTriangle className="w-4 h-4 text-rose-600 dark:text-rose-400" />
                  </div>
                  <div>
                    <div className="text-xs font-bold text-slate-900 dark:text-white">
                      {stats.error.toLocaleString()} {t("dashboard.products_with_errors")}
                    </div>
                    <div className="text-[11px] text-slate-500 dark:text-slate-400">
                      {t("dashboard.fix_in_validation")}
                    </div>
                  </div>
                </div>
                <ChevronRight className="w-4 h-4 text-slate-400 group-hover:translate-x-0.5 transition-transform" />
              </Link>

              <Link
                href="/dashboard/validation"
                className="flex items-center justify-between p-3 rounded-xl border border-slate-200/70 dark:border-slate-800 hover:border-slate-300 hover:bg-slate-50/60 dark:hover:bg-slate-900/40 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-amber-50 dark:bg-amber-950/50 flex items-center justify-center shrink-0">
                    <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                  </div>
                  <div>
                    <div className="text-xs font-bold text-slate-900 dark:text-white">
                      {stats.need_attributes.toLocaleString()} {t("dashboard.missing_attrs")}
                    </div>
                    <div className="text-[11px] text-slate-500 dark:text-slate-400">
                      {t("dashboard.compliance_otto")}
                    </div>
                  </div>
                </div>
                <ChevronRight className="w-4 h-4 text-slate-400 group-hover:translate-x-0.5 transition-transform" />
              </Link>

              <Link
                href="/dashboard/export"
                className="flex items-center justify-between p-3 rounded-xl border border-slate-200/70 dark:border-slate-800 hover:border-slate-300 hover:bg-slate-50/60 dark:hover:bg-slate-900/40 transition-all group"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-emerald-50 dark:bg-emerald-950/50 flex items-center justify-center shrink-0">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                  </div>
                  <div>
                    <div className="text-xs font-bold text-slate-900 dark:text-white">
                      {stats.ready.toLocaleString()} {t("dashboard.ready_for_pub")}
                    </div>
                    <div className="text-[11px] text-slate-500 dark:text-slate-400">
                      {t("dashboard.configure_export")}
                    </div>
                  </div>
                </div>
                <ChevronRight className="w-4 h-4 text-slate-400 group-hover:translate-x-0.5 transition-transform" />
              </Link>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 dark:border-slate-800">
            <Link
              href="/dashboard/validation"
              className="text-xs font-semibold text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 flex items-center justify-center gap-1.5 py-1.5"
            >
              <span>{t("dashboard.view_all_diagnostics")}</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>
      </div>

      {/* Buyer Activity & Drip Feed Planning Intelligence */}
      <div className="pt-2">
        <WeekdayChart weeks={12} />
      </div>
    </div>
  );
}

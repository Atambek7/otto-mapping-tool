"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchWithAuth } from "@/lib/api";
import { useAccount } from "@/lib/useAccount";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  FolderTree,
  Search,
  X,
  Layers,
  CheckCircle2,
  Clock,
  AlertTriangle,
  Calendar,
  ArrowRight,
  ChevronRight,
  LayoutGrid,
  List,
  Info,
  ArrowUpDown,
  PackageOpen,
  TrendingUp,
} from "lucide-react";

type Plan = {
  id?: number;
  factory_id: string;
  factory_name: string | null;
  account_name: "JV" | "XL";
  executor_type: string;
  status?: string;
  total_products: number;
  created_at: string | null;
  progress?: {
    published?: number;
    processing?: number;
    pending?: number;
    failed?: number;
  };
};

type FactorySummary = {
  id: string;
  name: string;
  plans: number;
  products: number;
  published: number;
  processing: number;
  pending: number;
  failed: number;
  latestPlanAt: string | null;
};

type SortMode = "name_asc" | "products_desc" | "published_desc" | "date_desc";

function normalizeAccount(value: unknown) {
  return String(value ?? "").toUpperCase().replace("ACCOUNTTYPE.", "").trim();
}

function readCachedFactoryNames(account: "JV" | "XL") {
  const names: Record<string, string> = {};
  try {
    const raw = localStorage.getItem(`otto_factories_list_${account}_v2`);
    const factories: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(factories)) {
      for (const factory of factories) {
        if (
          factory &&
          normalizeAccount(factory.account) === account &&
          typeof factory.id === "string" &&
          typeof factory.name === "string" &&
          factory.name.trim()
        ) {
          names[factory.id] = factory.name.trim();
        }
      }
    }
  } catch {
    // A missing or stale browser cache is handled by the read-only API below.
  }
  return names;
}

export default function DripFactoriesPage() {
  const { account } = useAccount();
  const { t, i18n } = useTranslation();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [factoryNames, setFactoryNames] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  // Search, sort & view state
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("name_asc");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  useEffect(() => {
    const controller = new AbortController();
    const cachedNames = readCachedFactoryNames(account);
    setPlans([]);
    setFactoryNames(cachedNames);
    setLoaded(false);
    setError(false);

    const load = async () => {
      try {
        const response = await fetchWithAuth(
          `/pipeline/plans?account_name=${encodeURIComponent(account)}`,
          {
            signal: controller.signal,
          },
          0
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value: unknown = await response.json();
        if (!Array.isArray(value)) throw new Error("Invalid plans response");
        if (controller.signal.aborted) return;

        const accountPlans = (value as Plan[]).filter((plan) => plan.account_name === account);
        setPlans(accountPlans);
        setLoaded(true);

        const missingNames = new Set(
          accountPlans
            .filter((plan) => plan.executor_type === "otto_api" && plan.factory_id)
            .filter(
              (plan) =>
                !cachedNames[plan.factory_id] &&
                (!plan.factory_name || plan.factory_name.trim() === plan.factory_id)
            )
            .map((plan) => plan.factory_id)
        );
        if (missingNames.size === 0) return;

        try {
          const factoriesResponse = await fetchWithAuth(
            `/aftercool/factories?account=${encodeURIComponent(account)}`,
            { signal: controller.signal },
            0
          );
          if (!factoriesResponse.ok || controller.signal.aborted) return;
          const raw: unknown = await factoriesResponse.json();
          const factories = Array.isArray(raw)
            ? raw
            : raw && typeof raw === "object" && Array.isArray((raw as { items?: unknown }).items)
            ? (raw as { items: unknown[] }).items
            : [];
          const loadedNames: Record<string, string> = {};
          for (const factory of factories) {
            if (
              factory &&
              typeof factory === "object" &&
              normalizeAccount((factory as { account?: unknown }).account) === account
            ) {
              const item = factory as { id?: unknown; name?: unknown };
              const id = String(item.id ?? "").trim();
              const name = String(item.name ?? "").trim();
              if (missingNames.has(id) && name) loadedNames[id] = name;
            }
          }
          if (!controller.signal.aborted) {
            setFactoryNames((previous) => ({ ...previous, ...loadedNames }));
          }
        } catch {
          // Factory names are supplemental; keep showing the plans if lookup is unavailable.
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoaded(true);
      }
    };

    void load();

    return () => controller.abort();
  }, [account]);

  // Aggregate factories
  const rawFactories = useMemo(() => {
    // 1. Group valid non-cancelled plans by factory_id
    const plansByFactory = new Map<string, Plan[]>();
    for (const plan of plans) {
      if (plan.account_name !== account || plan.executor_type !== "otto_api" || !plan.factory_id) {
        continue;
      }
      // Never count cancelled plans in queue, errors, or products
      if (plan.status && String(plan.status).toLowerCase() === "cancelled") {
        continue;
      }
      const list = plansByFactory.get(plan.factory_id) ?? [];
      list.push(plan);
      plansByFactory.set(plan.factory_id, list);
    }

    const grouped = new Map<string, FactorySummary>();

    // 2. For each factory, process and aggregate its valid plans
    for (const [factoryId, facPlans] of plansByFactory.entries()) {
      // Sort chronologically (oldest to newest)
      facPlans.sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));

      // Helper: check if a newer plan exists that had publications or active queue
      const hasNewerWithPublications = (index: number) => {
        return facPlans
          .slice(index + 1)
          .some(
            (p) =>
              (p.progress?.published ?? 0) > 0 ||
              (p.progress?.pending ?? 0) > 0 ||
              (p.progress?.processing ?? 0) > 0
          );
      };

      // Filter out older aborted/failed plans (0 published, 0 queue) when a newer attempt exists
      const validPlans = facPlans.filter((p, idx) => {
        const pub = p.progress?.published ?? 0;
        const pend = p.progress?.pending ?? 0;
        const proc = p.progress?.processing ?? 0;
        if (pub === 0 && pend === 0 && proc === 0 && hasNewerWithPublications(idx)) {
          return false;
        }
        return true;
      });

      const firstPlan = facPlans[0];
      const factory: FactorySummary = {
        id: factoryId,
        name:
          factoryNames[factoryId] ||
          (firstPlan.factory_name?.trim() && firstPlan.factory_name.trim() !== factoryId
            ? firstPlan.factory_name.trim()
            : factoryId),
        plans: validPlans.length,
        products: 0,
        published: 0,
        processing: 0,
        pending: 0,
        failed: 0,
        latestPlanAt: null,
      };

      for (const plan of validPlans) {
        factory.products += Number(plan.total_products) || 0;
        factory.published += Number(plan.progress?.published) || 0;
        factory.processing += Number(plan.progress?.processing) || 0;
        factory.pending += Number(plan.progress?.pending) || 0;
        factory.failed += Number(plan.progress?.failed) || 0;
        if (plan.created_at && (!factory.latestPlanAt || plan.created_at > factory.latestPlanAt)) {
          factory.latestPlanAt = plan.created_at;
        }
      }

      grouped.set(factoryId, factory);
    }

    return Array.from(grouped.values());
  }, [account, factoryNames, plans]);

  // Summary Metrics
  const summaryMetrics = useMemo(() => {
    const totalFactories = rawFactories.length;
    const totalPlans = rawFactories.reduce((sum, f) => sum + f.plans, 0);
    const totalProducts = rawFactories.reduce((sum, f) => sum + f.products, 0);
    const totalPublished = rawFactories.reduce((sum, f) => sum + f.published, 0);
    const totalInQueue = rawFactories.reduce((sum, f) => sum + f.processing + f.pending, 0);
    const totalFailed = rawFactories.reduce((sum, f) => sum + f.failed, 0);
    const overallPercent = totalProducts > 0 ? Math.round((totalPublished / totalProducts) * 100) : 0;

    return {
      totalFactories,
      totalPlans,
      totalProducts,
      totalPublished,
      totalInQueue,
      totalFailed,
      overallPercent,
    };
  }, [rawFactories]);

  // Filtered & Sorted Factories
  const filteredFactories = useMemo(() => {
    const locale = i18n.language.startsWith("ru") ? "ru" : "en";
    let list = rawFactories;

    if (search.trim()) {
      const q = search.toLowerCase().trim();
      list = list.filter((f) => f.name.toLowerCase().includes(q) || f.id.includes(q));
    }

    return [...list].sort((a, b) => {
      switch (sortMode) {
        case "name_asc":
          return a.name.localeCompare(b.name, locale);
        case "products_desc":
          return b.products - a.products;
        case "published_desc":
          return b.published - a.published;
        case "date_desc":
          return (b.latestPlanAt || "").localeCompare(a.latestPlanAt || "");
        default:
          return 0;
      }
    });
  }, [rawFactories, search, sortMode, i18n.language]);

  const formatDate = (value: string) =>
    new Date(value).toLocaleString(i18n.language.startsWith("ru") ? "ru-RU" : "en-US", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

  return (
    <main className="p-6 space-y-6 max-w-[1600px] mx-auto">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">
            <span>Drip Feed</span>
            <span>•</span>
            <span className="text-indigo-600 dark:text-indigo-400">
              {t("products_search.account", { account })}
            </span>
          </div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
            <span>{t("dripFactories.title")}</span>
            <span className="text-base font-medium px-2 py-0.5 rounded-md bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400 border border-indigo-200/50 dark:border-indigo-800/40 font-mono">
              {account}
            </span>
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {t("dripFactories.subtitle")}
          </p>
        </div>

        {/* Caveat Banner */}
        <div className="flex items-center gap-2.5 px-3.5 py-2 rounded-xl bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 text-xs text-slate-500 dark:text-slate-400 max-w-xl">
          <Info className="w-4 h-4 text-indigo-500 shrink-0" />
          <span className="leading-snug">{t("dripFactories.caveat")}</span>
        </div>
      </div>

      {/* KPI Stats Cards */}
      {loaded && !error && rawFactories.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3.5">
          {/* Card 1: Factories count */}
          <div className="bg-white dark:bg-[#0B0F19] border border-slate-200/80 dark:border-slate-800/80 rounded-xl p-4 shadow-sm flex items-center gap-3.5">
            <div className="w-10 h-10 rounded-lg bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-900/40 flex items-center justify-center text-indigo-600 dark:text-indigo-400 shrink-0">
              <FolderTree className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <div className="text-2xl font-bold text-slate-900 dark:text-white tabular-nums">
                {summaryMetrics.totalFactories}
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 font-medium truncate">
                {t("dripFactories.statFactories")}
              </div>
            </div>
          </div>

          {/* Card 2: Total Plans */}
          <div className="bg-white dark:bg-[#0B0F19] border border-slate-200/80 dark:border-slate-800/80 rounded-xl p-4 shadow-sm flex items-center gap-3.5">
            <div className="w-10 h-10 rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700/60 flex items-center justify-center text-slate-600 dark:text-slate-300 shrink-0">
              <Layers className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <div className="text-2xl font-bold text-slate-900 dark:text-white tabular-nums">
                {summaryMetrics.totalPlans}
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 font-medium truncate">
                {t("dripFactories.statPlans")} ({summaryMetrics.totalProducts})
              </div>
            </div>
          </div>

          {/* Card 3: Published */}
          <div className="bg-white dark:bg-[#0B0F19] border border-slate-200/80 dark:border-slate-800/80 rounded-xl p-4 shadow-sm flex items-center gap-3.5">
            <div className="w-10 h-10 rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-100 dark:border-emerald-900/40 flex items-center justify-center text-emerald-600 dark:text-emerald-400 shrink-0">
              <CheckCircle2 className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">
                {summaryMetrics.totalPublished}
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 font-medium truncate">
                {t("dripFactories.statPublished")} ({summaryMetrics.overallPercent}%)
              </div>
            </div>
          </div>

          {/* Card 4: In Queue / Errors */}
          <div className="bg-white dark:bg-[#0B0F19] border border-slate-200/80 dark:border-slate-800/80 rounded-xl p-4 shadow-sm flex items-center gap-3.5">
            <div className="w-10 h-10 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-100 dark:border-amber-900/40 flex items-center justify-center text-amber-600 dark:text-amber-400 shrink-0">
              <Clock className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <div className="text-2xl font-bold text-amber-600 dark:text-amber-400 tabular-nums">
                {summaryMetrics.totalInQueue}
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 font-medium truncate">
                {t("dripFactories.statInQueue")}
                {summaryMetrics.totalFailed > 0 && (
                  <span className="text-rose-500 ml-1">
                    • {summaryMetrics.totalFailed} {t("dripFactories.statFailed").toLowerCase()}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Toolbar: Search, Sort, View Switcher */}
      {loaded && !error && rawFactories.length > 0 && (
        <div className="bg-white dark:bg-[#0B0F19] p-3 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm flex flex-col sm:flex-row items-center justify-between gap-3">
          {/* Search Input */}
          <div className="relative w-full sm:w-80">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("dripFactories.searchPlaceholder")}
              className="pl-10 pr-9 h-9 bg-slate-50 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700 text-xs focus-visible:ring-indigo-500"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Sort & View Mode */}
          <div className="flex items-center gap-2.5 w-full sm:w-auto justify-end">
            <div className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
              <ArrowUpDown className="w-3.5 h-3.5 text-slate-400" />
              <select
                value={sortMode}
                onChange={(e) => setSortMode(e.target.value as SortMode)}
                className="h-9 px-2.5 bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-medium text-slate-700 dark:text-slate-300 focus:outline-hidden focus:ring-1 focus:ring-indigo-500 cursor-pointer"
              >
                <option value="name_asc">{t("dripFactories.sortByName")}</option>
                <option value="products_desc">{t("dripFactories.sortByProducts")}</option>
                <option value="published_desc">{t("dripFactories.sortByPublished")}</option>
                <option value="date_desc">{t("dripFactories.sortByDate")}</option>
              </select>
            </div>

            {/* View Mode Toggle */}
            <div className="flex items-center bg-slate-100 dark:bg-slate-800/80 p-0.5 rounded-lg border border-slate-200 dark:border-slate-700/60">
              <button
                type="button"
                onClick={() => setViewMode("grid")}
                className={`p-1.5 rounded-md text-xs font-medium transition-colors ${
                  viewMode === "grid"
                    ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-white shadow-xs"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                }`}
                title="Сетка"
              >
                <LayoutGrid className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={() => setViewMode("list")}
                className={`p-1.5 rounded-md text-xs font-medium transition-colors ${
                  viewMode === "list"
                    ? "bg-white dark:bg-slate-700 text-indigo-600 dark:text-white shadow-xs"
                    : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
                }`}
                title="Список"
              >
                <List className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Loading Skeletons */}
      {!loaded && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl p-5 shadow-sm space-y-4 animate-pulse"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-2 flex-1">
                  <div className="h-5 bg-slate-200 dark:bg-slate-800 rounded w-3/4" />
                  <div className="h-4 bg-slate-100 dark:bg-slate-850 rounded w-1/3" />
                </div>
                <div className="w-8 h-8 bg-slate-100 dark:bg-slate-800 rounded-lg" />
              </div>
              <div className="space-y-2">
                <div className="h-2 bg-slate-100 dark:bg-slate-800 rounded-full" />
                <div className="flex gap-2">
                  <div className="h-5 bg-slate-100 dark:bg-slate-800 rounded w-16" />
                  <div className="h-5 bg-slate-100 dark:bg-slate-800 rounded w-16" />
                </div>
              </div>
              <div className="pt-3 border-t border-slate-100 dark:border-slate-800/70 h-4 bg-slate-100 dark:bg-slate-850 rounded w-1/2" />
            </div>
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <div
          role="alert"
          className="flex items-center gap-3 p-4 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900/50 text-rose-700 dark:text-rose-300 text-sm shadow-sm"
        >
          <AlertTriangle className="w-5 h-5 shrink-0 text-rose-500" />
          <span>{t("dripFactories.loadError")}</span>
        </div>
      )}

      {/* Empty State: No plans at all */}
      {loaded && !error && rawFactories.length === 0 && (
        <div className="flex flex-col items-center justify-center p-12 bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl text-center shadow-sm">
          <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 mb-3">
            <PackageOpen className="w-6 h-6" />
          </div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            {t("dripFactories.empty")}
          </h2>
        </div>
      )}

      {/* Empty Search: No matches */}
      {loaded && !error && rawFactories.length > 0 && filteredFactories.length === 0 && (
        <div className="flex flex-col items-center justify-center p-12 bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl text-center shadow-sm">
          <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 mb-3">
            <Search className="w-6 h-6" />
          </div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            {t("dripFactories.noMatches")}
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-md mt-1 leading-relaxed">
            {t("dripFactories.noMatchesDesc", { query: search })}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSearch("")}
            className="mt-4 text-xs font-medium"
          >
            Сбросить поиск
          </Button>
        </div>
      )}

      {/* FACTORIES GRID VIEW */}
      {loaded && !error && filteredFactories.length > 0 && viewMode === "grid" && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredFactories.map((factory) => {
            const pubPercent =
              factory.products > 0
                ? Math.min(100, Math.round((factory.published / factory.products) * 100))
                : 0;
            const inQueue = factory.processing + factory.pending;
            const inQueuePercent =
              factory.products > 0
                ? Math.min(100 - pubPercent, Math.round((inQueue / factory.products) * 100))
                : 0;
            const failedPercent =
              factory.products > 0
                ? Math.min(
                    100 - pubPercent - inQueuePercent,
                    Math.round((factory.failed / factory.products) * 100)
                  )
                : 0;

            return (
              <div
                key={factory.id}
                className="bg-white dark:bg-[#0B0F19] border border-slate-200/80 dark:border-slate-800/80 rounded-xl p-5 hover:border-indigo-500/50 dark:hover:border-indigo-500/40 hover:shadow-md transition-all flex flex-col justify-between group"
              >
                {/* Header */}
                <div>
                  <div className="flex items-start justify-between gap-3 mb-2.5">
                    <div className="min-w-0 flex-1">
                      <Link
                        href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                        className="text-base font-bold text-slate-900 dark:text-slate-100 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors line-clamp-1"
                        title={factory.name}
                      >
                        {factory.name}
                      </Link>
                      <div className="flex items-center gap-2 mt-1">
                        <Link
                          href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                          onClick={(e) => e.stopPropagation()}
                          className="inline-flex items-center gap-1 text-[11px] font-mono text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/40 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 px-2 py-0.5 rounded border border-indigo-200/50 dark:border-indigo-800/40 transition-colors cursor-pointer"
                          title="Перейти к фабрике"
                        >
                          <FolderTree className="w-3 h-3 text-indigo-500 shrink-0" />
                          <span>{factory.id}</span>
                        </Link>
                        <span className="text-[11px] text-slate-400 dark:text-slate-500">
                          {t("dripFactories.planCount", { count: factory.plans })}
                        </span>
                      </div>
                    </div>

                    <Link
                      href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                      className="shrink-0 p-2 text-slate-400 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors"
                      title={t("dripFactories.openFactory")}
                    >
                      <ArrowRight className="w-4 h-4" />
                    </Link>
                  </div>

                  {/* Progress Bar */}
                  <div className="space-y-1.5 my-3.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-slate-500 dark:text-slate-400 font-medium">
                        {t("dripFactories.progressTitle")}
                      </span>
                      <span className="font-semibold text-slate-700 dark:text-slate-200 font-mono">
                        {pubPercent}%
                      </span>
                    </div>

                    <div className="h-2 w-full bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden flex">
                      {pubPercent > 0 && (
                        <div
                          style={{ width: `${pubPercent}%` }}
                          className="bg-emerald-500 transition-all duration-300"
                          title={`Опубликовано: ${factory.published}`}
                        />
                      )}
                      {inQueuePercent > 0 && (
                        <div
                          style={{ width: `${inQueuePercent}%` }}
                          className="bg-amber-500 transition-all duration-300"
                          title={`В очереди: ${inQueue}`}
                        />
                      )}
                      {failedPercent > 0 && (
                        <div
                          style={{ width: `${failedPercent}%` }}
                          className="bg-rose-500 transition-all duration-300"
                          title={`Ошибок: ${factory.failed}`}
                        />
                      )}
                    </div>
                  </div>

                  {/* Badges / Chips */}
                  <div className="flex flex-wrap items-center gap-1.5 pt-1">
                    {/* Published Chip */}
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/50 dark:border-emerald-800/40 px-2 py-0.5 rounded">
                      <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                      <span>{factory.published} опубл.</span>
                    </span>

                    {/* In Queue Chip */}
                    {inQueue > 0 && (
                      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border border-amber-200/50 dark:border-amber-800/40 px-2 py-0.5 rounded">
                        <Clock className="w-3 h-3 text-amber-500 shrink-0" />
                        <span>{inQueue} в очереди</span>
                      </span>
                    )}

                    {/* Failed Chip */}
                    {factory.failed > 0 && (
                      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950/40 border border-rose-200/50 dark:border-rose-800/40 px-2 py-0.5 rounded">
                        <AlertTriangle className="w-3 h-3 text-rose-500 shrink-0" />
                        <span>{factory.failed} ошиб.</span>
                      </span>
                    )}

                    {/* Total Products Chip */}
                    <span className="inline-flex items-center gap-1 text-[11px] text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800/70 border border-slate-200/50 dark:border-slate-700/50 px-2 py-0.5 rounded font-mono">
                      {factory.products} тов.
                    </span>
                  </div>
                </div>

                {/* Footer */}
                <div className="pt-3.5 mt-3.5 border-t border-slate-100 dark:border-slate-800/70 flex items-center justify-between text-[11px] text-slate-400 dark:text-slate-500">
                  <div className="flex items-center gap-1.5 truncate">
                    <Calendar className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    <span>
                      {factory.latestPlanAt ? formatDate(factory.latestPlanAt) : "—"}
                    </span>
                  </div>
                  <Link
                    href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                    className="text-indigo-600 dark:text-indigo-400 font-medium hover:underline inline-flex items-center gap-0.5 text-xs group-hover:translate-x-0.5 transition-transform"
                  >
                    <span>{t("dripFactories.openFactory")}</span>
                    <ChevronRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* FACTORIES LIST VIEW */}
      {loaded && !error && filteredFactories.length > 0 && viewMode === "list" && (
        <div className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden shadow-sm divide-y divide-slate-100 dark:divide-slate-800">
          {filteredFactories.map((factory) => {
            const pubPercent =
              factory.products > 0
                ? Math.min(100, Math.round((factory.published / factory.products) * 100))
                : 0;
            const inQueue = factory.processing + factory.pending;

            return (
              <div
                key={factory.id}
                className="p-4 hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors flex flex-col md:flex-row md:items-center justify-between gap-4 group"
              >
                {/* Left: Info */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2.5">
                    <Link
                      href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                      className="text-base font-bold text-slate-900 dark:text-slate-100 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors truncate"
                      title={factory.name}
                    >
                      {factory.name}
                    </Link>
                    <Link
                      href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                      className="inline-flex items-center gap-1 text-[11px] font-mono text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/40 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 px-2 py-0.5 rounded border border-indigo-200/50 dark:border-indigo-800/40 transition-colors shrink-0"
                    >
                      <FolderTree className="w-3 h-3 text-indigo-500 shrink-0" />
                      <span>{factory.id}</span>
                    </Link>
                  </div>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                    <span>{t("dripFactories.planCount", { count: factory.plans })}</span>
                    <span>•</span>
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      {factory.latestPlanAt ? formatDate(factory.latestPlanAt) : "—"}
                    </span>
                  </div>
                </div>

                {/* Center: Progress & Pills */}
                <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 shrink-0">
                  <div className="w-48 space-y-1">
                    <div className="flex justify-between text-[11px]">
                      <span className="text-slate-400">Прогресс</span>
                      <span className="font-mono font-semibold text-slate-700 dark:text-slate-300">
                        {pubPercent}%
                      </span>
                    </div>
                    <div className="h-1.5 w-full bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                      <div
                        style={{ width: `${pubPercent}%` }}
                        className="h-full bg-emerald-500 rounded-full"
                      />
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/50 dark:border-emerald-800/40 px-2 py-0.5 rounded">
                      <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                      <span>{factory.published}</span>
                    </span>
                    {inQueue > 0 && (
                      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border border-amber-200/50 dark:border-amber-800/40 px-2 py-0.5 rounded">
                        <Clock className="w-3 h-3 text-amber-500" />
                        <span>{inQueue}</span>
                      </span>
                    )}
                    {factory.failed > 0 && (
                      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950/40 border border-rose-200/50 dark:border-rose-800/40 px-2 py-0.5 rounded">
                        <AlertTriangle className="w-3 h-3 text-rose-500" />
                        <span>{factory.failed}</span>
                      </span>
                    )}
                    <span className="text-xs font-mono text-slate-400 px-1">
                      / {factory.products}
                    </span>
                  </div>

                  <Link
                    href={`/dashboard/factories/${encodeURIComponent(factory.id)}`}
                    className="p-2 text-slate-400 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors"
                    title={t("dripFactories.openFactory")}
                  >
                    <ArrowRight className="w-4 h-4" />
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}

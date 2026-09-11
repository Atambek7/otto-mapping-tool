"use client";

import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";
import {
  ShieldCheck, RefreshCw, LayoutGrid, List, Search,
  DownloadCloud, AlertTriangle, AlertCircle, X
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { FactoryCard } from "@/components/factories/FactoryCard";
import { useAccount } from "@/lib/useAccount";
import { useDragSelection } from "@/hooks/useDragSelection";
import { useTranslation } from "react-i18next";

interface InvalidFactoryGroup {
  factoryId: string;
  name: string;
  invalidCount: number;
  errorBreakdown: Record<string, number>;
}

export default function ValidationIndexPage() {
  const router = useRouter();
  const { account } = useAccount();
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [groupedFactories, setGroupedFactories] = useState<InvalidFactoryGroup[]>([]);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [search, setSearch] = useState("");
  const [isExporting, setIsExporting] = useState(false);

  // Global error breakdown across all factories
  const globalBreakdown = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const f of groupedFactories) {
      for (const [err, cnt] of Object.entries(f.errorBreakdown)) {
        counts[err] = (counts[err] || 0) + cnt;
      }
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 4);
  }, [groupedFactories]);

  const totalInvalid = useMemo(() =>
    groupedFactories.reduce((s, f) => s + f.invalidCount, 0)
  , [groupedFactories]);

  const filteredFactories = useMemo(() => {
    if (!search.trim()) return groupedFactories;
    const q = search.toLowerCase();
    return groupedFactories.filter(f => f.name.toLowerCase().includes(q) || f.factoryId.includes(q));
  }, [groupedFactories, search]);

  const {
    selectedIds, setSelectedIds, handleCardMouseDown, suppressClick
  } = useDragSelection(filteredFactories.map(f => ({ id: f.factoryId })));

  const loadGroups = async () => {
    setLoading(true);
    try {
      // 1. Try fast server-aggregated summary first
      let summaryData: any[] | null = null;
      try {
        const sumRes = await fetchWithAuth(`/validation/summary?account=${account}`);
        if (sumRes.ok) {
          const raw = await sumRes.json();
          if (Array.isArray(raw)) summaryData = raw;
        }
      } catch (e) {}

      let items: any[] = [];
      if (!summaryData) {
        const res = await fetchWithAuth(`/validation/products?account=${account}`);
        if (res.ok) {
          const data = await res.json();
          items = Array.isArray(data) ? data : (data?.items ?? []);
        }
      }

      // Load cached global factory names map
      let factoryNames: Record<string, string> = {};
      const savedNames = localStorage.getItem("otto_all_factory_names");
      if (savedNames) {
        try {
          factoryNames = JSON.parse(savedNames);
        } catch (e) {}
      }

      // Also check if we can populate from otto_factories_list
      const savedList = localStorage.getItem("otto_factories_list");
      if (savedList) {
        try {
          const parsed = JSON.parse(savedList);
          const list = Array.isArray(parsed) ? parsed : (parsed?.items ?? []);
          list.forEach((f: any) => {
            if (f.id && f.name) factoryNames[String(f.id)] = f.name;
          });
        } catch (e) {}
      }

      const fidsToCheck = summaryData 
        ? summaryData.map((s: any) => String(s.factory_id))
        : items.map((p: any) => String(p.factory_id));
      const uniqueFids = Array.from(new Set(fidsToCheck)) as string[];
      const hasMissingNames = uniqueFids.some(fid => !factoryNames[fid] || factoryNames[fid] === fid);

      if (hasMissingNames || Object.keys(factoryNames).length === 0) {
        try {
          const factRes = await fetchWithAuth("/aftercool/factories");
          if (factRes.ok) {
            const raw = await factRes.json();
            const allRawFactories = Array.isArray(raw) ? raw : (raw?.items ?? []);
            allRawFactories.forEach((f: any) => {
              if (f.id && f.name) {
                factoryNames[String(f.id)] = f.name;
              }
            });
            localStorage.setItem("otto_all_factory_names", JSON.stringify(factoryNames));
          }
        } catch (e) {
          console.error("Failed to fetch factory names", e);
        }
      }

      const groups: Record<string, { count: number; breakdown: Record<string, number> }> = {};
      if (summaryData) {
        summaryData.forEach((s: any) => {
          // Only show factories that actually have errors or need attributes
          const errCount = s.errors ?? 0;
          const needCount = s.need_attributes ?? 0;
          if (errCount === 0 && needCount === 0) return;
          const fid = String(s.factory_id);
          const breakdown: Record<string, number> = {};
          if (errCount > 0) breakdown["Missing Required Fields"] = errCount;
          if (needCount > 0) breakdown["Needs Attributes"] = needCount;
          groups[fid] = { count: s.count, breakdown };
        });
      } else {
        // Fallback: only include items that actually have errors
        items.filter((p: any) => Array.isArray(p.errors) && p.errors.length > 0).forEach((p: any) => {
          const fid = String(p.factory_id);
          if (!groups[fid]) groups[fid] = { count: 0, breakdown: {} };
          groups[fid].count++;
          (p.errors || []).forEach((err: string) => {
            const label = err.includes(":") ? err.split(":")[0].trim() : err;
            groups[fid].breakdown[label] = (groups[fid].breakdown[label] || 0) + 1;
          });
        });
      }

        const result: InvalidFactoryGroup[] = Object.keys(groups)
          // Extra safety: exclude groups with no breakdown entries (no real issues)
          .filter(fid => Object.keys(groups[fid].breakdown).length > 0)
          .map(fid => ({
            factoryId: fid,
            name: factoryNames[fid] || fid,
            invalidCount: groups[fid].count,
            errorBreakdown: groups[fid].breakdown,
          }));
        result.sort((a, b) => b.invalidCount - a.invalidCount);
        setGroupedFactories(result);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadGroups(); }, [account]);

  const handleExportCSV = async () => {
    if (selectedIds.size === 0) return;
    setIsExporting(true);
    try {
      const ids = Array.from(selectedIds);
      const res = await fetchWithAuth("/validation/export/csv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ factory_ids: ids })
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `validation_export_${ids.length}_factories.zip`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setSelectedIds(new Set());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="space-y-6 pb-20">
      {/* ── HEADER ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
            <ShieldCheck className="w-6 h-6 text-indigo-600 dark:text-indigo-400" />
            {t("validation.title")}
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded-md ml-1">
              {account}
            </span>
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            {t("validation.subtitle")}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex p-1 bg-slate-100 dark:bg-slate-900 rounded-lg border border-slate-200/80 dark:border-slate-800">
            <button
              onClick={() => setViewMode("list")}
              title={t("validation.view_list")}
              className={cn(
                "p-1 rounded-md transition-colors",
                viewMode === "list"
                  ? "bg-white dark:bg-card text-indigo-600 dark:text-indigo-400 shadow-xs"
                  : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              )}
            >
              <List className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode("grid")}
              title={t("validation.view_grid")}
              className={cn(
                "p-1 rounded-md transition-colors",
                viewMode === "grid"
                  ? "bg-white dark:bg-card text-indigo-600 dark:text-indigo-400 shadow-xs"
                  : "text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              )}
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={loadGroups}
            disabled={loading}
            className="gap-1.5 h-9 border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium hover:bg-slate-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>{t("common.refresh")}</span>
          </Button>

          <Button
            variant="outline"
            size="sm"
            className="h-9 border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium hover:bg-slate-50"
            onClick={() => {
              if (selectedIds.size === filteredFactories.length && filteredFactories.length > 0) {
                setSelectedIds(new Set());
              } else {
                setSelectedIds(new Set(filteredFactories.map(f => f.factoryId)));
              }
            }}
          >
            {selectedIds.size === filteredFactories.length && filteredFactories.length > 0
              ? t("validation.deselect_all")
              : t("validation.select_all")}
          </Button>
        </div>
      </div>

      {/* ── TOP SUMMARY (Compact strip + top errors) ── */}
      {!loading && groupedFactories.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {/* Total Affected */}
          <div className="bg-white dark:bg-card border border-slate-200/80 dark:border-slate-800 rounded-xl p-4 shadow-xs flex flex-col justify-between">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">
              {t("validation.total_affected")}
            </span>
            <span className="text-2xl font-bold text-rose-600 dark:text-rose-400 tabular-nums tracking-tight mt-1">
              {totalInvalid.toLocaleString()}
            </span>
            <span className="text-[11px] text-slate-400 mt-1 block">
              {t("validation.across_catalogs", { count: groupedFactories.length })}
            </span>
          </div>

          {/* Top Error Types */}
          {globalBreakdown.map(([label, count]) => (
            <div key={label} className="bg-white dark:bg-card border border-slate-200/80 dark:border-slate-800 rounded-xl p-4 shadow-xs flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between gap-1 mb-1">
                  <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider truncate block" title={label}>
                    {label}
                  </span>
                  <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                </div>
                <span className="text-xl font-bold text-slate-900 dark:text-white tabular-nums tracking-tight">
                  {count.toLocaleString()}
                </span>
              </div>
              <div className="mt-2.5 h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-600 rounded-full transition-all duration-700"
                  style={{ width: `${Math.min(100, Math.round((count / Math.max(1, totalInvalid)) * 100))}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── SEARCH ── */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("validation.filter_placeholder")}
          className="pl-9 h-9 text-xs rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card shadow-xs"
        />
        {search && (
          <button
            onClick={() => setSearch("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* ── FACTORIES LIST / GRID ── */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-28 bg-white dark:bg-card rounded-xl border border-slate-200/60 dark:border-slate-800 animate-pulse" />
          ))}
        </div>
      ) : groupedFactories.length === 0 ? (
        <div className="py-20 text-center flex flex-col items-center justify-center space-y-3 bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 p-8 shadow-xs">
          <div className="w-14 h-14 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 rounded-full flex items-center justify-center">
            <ShieldCheck className="w-7 h-7" />
          </div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">
            {t("validation.inbox_clear_title")}
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm">
            {t("validation.inbox_clear_desc", { account })}
          </p>
        </div>
      ) : (
        <div className={cn("relative select-none", viewMode === "grid" ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" : "flex flex-col gap-2")}>
          {filteredFactories.map((f, index) => (
            <FactoryCard
              key={f.factoryId}
              id={f.factoryId}
              name={f.name}
              invalidCount={f.invalidCount}
              errorBreakdown={f.errorBreakdown}
              viewMode={viewMode}
              variant="validation"
              isSelected={selectedIds.has(f.factoryId)}
              onToggleSelect={() => {
                setSelectedIds(prev => {
                  const next = new Set(prev);
                  if (next.has(f.factoryId)) next.delete(f.factoryId); else next.add(f.factoryId);
                  return next;
                });
              }}
              onMouseDown={(e) => handleCardMouseDown(index, f.factoryId, e)}
              onClick={() => {
                if (suppressClick.current) return;
                router.push(`/dashboard/validation/${f.factoryId}`);
              }}
            />
          ))}
        </div>
      )}

      {/* ── FLOATING SELECTION BAR ── */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-8 left-1/2 -translate-x-1/2 bg-white dark:bg-card text-slate-900 dark:text-white px-4 py-3 rounded-xl shadow-2xl border border-slate-200 dark:border-slate-800 flex items-center gap-3 z-50 animate-in slide-in-from-bottom-4">
          <div className="flex items-center gap-2 shrink-0">
            <div className="h-6 px-2 rounded-md bg-rose-50 dark:bg-rose-950/60 border border-rose-200/80 dark:border-rose-900/60 flex items-center justify-center text-xs font-bold text-rose-600 dark:text-rose-400">
              {selectedIds.size}
            </div>
            <div className="text-xs leading-tight">
              <div className="text-slate-900 dark:text-white font-medium">
                {t("validation.floating_selected", { count: selectedIds.size })}
              </div>
              <div className="text-slate-400 text-[10px]">
                {t("validation.floating_items", {
                  count: Array.from(selectedIds).reduce((s, id) => s + (groupedFactories.find(f => f.factoryId === id)?.invalidCount || 0), 0).toLocaleString()
                })}
              </div>
            </div>
          </div>

          <div className="w-px h-6 bg-slate-200 dark:bg-slate-700" />

          <Button
            size="sm"
            onClick={handleExportCSV}
            disabled={isExporting}
            className="h-8 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white gap-1.5 shadow-xs"
          >
            {isExporting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <DownloadCloud className="w-3.5 h-3.5" />}
            <span>{t("validation.export_csv")}</span>
          </Button>

          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors rounded-md hover:bg-slate-100 dark:hover:bg-slate-800"
            title={t("validation.deselect_all")}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}


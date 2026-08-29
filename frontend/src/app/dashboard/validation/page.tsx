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

interface InvalidFactoryGroup {
  factoryId: string;
  name: string;
  invalidCount: number;
  errorBreakdown: Record<string, number>;
}

export default function ValidationIndexPage() {
  const router = useRouter();
  const { account } = useAccount();
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
      const res = await fetchWithAuth("/validation/products");
      if (res.ok) {
        const data = await res.json();
        const items = Array.isArray(data) ? data : (data?.items ?? []);

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

        // Check if any factory in validation results is missing its name
        const uniqueFids = Array.from(new Set(items.map((p: any) => String(p.factory_id)))) as string[];
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
        items.forEach((p: any) => {
          const fid = String(p.factory_id);
          if (!groups[fid]) groups[fid] = { count: 0, breakdown: {} };
          groups[fid].count++;
          (p.errors || []).forEach((err: string) => {
            const label = err.includes(":") ? err.split(":")[0].trim() : err;
            groups[fid].breakdown[label] = (groups[fid].breakdown[label] || 0) + 1;
          });
        });

        const result: InvalidFactoryGroup[] = Object.keys(groups).map(fid => ({
          factoryId: fid,
          name: factoryNames[fid] || fid,
          invalidCount: groups[fid].count,
          errorBreakdown: groups[fid].breakdown,
        }));
        result.sort((a, b) => b.invalidCount - a.invalidCount);
        setGroupedFactories(result);
      }
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
    <div className="space-y-5 pb-20">
      {/* ── HEADER ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-zinc-100 tracking-tight flex items-center gap-2">
            <ShieldCheck className="w-6 h-6 text-indigo-600" />
            Validation Center
            <span className="text-sm font-normal text-slate-400 ml-1">({account})</span>
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Actionable Inbox — нажмите на фабрику, чтобы исправить ошибки
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex p-0.5 bg-slate-100 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700">
            <button onClick={() => setViewMode("grid")} className={cn("p-1.5 rounded-lg transition-colors", viewMode === "grid" ? "bg-white shadow-sm text-indigo-500 dark:bg-[#0B0F19] dark:text-indigo-400" : "text-slate-400 hover:text-slate-600")}>
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button onClick={() => setViewMode("list")} className={cn("p-1.5 rounded-lg transition-colors", viewMode === "list" ? "bg-white shadow-sm text-indigo-500 dark:bg-[#0B0F19] dark:text-indigo-400" : "text-slate-400 hover:text-slate-600")}>
              <List className="w-4 h-4" />
            </button>
          </div>
          <Button variant="outline" onClick={loadGroups} disabled={loading} className="gap-2 shrink-0">
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Обновить
          </Button>
          <Button
            variant="outline"
            className="gap-2 shrink-0"
            onClick={() => {
              if (selectedIds.size === filteredFactories.length && filteredFactories.length > 0) {
                setSelectedIds(new Set());
              } else {
                setSelectedIds(new Set(filteredFactories.map(f => f.factoryId)));
              }
            }}
          >
            {selectedIds.size === filteredFactories.length && filteredFactories.length > 0 ? "Снять выбор" : "Выбрать все"}
          </Button>
        </div>
      </div>

      {/* ── DATA QUALITY SUMMARY (top error breakdown) ── */}
      {!loading && groupedFactories.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {/* Total errors tile */}
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-xl p-4 flex flex-col justify-between">
            <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400 mb-2">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span className="text-xs font-semibold uppercase tracking-wider">Всего</span>
            </div>
            <div className="text-3xl font-extrabold text-red-600 dark:text-red-400 tabular-nums">{totalInvalid.toLocaleString()}</div>
            <div className="text-xs text-red-500/70 dark:text-red-400/60 mt-1">{groupedFactories.length} фабрик</div>
          </div>
          {/* Top 4 error type tiles */}
          {globalBreakdown.map(([label, count]) => (
            <div key={label} className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl p-4">
              <div className="flex items-center gap-1.5 mb-2">
                <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                <span className="text-xs text-slate-500 dark:text-slate-400 truncate leading-none" title={label}>{label}</span>
              </div>
              <div className="text-2xl font-bold text-slate-800 dark:text-white tabular-nums">{count.toLocaleString()}</div>
              <div className="mt-2 h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-amber-400 to-orange-400 rounded-full transition-all duration-700"
                  style={{ width: `${Math.round((count / totalInvalid) * 100)}%` }}
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
          placeholder="Поиск по фабрике или ID..."
          className="pl-9 bg-white dark:bg-[#0B0F19] rounded-xl border-slate-200 dark:border-slate-800 shadow-sm dark:text-white"
        />
        {search && (
          <button onClick={() => setSearch("")} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* ── FACTORY LIST ── */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}
        </div>
      ) : groupedFactories.length === 0 ? (
        <div className="p-16 text-center flex flex-col items-center justify-center space-y-3 bg-white dark:bg-[#0B0F19] rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <div className="w-16 h-16 bg-emerald-50 dark:bg-emerald-950/30 rounded-full flex items-center justify-center">
            <ShieldCheck className="w-8 h-8 text-emerald-500" />
          </div>
          <p className="text-slate-600 dark:text-slate-300 font-semibold text-lg">Ошибок нет!</p>
          <p className="text-sm text-slate-400 max-w-sm">Все товары аккаунта {account} готовы к выгрузке.</p>
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

      {/* ── SELECTION BAR ── */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/95 backdrop-blur-md text-white px-5 py-3 rounded-2xl shadow-2xl shadow-red-500/20 flex items-center gap-4 z-50 animate-in slide-in-from-bottom-4 border border-slate-700/50">
          {/* Scope */}
          <div className="flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 rounded-full bg-red-500/20 border border-red-500/40 flex items-center justify-center text-xs font-bold text-red-300">
              {selectedIds.size}
            </div>
            <div className="text-xs leading-tight">
              <div className="text-slate-200 font-medium">{selectedIds.size} фабрик выбрано</div>
              <div className="text-slate-500">
                {Array.from(selectedIds).reduce((s, id) => s + (groupedFactories.find(f => f.factoryId === id)?.invalidCount || 0), 0).toLocaleString()} товаров
              </div>
            </div>
          </div>
          <div className="w-px h-8 bg-slate-700/70" />
          <button
            onClick={handleExportCSV}
            disabled={isExporting}
            className="flex items-center gap-2 h-9 px-4 text-sm font-semibold rounded-xl bg-gradient-to-r from-rose-500 to-red-600 hover:from-rose-600 hover:to-red-700 text-white transition-all shadow-md shadow-red-500/30 disabled:opacity-60"
          >
            {isExporting ? <RefreshCw className="w-4 h-4 animate-spin" /> : <DownloadCloud className="w-4 h-4" />}
            Экспорт CSV
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1.5 text-slate-500 hover:text-white transition-colors rounded-lg hover:bg-slate-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}


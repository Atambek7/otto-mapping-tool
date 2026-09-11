"use client";

import { useEffect, useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";
import { getCachedFactory, getAllCachedFactoryIds, setCachedFactory } from "@/lib/cache";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Search, CheckCircle2, AlertCircle, Database, RefreshCw, LayoutGrid, List, ChevronDown, ArrowUpDown
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAccount } from "@/lib/useAccount";
import { useDragSelection } from "@/hooks/useDragSelection";
import { FactoryCard } from "@/components/factories/FactoryCard";
import { BatchActionBar } from "@/components/factories/BatchActionBar";
import { useTranslation } from "react-i18next";

interface Factory {
  id: string;
  name: string;
  account: string;
  kind: string;
  status: string;
  updated: string | null;
  items_count: number;
}

export default function DashboardPage() {
  const router = useRouter();
  const [factories, setFactories] = useState<Factory[]>([]);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [search, setSearch] = useState("");
  const [showOnlyCached, setShowOnlyCached] = useState(false);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [sortMode, setSortMode] = useState<"count_asc"|"count_desc">("count_desc");
  
  const [downloading, setDownloading] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<{current: number, total: number} | null>(null);
  const [isAutoMapping, setIsAutoMapping] = useState(false);
  const [autoMapProgress, setAutoMapProgress] = useState<{current: number, total: number} | null>(null);
  
  const [cachedIds, setCachedIds] = useState<Record<string, boolean>>({});
  const { account } = useAccount();
  const { t } = useTranslation();

  // ── FILTERING & SORTING ──
  const filtered = useMemo(() => {
    let arr = factories;
    if (showOnlyCached) arr = arr.filter((f) => cachedIds[f.id]);
    if (search.trim()) {
      const q = search.toLowerCase();
      arr = arr.filter((f) => f.name.toLowerCase().includes(q) || f.id.includes(q));
    }
    arr = [...arr].sort((a, b) => {
      switch (sortMode) {
        case "count_asc": return a.items_count - b.items_count;
        case "count_desc": return b.items_count - a.items_count;
        default: return 0;
      }
    });
    return arr;
  }, [factories, search, sortMode, showOnlyCached, cachedIds]);

  // ── HOOKS ──
  const { selectedIds, setSelectedIds, handleCardMouseDown, suppressClick } = useDragSelection(filtered);

  // ── API LOAD ──
  const load = async (forceRefresh = false) => {
    // Always load from cache first for instant display
    let cacheMap: Record<string, boolean> = {};
    try {
      const keys = await getAllCachedFactoryIds();
      keys.forEach(k => cacheMap[k] = true);
      setCachedIds(cacheMap);
    } catch (e) {
      console.error("Failed to load cache keys", e);
    }

    const saved = localStorage.getItem("otto_factories_list");
    if (saved && !forceRefresh) {
      try {
        const parsed = JSON.parse(saved);
        const parsedArray = Array.isArray(parsed) ? parsed : (parsed?.items ?? []);
        setFactories(parsedArray);
        setLoading(false);
        // Silently refresh in background (no spinner)
        fetchWithAuth("/aftercool/factories").then(async res => {
          if (!res.ok) return;
          const data = await res.json();
          const all: Factory[] = Array.isArray(data) ? data : data?.items ?? [];
          const f_data = all.filter((f) => f.account === account && f.kind === "lister" && (f.items_count > 0 || cacheMap[f.id]));
          setFactories(f_data);
          localStorage.setItem("otto_factories_list", JSON.stringify(f_data));
        }).catch(() => {});
        return;
      } catch (e) {}
    }

    // No cache: show spinner and load
    if (forceRefresh) setIsRefreshing(true); else setLoading(true);
    try {
      const res = await fetchWithAuth("/aftercool/factories");
      if (!res.ok) throw new Error("Failed");
      const data = await res.json();
      const all: Factory[] = Array.isArray(data) ? data : data?.items ?? [];
      const f_data = all.filter((f) => f.account === account && f.kind === "lister" && (f.items_count > 0 || cacheMap[f.id]));
      const compactList = f_data.map(f => ({ id: f.id, name: f.name, items_count: f.items_count, account: f.account, kind: f.kind }));
      try {
        localStorage.setItem("otto_factories_list", JSON.stringify(compactList));
      } catch (e) {
        console.warn("LocalStorage quota reached:", e);
      }
      setError(false);
    } catch (e) {
      console.error(e);
      if (factories.length === 0) setError(true);
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => { load(); }, [account]);

  // ── BATCH ACTIONS (PARALLEL CONCURRENCY) ──
  const handleBatchCache = async () => {
    if (selectedIds.size === 0) return;
    setDownloading(true);
    const ids = Array.from(selectedIds);
    setDownloadProgress({ current: 0, total: ids.length });
    
    const concurrency = 3;
    let completed = 0;
    for (let i = 0; i < ids.length; i += concurrency) {
      const chunk = ids.slice(i, i + concurrency);
      await Promise.all(
        chunk.map(async (fid) => {
          try {
            const res = await fetchWithAuth(`/aftercool/products?factory_id=${fid}&account=${account}&dataset=lister&limit=500&include_row=1`);
            if (res.ok) {
              const data = await res.json();
              const items = data?.items || data || [];
              await setCachedFactory({ factoryId: fid, products: items, total: items.length, fetchedAt: Date.now() }, true);
              setCachedIds(prev => ({ ...prev, [fid]: true }));
            }
          } catch (e) {
            console.error(e);
          } finally {
            completed++;
            setDownloadProgress({ current: completed, total: ids.length });
          }
        })
      );
    }
    setDownloading(false);
    setDownloadProgress(null);
    setSelectedIds(new Set());
  };

  const handleBatchDeleteCache = async () => {
    if (selectedIds.size === 0) return;
    setDownloading(true);
    const ids = Array.from(selectedIds);
    const { deleteCachedFactory } = await import('@/lib/cache');
    await Promise.all(
      ids.map(async (fid) => {
        try {
          await deleteCachedFactory(fid);
        } catch (e) {
          console.error(e);
        }
      })
    );
    setCachedIds(prev => {
      const nextMap = { ...prev };
      ids.forEach(fid => delete nextMap[fid]);
      return nextMap;
    });
    setDownloading(false);
    setSelectedIds(new Set());
  };

  const handleBatchExport = async (format: "json" | "csv" | "db") => {
    if (selectedIds.size === 0) return;
    setDownloading(true);
    const ids = Array.from(selectedIds);
    try {
      const res = await fetchWithAuth(`/mapping/bulk/export?format=${format}`, {
        method: "POST",
        body: JSON.stringify({ factory_ids: ids })
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `export_${account}_${new Date().toISOString()}.${format === 'db' ? 'sqlite' : format}`;
        a.click();
      }
    } catch (e) { console.error(e); }
    setDownloading(false);
    setSelectedIds(new Set());
  };

  const handleBatchAutoMap = async (useVision: boolean) => {
    if (selectedIds.size === 0) return;
    setIsAutoMapping(true);
    const ids = Array.from(selectedIds);
    
    let totalProducts = 0;
    const factoryPayloads: { fid: string, payload: any[] }[] = [];

    // Step 1: Gather all products to calculate total
    for (let i = 0; i < ids.length; i++) {
      const fid = ids[i];
      try {
        let items: any[] = [];
        const cached = await getCachedFactory(fid);
        if (cached?.products?.length) {
          items = cached.products;
        } else {
          const res = await fetchWithAuth(`/aftercool/products?factory_id=${fid}&account=${account}&dataset=lister&limit=500&include_row=1`);
          if (res.ok) {
            const data = await res.json();
            items = data?.items || data || [];
          }
        }

        const payload = items.slice(0, 500).map((p: any) => {
          const row = p.row || {};
          const ean = p.ean || row.EAN;
          if (!ean) return null;
          let specificsStr = "";
          const rawSpec = row.CustomItemSpecifics || row.ItemSpecifics || "";
          if (rawSpec) {
            try {
              const parsed = typeof rawSpec === "string" ? JSON.parse(rawSpec) : rawSpec;
              if (Array.isArray(parsed)) specificsStr = parsed.map((s: any) => `${s.Name || s.name}: ${s.Value || s.value}`).join("; ");
              else specificsStr = String(rawSpec);
            } catch { specificsStr = String(rawSpec); }
          }
          return {
            ean, title: row.Artikelbeschreibung || p.name || "", specifics: specificsStr,
            gallery_url: row.GalleryURL || "", picture_urls: row.PictureURLs || row.PictureURL || ""
          };
        }).filter(Boolean);

        if (payload.length > 0) {
          factoryPayloads.push({ fid, payload });
          totalProducts += payload.length;
        }
      } catch (e) { console.error(e); }
    }

    if (totalProducts === 0) {
      setIsAutoMapping(false);
      setSelectedIds(new Set());
      setTimeout(() => alert(t("factories.no_products_alert")), 100);
      return;
    }

    setAutoMapProgress({ current: 0, total: totalProducts });
    let processedCount = 0;
    const BATCH_SIZE = 10;

    // Step 2: Process in smaller chunks to prevent backend lags and timeouts
    for (const { fid, payload } of factoryPayloads) {
      for (let i = 0; i < payload.length; i += BATCH_SIZE) {
        const batch = payload.slice(i, i + BATCH_SIZE);
        try {
          await fetchWithAuth("/mapping/auto-map", {
            method: "POST",
            body: JSON.stringify({ factory_id: fid, account, products: batch, only_required: true, use_vision: useVision })
          });
        } catch (e) { console.error(e); }
        processedCount += batch.length;
        setAutoMapProgress({ current: processedCount, total: totalProducts });
      }
    }

    setIsAutoMapping(false);
    setAutoMapProgress(null);
    setSelectedIds(new Set());
    setTimeout(() => alert(t("factories.auto_map_complete", { count: ids.length })), 100);
  };

  // ── UI HELPERS ──
  const stats = useMemo(() => ({
    total: factories.length,
    totalItems: factories.reduce((sum, f) => sum + (f.items_count || 0), 0),
    ok: factories.filter((f) => f.items_count > 0).length,
    missing: factories.filter((f) => f.items_count === 0).length,
    cached: factories.filter((f) => cachedIds[f.id]).length,
  }), [factories, cachedIds]);

  const statusLabel = (f: Factory) => {
    if (f.items_count > 0) return { text: `${f.items_count} ${t("factories.items_suffix")}`, cls: "bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/50" };
    if (cachedIds[f.id]) return { text: t("factories.in_cache"), cls: "bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400 border-indigo-200 dark:border-indigo-800/50" };
    return { text: t("factories.no_data"), cls: "bg-slate-100 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-800/50" };
  };

  return (
    <div className="space-y-6 pb-20">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">{t("factories.title")}</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            {t("factories.subtitle", { account })}
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => load(true)}
          disabled={loading || isRefreshing}
          className="gap-2 shrink-0 border-slate-200 dark:border-slate-800 bg-white dark:bg-card hover:bg-slate-50 dark:hover:bg-slate-900"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${(loading || isRefreshing) ? "animate-spin" : ""}`} />
          <span>{isRefreshing ? t("factories.refreshing") : t("factories.refresh_list")}</span>
        </Button>
      </div>

      {/* Compact Summary Strip */}
      {!loading && !error && (
        <div className="bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 shadow-xs divide-y sm:divide-y-0 sm:divide-x divide-slate-100 dark:divide-slate-800 grid grid-cols-2 sm:grid-cols-5">
          <div className="p-3.5 sm:p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("factories.total_products")}</span>
            <span className="text-xl font-bold text-indigo-600 dark:text-indigo-400 tracking-tight mt-0.5 block">
              {stats.totalItems.toLocaleString()}
            </span>
          </div>
          <div className="p-3.5 sm:p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("factories.factories_count")}</span>
            <span className="text-xl font-bold text-slate-900 dark:text-white tracking-tight mt-0.5 block">
              {stats.total.toLocaleString()}
            </span>
          </div>
          <div className="p-3.5 sm:p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("factories.active")}</span>
            <span className="text-xl font-bold text-emerald-600 dark:text-emerald-400 tracking-tight mt-0.5 block">
              {stats.ok.toLocaleString()}
            </span>
          </div>
          <div className="p-3.5 sm:p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("factories.no_data")}</span>
            <span className="text-xl font-bold text-slate-400 dark:text-slate-500 tracking-tight mt-0.5 block">
              {stats.missing.toLocaleString()}
            </span>
          </div>
          <div className="p-3.5 sm:p-4 col-span-2 sm:col-span-1">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("factories.cached")}</span>
            <span className="text-xl font-bold text-indigo-700 dark:text-indigo-300 tracking-tight mt-0.5 block">
              {stats.cached.toLocaleString()}
            </span>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("factories.search_placeholder")}
            className="pl-9 bg-white dark:bg-card rounded-lg border-slate-200 dark:border-slate-800 text-sm shadow-xs placeholder:text-slate-400"
          />
        </div>
        
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={showOnlyCached ? "default" : "outline"}
            size="sm"
            className={cn(
              "h-9 rounded-lg font-medium transition-colors text-xs gap-1.5",
              showOnlyCached
                ? "bg-indigo-600 hover:bg-indigo-700 text-white"
                : "border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-slate-700 dark:text-slate-300 hover:bg-slate-50"
            )}
            onClick={() => setShowOnlyCached(!showOnlyCached)}
          >
            <Database className="w-3.5 h-3.5" />
            <span>{t("factories.cached_btn", { count: stats.cached })}</span>
          </Button>

          <div className="relative">
            <select
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as any)}
              className="appearance-none bg-white dark:bg-card border border-slate-200 dark:border-slate-800 rounded-lg h-9 pl-9 pr-8 text-xs font-medium text-slate-700 dark:text-slate-300 shadow-xs focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
            >
              <option value="count_desc">{t("factories.sort_desc")}</option>
              <option value="count_asc">{t("factories.sort_asc")}</option>
            </select>
            <div className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">
              <ArrowUpDown className="w-3.5 h-3.5" />
            </div>
            <div className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400">
              <ChevronDown className="w-3.5 h-3.5" />
            </div>
          </div>

          <Button
            variant="outline"
            size="sm"
            className="h-9 rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50"
            onClick={() => setSelectedIds(selectedIds.size === filtered.length ? new Set() : new Set(filtered.map(f => f.id)))}
          >
            {selectedIds.size === filtered.length ? t("factories.deselect_all") : t("factories.select_all")}
          </Button>

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
        </div>
      </div>

      {/* Main Content Area */}
      {error ? (
        <div className="py-16 flex flex-col items-center justify-center text-center bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 p-8">
          <div className="w-12 h-12 rounded-full bg-rose-50 dark:bg-rose-950/40 text-rose-600 dark:text-rose-400 flex items-center justify-center mb-3">
            <AlertCircle className="w-6 h-6" />
          </div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">{t("factories.error_title")}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm mt-1 mb-4">
            {t("factories.error_desc")}
          </p>
          <Button variant="outline" size="sm" onClick={() => load(true)} className="gap-2">
            <RefreshCw className="w-3.5 h-3.5" /> {t("factories.retry")}
          </Button>
        </div>
      ) : loading ? (
        <div className={cn("relative", viewMode === "grid" ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" : "flex flex-col gap-2")}>
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="h-20 bg-white dark:bg-card rounded-xl border border-slate-200/60 dark:border-slate-800 p-4 animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-16 flex flex-col items-center justify-center text-center bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 p-8">
          <div className="w-12 h-12 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-400 flex items-center justify-center mb-3">
            <Search className="w-6 h-6" />
          </div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">{t("factories.empty_title")}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm mt-1">
            {t("factories.empty_desc")}
          </p>
        </div>
      ) : (
        <div className={cn("relative select-none", viewMode === "grid" ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" : "flex flex-col gap-2")}>
          {filtered.map((f, index) => (
            <FactoryCard
              key={f.id}
              id={f.id}
              name={f.name || f.id}
              totalProducts={f.items_count}
              viewMode={viewMode}
              isSelected={selectedIds.has(f.id)}
              onToggleSelect={() => {
                setSelectedIds(prev => {
                  const next = new Set(prev);
                  if (next.has(f.id)) next.delete(f.id); else next.add(f.id);
                  return next;
                });
              }}
              onMouseDown={(e) => handleCardMouseDown(index, f.id, e)}
              onClick={() => {
                if (suppressClick.current) return;
                router.push(`/dashboard/factories/${f.id}`);
              }}
              isCached={!!cachedIds[f.id]}
            />
          ))}
        </div>
      )}

      {/* Floating Batch Action Bar */}
      <BatchActionBar
        selectedIds={selectedIds}
        cachedIds={cachedIds}
        downloading={downloading}
        isAutoMapping={isAutoMapping}
        downloadProgress={downloadProgress}
        autoMapProgress={autoMapProgress}
        handleBatchCache={handleBatchCache}
        handleBatchDeleteCache={handleBatchDeleteCache}
        handleBatchExport={handleBatchExport}
        handleBatchAutoMap={handleBatchAutoMap}
        setSelectedIds={setSelectedIds}
      />
    </div>
  );
}

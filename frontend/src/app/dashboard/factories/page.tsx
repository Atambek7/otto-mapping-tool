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
      setFactories(f_data);
      localStorage.setItem("otto_factories_list", JSON.stringify(f_data));
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

  // ── BATCH ACTIONS ──
  const handleBatchCache = async () => {
    if (selectedIds.size === 0) return;
    setDownloading(true);
    const ids = Array.from(selectedIds);
    setDownloadProgress({ current: 0, total: ids.length });
    
    for (let i = 0; i < ids.length; i++) {
      const fid = ids[i];
      try {
        const res = await fetchWithAuth(`/aftercool/products?factory_id=${fid}&account=${account}&dataset=lister&limit=500&include_row=1`);
        if (res.ok) {
          const data = await res.json();
          const items = data?.items || data || [];
          // Pass true as second argument to mark as manually pinned
          await setCachedFactory({ factoryId: fid, products: items, total: items.length, fetchedAt: Date.now() }, true);
          setCachedIds(prev => ({ ...prev, [fid]: true }));
        }
      } catch (e) { console.error(e); }
      setDownloadProgress({ current: i + 1, total: ids.length });
    }
    setDownloading(false);
    setDownloadProgress(null);
    setSelectedIds(new Set());
  };

  const handleBatchDeleteCache = async () => {
    if (selectedIds.size === 0) return;
    setDownloading(true);
    const ids = Array.from(selectedIds);
    for (let i = 0; i < ids.length; i++) {
      const fid = ids[i];
      try {
        await import('@/lib/cache').then(m => m.deleteCachedFactory(fid));
        setCachedIds(prev => { const n = { ...prev }; delete n[fid]; return n; });
      } catch (e) { console.error(e); }
    }
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
      setTimeout(() => alert("Нет товаров для обработки."), 100);
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
    setTimeout(() => alert(`Автозаполнение завершено для ${ids.length} фабрик! Проверьте результаты в Validation.`), 100);
  };

  // ── UI HELPERS ──
  const stats = useMemo(() => ({
    total: factories.length,
    ok: factories.filter((f) => f.items_count > 0).length,
    missing: factories.filter((f) => f.items_count === 0).length,
    cached: factories.filter((f) => cachedIds[f.id]).length,
  }), [factories, cachedIds]);

  const statusLabel = (f: Factory) => {
    if (f.items_count > 0) return { text: `${f.items_count} товаров`, cls: "bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/50" };
    if (cachedIds[f.id]) return { text: "Доступно в кэше", cls: "bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400 border-indigo-200 dark:border-indigo-800/50" };
    return { text: "Нет данных", cls: "bg-slate-100 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-800/50" };
  };

  // (Moved statusBadge to FactoryCard internally)

  return (
    <div className="space-y-6 pb-16">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-zinc-100 tracking-tight">Рабочие папки {account}</h1>
          <p className="text-sm text-slate-500 mt-1">Фабрики {account} · Lister — выберите папку для маппинга категорий</p>
        </div>
        <Button variant="outline" onClick={() => load(true)} disabled={loading || isRefreshing} className="gap-2 shrink-0">
          <RefreshCw className={`w-4 h-4 ${(loading || isRefreshing) ? "animate-spin" : ""}`} /> Обновить список
        </Button>
      </div>

      {!loading && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: "Всего фабрик", value: stats.total, color: "text-slate-700 dark:text-white" },
            { label: "Активных", value: stats.ok, color: "text-emerald-600 dark:text-emerald-400" },
            { label: "Без данных", value: stats.missing, color: "text-slate-400 dark:text-white" },
            { label: "Кэшировано", value: stats.cached, color: "text-indigo-600 dark:text-indigo-400" },
          ].map((s) => (
            <div key={s.label} className="bg-white dark:bg-[#0B0F19] rounded-2xl border border-slate-200/60 dark:border-slate-800 p-4 shadow-sm">
              <p className="text-xs text-slate-500 dark:text-white mb-1">{s.label}</p>
              <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по названию или ID фабрики..." className="pl-9 bg-white dark:bg-[#0B0F19] rounded-xl border-slate-200 dark:border-slate-800 shadow-sm dark:text-white" />
        </div>
        
        <div className="flex flex-wrap items-center gap-2">
          <Button variant={showOnlyCached ? "default" : "outline"} size="sm" className={cn("h-9 rounded-xl shadow-sm font-medium transition-colors", showOnlyCached ? "bg-indigo-600 hover:bg-indigo-700 text-white border-transparent" : "border-slate-200 dark:border-slate-700 bg-white dark:bg-[#0B0F19] text-slate-600 dark:text-white")} onClick={() => setShowOnlyCached(!showOnlyCached)}>
            <Database className="w-4 h-4 mr-2" /> В кэше ({stats.cached})
          </Button>

          <div className="relative">
            <select value={sortMode} onChange={(e) => setSortMode(e.target.value as any)} className="appearance-none bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl h-9 pl-10 pr-8 text-sm text-slate-700 dark:text-white shadow-sm focus:outline-none cursor-pointer">
              <option value="count_desc">По кол-ву товаров (убывание)</option>
              <option value="count_asc">По кол-ву товаров (возрастание)</option>
            </select>
            <div className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500"><ArrowUpDown className="w-4 h-4" /></div>
            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400"><ChevronDown className="w-4 h-4" /></div>
          </div>

          <Button variant="outline" size="sm" className="h-9 rounded-xl border-slate-200 dark:border-slate-700 bg-white dark:bg-[#0B0F19] shadow-sm text-slate-600 dark:text-white font-medium" onClick={() => setSelectedIds(selectedIds.size === filtered.length ? new Set() : new Set(filtered.map(f => f.id)))}>
            {selectedIds.size === filtered.length ? "Снять выбор" : "Выбрать все"}
          </Button>

          <div className="flex p-0.5 bg-slate-100 dark:bg-slate-800/50 rounded-xl border border-slate-200 dark:border-slate-700">
            <button onClick={() => setViewMode("grid")} className={cn("p-1.5 rounded-lg transition-colors", viewMode === "grid" ? "bg-white shadow-sm text-rose-500 dark:bg-[#0B0F19] dark:text-rose-400" : "text-slate-400 hover:text-slate-600")}><LayoutGrid className="w-4 h-4" /></button>
            <button onClick={() => setViewMode("list")} className={cn("p-1.5 rounded-lg transition-colors", viewMode === "list" ? "bg-white shadow-sm text-rose-500 dark:bg-[#0B0F19] dark:text-rose-400" : "text-slate-400 hover:text-slate-600")}><List className="w-4 h-4" /></button>
          </div>
        </div>
      </div>

      {error ? (
        <div className="py-20 flex flex-col items-center gap-3 text-slate-400 border border-dashed rounded-2xl"><AlertCircle className="w-10 h-10 opacity-40" /><p className="text-sm">Не удалось загрузить фабрики. Проверьте, запущен ли бэкенд.</p><Button variant="outline" onClick={() => load(true)} className="gap-2 mt-2"><RefreshCw className="w-4 h-4" /> Повторить</Button></div>
      ) : loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">{Array.from({ length: 12 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}</div>
      ) : filtered.length === 0 ? (
        <div className="py-20 text-center text-slate-400 border border-dashed rounded-2xl">Фабрики не найдены</div>
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

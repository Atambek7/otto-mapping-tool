"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";
import {
  getCachedFactory, setCachedFactory,
  deleteCachedFactory, formatCacheAge
} from "@/lib/cache";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Save, Download, ChevronLeft, ChevronRight, RefreshCw,
  Database, Wifi, WifiOff, Search, ArrowUpDown,
  ArrowUp, ArrowDown, FileJson, FileSpreadsheet
} from "lucide-react";
import { cn } from "@/lib/utils";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { Checkbox } from "@/components/ui/checkbox";

const PAGE_SIZE = 50;

type SortDir = "asc" | "desc";

interface SortState { col: string; dir: SortDir }

// ── helpers ────────────────────────────────────────────────────────────────
function getVal(p: unknown, col: string): string {
  const item = p as Record<string, unknown>;
  const row = (item.row ?? {}) as Record<string, string>;
  switch (col) {
    case "name": return String(row.Artikelbeschreibung || item.name || "");
    case "ean": return String(item.ean || row.EAN || "");
    case "price": return String(row.SofortkaufenPreis || row.Startpreis || "0");
    case "id": return String(item.product_id || row.ID || "");
    default: return "";
  }
}

function parseSpecifics(raw: unknown): [string, string][] {
  if (!raw) return [];
  try {
    const p = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (Array.isArray(p)) {
      return p
        .filter((item) => item?.Name && item?.Value)
        .slice(0, 6)
        .map((item) => [String(item.Name), String(item.Value)]);
    }
    if (typeof p === "object" && p !== null) {
      return (Object.entries(p) as [string, unknown][])
        .filter(([, v]) => v)
        .slice(0, 6)
        .map(([k, v]) => [k, String(v)]);
    }
  } catch { /* skip */ }
  return [];
}

// ── component ──────────────────────────────────────────────────────────────
export default function FactoryPage() {
  const params = useParams();
  const factoryId = params?.factory_id as string;

  const [allProducts, setAllProducts] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncProgress, setSyncProgress] = useState<{ loaded: number; total: number } | null>(null);
  const [fromCache, setFromCache] = useState(false);
  const [cacheAge, setCacheAge] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});

  // table controls
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>({ col: "", dir: "asc" });
  const [offset, setOffset] = useState(0);

  // row selection & batch
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchCategory, setBatchCategory] = useState("");

  // ── drafts ────────────────────────────────────────────────────────────────
  const loadDrafts = useCallback(async () => {
    if (!factoryId) return;
    const res = await fetchWithAuth(`/drafts/${factoryId}`);
    if (!res.ok) return;
    const data = await res.json();
    const map: Record<string, Record<string, string>> = {};
    data.forEach((d: { product_ean: string; data_json: string }) => {
      try { map[d.product_ean] = JSON.parse(d.data_json || "{}"); } catch { /* skip */ }
    });
    setDrafts(map);
  }, [factoryId]);

  // ── fetch all pages ───────────────────────────────────────────────────────
  const fetchAll = useCallback(async (): Promise<unknown[]> => {
    const BATCH = 200;
    const first = await fetchWithAuth(
      `/aftercool/products?factory_id=${factoryId}&account=JV&dataset=lister&limit=${BATCH}&offset=0&include_row=1`
    );
    if (!first.ok) throw new Error("fetch failed");
    const fd = await first.json();
    const serverTotal: number = fd.total ?? 0;
    const all: unknown[] = Array.isArray(fd) ? fd : fd?.items ?? [];
    setSyncProgress({ loaded: all.length, total: serverTotal });

    if (serverTotal > all.length) {
      const offsets: number[] = [];
      for (let o = BATCH; o < serverTotal; o += BATCH) offsets.push(o);
      const CONCURRENCY = 5;
      for (let i = 0; i < offsets.length; i += CONCURRENCY) {
        const batch = offsets.slice(i, i + CONCURRENCY);
        const results = await Promise.all(
          batch.map((o) =>
            fetchWithAuth(
              `/aftercool/products?factory_id=${factoryId}&account=JV&dataset=lister&limit=${BATCH}&offset=${o}&include_row=1`
            ).then((r) => r.json())
          )
        );
        for (const r of results) {
          all.push(...(Array.isArray(r) ? r : r?.items ?? []));
        }
        setSyncProgress({ loaded: all.length, total: serverTotal });
      }
    }
    return all;
  }, [factoryId]);

  // ── initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!factoryId) return;
    setAllProducts([]); setOffset(0);
    setFromCache(false); setCacheAge(null); setSyncProgress(null);

    const init = async () => {
      setLoading(true);
      await loadDrafts();
      const cached = await getCachedFactory(factoryId);
      if (cached?.products.length) {
        setAllProducts(cached.products);
        setFromCache(true);
        setCacheAge(formatCacheAge(cached.fetchedAt));
      } else {
        setSyncProgress({ loaded: 0, total: 0 });
        const all = await fetchAll();
        setAllProducts(all);
        await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() });
        setSyncProgress(null);
      }
      setLoading(false);
    };
    init().catch(console.error);
  }, [factoryId, loadDrafts, fetchAll]);

  // ── sync ──────────────────────────────────────────────────────────────────
  const handleSync = async () => {
    if (!factoryId || syncing) return;
    setSyncing(true); setFromCache(false);
    setSyncProgress({ loaded: 0, total: 0 });
    await deleteCachedFactory(factoryId);
    const all = await fetchAll();
    setAllProducts(all); setOffset(0);
    await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() });
    setCacheAge("только что");
    setSyncing(false); setSyncProgress(null);
  };

  // ── draft change ──────────────────────────────────────────────────────────
  const setField = (productId: string, field: string, value: string) => {
    setDrafts((prev) => ({
      ...prev,
      [productId]: { ...(prev[productId] ?? {}), [field]: value },
    }));
  };

  // ── save ──────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    setSaving(true);
    try {
      for (const [id, data] of Object.entries(drafts)) {
        await fetchWithAuth("/drafts/", {
          method: "POST",
          body: JSON.stringify({
            factory_name: factoryId,
            product_ean: id,
            data_json: JSON.stringify(data),
            is_ok: !!data?.category,
          }),
        });
      }
      alert("Успешно сохранено!");
    } catch { alert("Ошибка при сохранении"); }
    finally { setSaving(false); }
  };

  // ── exports ───────────────────────────────────────────────────────────────
  const exportCSV = () => {
    const rows = ["ProductID,EAN,Name,Category"];
    allProducts.forEach((p: unknown, i) => {
      const item = p as Record<string, unknown>;
      const row = (item.row ?? {}) as Record<string, string>;
      const id = String(item.product_id || row.ID || i);
      const ean = String(item.ean || row.EAN || "");
      const name = (String(row.Artikelbeschreibung || item.name || "")).replace(/"/g, '""');
      const cat = ((drafts[id]?.category) || "").replace(/"/g, '""');
      rows.push(`"${id}","${ean}","${name}","${cat}"`);
    });
    const blob = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8;" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `otto_${factoryId}.csv` });
    a.click();
  };

  const exportJSON = () => {
    const out = allProducts.map((p: unknown, i) => {
      const item = p as Record<string, unknown>;
      const row = (item.row ?? {}) as Record<string, string>;
      const id = String(item.product_id || row.ID || i);
      return {
        product_id: id,
        ean: String(item.ean || row.EAN || ""),
        name: String(row.Artikelbeschreibung || item.name || ""),
        price: String(row.SofortkaufenPreis || row.Startpreis || ""),
        category: drafts[id]?.category || "",
        specifics: (() => {
          try { return typeof row.CustomItemSpecifics === "string" ? JSON.parse(row.CustomItemSpecifics) : row.CustomItemSpecifics || {}; }
          catch { return {}; }
        })(),
        is_ok: !!(drafts[id]?.category),
      };
    });
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `otto_${factoryId}.json` });
    a.click();
  };

  // ── sort handler ──────────────────────────────────────────────────────────
  const toggleSort = (col: string) => {
    setSort((prev) =>
      prev.col === col
        ? { col, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { col, dir: "asc" }
    );
    setOffset(0);
  };

  // ── filtered + sorted (client-side, uses allProducts) ────────────────────
  const processed = useMemo(() => {
    let arr = allProducts;
    if (search.trim()) {
      const q = search.toLowerCase();
      arr = arr.filter((p) => {
        const item = p as Record<string, unknown>;
        const row = (item.row ?? {}) as Record<string, string>;
        return (
          (row.Artikelbeschreibung || "").toLowerCase().includes(q) ||
          String(item.ean || row.EAN || "").includes(q) ||
          String(item.product_id || row.ID || "").includes(q)
        );
      });
    }
    if (sort.col) {
      arr = [...arr].sort((a, b) => {
        const av = getVal(a, sort.col);
        const bv = getVal(b, sort.col);
        const cmp = av.localeCompare(bv, "de");
        return sort.dir === "asc" ? cmp : -cmp;
      });
    }
    return arr;
  }, [allProducts, search, sort]);

  const total = processed.length;
  const pageItems = processed.slice(offset, offset + PAGE_SIZE);
  const pageStart = total > 0 ? offset + 1 : 0;
  const pageEnd = Math.min(offset + PAGE_SIZE, total);
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  const SortIcon = ({ col }: { col: string }) => {
    if (sort.col !== col) return <ArrowUpDown className="w-3 h-3 opacity-30 ml-1 inline" />;
    return sort.dir === "asc"
      ? <ArrowUp className="w-3 h-3 text-indigo-500 ml-1 inline" />
      : <ArrowDown className="w-3 h-3 text-indigo-500 ml-1 inline" />;
  };

  const okCount = Object.values(drafts).filter((d) => d?.category).length;

  const toggleAll = () => {
    if (selectedIds.size === pageItems.length) {
      setSelectedIds(new Set());
    } else {
      const allIds = pageItems.map((p: any) => {
        const row = p.row ?? {};
        return String(p.product_id || row.ID);
      });
      setSelectedIds(new Set(allIds));
    }
  };

  const toggleOne = (id: string) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  };

  const applyBatchCategory = () => {
    if (!batchCategory) return;
    setDrafts((prev) => {
      const next = { ...prev };
      selectedIds.forEach((id) => {
        next[id] = { ...(next[id] ?? {}), category: batchCategory };
      });
      return next;
    });
    setSelectedIds(new Set());
    setBatchCategory("");
  };

  return (
    <div className="space-y-4 pb-12">

      {/* ── Header toolbar ── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-white dark:bg-zinc-900 p-4 rounded-2xl border border-slate-200/60 shadow-sm">
        <div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900 dark:text-zinc-100">
            {factoryId}
          </h1>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {loading || syncing ? (
              syncProgress ? (
                <span className="text-xs text-slate-400 flex items-center gap-1">
                  <RefreshCw className="w-3 h-3 animate-spin" />
                  {syncProgress.total > 0
                    ? `Загрузка ${syncProgress.loaded} / ${syncProgress.total}`
                    : "Загрузка..."}
                </span>
              ) : <span className="text-xs text-slate-400">Загрузка...</span>
            ) : fromCache ? (
              <span className="text-xs text-emerald-600 flex items-center gap-1">
                <Database className="w-3 h-3" /> Кэш · {cacheAge}
              </span>
            ) : (
              <span className="text-xs text-indigo-600 flex items-center gap-1">
                <Wifi className="w-3 h-3" /> Сервер
              </span>
            )}
            {!loading && allProducts.length > 0 && (
              <span className="text-xs text-slate-400">
                · {allProducts.length.toLocaleString("ru")} товаров
                · <span className="text-emerald-600 font-medium">{okCount} OK</span>
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={handleSync} disabled={syncing || loading} className="gap-1.5 text-sm">
            <RefreshCw className={cn("w-3.5 h-3.5", (syncing || loading) && "animate-spin")} />
            Синхронизировать
          </Button>
          <Button variant="outline" onClick={exportCSV} className="gap-1.5 text-sm">
            <FileSpreadsheet className="w-3.5 h-3.5" /> CSV
          </Button>
          <Button variant="outline" onClick={exportJSON} className="gap-1.5 text-sm">
            <FileJson className="w-3.5 h-3.5" /> JSON
          </Button>
          <Button onClick={handleSave} disabled={saving} className="gap-1.5 text-sm bg-indigo-600 hover:bg-indigo-700 text-white">
            <Save className="w-3.5 h-3.5" />
            {saving ? "Сохранение..." : "Сохранить"}
          </Button>
        </div>
      </div>

      {/* ── Sync progress bar ── */}
      {(syncing || (loading && syncProgress)) && syncProgress && syncProgress.total > 0 && (
        <div className="bg-white dark:bg-zinc-900 rounded-2xl border border-slate-200/60 p-4 shadow-sm">
          <div className="flex justify-between text-xs text-slate-500 mb-2">
            <span>Скачивание...</span>
            <span>{syncProgress.loaded.toLocaleString("ru")} / {syncProgress.total.toLocaleString("ru")}</span>
          </div>
          <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div
              className="h-1.5 bg-indigo-500 rounded-full transition-all duration-300"
              style={{ width: `${Math.round((syncProgress.loaded / syncProgress.total) * 100)}%` }}
            />
          </div>
        </div>
      )}

      {/* ── Search + pagination info + Batch actions ── */}
      {!loading && (
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3 w-full sm:w-auto">
            <div className="relative flex-1 sm:w-64">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
              <Input
                value={search}
                onChange={(e) => { setSearch(e.target.value); setOffset(0); }}
                placeholder="Поиск по названию, EAN, ID..."
                className="pl-8 h-9 text-sm bg-white rounded-xl border-slate-200 shadow-sm"
              />
            </div>
            {total > 0 && (
              <span className="text-sm text-slate-500 whitespace-nowrap">
                {search ? `Найдено ${total.toLocaleString("ru")}` : `${pageStart}–${pageEnd} из ${total.toLocaleString("ru")}`}
              </span>
            )}
          </div>
          
          {/* Batch category application */}
          {selectedIds.size > 0 && (
            <div className="flex items-center gap-2 bg-indigo-50 border border-indigo-100 rounded-xl px-2 py-1 shadow-sm">
              <span className="text-xs font-medium text-indigo-700 px-2 whitespace-nowrap">
                Выбрано: {selectedIds.size}
              </span>
              <div className="w-48">
                <CategoryAutocomplete 
                  value={batchCategory} 
                  onChange={setBatchCategory} 
                />
              </div>
              <Button 
                size="sm" 
                onClick={applyBatchCategory}
                disabled={!batchCategory}
                className="h-7 text-xs bg-indigo-600 hover:bg-indigo-700 text-white rounded-md"
              >
                Применить
              </Button>
            </div>
          )}
        </div>
      )}

      {/* ── Table ── */}
      <div className="bg-white dark:bg-zinc-900 rounded-2xl border border-slate-200/60 shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-6 space-y-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex gap-4 items-center">
                <div className="w-10 h-10 rounded-lg bg-slate-100 animate-pulse shrink-0" />
                <div className="flex-1 space-y-2">
                  <div className="h-3.5 bg-slate-100 rounded animate-pulse w-2/3" />
                  <div className="h-3 bg-slate-100 rounded animate-pulse w-1/4" />
                </div>
                <div className="h-8 w-32 bg-slate-100 rounded-lg animate-pulse" />
              </div>
            ))}
          </div>
        ) : pageItems.length === 0 ? (
          <div className="py-24 flex flex-col items-center gap-3 text-slate-400">
            <WifiOff className="w-10 h-10 opacity-30" />
            <p className="text-sm">{search ? "Ничего не найдено" : "Товары не найдены"}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-slate-100 dark:border-zinc-800 bg-slate-50 dark:bg-zinc-800/50">
                  <th className="w-10 px-3 py-3">
                    <Checkbox
                      checked={pageItems.length > 0 && selectedIds.size === pageItems.length}
                      onCheckedChange={toggleAll}
                    />
                  </th>
                  <th className="w-14 px-2 py-3 text-left">
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">#</span>
                  </th>
                  <th className="w-16 px-2 py-3" />
                  <th
                    className="px-3 py-3 text-left cursor-pointer select-none hover:text-indigo-600"
                    onClick={() => toggleSort("name")}
                  >
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      Название <SortIcon col="name" />
                    </span>
                  </th>
                  <th
                    className="px-3 py-3 text-left cursor-pointer select-none hover:text-indigo-600 whitespace-nowrap"
                    onClick={() => toggleSort("ean")}
                  >
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                      EAN <SortIcon col="ean" />
                    </span>
                  </th>
                  <th className="px-3 py-3 text-left min-w-[200px]">
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Характеристики</span>
                  </th>
                  <th className="px-3 py-3 text-left min-w-[250px]">
                    <span className="text-xs font-semibold text-indigo-500 uppercase tracking-wide">Категория Otto</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-zinc-800">
                {pageItems.map((p: unknown, idx) => {
                  const item = p as Record<string, unknown>;
                  const row = (item.row ?? {}) as Record<string, string>;
                  const productId = String(item.product_id || row.ID || `item-${offset + idx}`);
                  const realEan = String(item.ean || row.EAN || "");
                  const name = String(row.Artikelbeschreibung || item.name || row.Name || "");
                  const price = String(row.SofortkaufenPreis || row.Startpreis || "");
                  const galleryUrl = String(row.GalleryURL || row.PictureURL || "");
                  const specs = parseSpecifics(row.CustomItemSpecifics);
                  const draft = drafts[productId] ?? {};
                  const isOk = !!draft.category;

                  return (
                    <tr
                      key={productId}
                      className={cn(
                        "hover:bg-slate-50 dark:hover:bg-zinc-800/40 transition-colors",
                        isOk && "bg-emerald-50/30 dark:bg-emerald-900/5"
                      )}
                    >
                      {/* Checkbox */}
                      <td className="px-3 py-2">
                        <Checkbox
                          checked={selectedIds.has(productId)}
                          onCheckedChange={() => toggleOne(productId)}
                        />
                      </td>

                      {/* Row number */}
                      <td className="px-2 py-2 text-xs text-slate-400 font-mono">
                        {offset + idx + 1}
                      </td>

                      {/* Image */}
                      <td className="px-2 py-2">
                        <div className="w-12 h-12 rounded-lg bg-slate-100 dark:bg-zinc-800 overflow-hidden flex items-center justify-center">
                          {galleryUrl ? (
                            <img
                              src={galleryUrl}
                              alt=""
                              className="w-full h-full object-contain"
                              onError={(e) => { e.currentTarget.style.display = "none"; }}
                            />
                          ) : (
                            <span className="text-slate-300 text-[10px]">нет</span>
                          )}
                        </div>
                      </td>

                      {/* Name — read only, wrap normally */}
                      <td className="px-3 py-2 max-w-sm">
                        <div className="text-xs text-slate-900 leading-snug">
                          {name}
                        </div>
                      </td>

                      {/* EAN */}
                      <td className="px-3 py-2">
                        <span className="text-xs font-mono text-slate-600 dark:text-slate-300 whitespace-nowrap">
                          {realEan}
                        </span>
                      </td>

                      {/* Specs */}
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {specs.map(([k, v], i) => (
                            <div key={i} className="inline-flex text-[10px] rounded overflow-hidden border border-slate-200">
                              <span className="bg-slate-100 text-slate-500 px-1.5 py-0.5 font-medium">{k}</span>
                              <span className="bg-white text-slate-700 px-1.5 py-0.5">{v}</span>
                            </div>
                          ))}
                        </div>
                      </td>

                      {/* Category — autocomplete picker */}
                      <td className="px-3 py-2">
                        <CategoryAutocomplete
                          value={draft.category ?? ""}
                          onChange={(val) => setField(productId, "category", val)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Pagination ── */}
      {!loading && total > PAGE_SIZE && (
        <div className="flex justify-center items-center gap-3 py-4">
          <Button variant="outline" size="sm" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            <ChevronLeft className="w-4 h-4 mr-1" /> Назад
          </Button>
          <span className="text-sm text-slate-500 font-medium">
            {currentPage} / {totalPages}
          </span>
          <Button variant="outline" size="sm" disabled={pageEnd >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}>
            Вперед <ChevronRight className="w-4 h-4 ml-1" />
          </Button>
        </div>
      )}
    </div>
  );
}

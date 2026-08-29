"use client";

import { useEffect, useState, useMemo } from "react";
import { fetchWithAuth } from "@/lib/api";
import { ProductDrawer } from "@/components/ProductDrawer";
import { ShieldCheck, AlertCircle, RefreshCw, CheckCircle2, Trash2, Search, X, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

export default function FactoryValidationPage() {
  const params = useParams();
  const router = useRouter();
  const factoryId = params.factory_id as string;
  const [invalidProducts, setInvalidProducts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeProduct, setActiveProduct] = useState<any | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [loadingProduct, setLoadingProduct] = useState<string | null>(null);
  const [factoryNames, setFactoryNames] = useState<Record<string, string>>({});
  const [categoryNames, setCategoryNames] = useState<Record<string, string>>({});

  const loadProducts = async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth(`/validation/products?factory_id=${factoryId}`);
      if (res.ok) {
        const data = await res.json();
        setInvalidProducts(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProducts();
    // Load factory names from localStorage cache
      const loadFactories = async () => {
        try {
          let map: Record<string, string> = {};
          const savedGlobal = localStorage.getItem("otto_all_factory_names");
          if (savedGlobal) {
            try { map = JSON.parse(savedGlobal); } catch (e) {}
          }
          const savedList = localStorage.getItem("otto_factories_list");
          if (savedList) {
            try {
              const parsed = JSON.parse(savedList);
              const list = Array.isArray(parsed) ? parsed : (parsed?.items ?? []);
              list.forEach((f: any) => { if (f.id && f.name) map[String(f.id)] = f.name; });
            } catch (e) {}
          }

          if (!map[String(factoryId)]) {
            const res = await fetchWithAuth(`/aftercool/factories`);
            if (res.ok) {
              const raw = await res.json();
              const list = Array.isArray(raw) ? raw : (raw?.items ?? []);
              list.forEach((f: any) => { if (f.id && f.name) map[String(f.id)] = f.name; });
              localStorage.setItem("otto_all_factory_names", JSON.stringify(map));
            }
          }
          setFactoryNames(map);
        } catch (e) {}
      };
      loadFactories();

    // Load category names from static file
    fetch('/otto_categories.json')
      .then(res => res.json())
      .then(data => {
        const map: Record<string, string> = {};
        data.forEach((c: any) => { map[String(c.categoryGroupId)] = c.categoryGroupPath; });
        setCategoryNames(map);
      })
      .catch(e => console.error("Failed to load categories", e));
  }, []);

  const handleForceApprove = async () => {
    if (selectedIds.size === 0) return;
    setLoading(true);
    try {
      const res = await fetchWithAuth("/validation/approve", {
        method: "POST",
        body: JSON.stringify({ product_eans: Array.from(selectedIds) })
      });
      if (res.ok) {
        setInvalidProducts(prev => prev.filter(p => !selectedIds.has(p.product_ean)));
        setSelectedIds(new Set());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteMappings = async () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`Вы уверены, что хотите сбросить маппинг для ${selectedIds.size} товаров?`)) return;
    setLoading(true);
    try {
      const res = await fetchWithAuth("/mapping/bulk/delete", {
        method: "POST",
        body: JSON.stringify({ product_eans: Array.from(selectedIds) })
      });
      if (res.ok) {
        setInvalidProducts(prev => prev.filter(p => !selectedIds.has(p.product_ean)));
        setSelectedIds(new Set());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = (ean: string, cat: string, catName: string, attrs: any) => {
    setInvalidProducts(prev => prev.filter(p => p.product_ean !== ean));
    setActiveProduct(null);
  };

  const openProduct = async (p: any) => {
    // Optimistically open with minimal info first
    setActiveProduct({
      ean: p.product_ean,
      row: { EAN: p.product_ean },
      name: p.product_ean,
      img: null,
      price: ""
    });
    // Then fetch real product data from Aftercool
    setLoadingProduct(p.product_ean);
    try {
      const res = await fetchWithAuth(
        `/aftercool/products?factory_id=${p.factory_id}&account=JV&dataset=lister&q=${p.product_ean}&limit=1&include_row=1`
      );
      if (res.ok) {
        const data = await res.json();
        const item = (data?.items || data || [])[0];
        if (item) {
          const row = item.row || {};
          setActiveProduct({
            ean: p.product_ean,
            row,
            name: row.Artikelbeschreibung || item.name || p.product_ean,
            img: row.GalleryURL || item.img || null,
            price: row.StartPrice || item.price || ""
          });
        }
      }
    } catch (e) {
      console.error("Failed to load product details", e);
    } finally {
      setLoadingProduct(null);
    }
  };

  // Client-side search filter
  const filtered = useMemo(() => {
    if (!search.trim()) return invalidProducts;
    const q = search.toLowerCase();
    return invalidProducts.filter(p => {
      const ean = (p.product_ean || "").toLowerCase();
      const factory = (factoryNames[p.factory_id] || p.factory_id || "").toLowerCase();
      const catName = categoryNames[p.category_id] || p.category_name || p.category_id || "";
      return ean.includes(q) || factory.includes(q) || catName.toLowerCase().includes(q);
    });
  }, [invalidProducts, search, factoryNames, categoryNames]);

  // Error breakdown by type
  const errorBreakdown = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of invalidProducts) {
      (p.errors || []).forEach((err: string) => {
        const label = err.includes(":") ? err.split(":")[0].trim() : err;
        counts[label] = (counts[label] || 0) + 1;
      });
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [invalidProducts]);

  return (
    <div className="flex flex-col h-full space-y-4">
      {/* ── HEADER ── */}
      <div className="flex flex-col sm:flex-row justify-between gap-4">
        <div>
          <button onClick={() => router.back()} className="flex items-center text-sm font-medium text-slate-500 hover:text-indigo-600 mb-2 transition-colors">
            <ArrowLeft className="w-4 h-4 mr-1" /> Назад к списку
          </button>
          <h1 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-indigo-600" />
            {factoryNames[factoryId] || factoryId}
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            <span className="font-medium text-red-600 dark:text-red-400">{invalidProducts.length}</span> товаров требуют внимания
            {search && ` · показано ${filtered.length}`}
          </p>
        </div>
        <div className="flex gap-2 flex-wrap items-start">
          <Button variant="outline" onClick={loadProducts} disabled={loading} className="dark:bg-transparent dark:border-slate-800">
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} /> Обновить
          </Button>
        </div>
      </div>

      {/* ── ERROR BREAKDOWN STRIP ── */}
      {!loading && errorBreakdown.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {errorBreakdown.map(([label, count]) => (
            <button
              key={label}
              onClick={() => setSearch(search === label ? "" : label)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                search === label
                  ? "bg-red-100 border-red-300 text-red-700 dark:bg-red-900/40 dark:border-red-700 dark:text-red-300"
                  : "bg-white border-slate-200 text-slate-600 hover:border-red-200 hover:text-red-600 dark:bg-[#0B0F19] dark:border-slate-700 dark:text-slate-400"
              }`}
            >
              <AlertCircle className="w-3 h-3" />
              {label}
              <span className="ml-1 tabular-nums font-bold">{count}</span>
            </button>
          ))}
        </div>
      )}

      {/* Search Bar */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
        <Input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Поиск по EAN, фабрике или категории..."
          className="pl-9 pr-9 dark:bg-[#0B0F19] dark:border-slate-800 dark:text-white dark:placeholder-slate-500"
        />
        {search && (
          <button
            onClick={() => setSearch("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm">
        {loading ? (
          <div className="p-10 text-center text-slate-400">Loading validation data...</div>
        ) : invalidProducts.length === 0 ? (
          <div className="p-16 text-center flex flex-col items-center justify-center space-y-3">
            <div className="w-16 h-16 bg-emerald-50 dark:bg-emerald-950/30 rounded-full flex items-center justify-center">
              <ShieldCheck className="w-8 h-8 text-emerald-500" />
            </div>
            <p className="text-slate-600 dark:text-slate-300 font-medium">All mapped products are valid!</p>
            <p className="text-sm text-slate-400 max-w-sm">Great job. You have no products with mapping errors.</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-16 text-center text-slate-400 dark:text-slate-500">
            Ничего не найдено по запросу «{search}»
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-800 text-slate-500 dark:text-slate-400 sticky top-0 z-10">
              <tr>
                <th className="py-3 px-4 w-12">
                  <Checkbox
                    checked={selectedIds.size === filtered.length && filtered.length > 0}
                    onCheckedChange={(c) => setSelectedIds(c ? new Set(filtered.map(p => p.product_ean)) : new Set())}
                  />
                </th>
                <th className="py-3 px-4 font-semibold w-48 text-slate-700 dark:text-slate-300">EAN</th>
                <th className="py-3 px-4 font-semibold w-56 text-slate-700 dark:text-slate-300">Factory / Category</th>
                <th className="py-3 px-4 font-semibold text-slate-700 dark:text-slate-300">Errors</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p, i) => {
                const isSelected = selectedIds.has(p.product_ean);
                const factoryName = factoryNames[p.factory_id] || p.factory_id;
                return (
                  <tr
                    key={p.product_ean + i}
                    onClick={() => openProduct(p)}
                    className={`border-b border-slate-100 dark:border-slate-800/50 hover:bg-slate-50 dark:hover:bg-slate-800/30 cursor-pointer transition-colors ${isSelected ? 'bg-indigo-50/50 dark:bg-indigo-950/20' : ''} ${loadingProduct === p.product_ean ? 'opacity-60' : ''}`}
                  >
                    <td className="py-3 px-4" onClick={e => e.stopPropagation()}>
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => {
                          const next = new Set(selectedIds);
                          if (next.has(p.product_ean)) next.delete(p.product_ean); else next.add(p.product_ean);
                          setSelectedIds(next);
                        }}
                      />
                    </td>
                    <td className="py-3 px-4 font-mono text-xs text-slate-600 dark:text-slate-400">
                      {p.product_ean}
                    </td>
                    <td className="py-3 px-4">
                      <div className="font-medium text-slate-700 dark:text-white text-sm">{factoryName}</div>
                      <div
                        className="text-xs text-slate-500 dark:text-slate-400 truncate max-w-[200px] mt-0.5"
                        title={categoryNames[p.category_id] || p.category_name || p.category_id || "No Category"}
                      >
                        {categoryNames[p.category_id] || p.category_name || p.category_id || "No Category"}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex flex-col gap-1">
                        {(p.errors || []).map((err: string, idx: number) => (
                          <span key={idx} className="flex items-center text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 px-2 py-1 rounded-md max-w-fit">
                            <AlertCircle className="w-3 h-3 mr-1.5 flex-shrink-0" />
                            {err}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {activeProduct && (
        <>
          <div className="fixed inset-0 z-40 bg-black/20 dark:bg-black/40 backdrop-blur-[1px] animate-in fade-in duration-200" onClick={() => setActiveProduct(null)} />
          <ProductDrawer
            product={activeProduct}
            factoryId={invalidProducts.find(p => p.product_ean === activeProduct.ean)?.factory_id || ""}
            onClose={() => setActiveProduct(null)}
            onSave={handleSave}
          />
        </>
      )}

      {/* ── CONTEXTUAL SELECTION BAR ── */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/95 backdrop-blur-md text-white px-5 py-3 rounded-2xl shadow-2xl shadow-red-500/20 flex items-center gap-3 z-50 animate-in slide-in-from-bottom-4 border border-slate-700/50">
          {/* Selection scope */}
          <div className="flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 rounded-full bg-red-500/20 border border-red-500/40 flex items-center justify-center text-xs font-bold text-red-300">{selectedIds.size}</div>
            <div className="text-xs leading-tight">
              <div className="text-slate-200 font-medium">{selectedIds.size} выбрано</div>
              <div className="text-slate-500">из {filtered.length} в таблице</div>
            </div>
          </div>
          <div className="w-px h-8 bg-slate-700/70" />
          <button
            onClick={handleForceApprove}
            disabled={loading}
            className="flex items-center gap-2 h-8 px-3 text-xs font-semibold rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white transition-all disabled:opacity-60"
          >
            <CheckCircle2 className="w-3.5 h-3.5" /> Подтвердить
          </button>
          <button
            onClick={handleDeleteMappings}
            disabled={loading}
            className="flex items-center gap-2 h-8 px-3 text-xs font-medium rounded-xl bg-slate-800 hover:bg-red-600 text-slate-300 hover:text-white transition-all disabled:opacity-60"
          >
            <Trash2 className="w-3.5 h-3.5" /> Сбросить маппинг
          </button>
          <button onClick={() => setSelectedIds(new Set())} className="p-1.5 text-slate-500 hover:text-white transition-colors rounded-lg hover:bg-slate-800">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

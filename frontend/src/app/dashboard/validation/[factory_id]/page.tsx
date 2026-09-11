"use client";

import { useEffect, useState, useMemo } from "react";
import { fetchWithAuth } from "@/lib/api";
import { ProductDrawer } from "@/components/ProductDrawer";
import { ShieldCheck, AlertCircle, RefreshCw, CheckCircle2, Trash2, Search, X, AlertTriangle, PackageOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useAccount } from "@/lib/useAccount";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";

export default function FactoryValidationPage() {
  const params = useParams();
  const router = useRouter();
  const factoryId = params.factory_id as string;
  const { account } = useAccount();
  const { t } = useTranslation();
  const [invalidProducts, setInvalidProducts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeProduct, setActiveProduct] = useState<any | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [loadingProduct, setLoadingProduct] = useState<string | null>(null);
  const [factoryNames, setFactoryNames] = useState<Record<string, string>>({});
  const [categoryNames, setCategoryNames] = useState<Record<string, string>>({});
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);

  const handleImageHover = (img: string, name: string, price: string, e: React.MouseEvent<HTMLElement>) => {
    if (!img) return;
    const rect = e.currentTarget.getBoundingClientRect();
    setImgPreview({
      url: img,
      name,
      price,
      rect: {
        top: rect.top,
        left: rect.left,
        right: rect.right,
        bottom: rect.bottom,
        height: rect.height,
      },
    });
  };

  const handleImageLeave = () => {
    setImgPreview(null);
  };

  const loadProducts = async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth(`/validation/products?factory_id=${factoryId}&account=${account}`);
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
    if (!confirm(t("validation.confirm_reset", { count: selectedIds.size }))) return;
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
    // Optimistically open with existing info immediately
    setActiveProduct({
      ean: p.product_ean,
      row: { EAN: p.product_ean },
      name: p.product_name || p.product_ean,
      img: p.image_url || null,
      price: p.price || ""
    });
    // Then fetch full product data from Aftercool
    setLoadingProduct(p.product_ean);
    try {
      const res = await fetchWithAuth(
        `/aftercool/products?factory_id=${p.factory_id}&account=${account}&dataset=lister&q=${p.product_ean}&limit=1&include_row=1`
      );
      if (res.ok) {
        const data = await res.json();
        const item = (data?.items || data || [])[0];
        if (item) {
          const row = item.row || {};
          setActiveProduct({
            ean: p.product_ean,
            row,
            name: row.Artikelbeschreibung || item.name || p.product_name || p.product_ean,
            img: row.GalleryURL || item.img || p.image_url || null,
            price: row.StartPrice || item.price || p.price || ""
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
      const name = (p.product_name || "").toLowerCase();
      const factory = (factoryNames[p.factory_id] || p.factory_id || "").toLowerCase();
      const catName = categoryNames[p.category_id] || p.category_name || p.category_id || "";
      return ean.includes(q) || name.includes(q) || factory.includes(q) || catName.toLowerCase().includes(q);
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
    <div className="flex flex-col h-full space-y-5 pb-20">
      {/* ── BREADCRUMB & HEADER ── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-xs text-slate-400 dark:text-slate-500">
          <Link href="/dashboard/validation" className="hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">
            {t("validation.title")}
          </Link>
          <span>/</span>
          <span className="text-slate-700 dark:text-slate-300 font-medium truncate max-w-xs sm:max-w-md">
            {factoryNames[factoryId] || factoryId}
          </span>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button
              onClick={() => router.back()}
              className="h-9 w-9 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-card flex items-center justify-center hover:bg-slate-50 transition-colors text-slate-600 dark:text-slate-300 shrink-0"
              title={t("validation.back")}
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <div>
              <h1 className="text-xl font-bold text-slate-900 dark:text-white tracking-tight leading-tight flex items-center gap-2">
                {factoryNames[factoryId] || factoryId}
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                <span className="font-semibold text-rose-600 dark:text-rose-400">
                  {t("validation.items_require_attention", { count: invalidProducts.length })}
                </span>
                {search && ` · ${t("validation.filtered_to", { count: filtered.length })}`}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={loadProducts}
              disabled={loading}
              className="gap-1.5 h-9 border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium hover:bg-slate-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>{t("common.refresh")}</span>
            </Button>
          </div>
        </div>
      </div>

      {/* ── ERROR BREAKDOWN CHIPS ── */}
      {!loading && errorBreakdown.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-slate-400 mr-1">{t("validation.filter_by_issue")}</span>
          {errorBreakdown.map(([label, count]) => (
            <button
              key={label}
              onClick={() => setSearch(search === label ? "" : label)}
              className={cn(
                "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border transition-colors shadow-xs",
                search === label
                  ? "bg-rose-50 border-rose-300 text-rose-700 dark:bg-rose-950/40 dark:border-rose-700 dark:text-rose-300 font-semibold"
                  : "bg-white border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-400 hover:border-slate-300 dark:hover:border-slate-700 hover:bg-slate-50"
              )}
            >
              <AlertCircle className="w-3 h-3 text-rose-500" />
              <span>{label}</span>
              <span className="tabular-nums font-bold text-rose-600 dark:text-rose-400">({count})</span>
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
          placeholder={t("validation.filter_ean_placeholder")}
          className="pl-9 pr-9 h-9 text-xs rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card shadow-xs"
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

      {/* Table */}
      <div className="flex-1 overflow-auto bg-white dark:bg-card border border-slate-200/80 dark:border-slate-800 rounded-xl shadow-xs">
        {loading ? (
          <div className="p-16 text-center text-xs text-slate-400">{t("validation.loading_errors")}</div>
        ) : invalidProducts.length === 0 ? (
          <div className="p-16 text-center flex flex-col items-center justify-center space-y-3">
            <div className="w-14 h-14 bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400 rounded-full flex items-center justify-center">
              <ShieldCheck className="w-7 h-7" />
            </div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">{t("validation.all_valid_title")}</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm">{t("validation.all_valid_desc")}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-16 text-center text-xs text-slate-400">
            {t("validation.no_matches", { search })}
          </div>
        ) : (
          <table className="w-full text-left text-xs border-collapse">
            <thead className="bg-slate-50/90 dark:bg-slate-900/80 backdrop-blur-xs border-b border-slate-200/80 dark:border-slate-800 text-slate-500 font-semibold sticky top-0 z-10">
              <tr>
                <th className="py-2.5 px-4 w-12">
                  <Checkbox
                    checked={selectedIds.size === filtered.length && filtered.length > 0}
                    onCheckedChange={(c) => setSelectedIds(c ? new Set(filtered.map(p => p.product_ean)) : new Set())}
                  />
                </th>
                <th className="py-2.5 px-3 w-16 text-center">{t("validation.th_image", "Фото")}</th>
                <th className="py-2.5 px-4 w-72">{t("validation.th_product", "Товар")}</th>
                <th className="py-2.5 px-4 w-52">{t("validation.th_category")}</th>
                <th className="py-2.5 px-4">{t("validation.th_errors")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
              {filtered.map((p, i) => {
                const isSelected = selectedIds.has(p.product_ean);
                return (
                  <tr
                    key={p.product_ean + i}
                    onClick={() => openProduct(p)}
                    className={cn(
                      "hover:bg-slate-50/70 dark:hover:bg-slate-800/30 cursor-pointer transition-colors group",
                      isSelected && "bg-indigo-50/50 dark:bg-indigo-950/20",
                      loadingProduct === p.product_ean && "opacity-60"
                    )}
                  >
                    <td className="py-3 px-4 w-12" onClick={e => e.stopPropagation()}>
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => {
                          const next = new Set(selectedIds);
                          if (next.has(p.product_ean)) next.delete(p.product_ean); else next.add(p.product_ean);
                          setSelectedIds(next);
                        }}
                      />
                    </td>
                    <td className="py-2 px-3 w-16 text-center" onClick={e => e.stopPropagation()}>
                      <div
                        className="w-11 h-11 rounded-lg border border-slate-200 dark:border-slate-700/80 bg-white dark:bg-slate-900 flex items-center justify-center p-0.5 overflow-hidden shadow-2xs group-hover:border-indigo-300 dark:group-hover:border-indigo-700 transition-colors mx-auto relative cursor-pointer"
                        onMouseEnter={(e) => handleImageHover(p.image_url, p.product_name || p.product_ean, p.price ? `€${p.price}` : "", e)}
                        onMouseLeave={handleImageLeave}
                        onClick={() => openProduct(p)}
                      >
                        {p.image_url ? (
                          <img
                            src={p.image_url}
                            alt=""
                            className="w-full h-full object-contain pointer-events-none"
                            loading="lazy"
                          />
                        ) : (
                          <PackageOpen className="w-4 h-4 text-slate-300 dark:text-slate-600" />
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4 max-w-[280px]">
                      <div className="flex flex-col gap-1">
                        <span
                          className="font-medium text-xs text-slate-800 dark:text-slate-200 line-clamp-2 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors leading-snug"
                          title={p.product_name || p.product_ean}
                        >
                          {p.product_name || p.product_ean}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className="inline-flex font-mono text-[10px] text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/50 shrink-0">
                            {p.product_ean}
                          </span>
                          {p.price && (
                            <span className="text-[10px] text-slate-400 font-mono">
                              €{p.price}
                            </span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="py-3 px-4 w-52">
                      <div
                        className="text-xs font-medium text-slate-800 dark:text-slate-200 truncate max-w-[200px]"
                        title={categoryNames[p.category_id] || p.category_name || p.category_id || t("validation.no_category")}
                      >
                        {categoryNames[p.category_id] || p.category_name || p.category_id || (
                          <span className="text-slate-400 italic">{t("validation.no_category")}</span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex flex-wrap gap-1.5">
                        {(p.errors || []).map((err: string, idx: number) => (
                          <span
                            key={idx}
                            className="inline-flex items-center text-[11px] font-medium text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 border border-rose-200/70 dark:border-rose-900/60 px-2 py-0.5 rounded-md"
                          >
                            <AlertCircle className="w-3 h-3 mr-1 text-rose-500 shrink-0" />
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
          <div className="fixed inset-0 z-40 bg-slate-900/20 dark:bg-black/40 backdrop-blur-xs animate-in fade-in duration-200" onClick={() => setActiveProduct(null)} />
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
        <div className="fixed bottom-8 left-1/2 -translate-x-1/2 bg-white dark:bg-card text-slate-900 dark:text-white px-4 py-3 rounded-xl shadow-2xl border border-slate-200 dark:border-slate-800 flex items-center gap-3 z-50 animate-in slide-in-from-bottom-4">
          <div className="flex items-center gap-2 shrink-0">
            <div className="h-6 px-2 rounded-md bg-rose-50 dark:bg-rose-950/60 border border-rose-200/80 dark:border-rose-900/60 flex items-center justify-center text-xs font-bold text-rose-600 dark:text-rose-400">
              {selectedIds.size}
            </div>
            <div className="text-xs leading-tight">
              <div className="text-slate-900 dark:text-white font-medium">
                {t("workspace.selected_count", { count: selectedIds.size })}
              </div>
              <div className="text-slate-400 text-[10px]">
                {t("workspace.of_products", { total: filtered.length })}
              </div>
            </div>
          </div>

          <div className="w-px h-6 bg-slate-200 dark:bg-slate-700" />

          <Button
            size="sm"
            onClick={handleForceApprove}
            disabled={loading}
            className="h-8 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white gap-1.5 shadow-xs"
          >
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>{t("validation.force_approve")}</span>
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleDeleteMappings}
            disabled={loading}
            className="h-8 text-xs font-medium border-rose-200 text-rose-600 hover:bg-rose-50 dark:border-rose-900/60 dark:text-rose-400 gap-1.5"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>{t("validation.reset_mapping")}</span>
          </Button>

          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors rounded-md hover:bg-slate-100 dark:hover:bg-slate-800"
            title={t("export_center.deselect_all")}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      <ImageHoverPreview preview={imgPreview} />
    </div>
  );
}

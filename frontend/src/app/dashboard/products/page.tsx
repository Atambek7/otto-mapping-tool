"use client";

import { useState, useRef } from "react";
import { fetchWithAuth } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Search,
  PackageOpen,
  FolderTree,
  Barcode,
  Sparkles,
  ChevronRight,
  X,
  Layers,
  ArrowRight,
} from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ProductDrawer } from "@/components/ProductDrawer";
import { useAccount } from "@/lib/useAccount";
import { useTranslation } from "react-i18next";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";

export default function ProductsPage() {
  const { account } = useAccount();
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [products, setProducts] = useState<any[]>([]);
  const [activeProduct, setActiveProduct] = useState<any | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);

  const parentRef = useRef<HTMLDivElement>(null);

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

  const getProductPrice = (row: any) => {
    if (!row) return "";
    const parseVal = (val: any) => {
      if (!val) return 0;
      const clean = String(val).replace(",", ".");
      const num = parseFloat(clean);
      return isNaN(num) ? 0 : num;
    };
    const sofort = parseVal(row.SofortkaufenPreis);
    const start = parseVal(row.Startpreis);
    const finalPrice = sofort > 0 ? sofort : start;
    return finalPrice > 0 ? `€${finalPrice.toFixed(2)}` : "";
  };

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!search.trim()) return;

    setLoading(true);
    setHasSearched(true);
    try {
      const res = await fetchWithAuth(
        `/aftercool/products?account=${account}&dataset=lister&q=${encodeURIComponent(
          search
        )}&limit=100&include_row=1&dedup=1`
      );
      if (res.ok) {
        const data = await res.json();
        const rawItems = data?.items || (Array.isArray(data) ? data : []);
        const seenEans = new Set<string>();
        const uniqueItems = rawItems.filter((item: any) => {
          const ean = String(item.ean || item.row?.EAN || "").trim();
          if (!ean || ean === "—" || ean === "0") return true;
          if (seenEans.has(ean)) return false;
          seenEans.add(ean);
          return true;
        });
        setProducts(uniqueItems);
      } else {
        setProducts([]);
      }
    } catch (e) {
      console.error(e);
      setProducts([]);
    } finally {
      setLoading(false);
    }
  };

  const rowVirtualizer = useVirtualizer({
    count: products.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 72,
    overscan: 10,
  });

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] space-y-4 max-w-[1600px] mx-auto pb-4">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 shrink-0">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">
            <span>{t("products_search.tag")}</span>
            <span>•</span>
            <span className="text-indigo-600 dark:text-indigo-400">
              {t("products_search.account", { account })}
            </span>
          </div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">
            {t("products_search.title")}
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {t("products_search.subtitle")}
          </p>
        </div>

        {hasSearched && (
          <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400 bg-white dark:bg-[#0B0F19] px-3 py-1.5 rounded-lg border border-slate-200 dark:border-slate-800 shadow-sm self-start sm:self-auto">
            <Layers className="w-3.5 h-3.5 text-indigo-500" />
            <span>
              {t("products_search.matches_found", { count: products.length })}
            </span>
          </div>
        )}
      </div>

      {/* Search Toolbar */}
      <div className="bg-white dark:bg-[#0B0F19] p-3 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm shrink-0">
        <form onSubmit={handleSearch} className="flex gap-2.5">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("products_search.placeholder")}
              className="pl-10 pr-9 h-10 bg-slate-50 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700 text-sm focus-visible:ring-indigo-500"
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
          <Button
            type="submit"
            disabled={loading || !search.trim()}
            className="h-10 px-6 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-sm transition-colors shrink-0"
          >
            {loading ? (
              <span className="flex items-center gap-2">
                <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                {t("products_search.searching_btn")}
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <Search className="w-3.5 h-3.5" />
                {t("products_search.search_btn")}
              </span>
            )}
          </Button>
        </form>
      </div>

      {/* Initial state before searching */}
      {!hasSearched && (
        <div className="flex-1 flex flex-col items-center justify-center p-12 bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl text-center shadow-sm">
          <div className="w-12 h-12 rounded-xl bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-900/50 flex items-center justify-center text-indigo-600 dark:text-indigo-400 mb-3 shadow-xs">
            <Search className="w-6 h-6" />
          </div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            {t("products_search.initial_title")}
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-md mt-1 mb-4 leading-relaxed">
            {t("products_search.initial_desc", { account })}
          </p>
        </div>
      )}

      {/* Empty State: No results found */}
      {hasSearched && products.length === 0 && !loading && (
        <div className="flex-1 flex flex-col items-center justify-center p-12 bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl text-center shadow-sm">
          <div className="w-12 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-400 mb-3">
            <PackageOpen className="w-6 h-6" />
          </div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-white">
            {t("products_search.empty_title")}
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-md mt-1 leading-relaxed">
            {t("products_search.empty_desc", { account, search })}
          </p>
        </div>
      )}

      {/* Results Table */}
      {products.length > 0 && (
        <div
          ref={parentRef}
          className="flex-1 overflow-auto bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm relative"
        >
          {/* Table Header */}
          <div className="sticky top-0 bg-slate-50/95 dark:bg-slate-850/95 backdrop-blur-xs border-b border-slate-200 dark:border-slate-800 z-10 flex items-center text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400 py-3 px-4">
            <div className="w-14 text-center">{t("products_search.th_image")}</div>
            <div className="flex-1 px-3">{t("products_search.th_name")}</div>
            <div className="w-48 px-3">{t("products_search.th_ean")}</div>
            <div className="w-56 px-3">{t("products_search.th_factory")}</div>
            <div className="w-12 text-center">{t("products_search.th_open")}</div>
          </div>

          {/* Virtualized Rows */}
          <div
            style={{
              height: `${rowVirtualizer.getTotalSize()}px`,
              position: "relative",
            }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const p = products[virtualRow.index];
              const row = p.row || {};
              const img =
                row.GalleryURL ||
                row.PictureURL ||
                (Array.isArray(row.PictureURLs)
                  ? row.PictureURLs[0]
                  : typeof row.PictureURLs === "string"
                  ? row.PictureURLs.split("|")[0]
                  : "") ||
                p.img ||
                "";
              const productName =
                row.Artikelbeschreibung || p.name || "Untitled Product";
              const ean = p.ean || row.EAN || "—";
              const factoryId = p.factory_id || "—";

              return (
                <div
                  key={virtualRow.key}
                  className="absolute top-0 left-0 w-full flex items-center border-b border-slate-100 dark:border-slate-800/70 hover:bg-slate-50/80 dark:hover:bg-slate-800/40 cursor-pointer px-4 text-xs transition-colors group"
                  style={{
                    height: `${virtualRow.size}px`,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  onClick={() => setActiveProduct(p)}
                >
                  {/* Thumbnail */}
                  <div className="w-14 flex items-center justify-center shrink-0" onClick={e => e.stopPropagation()}>
                    <div
                      className="w-11 h-11 rounded-lg border border-slate-200 dark:border-slate-700/80 bg-white dark:bg-slate-900 flex items-center justify-center p-0.5 overflow-hidden shadow-2xs group-hover:border-indigo-300 dark:group-hover:border-indigo-700 transition-colors relative cursor-pointer"
                      onMouseEnter={(e) => handleImageHover(img, productName, getProductPrice(row), e)}
                      onMouseLeave={handleImageLeave}
                      onClick={() => setActiveProduct(p)}
                    >
                      {img ? (
                        <img
                          src={img}
                          alt=""
                          className="w-full h-full object-contain pointer-events-none"
                          loading="lazy"
                        />
                      ) : (
                        <PackageOpen className="w-4 h-4 text-slate-300 dark:text-slate-600" />
                      )}
                    </div>
                  </div>

                  {/* Title */}
                  <div className="flex-1 min-w-0 px-3">
                    <div className="font-semibold text-slate-900 dark:text-slate-100 truncate group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors">
                      {productName}
                    </div>
                    {getProductPrice(row) && (
                      <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                        {getProductPrice(row)}
                      </div>
                    )}
                  </div>

                  {/* EAN */}
                  <div
                    className="w-48 px-3 shrink-0"
                    data-no-drag
                    onClick={(e) => e.stopPropagation()}
                    onMouseDown={(e) => e.stopPropagation()}
                  >
                    <span
                      className="inline-flex items-center gap-1.5 font-mono text-[11px] text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/50 select-text cursor-text"
                      onClick={(e) => e.stopPropagation()}
                      onMouseDown={(e) => e.stopPropagation()}
                    >
                      <Barcode className="w-3 h-3 text-slate-400 select-none pointer-events-none" />
                      {ean}
                    </span>
                  </div>

                  {/* Factory */}
                  <div className="w-56 px-3 shrink-0">
                    <span className="inline-flex items-center gap-1.5 text-[11px] text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/40 px-2 py-0.5 rounded font-medium truncate max-w-[200px] border border-indigo-200/50 dark:border-indigo-800/40">
                      <FolderTree className="w-3 h-3 text-indigo-500 shrink-0" />
                      <span className="truncate">{factoryId}</span>
                    </span>
                  </div>

                  {/* Inspect action */}
                  <div className="w-12 flex items-center justify-center shrink-0 text-slate-400 group-hover:text-indigo-600 dark:group-hover:text-indigo-400 transition-colors">
                    <ChevronRight className="w-4 h-4" />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Product Drawer */}
      {activeProduct && (
        <ProductDrawer
          product={activeProduct}
          factoryId={activeProduct.factory_id || activeProduct.row?.factory_id || ""}
          account={account}
          onClose={() => setActiveProduct(null)}
          onSave={() => {}}
        />
      )}

      <ImageHoverPreview preview={imgPreview} />
    </div>
  );
}

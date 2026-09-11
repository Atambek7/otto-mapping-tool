"use client";

import React, { useEffect, useState, useRef, useMemo, useCallback, memo } from "react";
import { useParams } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";
import { getCachedFactory, setCachedFactory } from "@/lib/cache";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { useVirtualizer } from "@tanstack/react-virtual";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { ProductDrawer } from "@/components/ProductDrawer";
import { MassEditDrawer } from "@/components/MassEditDrawer";
import { toast } from "sonner";

import {
  Download, RefreshCw, Search, ArrowUpDown, ArrowUp, ArrowDown, Database, Wifi, X, Sparkles, Loader2, ChevronLeft, Upload, FileDown, FileArchive, Bot, Play, Pause, Clock, AlertCircle, Calendar, ChevronDown, ChevronRight, FileSpreadsheet, Check, Layers
} from "lucide-react";
import { useAccount } from "@/lib/useAccount";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { AttributeCombobox } from "@/components/AttributeCombobox";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";
import DripScheduleModal from "@/components/DripScheduleModal";
import { useTranslation } from "react-i18next";

// --- Helpers ---
function parseSpecifics(raw: any) {
  if (!raw) return [];
  try {
    const p = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (Array.isArray(p)) return p.filter(i => i?.Name && i?.Value).slice(0, 4).map(i => [String(i.Name), String(i.Value)]);
    if (typeof p === "object" && p !== null) return Object.entries(p).slice(0, 4).map(([k, v]) => [k, String(v)]);
  } catch { }
  return [];
}

const getRowKey = (p: any, index: number): string => {
  if (!p) return `row-${index}`;
  const row = p.row || {};
  return String(row.ItemID || row.id || p.id || `${p.ean || row.EAN || 'item'}_${index}`);
};

const VirtualRow = React.memo(({
  index, top, height, product, ean, rowKey, img, price, mapData, isSelected,
  isDraggingCurrent, handleRowMouseDown, toggleSelection, setActiveProduct,
  onImageHover, onImageLeave, isMirrored
}: {
  index: number; top: number; height: number; product: any; ean: string; rowKey: string; img: string; price: string; mapData: any;
  isSelected: boolean; isDraggingCurrent: boolean; isMirrored?: boolean;
  handleRowMouseDown: (index: number, rowKey: string, e: React.MouseEvent) => void;
  toggleSelection: (rowKey: string) => void;
  setActiveProduct: (p: any) => void;
  onImageHover: (img: string, name: string, price: string, e: React.MouseEvent<HTMLElement>) => void;
  onImageLeave: () => void;
}) => {
  const { t } = useTranslation();
  const row = product.row || {};
  return (
    <div
      data-row-index={index}
      className={cn(
        "absolute top-0 left-0 w-full flex items-center border-b border-slate-100 dark:border-slate-800/60 hover:bg-slate-50/70 dark:hover:bg-slate-800/30 cursor-pointer transition-colors text-sm select-none",
        isSelected && "bg-indigo-50/50 hover:bg-indigo-50/70 dark:bg-indigo-950/20 dark:hover:bg-indigo-950/30"
      )}
      style={{
        height: `${height}px`,
        transform: `translateY(${top}px)`,
      }}
      onMouseDown={(e) => handleRowMouseDown(index, rowKey, e)}
      onClick={(e) => { if (!isDraggingCurrent) setActiveProduct(product); }}
    >
      <div className="w-12 px-4 shrink-0" data-no-drag onClick={(e) => e.stopPropagation()}>
        <Checkbox checked={isSelected} onCheckedChange={() => toggleSelection(rowKey)} />
      </div>
      <div className="w-24 px-3 shrink-0" data-no-drag>
        <div
          className="w-16 h-16 rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 flex items-center justify-center p-1 overflow-hidden shadow-xs hover:ring-2 hover:ring-indigo-500/30 transition-all cursor-zoom-in relative"
          onMouseEnter={(e) => {
            if (img) onImageHover(img, row.Artikelbeschreibung || product.name || "", price !== "-" ? `${price}` : "", e);
          }}
          onMouseLeave={onImageLeave}
        >
          {img ? (
            <img
              src={img}
              className={cn("w-full h-full object-contain transition-transform", isMirrored && "-scale-x-100")}
              alt=""
              loading="lazy"
            />
          ) : (
            <span className="text-slate-300 dark:text-slate-600 text-xs">—</span>
          )}
          {isMirrored && img && (
            <div className="absolute bottom-0.5 right-0.5 px-1 py-0.2 bg-purple-600/90 text-white text-[8px] font-bold rounded">
              XL
            </div>
          )}
        </div>
      </div>
      <div className="flex-1 px-4 min-w-0">
        <p className="font-medium text-slate-900 dark:text-white text-sm line-clamp-2 leading-snug">
          {row.Artikelbeschreibung || product.name || t("workspace.untitled_product")}
        </p>
      </div>
      <div className="w-48 px-4 shrink-0">
        <span className="font-mono text-xs text-slate-600 dark:text-slate-400 bg-slate-100 dark:bg-slate-800/60 px-2 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/50 inline-block">
          {ean || t("workspace.no_ean")}
        </span>
      </div>
      <div className="w-24 px-4 shrink-0 font-semibold text-slate-900 dark:text-white tabular-nums text-sm">
        {price !== "-" ? `${price} €` : "—"}
      </div>
      <div className="w-64 px-4 shrink-0 truncate text-xs text-slate-700 dark:text-slate-300">
        {mapData?.category ? (
          <span className={mapData.status === "Error" ? "text-slate-500 dark:text-slate-400" : "font-medium"}>
            {mapData.category_name || mapData.category}
          </span>
        ) : (
          <span className="text-slate-400 italic">{t("workspace.not_mapped")}</span>
        )}
      </div>
      <div className="w-40 px-4 shrink-0">
        {mapData?.status === "Ready" ? (
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800/60">
            {t("workspace.status_ready")}
          </span>
        ) : mapData?.status === "Need Attributes" ? (
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800/60">
            {t("workspace.status_need")}
          </span>
        ) : mapData?.status === "Error" ? (
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200/80 dark:bg-rose-950/40 dark:text-rose-400 dark:border-rose-800/60">
            {t("workspace.status_error")}
          </span>
        ) : (
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200/80 dark:bg-slate-800/60 dark:text-slate-400 dark:border-slate-700/50">
            {t("workspace.status_unmapped")}
          </span>
        )}
      </div>
    </div>
  );
});

export default function FactoryWorkspace() {
  const params = useParams();
  const factoryId = params?.factory_id as string;
  const { account } = useAccount();
  const { t } = useTranslation();

  const [products, setProducts] = useState<any[]>([]);
  const [factoryName, setFactoryName] = useState("");
  const [loading, setLoading] = useState(true);
  const [isBackgroundLoading, setIsBackgroundLoading] = useState(false);
  const [totalCatalogCount, setTotalCatalogCount] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sort, setSort] = useState({ col: "", dir: "asc" });
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [popupPos, setPopupPos] = useState<{ x: number, y: number } | null>(null);
  const [isMassEditOpen, setIsMassEditOpen] = useState(false);

  const [isExportMenuOpen, setIsExportMenuOpen] = useState(false);
  const exportMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(e.target as Node)) {
        setIsExportMenuOpen(false);
      }
    };
    if (isExportMenuOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [isExportMenuOpen]);

  const [activeProduct, setActiveProduct] = useState<any | null>(null);
  const [previewImage, setPreviewImage] = useState<HoverImagePreviewState | null>(null);

  const handleImageHover = useCallback((imgUrl: string, name: string, price: string, e: React.MouseEvent<HTMLElement>) => {
    if (!imgUrl || isDragging.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    setPreviewImage({
      url: imgUrl,
      name,
      price,
      isMirrored: account === "XL",
      rect: {
        top: r.top,
        left: r.left,
        right: r.right,
        bottom: r.bottom,
        height: r.height,
      },
    });
  }, [account]);

  const handleImageLeave = useCallback(() => {
    setPreviewImage(null);
  }, []);

  const [isAutoMapping, setIsAutoMapping] = useState(false);
  const [isSyncingOkb, setIsSyncingOkb] = useState(false);
  const isSyncCancelled = useRef(false);
  const isAutoMappingCancelled = useRef(false);
  const [isPublishing, setIsPublishing] = useState(false);

  const [taskProgress, setTaskProgress] = useState<{
    title: string;
    current?: number;
    total?: number;
    percent?: number;
    eta?: string;
    message?: string;
    isIndeterminate?: boolean;
    colorClass?: string;
    onCancel?: () => void;
  } | null>(null);

  // Drip Feed Pipeline state
  const [isDripModalOpen, setIsDripModalOpen] = useState(false);
  const [activePlan, setActivePlan] = useState<any | null>(null);

  const fetchActivePlan = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`/pipeline/plans?account_name=${account}&factory_id=${factoryId}`);
      if (res.ok) {
        const data = await res.json();
        const current = data.find((p: any) => p.status === "active" || p.status === "paused") || null;
        setActivePlan(current);
      }
    } catch (err) {
      console.error("Failed to fetch active plan", err);
    }
  }, [account, factoryId]);

  useEffect(() => {
    fetchActivePlan();
    const interval = setInterval(fetchActivePlan, 15000);
    return () => clearInterval(interval);
  }, [fetchActivePlan]);

  const handlePausePlan = async (planId: number) => {
    try {
      const res = await fetchWithAuth(`/pipeline/plan/${planId}/pause`, { method: "POST" });
      if (res.ok) {
        toast.success(t("workspace.toast_paused"));
        fetchActivePlan();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleResumePlan = async (planId: number) => {
    try {
      const res = await fetchWithAuth(`/pipeline/plan/${planId}/resume`, { method: "POST" });
      if (res.ok) {
        toast.success(t("workspace.toast_resumed"));
        fetchActivePlan();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleCancelPlan = async (planId: number) => {
    if (!confirm(t("workspace.confirm_cancel_plan"))) return;
    try {
      const res = await fetchWithAuth(`/pipeline/plan/${planId}/cancel`, { method: "POST" });
      if (res.ok) {
        toast.success(t("workspace.toast_plan_cancelled"));
        fetchActivePlan();
      }
    } catch (err) {
      console.error(err);
    }
  };

  // Drag & Drop overlay state
  const [isDragOver, setIsDragOver] = useState(false);
  const [dragFileType, setDragFileType] = useState<"csv" | "zip" | "db" | "unknown" | null>(null);
  const dragCounterRef = useRef(0);

  // Mappings state: EAN -> { category, status, category_name, attributes }
  const [mappings, setMappings] = useState<Record<string, { category: string, status: string, category_name?: string, attributes?: Record<string, string> }>>({});

  // Drag-to-select state (all via refs to avoid stale closures)
  const isDragging = useRef(false);
  const dragStartIndex = useRef<number | null>(null);
  const dragStartSelected = useRef(false);
  const dragStartSelection = useRef<Set<string>>(new Set());
  const lastDragIndex = useRef<number | null>(null);
  const dragStartX = useRef<number>(0);
  const dragStartY = useRef<number>(0);
  const pendingDragEan = useRef<string | null>(null);
  const pendingDragToggle = useRef<boolean>(false);
  const lastMoveTime = useRef(0);
  // Keep processed in ref for use inside event handlers without stale closure
  const processedRef = useRef<any[]>([]);
  const selectedIdsRef = useRef<Set<string>>(new Set());

  const parentRef = useRef<HTMLDivElement>(null);

  const fetchMappings = useCallback(async () => {
    if (!factoryId) return;
    try {
      const mapRes = await fetchWithAuth(`/mapping/factory/${factoryId}?account=${account}`);
      if (mapRes.ok) {
        const mapData = await mapRes.json();
        const normalized: Record<string, { category: string, status: string, category_name?: string }> = {};
        for (const [ean, val] of Object.entries(mapData as any)) {
          const v = val as any;
          normalized[ean] = { category: v.category_id || v.category_name || "", status: v.status || "", category_name: v.category_name || "" };
        }
        setMappings(normalized);
      }
    } catch (e) {
      console.error("Failed to fetch mappings", e);
    }
  }, [factoryId, account]);

  const handleOkbSync = async () => {
    if (!factoryId || selectedIds.size === 0 || !account) return;

    const selectedProducts = processed.filter((p, i) => selectedIds.has(getRowKey(p, i)));
    const eansToSync = Array.from(new Set(selectedProducts.map(p => p.ean || p.row?.EAN).filter(Boolean)));
    if (eansToSync.length === 0) return;

    let hasFilledAttrs = false;
    for (const p of selectedProducts) {
      const ean = p.ean || p.row?.EAN;
      if (ean && mappings[ean]) {
        const s = mappings[ean].status;
        if (s === "Ready" || s === "Partial" || s === "Error") {
          hasFilledAttrs = true;
          break;
        }
      }
    }

    if (hasFilledAttrs) {
      if (!confirm(t("workspace.confirm_okb_overwrite"))) {
        return;
      }
    }

    setIsSyncingOkb(true);
    isSyncCancelled.current = false;
    setTaskProgress({
      title: t("workspace.task_loading_okb"),
      current: 0,
      total: eansToSync.length,
      colorClass: "from-emerald-500 to-teal-500",
      onCancel: () => { isSyncCancelled.current = true; }
    });

    let ok = 0;
    let notFound = 0;
    let error = 0;

    const chunkSize = 10;
    for (let i = 0; i < eansToSync.length; i += chunkSize) {
      if (isSyncCancelled.current) break;
      const chunk = eansToSync.slice(i, i + chunkSize);
      const chunkProducts = selectedProducts.filter(p => chunk.includes(p.ean || p.row?.EAN));
      const productsData = chunkProducts.map(p => ({
        ean: p.ean || p.row?.EAN,
        row: p.row || {}
      }));

      try {
        const res = await fetchWithAuth("/mapping/sync-okb-bulk", {
          method: "POST",
          body: JSON.stringify({
            product_eans: chunk,
            factory_id: factoryId,
            account: account,
            products_data: productsData
          })
        });
        if (res.ok) {
          const data = await res.json();
          for (const r of (data.results || [])) {
            if (r.status === "success") ok++;
            else if (r.status === "not_found" || r.status === "unresolved") notFound++;
            else error++;
          }
        }
      } catch (e: any) {
        console.error(e);
      }
      setTaskProgress(prev => prev ? { ...prev, current: Math.min(i + chunkSize, eansToSync.length) } : null);
    }

    setIsSyncingOkb(false);
    setTaskProgress(null);

    if (!isSyncCancelled.current) {
      setTimeout(async () => {
        alert(t("workspace.alert_okb_done", { ok, notFound, error }));
        await fetchMappings();
      }, 100);
    } else {
      fetchMappings();
    }
  };

  useEffect(() => {
    if (!factoryId || !account) return;
    const load = async () => {
      // 1. Instant cache render (0-15ms)
      const cached = await getCachedFactory(factoryId, account);
      if (cached?.products.length) {
        setProducts(cached.products);
        setTotalCatalogCount(cached.total || cached.products.length);
        setLoading(false);
        setIsBackgroundLoading(false);
      } else {
        setLoading(true);
      }

      // 2. Fetch from network if not cached or if it looks truncated (exactly 500)
      let all = cached?.products || [];
      if (!all.length || all.length === 500) {
        try {
          setIsBackgroundLoading(true);
          all = [];
          let offset = 0;
          let limit = 50; // Super fast initial chunk (50 items) for immediate perceived load!

          while (true) {
            const res = await fetchWithAuth(
              `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=${limit}&offset=${offset}&include_row=1`
            );
            if (!res.ok) break;
            const data = await res.json();
            const chunk = data?.items || data || [];
            if (!Array.isArray(chunk) || chunk.length === 0) break;
            all = all.concat(chunk);

            if (data?.total) {
              setTotalCatalogCount(data.total);
            }

            // Immediately display products on screen as soon as first 50 items arrive!
            setProducts([...all]);
            setLoading(false);

            if (chunk.length < limit) break;
            offset += limit;
            // Subsequent chunks: 500 items for high network throughput
            limit = 500;
          }

          if (all.length > 0) {
            await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() }, false, account);
            setProducts(all);
            setTotalCatalogCount(all.length);
          }
        } catch (e) {
          console.error(e);
        } finally {
          setLoading(false);
          setIsBackgroundLoading(false);
        }
      }

      // 3. Resolve factory name and fetch mappings in parallel
      const namePromise = (async () => {
        try {
          const saved = localStorage.getItem("otto_factories_list");
          let list = [];
          if (saved) {
            try {
              const parsed = JSON.parse(saved);
              list = Array.isArray(parsed) ? parsed : (parsed?.items ?? []);
            } catch (e) { }
          }
          let found = list.find((f: any) => String(f.id) === String(factoryId));
          if (found?.name) {
            setFactoryName(found.name);
          } else {
            const nameRes = await fetchWithAuth(`/aftercool/factories?account=${account}`);
            if (nameRes.ok) {
              const rawList = await nameRes.json();
              const freshList = Array.isArray(rawList) ? rawList : (rawList?.items ?? []);
              localStorage.setItem("otto_factories_list", JSON.stringify(freshList));
              const fFound = freshList.find((f: any) => String(f.id) === String(factoryId));
              if (fFound?.name) setFactoryName(fFound.name);
            }
          }
        } catch (e) { }
      })();

      await Promise.all([fetchMappings(), namePromise]);
    };
    load();
  }, [factoryId, account, fetchMappings]);

  const handleSync = async () => {
    setIsBackgroundLoading(true);
    try {
      let all: any[] = [];
      let offset = 0;
      let limit = 50;

      while (true) {
        const res = await fetchWithAuth(
          `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=${limit}&offset=${offset}&include_row=1&refresh=true`
        );
        if (!res.ok) break;
        const data = await res.json();
        const chunk = data?.items || data || [];
        if (!Array.isArray(chunk) || chunk.length === 0) break;
        all = all.concat(chunk);

        if (data?.total) {
          setTotalCatalogCount(data.total);
        }

        setProducts([...all]);
        setLoading(false);

        if (chunk.length < limit) break;
        offset += limit;
        limit = 500;
      }

      if (all.length > 0) {
        await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() }, false, account);
        setProducts(all);
        setTotalCatalogCount(all.length);
        await fetchMappings();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setIsBackgroundLoading(false);
      setLoading(false);
    }
  };

  const handleGlobalExport = async () => {
    if (!factoryId) return;
    try {
      const res = await fetchWithAuth(`/export/otto_json/${factoryId}?account=${account}`);
      if (!res.ok) { alert(t("workspace.alert_file_gen_error")); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const cleanName = (factoryName || factoryId).replace(/[\\/:*?"<>|]/g, "_");
      a.download = `${cleanName}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error(e);
      alert(t("workspace.alert_file_download_error"));
    }
  };

  const handlePublishToOtto = async () => {
    const readyProducts = processed.filter(p => mappings[p.ean || p.row?.EAN]?.status === "Ready");
    if (readyProducts.length === 0) {
      alert(t("workspace.alert_no_ready_to_pub"));
      return;
    }
    if (!confirm(t("workspace.confirm_pub_otto", { count: readyProducts.length }))) {
      return;
    }

    setIsPublishing(true);
    setTaskProgress({
      title: t("workspace.task_pub_otto"),
      current: 0,
      total: readyProducts.length,
      colorClass: "from-emerald-500 to-teal-500",
      isIndeterminate: true
    });

    try {
      const res = await fetchWithAuth(`/otto/publish/${factoryId}`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        const taskId = data.task_id;

        // Poll for status
        const pollTimer = setInterval(async () => {
          try {
            const statusRes = await fetchWithAuth(`/otto/publish/status/${taskId}`);
            if (statusRes.ok) {
              const statusData = await statusRes.json();
              if (statusData.status === "PROGRESS" && statusData.progress) {
                setTaskProgress({
                  title: statusData.progress.message || "Publishing...",
                  current: statusData.progress.current,
                  total: statusData.progress.total,
                  colorClass: "from-emerald-500 to-teal-500"
                });
              } else if (statusData.status === "SUCCESS") {
                clearInterval(pollTimer);
                setIsPublishing(false);
                setTaskProgress(null);
                const msg = statusData.result?.message || `Successfully published ${statusData.result?.published} products.`;
                if (statusData.result?.errors && statusData.result.errors.length > 0) {
                  alert(msg + "\nSome products had errors publishing. Check console for details.");
                  console.error("Publish errors:", statusData.result.errors);
                } else {
                  alert(msg);
                }
                await fetchMappings();
              } else if (statusData.status === "FAILURE") {
                clearInterval(pollTimer);
                setIsPublishing(false);
                setTaskProgress(null);
                setTimeout(() => alert("Task failed: " + statusData.error), 100);
              }
            }
          } catch (e) {
            console.error("Error polling status", e);
          }
        }, 1000);
      } else {
        const err = await res.json().catch(() => ({}));
        setIsPublishing(false);
        setTaskProgress(null);
        setTimeout(() => alert("Failed to queue publish: " + (err.detail || res.statusText)), 100);
      }
    } catch (e) {
      console.error(e);
      setIsPublishing(false);
      setTaskProgress(null);
      setTimeout(() => alert("Error publishing to Otto."), 100);
    }
  };

  const handleAutoMap = async (useVision: boolean = false, target: "all" | "categories" | "attributes" = "all") => {
    if (selectedIds.size === 0) return;
    setIsAutoMapping(true);

    const selectedProducts = processed.filter((p, i) => selectedIds.has(getRowKey(p, i)));
    const payloadProducts = selectedProducts.map(p => {
      const row = p.row || {};
      const ean = p.ean || row.EAN || "";
      if (!ean) return null;

      let specificsStr = "";
      const rawSpec = row.CustomItemSpecifics || row.ItemSpecifics || "";
      if (rawSpec) {
        try {
          const parsed = typeof rawSpec === "string" ? JSON.parse(rawSpec) : rawSpec;
          if (Array.isArray(parsed)) {
            specificsStr = parsed.map(i => `${i.Name}: ${i.Value}`).join(", ");
          } else if (typeof parsed === "object") {
            specificsStr = Object.entries(parsed).map(([k, v]) => `${k}: ${v}`).join(", ");
          }
        } catch (e) {
          specificsStr = String(rawSpec);
        }
      }

      return {
        ean,
        title: row.Artikelbeschreibung || p.name || "",
        description: row.Beschreibung || "",
        specifics: specificsStr,
        gallery_url: row.GalleryURL || "",
        picture_urls: row.PictureURLs || row.PictureURL || ""
      };
    }).filter(Boolean);

    if (payloadProducts.length === 0) {
      setIsAutoMapping(false);
      return;
    }

    setTaskProgress({
      title: t("workspace.task_ai_processing"),
      current: 0,
      total: payloadProducts.length,
      colorClass: "from-indigo-500 to-purple-500",
      onCancel: () => { isAutoMappingCancelled.current = true; }
    });
    isAutoMappingCancelled.current = false;
    const BATCH_SIZE = 5;
    let processedCount = 0;

    try {
      for (let i = 0; i < payloadProducts.length; i += BATCH_SIZE) {
        if (isAutoMappingCancelled.current) {
          break;
        }
        const batch = payloadProducts.slice(i, i + BATCH_SIZE);

        const res = await fetchWithAuth(`/mapping/auto-map`, {
          method: "POST",
          body: JSON.stringify({
            factory_id: factoryId,
            account: account,
            products: batch,
            use_vision: useVision,
            target: target
          })
        });

        if (res.ok) {
          const data = await res.json();
          // Update mappings state incrementally
          setMappings(prev => {
            const next = { ...prev };
            (data.results || []).forEach((r: any) => {
              if ((r.status === "success" || r.status === "partial") && r.ean) {
                const statusStr = r.status === "partial" ? "Need Attributes" :
                  (r.errors && r.errors.length > 0) ? "Error" : "Ready";
                next[r.ean] = {
                  category: r.category_id || r.category_name,
                  category_name: r.category_name,
                  status: statusStr
                };
              }
            });
            return next;
          });
        } else {
          const errorCode = res.headers.get("x-error-code");
          if (errorCode === "OPENAI_API_KEY_MISSING") {
            toast.error(t("product_drawer.toast_openai_missing_title"), {
              id: "openai-error-toast",
              description: t("product_drawer.toast_openai_missing_desc"),
              duration: 7000,
            });
          } else if (errorCode === "OPENAI_API_ERROR") {
            toast.error(t("product_drawer.toast_openai_error_title"), {
              id: "openai-error-toast",
              description: t("product_drawer.toast_openai_error_desc"),
              duration: 7000,
            });
          } else {
            toast.error(t("workspace.toast_ai_mapping_error"), { id: "openai-error-toast" });
          }
          isAutoMappingCancelled.current = true; // stop the loop
          break;
        }

        processedCount += batch.length;
        setTaskProgress(prev => prev ? { ...prev, current: processedCount } : null);
      }

      setSelectedIds(new Set());
      setPopupPos(null);
    } catch (e) {
      console.error(e);
    } finally {
      setIsAutoMapping(false);
      setTimeout(() => setTaskProgress(null), 1000); // Keep full bar visible for 1 sec
      await fetchMappings(); // Refresh mappings from server to ensure full UI update
    }
  };

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleExportCategories = async () => {
    setTaskProgress({ title: t("workspace.task_sync_metadata"), isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
    try {
      // First sync product names and prices from Aftercool so they appear in the CSV
      await fetchWithAuth(`/aftercool/sync-metadata/${factoryId}?account=${account}&dataset=lister`, { method: "POST" });
    } catch (e) {
      console.warn("Metadata sync failed, exporting with available data", e);
    }

    setTaskProgress({ title: t("workspace.task_export_cat"), isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
    try {
      const selectedProducts = processed.filter((p, i) => selectedIds.has(getRowKey(p, i)));
      const eans = Array.from(new Set(selectedProducts.map(p => p.ean || p.row?.EAN).filter(Boolean)));
      const options: any = { method: "GET" };
      if (eans.length > 0) {
        options.method = "POST";
        options.body = JSON.stringify({ product_eans: eans });
      }

      const res = await fetchWithAuth(`/csv/export/categories/${factoryId}`, options);
      if (!res.ok) { setTimeout(() => alert(t("workspace.export_failed", { error: res.statusText })), 100); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Categories_${factoryName || factoryId}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { console.error(e); setTimeout(() => alert(t("workspace.export_failed", { error: "" })), 100); }
    finally { setTaskProgress(null); }
  };

  const formatEta = (seconds?: number): string => {
    if (seconds === undefined || seconds === null || seconds <= 0) return "";
    if (seconds < 60) return `~${seconds} сек`;
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s > 0 ? `~${m} мин ${s} сек` : `~${m} мин`;
  };

  const handleExportAttributes = async () => {
    const isSelected = selectedIds.size > 0;
    const targetProducts = isSelected
      ? processed.filter((p, i) => selectedIds.has(getRowKey(p, i)))
      : processed;

    const eans = Array.from(new Set(targetProducts.map(p => p.ean || p.row?.EAN).filter(Boolean)));
    if (eans.length === 0) {
      alert("Нет активных товаров для экспорта атрибутов.");
      return;
    }

    let isCancelled = false;

    setTaskProgress({
      title: t("workspace.task_export_attr"),
      current: 0,
      total: eans.length,
      percent: 0,
      eta: "рассчитывается...",
      isIndeterminate: false,
      colorClass: "from-purple-500 to-indigo-500",
      onCancel: () => {
        isCancelled = true;
        setTaskProgress(null);
      }
    });

    try {
      const startRes = await fetchWithAuth(
        `/csv/export/attributes/start/${factoryId}?account=${account}&factory_name=${encodeURIComponent(factoryName || "")}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_eans: eans })
        }
      );

      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({}));
        alert(err.detail || t("workspace.export_failed", { error: "" }));
        setTaskProgress(null);
        return;
      }

      const { task_id } = await startRes.json();
      if (!task_id) {
        throw new Error("Не удалось получить идентификатор задачи");
      }

      while (!isCancelled) {
        await new Promise((r) => setTimeout(r, 1000));
        if (isCancelled) break;

        const sRes = await fetchWithAuth(`/csv/export/attributes/status/${task_id}`);
        if (!sRes.ok) continue;

        const sData = await sRes.json();

        if (sData.status === "PROGRESS" && sData.progress) {
          const p = sData.progress;
          const etaText = formatEta(p.eta_seconds);
          setTaskProgress({
            title: p.message || t("workspace.task_export_attr"),
            current: p.current,
            total: p.total,
            percent: p.percent,
            eta: etaText,
            isIndeterminate: false,
            colorClass: "from-purple-500 to-indigo-500",
            onCancel: () => {
              isCancelled = true;
              setTaskProgress(null);
            }
          });
        } else if (sData.status === "SUCCESS") {
          setTaskProgress({
            title: "Загрузка архива...",
            current: eans.length,
            total: eans.length,
            percent: 100,
            eta: "готов",
            isIndeterminate: false,
            colorClass: "from-purple-500 to-indigo-500"
          });

          const downRes = await fetchWithAuth(`/csv/export/attributes/download/${task_id}`);
          if (downRes.ok) {
            const blob = await downRes.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            const finalFilename = sData.result?.filename || `Attributes_${(factoryName || factoryId).replace(/[\\/:*?"<>|]/g, "_")}.zip`;
            a.download = finalFilename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
          } else {
            alert(t("workspace.alert_file_download_error"));
          }

          setTimeout(() => setTaskProgress(null), 1200);
          break;
        } else if (sData.status === "FAILURE") {
          alert(`Ошибка экспорта атрибутов: ${sData.error || "Неизвестная ошибка"}`);
          setTaskProgress(null);
          break;
        }
      }
    } catch (e) {
      console.error("Export attributes error:", e);
      alert(t("workspace.export_failed", { error: "" }));
      setTaskProgress(null);
    }
  };

  const handleExportDB = async () => {
    // 1. Determine target items: if selected, use selected; otherwise use all active catalog products
    const isSelected = selectedIds.size > 0;
    const targetProducts = isSelected
      ? processed.filter((p, i) => selectedIds.has(getRowKey(p, i)))
      : processed;

    const eans = Array.from(new Set(targetProducts.map(p => p.ean || p.row?.EAN).filter(Boolean)));
    if (eans.length === 0) {
      alert("Нет активных товаров для экспорта.");
      return;
    }

    let isCancelled = false;

    setTaskProgress({
      title: t("workspace.task_export_db"),
      current: 0,
      total: eans.length,
      percent: 0,
      eta: "рассчитывается...",
      isIndeterminate: false,
      colorClass: "from-emerald-500 to-teal-500",
      onCancel: () => {
        isCancelled = true;
        setTaskProgress(null);
      }
    });

    try {
      // 2. Start Celery background task
      const startRes = await fetchWithAuth(
        `/mapping/export-db/start/${factoryId}?account=${account}&factory_name=${encodeURIComponent(factoryName || "")}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ product_eans: eans })
        }
      );

      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({}));
        alert(err.detail || t("workspace.alert_file_download_error"));
        setTaskProgress(null);
        return;
      }

      const { task_id } = await startRes.json();
      if (!task_id) {
        throw new Error("Не удалось получить идентификатор задачи экспорта");
      }

      // 3. Poll Celery task status with live ETA and progress
      while (!isCancelled) {
        await new Promise((r) => setTimeout(r, 1000));
        if (isCancelled) break;

        const sRes = await fetchWithAuth(`/mapping/export-db/status/${task_id}`);
        if (!sRes.ok) continue;

        const sData = await sRes.json();

        if (sData.status === "PROGRESS" && sData.progress) {
          const p = sData.progress;
          const etaText = formatEta(p.eta_seconds);
          setTaskProgress({
            title: p.message || t("workspace.task_export_db"),
            current: p.current,
            total: p.total,
            percent: p.percent,
            eta: etaText,
            isIndeterminate: false,
            colorClass: "from-emerald-500 to-teal-500",
            onCancel: () => {
              isCancelled = true;
              setTaskProgress(null);
            }
          });
        } else if (sData.status === "SUCCESS") {
          // Task completed: trigger download
          setTaskProgress({
            title: "Загрузка файла...",
            current: eans.length,
            total: eans.length,
            percent: 100,
            eta: "готов",
            isIndeterminate: false,
            colorClass: "from-emerald-500 to-teal-500"
          });

          const downRes = await fetchWithAuth(`/mapping/export-db/download/${task_id}`);
          if (downRes.ok) {
            const blob = await downRes.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            const finalFilename = sData.result?.filename || `NEW_BATCH_${account}_${(factoryName || factoryId).replace(/[\\/:*?"<>|]/g, "_")}.db`;
            a.download = finalFilename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
          } else {
            alert(t("workspace.alert_file_download_error"));
          }

          setTimeout(() => setTaskProgress(null), 1200);
          break;
        } else if (sData.status === "FAILURE") {
          alert(`Ошибка экспорта: ${sData.error || "Неизвестная ошибка"}`);
          setTaskProgress(null);
          break;
        }
      }
    } catch (e) {
      console.error("Export DB error:", e);
      alert(t("workspace.alert_file_download_error"));
      setTaskProgress(null);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const isDb = file.name.endsWith(".db") || file.name.endsWith(".sqlite");
    if (isDb) {
      setIsUploading(true);
      setTaskProgress({ title: t("workspace.task_import_db"), isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
      const formData = new FormData();
      formData.append("file", file);
      formData.append("factory_id", factoryId);
      formData.append("account", account);
      try {
        const res = await fetchWithAuth("/mapping/db/import", {
          method: "POST",
          body: formData
        });
        if (res.ok) {
          const data = await res.json();
          setTaskProgress(null);
          alert(t("workspace.alert_db_import_success", { imported: data.imported_count || 0, matched: data.matched_categories || 0 }));
          await fetchMappings();
        } else {
          setTaskProgress(null);
          alert(t("workspace.alert_db_import_error"));
        }
      } catch (err) {
        console.error(err);
        setTaskProgress(null);
        alert(t("workspace.alert_db_network_error"));
      } finally {
        setIsUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
      return;
    }

    setIsUploading(true);
    setTaskProgress({ title: t("workspace.task_upload_csv"), isIndeterminate: true, colorClass: "from-amber-500 to-orange-500" });
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetchWithAuth(`/csv/import/${factoryId}`, {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        const data = await res.json();
        const msgParts = [];
        if (data.updated_categories) msgParts.push(t("workspace.import_categories_count", { count: data.updated_categories }));
        if (data.updated_attributes) msgParts.push(t("workspace.import_attributes_count", { count: data.updated_attributes }));
        if (data.skipped_foreign_eans) msgParts.push(t("workspace.import_skipped_count", { count: data.skipped_foreign_eans }));
        
        let finalAlert = "";
        if ((data.updated_categories || 0) > 0 || (data.updated_attributes || 0) > 0) {
          finalAlert = t("workspace.import_success") + "\n" + msgParts.join(" | ");
        } else if ((data.skipped_foreign_eans || 0) > 0) {
          finalAlert = t("workspace.import_skipped_all", { count: data.skipped_foreign_eans });
        } else {
          finalAlert = t("workspace.import_no_changes");
        }

        setTaskProgress(null);
        setTimeout(() => alert(finalAlert), 100);
        await fetchMappings();
      } else {
        const err = await res.json().catch(() => ({}));
        setTaskProgress(null);
        setTimeout(() => alert(t("workspace.import_error", { error: err.detail || res.statusText })), 100);
      }
    } catch (err) {
      console.error(err);
      setTaskProgress(null);
      setTimeout(() => alert(t("workspace.import_network_error")), 100);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // ── Drag & Drop Import ──
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (dragCounterRef.current === 1) {
      // Try to detect file type from dataTransfer items
      const items = Array.from(e.dataTransfer.items);
      const fileItem = items.find(i => i.kind === "file");
      if (fileItem) {
        const type = fileItem.type || "";
        if (type === "application/zip" || type === "application/x-zip-compressed") setDragFileType("zip");
        else if (type === "text/csv" || type === "application/csv") setDragFileType("csv");
        else if (type.includes("sqlite") || type.includes("db") || type.includes("database")) setDragFileType("db");
        else setDragFileType("unknown");
      }
      setIsDragOver(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setIsDragOver(false);
      setDragFileType(null);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragOver(false);
    setDragFileType(null);

    const file = e.dataTransfer.files[0];
    if (!file) return;

    const isZip = file.name.endsWith(".zip");
    const isCsv = file.name.endsWith(".csv");
    const isDb = file.name.endsWith(".db") || file.name.endsWith(".sqlite");

    if (!isZip && !isCsv && !isDb) {
      alert(t("workspace.alert_unsupported_format"));
      return;
    }

    if (isDb) {
      setIsUploading(true);
      setTaskProgress({ title: t("workspace.task_import_db"), isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
      const formData = new FormData();
      formData.append("file", file);
      formData.append("factory_id", factoryId);
      formData.append("account", account);
      try {
        const res = await fetchWithAuth("/mapping/db/import", {
          method: "POST",
          body: formData
        });
        if (res.ok) {
          const data = await res.json();
          setTaskProgress(null);
          alert(t("workspace.alert_db_import_success", { imported: data.imported_count || 0, matched: data.matched_categories || 0 }));
          await fetchMappings();
        } else {
          setTaskProgress(null);
          alert(t("workspace.alert_db_import_error"));
        }
      } catch (err) {
        console.error(err);
        setTaskProgress(null);
        alert(t("workspace.alert_db_network_error"));
      } finally {
        setIsUploading(false);
      }
      return;
    }

    // ── Cross-factory protection ──
    const factoryIdInFile = file.name.match(/(?:Categories|Attributes)_([\d]+)/i)?.[1];
    if (factoryIdInFile && factoryIdInFile !== factoryId) {
      const confirmed = window.confirm(
        t("workspace.confirm_foreign_factory", {
          file: file.name,
          foreignId: factoryIdInFile,
          factory: factoryName || factoryId,
          currentId: factoryId
        })
      );
      if (!confirmed) return;
    }

    setIsUploading(true);
    const label = isZip ? t("workspace.task_import_attr_zip") : t("workspace.task_import_cat_csv");
    setTaskProgress({ title: label, isIndeterminate: true, colorClass: isZip ? "from-purple-500 to-pink-500" : "from-amber-500 to-orange-500" });

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetchWithAuth(`/csv/import/${factoryId}`, { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json();
        const msgParts = [];
        if (data.updated_categories) msgParts.push(t("workspace.import_categories_count", { count: data.updated_categories }));
        if (data.updated_attributes) msgParts.push(t("workspace.import_attributes_count", { count: data.updated_attributes }));
        if (data.skipped_foreign_eans) msgParts.push(t("workspace.import_skipped_count", { count: data.skipped_foreign_eans }));
        
        let finalAlert = "";
        if ((data.updated_categories || 0) > 0 || (data.updated_attributes || 0) > 0) {
          finalAlert = t("workspace.import_success") + "\n" + msgParts.join(" | ");
        } else if ((data.skipped_foreign_eans || 0) > 0) {
          finalAlert = t("workspace.import_skipped_all", { count: data.skipped_foreign_eans });
        } else {
          finalAlert = t("workspace.import_no_changes");
        }

        setTaskProgress(null);
        setTimeout(() => alert(finalAlert), 100);
        await fetchMappings();
      } else {
        const err = await res.json().catch(() => ({}));
        setTaskProgress(null);
        setTimeout(() => alert(t("workspace.import_error", { error: err.detail || res.statusText })), 100);
      }
    } catch (err) {
      console.error(err);
      setTaskProgress(null);
      setTimeout(() => alert(t("workspace.import_network_error")), 100);
    } finally {
      setIsUploading(false);
    }
  }, [factoryId, factoryName, fetchMappings]);

  // Client-side processing
  const processed = useMemo(() => {
    let arr = products;
    if (search.trim()) {
      const q = search.toLowerCase();
      arr = arr.filter(p => {
        const row = p.row || {};
        return (row.Artikelbeschreibung || "").toLowerCase().includes(q) || String(p.ean || row.EAN || "").includes(q);
      });
    }
    if (categoryFilter.trim()) {
      const q = categoryFilter.toLowerCase();
      arr = arr.filter(p => {
        const ean = p.ean || (p.row || {}).EAN;
        const cat = mappings[ean]?.category_name || mappings[ean]?.category || "";
        return cat.toLowerCase().includes(q);
      });
    }
    if (statusFilter.trim()) {
      const q = statusFilter.toLowerCase();
      arr = arr.filter(p => {
        const ean = p.ean || (p.row || {}).EAN;
        const st = mappings[ean]?.status || (mappings[ean]?.category ? "Ready" : "Unmapped");
        return st.toLowerCase().includes(q);
      });
    }
    if (sort.col) {
      arr = [...arr].sort((a, b) => {
        const rowA = a.row || {};
        const rowB = b.row || {};
        const valA = sort.col === "name" ? (rowA.Artikelbeschreibung || "") : (a.ean || rowA.EAN || "");
        const valB = sort.col === "name" ? (rowB.Artikelbeschreibung || "") : (b.ean || rowB.EAN || "");
        return sort.dir === "asc" ? valA.localeCompare(valB) : valB.localeCompare(valA);
      });
    }
    return arr;
  }, [products, search, sort, categoryFilter, statusFilter, mappings]);

  // Virtualizer
  const rowVirtualizer = useVirtualizer({
    count: processed.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100, // 100px row height for larger images

    overscan: 10,
  });

  // Keep refs in sync
  processedRef.current = processed;
  selectedIdsRef.current = selectedIds;

  const toggleSort = (col: string) => {
    setSort(prev => ({ col, dir: prev.col === col && prev.dir === "asc" ? "desc" : "asc" }));
  };

  const handleRowMouseDown = useCallback((index: number, rowKey: string, e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('[data-no-drag]')) return;
    e.preventDefault();
    dragStartIndex.current = index;
    dragStartSelected.current = !selectedIdsRef.current.has(rowKey);
    dragStartSelection.current = new Set(selectedIdsRef.current);
    lastDragIndex.current = index;
    dragStartX.current = e.clientX;
    dragStartY.current = e.clientY;
    pendingDragEan.current = rowKey;
    pendingDragToggle.current = !selectedIdsRef.current.has(rowKey);
    isDragging.current = false;
  }, []);

  const toggleSelection = useCallback((rowKey: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(rowKey)) next.delete(rowKey); else next.add(rowKey);
      return next;
    });
  }, []);

  const lastMouseY = useRef<number | null>(null);

  useEffect(() => {
    let animationFrameId: number;

    const applyDragRange = (index: number) => {
      if (dragStartIndex.current === null) return;
      const start = Math.min(dragStartIndex.current, index);
      const end = Math.max(dragStartIndex.current, index);
      const base = dragStartSelection.current;
      const shouldSelect = dragStartSelected.current;

      setSelectedIds(() => {
        const next = new Set(base);
        for (let i = start; i <= end; i++) {
          const p = processedRef.current[i];
          if (!p) continue;
          const key = getRowKey(p, i);
          if (shouldSelect) next.add(key);
          else next.delete(key);
        }
        return next;
      });
    };

    const autoScrollLoop = () => {
      if (isDragging.current && lastMouseY.current !== null && parentRef.current && dragStartIndex.current !== null) {
        const rect = parentRef.current.getBoundingClientRect();
        const y = lastMouseY.current;
        const edgeZone = 30;
        const maxSpeed = 12;

        if (y < rect.top + edgeZone) {
          const factor = Math.min(1.5, Math.max(0.1, (rect.top + edgeZone - y) / edgeZone));
          parentRef.current.scrollTop -= factor * maxSpeed;
        } else if (y > rect.bottom - edgeZone) {
          const factor = Math.min(1.5, Math.max(0.1, (y - (rect.bottom - edgeZone)) / edgeZone));
          parentRef.current.scrollTop += factor * maxSpeed;
        }
      }
      animationFrameId = requestAnimationFrame(autoScrollLoop);
    };
    animationFrameId = requestAnimationFrame(autoScrollLoop);

    const handleMouseMove = (e: MouseEvent) => {
      lastMouseY.current = e.clientY;

      if (dragStartIndex.current === null) return;

      if (!isDragging.current) {
        const dx = e.clientX - dragStartX.current;
        const dy = e.clientY - dragStartY.current;
        if (Math.hypot(dx, dy) > 4) {
          isDragging.current = true;
          applyDragRange(dragStartIndex.current);
        }
      }

      if (!isDragging.current) return;

      const now = performance.now();
      if (now - lastMoveTime.current < 33) return;
      lastMoveTime.current = now;

      const el = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
      if (!el) return;
      const row = el.closest('[data-row-index]') as HTMLElement | null;
      if (!row) return;
      const indexStr = row.getAttribute('data-row-index');
      if (indexStr === null) return;
      const index = parseInt(indexStr, 10);
      if (isNaN(index) || index === lastDragIndex.current) return;
      lastDragIndex.current = index;
      applyDragRange(index);
    };

    const handleGlobalMouseUp = () => {
      isDragging.current = false;
      dragStartIndex.current = null;
      lastDragIndex.current = null;
      pendingDragEan.current = null;
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleGlobalMouseUp);
    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, []);


  // ── DATA QUALITY STATS ──
  const dataQualityStats = useMemo(() => {
    const counts = { ready: 0, needAttrs: 0, error: 0, unmapped: 0 };
    for (const p of products) {
      const ean = p.ean || (p.row || {}).EAN;
      const st = mappings[ean]?.status;
      if (st === "Ready") counts.ready++;
      else if (st === "Need Attributes") counts.needAttrs++;
      else if (st === "Error") counts.error++;
      else counts.unmapped++;
    }
    return counts;
  }, [products, mappings]);

  if (loading && products.length === 0) {
    return (
      <div className="p-10 text-slate-500 dark:text-slate-400 flex items-center gap-3">
        <Loader2 className="w-5 h-5 animate-spin text-indigo-600 dark:text-indigo-400" />
        <span className="text-sm font-medium">{t("workspace.connecting_catalog")}</span>
      </div>
    );
  }

  return (
    <div
      className="flex flex-col flex-1 space-y-4 relative"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* ── DRAG & DROP OVERLAY ── */}
      {isDragOver && (
        <div className="absolute inset-0 z-50 flex flex-col items-center justify-center rounded-xl pointer-events-none"
          style={{ background: "rgba(79,70,229,0.13)", backdropFilter: "blur(4px)", border: "2.5px dashed #6366f1" }}>
          <div className="bg-white dark:bg-[#1A1D2B] rounded-2xl shadow-2xl px-10 py-8 flex flex-col items-center gap-3 border border-indigo-300 dark:border-indigo-700">
            {dragFileType === "zip" ? (
              <FileArchive className="w-14 h-14 text-purple-500" />
            ) : dragFileType === "csv" ? (
              <FileDown className="w-14 h-14 text-amber-500" />
            ) : dragFileType === "db" ? (
              <Database className="w-14 h-14 text-blue-500" />
            ) : (
              <Upload className="w-14 h-14 text-indigo-400" />
            )}
            <p className="text-xl font-bold text-slate-800 dark:text-white">
              {dragFileType === "zip" ? t("workspace.drag_drop_zip") :
               dragFileType === "csv" ? t("workspace.drag_drop_csv") :
               dragFileType === "db" ? t("workspace.drag_drop_db") :
               t("workspace.drag_drop_any")}
            </p>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {t("workspace.drag_drop_supported")}
            </p>
          </div>
        </div>
      )}
      {/* ── BREADCRUMB & HEADER ── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-xs text-slate-400 dark:text-slate-500">
          <Link href="/dashboard/factories" className="hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors">
            {t("workspace.factories_crumb")}
          </Link>
          <span>/</span>
          <span className="text-slate-700 dark:text-slate-300 font-medium truncate max-w-xs sm:max-w-md">
            {factoryName || factoryId}
          </span>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Link href="/dashboard/factories">
              <Button
                variant="outline"
                size="sm"
                className="h-9 w-9 p-0 rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card hover:bg-slate-50 shrink-0"
              >
                <ChevronLeft className="w-4 h-4 text-slate-600 dark:text-slate-300" />
              </Button>
            </Link>
            <div>
              <div className="flex items-center gap-2.5">
                <h1 className="text-xl font-bold text-slate-900 dark:text-white tracking-tight leading-tight">
                  {factoryName || factoryId}
                </h1>
                {isBackgroundLoading && (
                  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200/80 dark:bg-indigo-950/40 dark:text-indigo-300 dark:border-indigo-800/60 animate-pulse">
                    <Loader2 className="w-3 h-3 animate-spin text-indigo-600 dark:text-indigo-400" />
                    <span>{t("workspace.loading_badge", { current: products.length, total: totalCatalogCount || products.length })}</span>
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {t("workspace.products_in_workspace", {
                  current: products.length.toLocaleString(),
                  total: totalCatalogCount && totalCatalogCount > products.length ? t("workspace.of_total", { total: totalCatalogCount.toLocaleString() }) : "",
                  account
                })}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              onClick={handleSync}
              disabled={loading}
              variant="outline"
              size="sm"
              className="gap-1.5 h-9 rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium hover:bg-slate-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>{t("common.refresh")}</span>
            </Button>
          </div>
        </div>
      </div>

      {/* ── COMPACT SEGMENTED STATUS STRIP ── */}
      <div className="bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 shadow-xs divide-y sm:divide-y-0 sm:divide-x divide-slate-100 dark:divide-slate-800 grid grid-cols-2 sm:grid-cols-5">
        <button
          type="button"
          onClick={() => setStatusFilter("")}
          className={cn(
            "p-3 text-left transition-colors",
            statusFilter === "" ? "bg-slate-50/80 dark:bg-slate-800/40" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("workspace.all_items")}</span>
          <span className="text-lg font-bold text-slate-900 dark:text-white tracking-tight mt-0.5 block">
            {products.length.toLocaleString()}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setStatusFilter(statusFilter === "ready" ? "" : "ready")}
          className={cn(
            "p-3 text-left transition-colors",
            statusFilter === "ready" ? "bg-emerald-50/80 dark:bg-emerald-950/30" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("workspace.ready")}</span>
          <span className="text-lg font-bold text-emerald-600 dark:text-emerald-400 tracking-tight mt-0.5 block">
            {dataQualityStats.ready.toLocaleString()}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setStatusFilter(statusFilter === "need" ? "" : "need")}
          className={cn(
            "p-3 text-left transition-colors",
            statusFilter === "need" ? "bg-amber-50/80 dark:bg-amber-950/30" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("workspace.need_attrs")}</span>
          <span className="text-lg font-bold text-amber-600 dark:text-amber-400 tracking-tight mt-0.5 block">
            {dataQualityStats.needAttrs.toLocaleString()}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setStatusFilter(statusFilter === "error" ? "" : "error")}
          className={cn(
            "p-3 text-left transition-colors",
            statusFilter === "error" ? "bg-rose-50/80 dark:bg-rose-950/30" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("workspace.errors")}</span>
          <span className="text-lg font-bold text-rose-600 dark:text-rose-400 tracking-tight mt-0.5 block">
            {dataQualityStats.error.toLocaleString()}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setStatusFilter(statusFilter === "unmapped" ? "" : "unmapped")}
          className={cn(
            "p-3 text-left transition-colors col-span-2 sm:col-span-1",
            statusFilter === "unmapped" ? "bg-slate-100/80 dark:bg-slate-850" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("workspace.unmapped")}</span>
          <span className="text-lg font-bold text-slate-500 dark:text-slate-400 tracking-tight mt-0.5 block">
            {dataQualityStats.unmapped.toLocaleString()}
          </span>
        </button>
      </div>

      {/* ── ACTIVE PUBLISH PLAN BANNER (DRIP FEED) ── */}
      {activePlan && (
        <div className="bg-white dark:bg-card border border-indigo-200 dark:border-indigo-900/60 rounded-xl p-4 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className={`p-2 rounded-lg shrink-0 ${activePlan.status === "paused" ? "bg-amber-50 dark:bg-amber-950/40 text-amber-600" : "bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 animate-pulse"}`}>
              <Clock className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-slate-900 dark:text-white text-sm">
                  {t("workspace.active_drip_feed", { day: activePlan.current_day, total: activePlan.total_days })}
                </span>
                <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider ${
                  activePlan.status === "paused"
                    ? "bg-amber-50 text-amber-700 border border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-400"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-950/40 dark:text-emerald-400"
                }`}>
                  {activePlan.status === "paused" ? t("workspace.drip_paused") : t("workspace.drip_running")}
                </span>
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                {t("workspace.drip_daily_at", { time: activePlan.daily_time })}{" "}
                <strong className="text-emerald-600 dark:text-emerald-400 font-semibold">{activePlan.progress?.published || 0}</strong> / {activePlan.total_products} {t("workspace.drip_items")}
                {activePlan.progress?.failed > 0 && (
                  <span className="ml-2 text-rose-600 font-medium">· {t("workspace.drip_errors")} {activePlan.progress.failed}</span>
                )}
              </p>
              <div className="w-full bg-slate-100 dark:bg-slate-800 rounded-full h-1.5 mt-2 max-w-md overflow-hidden">
                <div
                  className="bg-indigo-600 h-1.5 rounded-full transition-all duration-500"
                  style={{ width: `${activePlan.progress?.percentage || 0}%` }}
                />
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 self-end md:self-auto shrink-0">
            {activePlan.status === "paused" ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleResumePlan(activePlan.id)}
                className="h-8 text-xs border-emerald-200 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-800/60 dark:text-emerald-400"
              >
                <Play className="w-3.5 h-3.5 mr-1" /> {t("workspace.resume")}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => handlePausePlan(activePlan.id)}
                className="h-8 text-xs border-amber-200 text-amber-700 hover:bg-amber-50 dark:border-amber-800/60 dark:text-amber-400"
              >
                <Pause className="w-3.5 h-3.5 mr-1" /> {t("workspace.pause")}
              </Button>
            )}

            <Button
              size="sm"
              variant="outline"
              onClick={() => handleCancelPlan(activePlan.id)}
              className="h-8 text-xs border-rose-200 text-rose-600 hover:bg-rose-50 dark:border-rose-800/60 dark:text-rose-400"
            >
              {t("workspace.cancel")}
            </Button>

            <Button
              size="sm"
              variant="ghost"
              onClick={fetchActivePlan}
              className="h-8 w-8 p-0 text-slate-400 hover:text-slate-600"
              title={t("workspace.refresh_status")}
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      )}

      {/* ── UNIFIED OPERATIONS TOOLBAR ── */}
      <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3 bg-white dark:bg-card p-3 rounded-xl border border-slate-200/80 dark:border-slate-800 shadow-xs">
        {/* Left: Search & Filter */}
        <div className="flex flex-1 items-center gap-2.5 max-w-lg">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("workspace.search_placeholder")}
              className="pl-9 h-9 text-xs rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card"
            />
          </div>
        </div>

        {/* Right: Grouped File Operations & Automation */}
        <div className="flex items-center gap-2 flex-wrap">
          {/* Hidden File Input */}
          <input
            type="file"
            ref={fileInputRef}
            className="hidden"
            accept=".csv, .zip, .db, .sqlite"
            onChange={handleFileUpload}
          />

          {/* Import Button */}
          <Button
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50"
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
          >
            {isUploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5 text-slate-500" />}
            <span>{t("workspace.import_btn")}</span>
          </Button>

          {/* Export Dropdown */}
          <div className="relative" ref={exportMenuRef}>
            <Button
              size="sm"
              variant="outline"
              className="h-9 gap-1.5 border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-50"
              onClick={() => setIsExportMenuOpen(!isExportMenuOpen)}
              disabled={products.length === 0}
            >
              <Download className="w-3.5 h-3.5 text-slate-500" />
              <span>{t("workspace.export_btn")}</span>
              <ChevronDown className="w-3.5 h-3.5 text-slate-400 ml-0.5" />
            </Button>

            {isExportMenuOpen && (
              <div className="absolute right-0 top-full mt-1.5 w-56 bg-white dark:bg-card rounded-xl border border-slate-200 dark:border-slate-800 shadow-lg p-1.5 z-30 animate-in fade-in slide-in-from-top-1">
                <button
                  onClick={() => { setIsExportMenuOpen(false); handleGlobalExport(); }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  <Download className="w-3.5 h-3.5 text-indigo-600" />
                  <div className="text-left">
                    <div>{t("workspace.export_otto_json")}</div>
                    <div className="text-[10px] text-slate-400 font-normal">{t("workspace.export_otto_json_desc")}</div>
                  </div>
                </button>
                <button
                  onClick={() => { setIsExportMenuOpen(false); handleExportDB(); }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  <Database className="w-3.5 h-3.5 text-emerald-600" />
                  <div className="text-left">
                    <div>{t("workspace.export_sqlite")}</div>
                    <div className="text-[10px] text-slate-400 font-normal">
                      {selectedIds.size > 0 
                        ? `Экспорт ${selectedIds.size} выбранных товаров` 
                        : `Экспорт всех ${processed.length} активных товаров`}
                    </div>
                  </div>
                </button>
                <button
                  onClick={() => { setIsExportMenuOpen(false); handleExportCategories(); }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  <FileSpreadsheet className="w-3.5 h-3.5 text-amber-600" />
                  <div className="text-left">
                    <div>{t("workspace.export_categories_csv")}</div>
                    <div className="text-[10px] text-slate-400 font-normal">
                      {selectedIds.size > 0 
                        ? `Экспорт ${selectedIds.size} выбранных товаров в CSV` 
                        : `Экспорт всех ${processed.length} активных товаров в CSV`}
                    </div>
                  </div>
                </button>
                <button
                  onClick={() => { setIsExportMenuOpen(false); handleExportAttributes(); }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                >
                  <FileArchive className="w-3.5 h-3.5 text-purple-600" />
                  <div className="text-left">
                    <div>{t("workspace.export_attributes_zip")}</div>
                    <div className="text-[10px] text-slate-400 font-normal">
                      {selectedIds.size > 0 
                        ? `Экспорт ${selectedIds.size} выбранных товаров в ZIP` 
                        : `Экспорт всех ${processed.length} активных товаров в ZIP`}
                    </div>
                  </div>
                </button>
              </div>
            )}
          </div>

          {/* Drip Feed Automation */}
          <Button
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/50 hover:bg-indigo-50 dark:bg-indigo-950/30 dark:hover:bg-indigo-950/50 text-indigo-700 dark:text-indigo-300 font-semibold text-xs"
            onClick={() => setIsDripModalOpen(true)}
            disabled={products.length === 0}
          >
            <Clock className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
            <span>{t("workspace.drip_feed_btn")}</span>
          </Button>
        </div>
      </div>

      {/* Virtual Table */}
      <div
        ref={parentRef}
        onScroll={() => {
          if (previewImage) setPreviewImage(null);
        }}
        className="flex-1 overflow-auto bg-white dark:bg-card border border-slate-200/80 dark:border-slate-800 rounded-xl shadow-xs relative"
      >
        {/* Header */}
        <div className="sticky top-0 bg-slate-50/90 dark:bg-[#0B0F19]/90 backdrop-blur-xs border-b border-slate-200/80 dark:border-slate-800 z-10 flex items-center text-xs font-semibold text-slate-500 dark:text-slate-400 py-3">
          <div className="w-12 px-4 shrink-0">
            <Checkbox onCheckedChange={(c) => setSelectedIds(c ? new Set(processed.map((p, i) => getRowKey(p, i))) : new Set())} />
          </div>
          <div className="w-24 px-3 shrink-0 text-[11px] uppercase tracking-wider">{t("workspace.th_image")}</div>
          <div className="flex-1 px-4 min-w-0 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors" onClick={() => toggleSort("name")}>
            {t("workspace.th_name")}
          </div>
          <div className="w-48 px-4 shrink-0 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors" onClick={() => toggleSort("ean")}>
            {t("workspace.th_ean")}
          </div>
          <div className="w-24 px-4 shrink-0">{t("workspace.th_price")}</div>
          <div className="w-64 px-4 shrink-0 flex flex-col gap-1">
            <span className="cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors" onClick={() => toggleSort("category")}>
              {t("workspace.th_category")}
            </span>
            <AttributeCombobox
              name="Filter"
              value={categoryFilter}
              onChange={(val) => setCategoryFilter(val)}
              options={Array.from(new Set(Object.values(mappings).map(m => m.category_name || m.category).filter(Boolean))).sort()}
              placeholder={t("workspace.filter_category_placeholder")}
              className="w-full h-7 text-xs border rounded-md px-2 pr-6 outline-none border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-slate-900 dark:text-slate-100 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div className="w-40 px-4 shrink-0 flex flex-col gap-1">
            <span className="cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors" onClick={() => toggleSort("status")}>
              {t("workspace.th_status")}
            </span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="h-7 text-xs px-2 border-slate-200 dark:border-slate-800 border bg-white dark:bg-card rounded-md shadow-xs focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 cursor-pointer text-slate-700 dark:text-slate-300"
            >
              <option value="">{t("workspace.all_statuses")}</option>
              <option value="ready">{t("workspace.status_ready")}</option>
              <option value="need">{t("workspace.status_need")}</option>
              <option value="error">{t("workspace.status_error")}</option>
              <option value="unmapped">{t("workspace.status_unmapped")}</option>
            </select>
          </div>
        </div>

        {/* Body */}
        <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
          {rowVirtualizer.getVirtualItems().map((virtualRow) => {
            const product = processed[virtualRow.index];
            const row = product.row || {};
            const ean = product.ean || row.EAN || "";
            const rowKey = getRowKey(product, virtualRow.index);
            const img = row.GalleryURL || row.PictureURL || "";
            const priceStr = row.Startpreis || row.SofortkaufenPreis || "0";
            const price = parseFloat(priceStr.replace(',', '.')) > 0 ? priceStr : (row.SofortkaufenPreis || "-");
            const mapData = mappings[ean];
            const isSelected = selectedIds.has(rowKey);

            return (
              <VirtualRow
                key={virtualRow.key}
                index={virtualRow.index}
                top={virtualRow.start}
                height={virtualRow.size}
                product={product}
                ean={ean}
                rowKey={rowKey}
                img={img}
                price={price as string}
                mapData={mapData}
                isSelected={isSelected}
                isDraggingCurrent={isDragging.current}
                handleRowMouseDown={handleRowMouseDown}
                toggleSelection={toggleSelection}
                setActiveProduct={setActiveProduct}
                onImageHover={handleImageHover}
                onImageLeave={handleImageLeave}
                isMirrored={account === "XL"}
              />
            );
          })}
        </div>

        {/* ── UNDER-PRODUCTS BACKGROUND LOADING INDICATOR ── */}
        {isBackgroundLoading && (
          <div className="sticky bottom-0 z-20 flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 bg-white/95 dark:bg-[#0E131F]/95 backdrop-blur-md border-t border-indigo-200/80 dark:border-indigo-900/60 shadow-lg text-xs animate-in slide-in-from-bottom-2 duration-200">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-indigo-50 dark:bg-indigo-950/60 flex items-center justify-center shrink-0 border border-indigo-100 dark:border-indigo-900/40">
                <Loader2 className="w-4 h-4 text-indigo-600 dark:text-indigo-400 animate-spin" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-bold text-slate-900 dark:text-white">
                    {t("workspace.under_loading_title")}
                  </span>
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-indigo-100/80 dark:bg-indigo-900/60 text-indigo-700 dark:text-indigo-300">
                    {t("workspace.under_loading_count", { current: products.length, total: totalCatalogCount || products.length })}
                  </span>
                </div>
                <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
                  {t("workspace.under_loading_hint")}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3 w-full sm:w-auto justify-end">
              {totalCatalogCount && totalCatalogCount > 0 && (
                <div className="w-36 h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden flex">
                  <div
                    className="h-full bg-indigo-600 dark:bg-indigo-500 rounded-full transition-all duration-300 ease-out"
                    style={{ width: `${Math.min(100, Math.round((products.length / totalCatalogCount) * 100))}%` }}
                  />
                </div>
              )}
              <span className="text-xs font-bold text-indigo-600 dark:text-indigo-400 tabular-nums">
                {totalCatalogCount && totalCatalogCount > 0
                  ? `${Math.round((products.length / totalCatalogCount) * 100)}%`
                  : t("common.loading")}
              </span>
            </div>
          </div>
        )}

        {/* ── CONTEXTUAL SELECTION BAR ── */}
        {(selectedIds.size > 0 || taskProgress) && (
          <div className="selection-popup fixed z-50 flex flex-col justify-center bg-white dark:bg-card text-slate-900 dark:text-white px-4 py-3 rounded-xl border border-slate-200/90 dark:border-slate-800 shadow-2xl shadow-indigo-950/10 animate-in fade-in slide-in-from-bottom-4 duration-200 bottom-8 left-1/2 -translate-x-1/2 min-w-[380px]">
            {taskProgress ? (
              /* ── PROGRESS STATE ── */
              <div className="w-full flex flex-col gap-2.5">
                <div className="flex justify-between items-center gap-4">
                  <span className="flex items-center gap-2 text-xs font-semibold text-slate-900 dark:text-white truncate max-w-[240px]" title={taskProgress.title}>
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-600 dark:text-emerald-400 shrink-0" />
                    <span className="truncate">{taskProgress.title}</span>
                  </span>
                  <div className="flex items-center gap-2.5 shrink-0">
                    {taskProgress.eta && (
                      <span className="text-[11px] font-semibold text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800/60 px-2 py-0.5 rounded-md">
                        {taskProgress.eta.startsWith("~") || taskProgress.eta === "готов" || taskProgress.eta.startsWith("рассчит") 
                          ? `Осталось: ${taskProgress.eta}` 
                          : taskProgress.eta}
                      </span>
                    )}
                    {!taskProgress.isIndeterminate && taskProgress.current !== undefined && taskProgress.total !== undefined && (
                      <span className="text-xs font-bold text-slate-700 dark:text-slate-300 tabular-nums">
                        {taskProgress.current} <span className="text-slate-400 font-normal">/ {taskProgress.total}</span>
                        <span className="ml-1 text-emerald-600 dark:text-emerald-400 font-semibold text-[11px]">
                          ({Math.round(((taskProgress.current || 0) / Math.max(1, (taskProgress.total || 1))) * 100)}%)
                        </span>
                      </span>
                    )}
                    {taskProgress.onCancel && (
                      <button onClick={taskProgress.onCancel} className="text-xs text-rose-600 hover:text-rose-700 transition-colors px-2 py-0.5 rounded-md hover:bg-rose-50 dark:hover:bg-rose-950/30">{t("common.cancel")}</button>
                    )}
                  </div>
                </div>
                <div className="w-full h-2 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                  {taskProgress.isIndeterminate ? (
                    <div className="h-full w-full bg-emerald-500 animate-pulse rounded-full" />
                  ) : (
                    <div
                      className="h-full bg-gradient-to-r from-emerald-500 to-teal-500 rounded-full transition-all duration-300 ease-out"
                      style={{ width: `${Math.min(100, Math.max(2, ((taskProgress.current || 0) / Math.max(1, (taskProgress.total || 1))) * 100))}%` }}
                    />
                  )}
                </div>
              </div>
            ) : (
              /* ── ACTIONS STATE ── */
              <div className="flex items-center gap-3">
                {/* Selection scope indicator */}
                <div className="flex items-center gap-2 shrink-0">
                  <div className="h-6 px-2 rounded-md bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200/80 dark:border-indigo-900/60 flex items-center justify-center text-xs font-bold text-indigo-700 dark:text-indigo-300">
                    {selectedIds.size}
                  </div>
                  <div className="text-xs leading-tight">
                    <div className="text-slate-900 dark:text-white font-medium">{t("workspace.selected_count", { count: selectedIds.size })}</div>
                    <div className="text-slate-400 text-[10px]">{t("workspace.of_products", { total: processed.length })}</div>
                  </div>
                </div>

                <div className="w-px h-6 bg-slate-200 dark:bg-slate-700 shrink-0" />

                {/* Mass Edit */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsMassEditOpen(true)}
                  disabled={isAutoMapping || isSyncingOkb}
                  className="h-8 px-2.5 text-xs font-medium border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/60 hover:bg-indigo-50 dark:bg-indigo-950/40 dark:hover:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 gap-1.5"
                >
                  <Layers className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
                  <span>Mass Edit</span>
                </Button>

                <div className="w-px h-6 bg-slate-200 dark:bg-slate-700 shrink-0" />

                {/* OKB Sync */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleOkbSync}
                  disabled={isSyncingOkb}
                  className="h-8 px-2.5 text-xs font-medium border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-slate-700 dark:text-slate-300 hover:bg-slate-50 gap-1.5"
                >
                  {isSyncingOkb ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5 text-slate-500" />}
                  <span>{t("workspace.okb_sync")}</span>
                </Button>

                {/* AI Categories */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (!confirm(t("workspace.confirm_ai_cat", { count: selectedIds.size }))) return;
                    handleAutoMap(false, "categories");
                  }}
                  disabled={isAutoMapping}
                  className="h-8 px-2.5 text-xs font-medium border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-slate-700 dark:text-slate-300 hover:bg-slate-50 gap-1.5"
                >
                  <Sparkles className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
                  <span>{t("workspace.ai_categories")}</span>
                </Button>

                {/* AI Attributes */}
                <Button
                  size="sm"
                  onClick={() => {
                    if (!confirm(t("workspace.confirm_ai_attr", { count: selectedIds.size }))) return;
                    handleAutoMap(false, "attributes");
                  }}
                  disabled={isAutoMapping}
                  className="h-8 px-3 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white gap-1.5 shadow-xs"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>{t("workspace.ai_attributes")}</span>
                </Button>

                {/* Close */}
                <button
                  onClick={() => { setSelectedIds(new Set()); setPopupPos(null); }}
                  className="ml-1 p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors rounded-md hover:bg-slate-100 dark:hover:bg-slate-800"
                  title={t("validation.deselect_all")}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {activeProduct && (
        <>
          {/* Backdrop — click outside drawer to close */}
          <div
            className="fixed inset-0 z-40 bg-black/20 dark:bg-black/40 backdrop-blur-[1px] animate-in fade-in duration-200"
            onClick={() => setActiveProduct(null)}
          />
          <ProductDrawer
            product={activeProduct}
            factoryId={factoryId}
            onClose={() => setActiveProduct(null)}
            onSave={(ean, cat, catName, attrs, status) => {
              setMappings(prev => ({
                ...prev,
                [ean]: { category: cat, category_name: catName, status: status || "Need Attributes" }
              }));
            }}
          />
        </>
      )}

      {/* ── DRIP SCHEDULE MODAL ── */}
      <DripScheduleModal
        factoryId={factoryId}
        factoryName={factoryName}
        isOpen={isDripModalOpen}
        onClose={() => setIsDripModalOpen(false)}
        onSuccess={() => {
          fetchActivePlan();
          fetchMappings();
        }}
        selectedEans={selectedIds.size > 0
          ? processed
              .filter((p, i) => selectedIds.has(getRowKey(p, i)))
              .map(p => p.ean || p.row?.EAN || "")
              .filter(Boolean)
          : undefined}
      />

      {/* ── MASS EDIT DRAWER ── */}
      {isMassEditOpen && (
        <MassEditDrawer
          selectedEans={
            processed
              .filter((p, i) => selectedIds.has(getRowKey(p, i)))
              .map(p => p.ean || p.row?.EAN || "")
              .filter(Boolean)
          }
          onClose={() => setIsMassEditOpen(false)}
          onSaved={() => {
            setIsMassEditOpen(false);
            fetchMappings();
          }}
        />
      )}

      {/* ── IMAGE PREVIEW HOVER PORTAL ── */}
      <ImageHoverPreview preview={previewImage} />

    </div>
  );
}

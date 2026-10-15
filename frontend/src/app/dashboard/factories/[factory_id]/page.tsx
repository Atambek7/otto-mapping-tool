"use client";

import React, { useEffect, useState, useRef, useMemo, useCallback, memo, startTransition, useDeferredValue } from "react";
import { useParams, useRouter } from "next/navigation";
import { fetchWithAuth } from "@/lib/api";
import { getCachedFactory, setCachedFactory } from "@/lib/cache";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { useVirtualizer } from "@tanstack/react-virtual";
import { CategoryAutocomplete } from "@/components/CategoryAutocomplete";
import { InlineCategoryCell } from "@/components/InlineCategoryCell";
import { InlineTitleCell } from "@/components/InlineTitleCell";
import { InlinePriceCell } from "@/components/InlinePriceCell";
import { ProductDrawer } from "@/components/ProductDrawer";
import { MassEditDrawer } from "@/components/MassEditDrawer";
import { toast } from "sonner";

import {
  Download, RefreshCw, Search, ArrowUpDown, ArrowUp, ArrowDown, Database, Wifi, X, Sparkles, Loader2, ChevronLeft, Upload, FileDown, FileArchive, Bot, Play, Pause, Clock, AlertCircle, Calendar, ChevronDown, ChevronRight, FileSpreadsheet, Check, Layers, ArrowRightLeft
} from "lucide-react";
import { useAccount } from "@/lib/useAccount";
import { showOpenAiErrorToast } from "@/lib/openaiError";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { AttributeCombobox } from "@/components/AttributeCombobox";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";
import DripScheduleModal from "@/components/DripScheduleModal";
import DripQueueModal from "@/components/DripQueueModal";
import AiTitleModal from "@/components/AiTitleModal";
import { PriceEndingModal } from "@/components/PriceEndingModal";
import { useTranslation } from "react-i18next";

// --- Helpers ---
const deCollator = new Intl.Collator("de", { numeric: true, sensitivity: "base" });

function parseSpecifics(raw: any) {
  if (!raw) return [];
  try {
    const p = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (Array.isArray(p)) return p.filter(i => i?.Name && i?.Value).slice(0, 4).map(i => [String(i.Name), String(i.Value)]);
    if (typeof p === "object" && p !== null) return Object.entries(p).slice(0, 4).map(([k, v]) => [k, String(v)]);
  } catch { }
  return [];
}

const parsePositivePrice = (val: any): string | null => {
  if (!val) return null;
  const s = String(val).replace("€", "").replace(/EUR/gi, "").replace(",", ".").trim();
  const num = parseFloat(s);
  return (!isNaN(num) && num > 0) ? (num % 1 === 0 ? String(Math.floor(num)) : num.toFixed(2)) : null;
};

const getItemNumericPrice = (item: any, mappings: Record<string, any>): number => {
  if (!item) return 0;
  const row = item.row || {};
  const ean = item.ean || row.EAN || "";

  // 1. Saved or edited mapping price has highest priority
  const mapPrice = parsePositivePrice(mappings[ean]?.price);
  if (mapPrice) {
    const num = parseFloat(mapPrice);
    if (!isNaN(num) && num > 0) return num;
  }

  // 2. Catalog Startpreis
  const startPrice = parsePositivePrice(row.Startpreis);
  if (startPrice) {
    const num = parseFloat(startPrice);
    if (!isNaN(num) && num > 0) return num;
  }

  // 3. Catalog SofortkaufenPreis
  const sofortPrice = parsePositivePrice(row.SofortkaufenPreis);
  if (sofortPrice) {
    const num = parseFloat(sofortPrice);
    if (!isNaN(num) && num > 0) return num;
  }

  // 4. Any other price field (e.g. row.price or item.price)
  const otherPrice = parsePositivePrice(row.price || item.price);
  if (otherPrice) {
    const num = parseFloat(otherPrice);
    if (!isNaN(num) && num > 0) return num;
  }

  return 0;
};

const getRowKey = (p: any, index: number): string => {
  if (!p) return `row-${index}`;
  const row = p.row || {};
  return String(row.ItemID || row.id || p.id || `${p.ean || row.EAN || 'item'}_${index}`);
};

export const getProductStatusKey = (product: any, mapData: any): "published" | "in progress" | "ready" | "need" | "error" | "unmapped" => {
  const rawStatus = (mapData?.status || "").toLowerCase().trim();

  // If there is no category assigned, product is strictly "unmapped"
  const cat = mapData?.category_id || mapData?.category || product?.category_id || product?.category;
  const hasCategory = Boolean(cat && String(cat).trim() !== "" && String(cat).trim() !== "0" && String(cat).toLowerCase().trim() !== "none");

  if (!hasCategory) {
    return "unmapped";
  }

  if (rawStatus === "published") return "published";
  if (rawStatus === "in progress" || rawStatus === "processing") return "in progress";
  if (rawStatus === "ready") return "ready";
  if (rawStatus === "need attributes" || rawStatus === "need_attributes" || rawStatus === "partial") return "need";
  if (rawStatus === "error") return "error";
  if (rawStatus === "not mapped" || rawStatus === "unmapped" || rawStatus === "not_mapped") return "unmapped";

  return "need";
};

interface VirtualRowProps {
  index: number;
  top: number;
  height: number;
  product: any;
  ean: string;
  rowKey: string;
  img: string;
  price: string;
  mapData: any;
  isSelected: boolean;
  isDraggingRef: React.MutableRefObject<boolean>;
  isMirrored?: boolean;
  factoryId: string;
  account: string;
  onCategorySaved: (ean: string, catId: string, catName: string) => void;
  onTitleSaved: (ean: string, newTitle: string) => void;
  onPriceSaved: (ean: string, newPrice: string) => void;
  handleRowMouseDown: (index: number, rowKey: string, e: React.MouseEvent) => void;
  toggleSelection: (rowKey: string) => void;
  setActiveProduct: (p: any) => void;
  onImageHover: (img: string, name: string, price: string, e: React.MouseEvent<HTMLElement>) => void;
  onImageLeave: () => void;
}

const VirtualRowComponent = ({
  index, top, height, product, ean, rowKey, img, price, mapData, isSelected,
  isDraggingRef, handleRowMouseDown, toggleSelection, setActiveProduct,
  onImageHover, onImageLeave, isMirrored, factoryId, account, onCategorySaved,
  onTitleSaved, onPriceSaved
}: VirtualRowProps) => {
  const { t } = useTranslation();
  const [isEditingCategory, setIsEditingCategory] = useState(false);
  const row = product.row || {};
  const rawTitle = (mapData?.product_name || "").trim();
  const currentTitle = (rawTitle && rawTitle !== "Без названия" && rawTitle.toLowerCase() !== "null")
    ? rawTitle
    : (row.Artikelbeschreibung || product.name || "");
  const currentPrice = parsePositivePrice(mapData?.price) || parsePositivePrice(price) || "-";

  return (
    <div
      data-row-index={index}
      className={cn(
        "absolute top-0 left-0 w-full flex items-center border-b border-slate-100 dark:border-slate-800/60 hover:bg-slate-50/70 dark:hover:bg-slate-800/30 cursor-pointer transition-colors text-sm select-none",
        isSelected && "bg-indigo-50/50 hover:bg-indigo-50/70 dark:bg-indigo-950/20 dark:hover:bg-indigo-950/30",
        isEditingCategory ? "z-[60]" : "z-0 focus-within:z-[60]"
      )}
      style={{
        height: `${height}px`,
        transform: `translateY(${top}px)`,
      }}
      onMouseDown={(e) => handleRowMouseDown(index, rowKey, e)}
      onClick={(e) => { if (!isDraggingRef.current) setActiveProduct(product); }}
    >
      <div className="w-12 px-4 shrink-0" data-no-drag onClick={(e) => e.stopPropagation()}>
        <Checkbox checked={isSelected} onCheckedChange={() => toggleSelection(rowKey)} />
      </div>
      <div className="w-24 px-3 shrink-0" data-no-drag>
        <div
          className="w-16 h-16 rounded-lg border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 flex items-center justify-center p-1 overflow-hidden shadow-xs hover:ring-2 hover:ring-indigo-500/30 transition-all cursor-zoom-in relative"
          onMouseEnter={(e) => {
            if (img) onImageHover(img, currentTitle, currentPrice !== "-" ? `${currentPrice}` : "", e);
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
        <InlineTitleCell
          title={currentTitle}
          ean={ean}
          factoryId={factoryId}
          account={account}
          onTitleSaved={onTitleSaved}
        />
      </div>
      <div
        className="w-48 px-4 shrink-0"
        data-no-drag
        onClick={(e) => e.stopPropagation()}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <span
          className="font-mono text-xs text-slate-600 dark:text-slate-400 bg-slate-100 dark:bg-slate-800/60 px-2 py-0.5 rounded border border-slate-200/60 dark:border-slate-700/50 inline-block select-text cursor-text"
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
        >
          {ean || t("workspace.no_ean")}
        </span>
      </div>
      <div className="w-28 px-3 shrink-0">
        <InlinePriceCell
          price={currentPrice}
          ean={ean}
          factoryId={factoryId}
          account={account}
          onPriceSaved={onPriceSaved}
        />
      </div>
      <div className="w-64 px-4 shrink-0 text-xs text-slate-700 dark:text-slate-300">
        <InlineCategoryCell
          ean={ean}
          factoryId={factoryId}
          account={account}
          categoryId={mapData?.category}
          categoryName={mapData?.category_name}
          status={mapData?.status}
          onCategorySaved={onCategorySaved}
          onEditingChange={setIsEditingCategory}
        />
      </div>
      <div className="w-40 px-4 shrink-0">
        {(() => {
          const stKey = getProductStatusKey(product, mapData);
          if (stKey === "published") {
            return (
              <span
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200/80 dark:bg-indigo-950/40 dark:text-indigo-400 dark:border-indigo-800/60"
                title={mapData?.published_at ? `Опубликован: ${new Date(mapData.published_at).toLocaleString("ru-RU")}` : "Опубликован на Otto"}
              >
                <Check className="w-3 h-3" />
                <span>{t("workspace.status_published")}</span>
              </span>
            );
          }
          if (stKey === "in progress") {
            return (
              <span
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200/80 dark:bg-blue-950/40 dark:text-blue-400 dark:border-blue-800/60"
                title="Отправлен в Otto Market, идет обработка..."
              >
                <Loader2 className="w-3 h-3 animate-spin text-blue-600 dark:text-blue-400" />
                <span>{t("workspace.status_in_progress")}...</span>
              </span>
            );
          }
          if (stKey === "ready") {
            return (
              <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800/60">
                {t("workspace.status_ready")}
              </span>
            );
          }
          if (stKey === "need") {
            const missingText = Array.isArray(mapData?.missing_attributes) && mapData.missing_attributes.length > 0
              ? mapData.missing_attributes.join(", ")
              : mapData?.last_error || undefined;
            return (
              <span
                className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800/60 cursor-help"
                title={missingText ? `Не заполнено: ${missingText}` : undefined}
              >
                {t("workspace.status_need")}
              </span>
            );
          }
          if (stKey === "error") {
            const errText = mapData?.last_error || (Array.isArray(mapData?.missing_attributes) && mapData.missing_attributes.length > 0
              ? mapData.missing_attributes.join("; ")
              : undefined);
            return (
              <span
                className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-rose-50 text-rose-700 border border-rose-200/80 dark:bg-rose-950/40 dark:text-rose-400 dark:border-rose-800/60 cursor-help"
                title={errText ? `Ошибки: ${errText}` : undefined}
              >
                {t("workspace.status_error")}
              </span>
            );
          }
          return (
            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-slate-100 text-slate-600 border border-slate-200/80 dark:bg-slate-800/60 dark:text-slate-400 dark:border-slate-700/50">
              {t("workspace.status_unmapped")}
            </span>
          );
        })()}
      </div>
    </div>
  );
}

function areVirtualRowPropsEqual(prev: any, next: any) {
  return (
    prev.index === next.index &&
    prev.top === next.top &&
    prev.height === next.height &&
    prev.isSelected === next.isSelected &&
    prev.ean === next.ean &&
    prev.rowKey === next.rowKey &&
    prev.img === next.img &&
    prev.price === next.price &&
    prev.isMirrored === next.isMirrored &&
    prev.factoryId === next.factoryId &&
    prev.account === next.account &&
    prev.mapData === next.mapData &&
    prev.product === next.product
  );
}

const VirtualRow = React.memo(VirtualRowComponent, areVirtualRowPropsEqual);

export default function FactoryWorkspace() {
  const params = useParams();
  const router = useRouter();
  const factoryId = params?.factory_id as string;
  const { account, setAccount } = useAccount();
  const { t } = useTranslation();

  const [products, setProducts] = useState<any[]>([]);
  const [streamedLoadedCount, setStreamedLoadedCount] = useState<number>(0);
  const [factoryName, setFactoryName] = useState("");
  const [loading, setLoading] = useState(true);
  const [isBackgroundLoading, setIsBackgroundLoading] = useState(false);
  const [totalCatalogCount, setTotalCatalogCount] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [onlyOver70, setOnlyOver70] = useState(false);
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
  const [isApplyingDefaults, setIsApplyingDefaults] = useState(false);
  const isSyncCancelled = useRef(false);
  const isAutoMappingCancelled = useRef(false);

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
  const [isQueueModalOpen, setIsQueueModalOpen] = useState(false);
  const [isAiTitleModalOpen, setIsAiTitleModalOpen] = useState(false);
  const [isPriceEndingModalOpen, setIsPriceEndingModalOpen] = useState(false);
  const [activePlan, setActivePlan] = useState<any | null>(null);
  const [hasAnyPlan, setHasAnyPlan] = useState(false);

  const fetchActivePlan = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`/pipeline/plans?account_name=${account}&factory_id=${factoryId}`);
      if (res.ok) {
        const data = await res.json();
        const current = data.find((p: any) => 
          (p.status === "active" || p.status === "paused") && 
          (p.progress?.pending > 0 || p.progress?.processing > 0)
        ) || null;
        setActivePlan((prev: any) => {
          if (!prev && !current) return null;
          if (!prev || !current) return current;
          if (
            prev.id === current.id &&
            prev.status === current.status &&
            prev.current_day === current.current_day &&
            prev.progress?.published === current.progress?.published &&
            prev.progress?.failed === current.progress?.failed &&
            prev.progress?.processing === current.progress?.processing &&
            prev.progress?.pending === current.progress?.pending
          ) {
            return prev;
          }
          return current;
        });
        setHasAnyPlan(Array.isArray(data) && data.length > 0);
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

  const [isTransferring, setIsTransferring] = useState(false);

  const handleTransferToAccount = async (targetEans?: string[]) => {
    if (!account) return;
    const targetAccount = account === "JV" ? "XL" : "JV";
    let eanList: string[] = [];
    if (targetEans && targetEans.length > 0) {
      eanList = targetEans;
    } else if (selectedIds.size > 0) {
      eanList = processed
        .filter((p, i) => {
          const key = getRowKey(p, i);
          const ean = p.ean || p.row?.EAN;
          const id = p.id || p.row?.id || p.row?.ItemID;
          return selectedIds.has(key) || (ean && selectedIds.has(String(ean))) || (id && selectedIds.has(String(id)));
        })
        .map(p => p.ean || p.row?.EAN || "")
        .filter(Boolean);

      if (eanList.length === 0) {
        eanList = Array.from(selectedIds).filter(id => /^\d{8,14}$/.test(id));
      }
    } else {
      eanList = processed.map(p => p.ean || p.row?.EAN || "").filter(Boolean);
    }

    if (eanList.length === 0) {
      toast.error("Нет товаров для переноса");
      return;
    }

    const confirmMsg = `Перенести ${eanList.length} товаров из ${account} в ${targetAccount}?\n\nКатегории и характеристики будут скопированы, а брендовые атрибуты (Marke, BattVO, Markeninformation, WEEE, Materialhinweis) автоматически адаптированы под стандарты ${targetAccount} (Brand Guard).`;
    if (!confirm(confirmMsg)) return;

    setIsTransferring(true);
    const toastId = toast.loading(`Подготовка к переносу ${eanList.length} товаров в ${targetAccount}...`);
    try {
      const CHUNK_SIZE = 100;
      let totalTransferredMappings = 0;
      let totalTransferredAttributes = 0;
      let resolvedTargetFid: string | null = null;

      for (let i = 0; i < eanList.length; i += CHUNK_SIZE) {
        const chunk = eanList.slice(i, i + CHUNK_SIZE);
        const processedCount = Math.min(i + CHUNK_SIZE, eanList.length);
        const percent = Math.round((processedCount / eanList.length) * 100);

        toast.loading(`Перенос товаров в ${targetAccount}: ${processedCount} из ${eanList.length} (${percent}%)...`, { id: toastId });

        const res = await fetchWithAuth("/mapping/transfer-to-account", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            product_eans: chunk,
            factory_id: factoryId,
            target_factory_id: resolvedTargetFid || undefined,
            source_account: account,
            target_account: targetAccount,
            copy_categories: true,
            copy_attributes: true
          }),
          timeout: 60000
        } as any);

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Ошибка ${res.status} при переносе пачки ${processedCount}/${eanList.length}`);
        }

        const data = await res.json();
        totalTransferredMappings += (data.transferred_mappings || 0);
        totalTransferredAttributes += (data.transferred_attributes || 0);
        if (data.target_factory_id && !resolvedTargetFid) {
          resolvedTargetFid = data.target_factory_id;
        }
      }

      toast.dismiss(toastId);
      if (resolvedTargetFid) {
        toast.success(
          `✅ Успешно перенесено в ${targetAccount}! Категорий: ${totalTransferredMappings}, Атрибутов: ${totalTransferredAttributes}.`,
          {
            action: {
              label: `Перейти в ${targetAccount}`,
              onClick: () => {
                setAccount(targetAccount as any);
                router.push(`/dashboard/factories/${resolvedTargetFid}`);
              }
            },
            duration: 10000,
          }
        );
      } else {
        toast.success(`✅ Успешно перенесено в ${targetAccount}! Категорий: ${totalTransferredMappings}, Атрибутов: ${totalTransferredAttributes}. Брендинг безопасно заменен на ${targetAccount}.`);
      }
    } catch (err: any) {
      toast.dismiss(toastId);
      toast.error(err.message || "Ошибка при переносе между аккаунтами");
    } finally {
      setIsTransferring(false);
    }
  };

  // Drag & Drop overlay state
  const [isDragOver, setIsDragOver] = useState(false);
  const [dragFileType, setDragFileType] = useState<"csv" | "zip" | "db" | "unknown" | null>(null);
  const dragCounterRef = useRef(0);

  // Mappings state: EAN -> { category, status, category_name, published_at, attributes, product_name, price, has_ai_title, has_manual_price }
  const [mappings, setMappings] = useState<Record<string, { category: string, status: string, category_name?: string, published_at?: string | null, attributes?: Record<string, string>, product_name?: string, price?: string, has_ai_title?: boolean, has_manual_price?: boolean }>>({});

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
  // Keep processed and products in ref for use inside event handlers without stale closure
  const processedRef = useRef<any[]>([]);
  const productsRef = useRef<any[]>([]);
  useEffect(() => {
    productsRef.current = products;
  }, [products]);
  const selectedIdsRef = useRef<Set<string>>(new Set());

  const parentRef = useRef<HTMLDivElement>(null);
  const lastStatusCheckTimeRef = useRef<string | null>(null);

  const fetchMappings = useCallback(async () => {
    if (!factoryId) return;
    try {
      const mapRes = await fetchWithAuth(`/mapping/factory/${factoryId}?account=${account}`);
      if (mapRes.ok) {
        const mapData = await mapRes.json();
        lastStatusCheckTimeRef.current = new Date().toISOString();
        const normalized: Record<string, { category: string, status: string, category_name?: string, published_at?: string | null, product_name?: string, price?: string, has_ai_title?: boolean, has_manual_price?: boolean }> = {};
        for (const [ean, val] of Object.entries(mapData as any)) {
          const v = val as any;
          normalized[ean] = {
            category: v.category_id || v.category_name || "",
            status: v.status || "",
            category_name: v.category_name || "",
            published_at: v.published_at || null,
            product_name: (v.product_name && v.product_name.trim() !== "Без названия" && v.product_name.toLowerCase() !== "null") ? v.product_name : undefined,
            price: parsePositivePrice(v.price) || undefined,
            has_ai_title: Boolean(v.has_ai_title),
            has_manual_price: Boolean(v.has_manual_price)
          };
        }
        setMappings(prev => {
          const prevKeys = Object.keys(prev);
          const newKeys = Object.keys(normalized);
          if (prevKeys.length === newKeys.length) {
            let hasDiff = false;
            for (const k of newKeys) {
              const oldVal = prev[k];
              const newVal = normalized[k];
              if (
                !oldVal ||
                oldVal.status !== newVal.status ||
                oldVal.category !== newVal.category ||
                oldVal.category_name !== newVal.category_name ||
                oldVal.published_at !== newVal.published_at ||
                oldVal.product_name !== newVal.product_name ||
                oldVal.price !== newVal.price ||
                oldVal.has_ai_title !== newVal.has_ai_title
              ) {
                hasDiff = true;
                break;
              }
            }
            if (!hasDiff) return prev;
          }
          return normalized;
        });
      }
    } catch (e) {
      console.error("Failed to fetch mappings", e);
    }
  }, [factoryId, account]);

  const pollStatusIncremental = useCallback(async () => {
    if (!factoryId) return;
    try {
      const sinceParam = lastStatusCheckTimeRef.current
        ? `&since=${encodeURIComponent(lastStatusCheckTimeRef.current)}`
        : "";
      const res = await fetchWithAuth(
        `/mapping/factory/${factoryId}/processing-status?account=${account}${sinceParam}`
      );
      if (!res.ok) return;

      const data = await res.json();
      if (data.server_time) {
        lastStatusCheckTimeRef.current = data.server_time;
      }

      const changedItems: any[] = data.changed || [];
      if (changedItems.length === 0) {
        return;
      }

      setMappings(prev => {
        let hasRealDiff = false;
        const next = { ...prev };

        for (const item of changedItems) {
          const ean = String(item.ean);
          const existing = prev[ean];
          const newStatus = item.status || "";
          const newCat = item.category_id || existing?.category || "";
          const newCatName = item.category_name || existing?.category_name || "";
          const newPubAt = item.published_at || null;

          const isDiff =
            !existing ||
            existing.status !== newStatus ||
            existing.category !== newCat ||
            existing.category_name !== newCatName ||
            existing.published_at !== newPubAt;

          if (isDiff) {
            hasRealDiff = true;
            next[ean] = {
              ...(existing || { category: "", status: "Not Mapped" }),
              category: newCat,
              category_name: newCatName,
              status: newStatus,
              published_at: newPubAt,
            };
          }
        }

        return hasRealDiff ? next : prev;
      });
    } catch (e) {
      console.error("Incremental mapping status poll failed", e);
    }
  }, [factoryId, account]);

  const handleApplyDefaultsBulk = async (options?: { targetGroup?: string; design?: string }) => {
    if (!factoryId || selectedIds.size === 0 || !account) return;
    const selectedEans = processed
      .filter((p, i) => selectedIds.has(getRowKey(p, i)))
      .map((p) => p.ean || p.row?.EAN || "")
      .filter(Boolean);

    if (selectedEans.length === 0) return;

    setIsApplyingDefaults(true);
    toast.info(`Применяются стандартные атрибуты для ${selectedEans.length} товаров (${account})...`);
    try {
      const res = await fetchWithAuth("/mapping/apply-defaults", {
        method: "POST",
        body: JSON.stringify({
          product_eans: selectedEans,
          account: account,
          target_group: options?.targetGroup || "Erwachsene ; Jugendliche",
          design: options?.design || "Modern",
          overwrite_existing: false,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        toast.success(`Успешно! Ready: ${data.ready}, Errors: ${data.errors}`);
        await fetchMappings();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || "Ошибка применения атрибутов");
      }
    } catch {
      toast.error("Сетевая ошибка при применении атрибутов");
    } finally {
      setIsApplyingDefaults(false);
    }
  };

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
    let isCancelled = false;
    const abortController = new AbortController();
    const signal = abortController.signal;
    const load = async () => {
      // 0. Failsafe check: Does factoryId belong to the active account?
      const otherAccount = account === "JV" ? "XL" : "JV";
      try {
        const activeListRaw = localStorage.getItem(`otto_factories_list_${account}`);
        const otherListRaw = localStorage.getItem(`otto_factories_list_${otherAccount}`);
        const activeList = activeListRaw ? JSON.parse(activeListRaw) : [];
        const otherList = otherListRaw ? JSON.parse(otherListRaw) : [];

        const belongsToActive = activeList.some((f: any) => String(f.id) === String(factoryId));
        const belongsToOther = otherList.some((f: any) => String(f.id) === String(factoryId));

        if (!belongsToActive && belongsToOther) {
          const originFactory = otherList.find((f: any) => String(f.id) === String(factoryId));
          if (originFactory?.name) {
            const normName = originFactory.name.toLowerCase().trim();
            const twin = activeList.find(
              (f: any) => f.name?.toLowerCase().trim() === normName && (f.kind === originFactory.kind || f.kind === "lister")
            );
            if (twin?.id) {
              router.replace(`/dashboard/factories/${twin.id}`);
              return;
            }
          }

          // Fallback to backend twin resolver
          try {
            const res = await fetchWithAuth(
              `/aftercool/twin-factory?factory_id=${factoryId}&source_account=${otherAccount}&target_account=${account}`
            );
            if (res.ok) {
              const data = await res.json();
              if (data.target_factory_id) {
                router.replace(`/dashboard/factories/${data.target_factory_id}`);
                return;
              }
            }
          } catch (e) {}

          // Not found in target account
          router.replace("/dashboard/factories");
          const label = originFactory?.name || factoryName || "";
          toast.info(`Фабрика ${label ? `«${label}» ` : ""}на данный момент отсутствует в аккаунте ${account}. Открыт список фабрик.`);
          return;
        }
      } catch (e) {
        console.error("Failsafe twin lookup error:", e);
      }

      // 1. Instant cache render (0-15ms)
      const cached = await getCachedFactory(factoryId, account);
      const isCachedTruncated = Boolean(
        cached?.products?.length && (
          cached.products.length === 500 || 
          (cached.total && cached.products.length < cached.total)
        )
      );

      if (cached?.products?.length) {
        setProducts(cached.products);
        setTotalCatalogCount(cached.total || cached.products.length);
        setLoading(false);
        if (!isCachedTruncated) {
          setIsBackgroundLoading(false);
        }
      } else {
        setLoading(true);
      }

      // 2. Fetch from network if not cached or if it looks truncated (exactly 500 or less than total)
      let all = cached?.products || [];
      if (!all.length || isCachedTruncated) {
        try {
          setIsBackgroundLoading(true);
          const PAGE_SIZE = 500;
          const res = await fetchWithAuth(
            `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=${PAGE_SIZE}&offset=0&include_row=1`,
            { signal }
          );
          if (!res.ok) throw new Error("Failed to fetch products");
          const data = await res.json();
          if (isCancelled || signal.aborted) return;
          const firstChunk = data?.items || data || [];
          all = [...firstChunk];

          // Immediately display products on screen (<200ms perceived load)
          setProducts([...all]);
          setLoading(false);

          const total = Number(data?.total) || all.length;
          setTotalCatalogCount(total);
          setStreamedLoadedCount(all.length);

          // If more products exist, fetch with Controlled Parallel Pagination (MAX_PARALLEL_PAGES = 3)
          if (total > all.length && firstChunk.length > 0) {
            const MAX_SAFE_TOTAL = 50000;
            const safeTotal = Math.min(total, MAX_SAFE_TOTAL);
            const remainingOffsets: number[] = [];
            for (let off = all.length; off < safeTotal; off += PAGE_SIZE) {
              remainingOffsets.push(off);
            }

            const MAX_PARALLEL_PAGES = 3;
            for (let i = 0; i < remainingOffsets.length; i += MAX_PARALLEL_PAGES) {
              if (isCancelled || signal.aborted) return;
              // 300ms throttle gives the browser main thread time to process user interactions
              await new Promise((r) => setTimeout(r, 300));
              if (isCancelled || signal.aborted) return;

              const batch = remainingOffsets.slice(i, i + MAX_PARALLEL_PAGES);
              const results = await Promise.all(
                batch.map(async (off) => {
                  try {
                    const r = await fetchWithAuth(
                      `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=${PAGE_SIZE}&offset=${off}&include_row=1&sync_db=1`,
                      { signal }
                    );
                    if (!r.ok) return [];
                    const d = await r.json();
                    return d?.items || d || [];
                  } catch (e: any) {
                    if (e?.name === "AbortError" || signal.aborted) return [];
                    return [];
                  }
                })
              );
              if (isCancelled || signal.aborted) return;

              const validChunks = results.filter((chunk) => Array.isArray(chunk) && chunk.length > 0);
              if (validChunks.length === 0) {
                // Aftercool returned no more products, stop pagination early
                break;
              }

              for (const chunk of validChunks) {
                all = all.concat(chunk);
              }

              // Update the lightweight counter only so the bottom progress bar updates smoothly
              // WITHOUT triggering expensive table, stats, and ProductDrawer re-renders!
              setStreamedLoadedCount(all.length);
            }
          }

          if (all.length > 0 && !isCancelled && !signal.aborted) {
            await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() }, false, account);
            // Single final transition update once background loading completes
            startTransition(() => {
              setProducts([...all]);
              setTotalCatalogCount(all.length);
              setStreamedLoadedCount(all.length);
            });
          }
        } catch (e: any) {
          if (e?.name !== "AbortError" && !signal.aborted) {
            console.error("Failed to load factory products:", e);
          }
        } finally {
          if (!isCancelled && !signal.aborted) {
            setLoading(false);
            setIsBackgroundLoading(false);
          }
        }
      }

      // 3. Resolve factory name and fetch mappings in parallel
      const namePromise = (async () => {
        try {
          const cacheKey = `otto_factories_list_${account}`;
          const saved = localStorage.getItem(cacheKey);
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
              localStorage.setItem(cacheKey, JSON.stringify(freshList));
              const fFound = freshList.find((f: any) => String(f.id) === String(factoryId));
              if (fFound?.name) setFactoryName(fFound.name);
            }
          }
        } catch (e) { }
      })();

      await Promise.all([fetchMappings(), namePromise]);
    };
    load();
    return () => {
      isCancelled = true;
      abortController.abort();
    };
  }, [factoryId, account, fetchMappings]);

  // Auto-poll mappings ONLY when any item is legitimately In Progress or Processing
  const hasInProgress = useMemo(() => {
    return Object.values(mappings).some(m => {
      const s = (m.status || "").toLowerCase().trim();
      return s === "in progress" || s === "processing";
    });
  }, [mappings]);

  useEffect(() => {
    if (!hasInProgress) return;

    const interval = setInterval(() => {
      pollStatusIncremental();
    }, 6000);

    return () => clearInterval(interval);
  }, [hasInProgress, pollStatusIncremental]);

  const handleSync = async () => {
    if (loading || isBackgroundLoading) return;
    setIsBackgroundLoading(true);
    try {
      const PAGE_SIZE = 500;
      const res = await fetchWithAuth(
        `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=${PAGE_SIZE}&offset=0&include_row=1&refresh=true`
      );
      if (!res.ok) throw new Error("Failed to sync products");
      const data = await res.json();
      let all = data?.items || data || [];
      setProducts([...all]);
      setLoading(false);

      const total = Number(data?.total) || all.length;
      setTotalCatalogCount(total);

      if (total > all.length && all.length > 0) {
        const MAX_SAFE_TOTAL = 50000;
        const safeTotal = Math.min(total, MAX_SAFE_TOTAL);
        const remainingOffsets: number[] = [];
        for (let off = all.length; off < safeTotal; off += PAGE_SIZE) {
          remainingOffsets.push(off);
        }

        const MAX_PARALLEL_PAGES = 3;
        let batchesSinceLastRender = 0;
        for (let i = 0; i < remainingOffsets.length; i += MAX_PARALLEL_PAGES) {
          await new Promise((r) => setTimeout(r, 300));

          const batch = remainingOffsets.slice(i, i + MAX_PARALLEL_PAGES);
          const results = await Promise.all(
            batch.map(async (off) => {
              try {
                const r = await fetchWithAuth(
                  `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=${PAGE_SIZE}&offset=${off}&include_row=1&refresh=true&sync_db=1`
                );
                if (!r.ok) return [];
                const d = await r.json();
                return d?.items || d || [];
              } catch {
                return [];
              }
            })
          );

          const validChunks = results.filter((chunk) => Array.isArray(chunk) && chunk.length > 0);
          if (validChunks.length === 0) {
            break;
          }

          for (const chunk of validChunks) {
            all = all.concat(chunk);
          }

          batchesSinceLastRender++;
          const isLastBatch = (i + MAX_PARALLEL_PAGES) >= remainingOffsets.length;
          if (batchesSinceLastRender >= 2 || isLastBatch) {
            batchesSinceLastRender = 0;
            const snapshot = [...all];
            startTransition(() => {
              setProducts(snapshot);
              setTotalCatalogCount(total);
            });
          }
        }
      }

      if (all.length > 0) {
        await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() }, false, account);
        setProducts([...all]);
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


  const handleAutoMap = async (useVision: boolean = false, target: "all" | "categories" | "attributes" = "all") => {
    if (selectedIds.size === 0) return;
    setIsAutoMapping(true);

    const selectedProducts = processed.filter((p, i) => {
      const key = getRowKey(p, i);
      const ean = p.ean || p.row?.EAN;
      const id = p.id || p.row?.id || p.row?.ItemID;
      return selectedIds.has(key) || (ean && selectedIds.has(String(ean))) || (id && selectedIds.has(String(id)));
    });

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

      const cat = mappings[ean]?.category || p.category_id || (p.row && p.row.category_id) || "";

      return {
        ean,
        title: row.Artikelbeschreibung || p.name || "",
        description: row.Beschreibung || "",
        specifics: specificsStr,
        gallery_url: row.GalleryURL || p.img || p.image_url || "",
        picture_urls: row.PictureURLs || row.PictureURL || "",
        category_id: cat
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
                  ...next[r.ean],
                  category: r.category_id || r.category_name || next[r.ean]?.category || "",
                  category_name: r.category_name || next[r.ean]?.category_name || "",
                  status: statusStr
                };
              }
            });
            return next;
          });
        } else {
          const errorCode = res.headers.get("x-error-code");
          const errorBody = await res.json().catch(() => ({}));
          const errorMsg = errorBody.detail || t("workspace.toast_ai_mapping_error");
          if (errorCode === "OPENAI_API_KEY_MISSING") {
            toast.error(t("product_drawer.toast_openai_missing_title"), {
              id: "openai-error-toast",
              description: t("product_drawer.toast_openai_missing_desc"),
              duration: 7000,
              action: {
                label: t("product_drawer.toast_openai_action_settings", { defaultValue: "Настройки" }),
                onClick: () => { window.location.href = "/dashboard/settings"; }
              }
            });
          } else if (errorCode === "OPENAI_API_ERROR") {
            showOpenAiErrorToast(String(errorMsg), t);
          } else {
            toast.error(String(errorMsg), { id: "openai-error-toast" });
          }
          isAutoMappingCancelled.current = true; // stop the loop
          break;
        }

        processedCount += batch.length;
        setTaskProgress(prev => prev ? { ...prev, current: processedCount } : null);
      }

      if (!isAutoMappingCancelled.current && payloadProducts.length > 0) {
        toast.success(t("workspace.toast_ai_done", { count: processedCount, defaultValue: `ИИ обработка завершена (${processedCount} товаров)` }));
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
    // Only sync from Aftercool if products are not loaded or missing names
    const needsSync = products.length === 0 || products.some(p => !(p.name || (p.row && p.row.Artikelbeschreibung)));
    if (needsSync) {
      setTaskProgress({ title: t("workspace.task_sync_metadata"), isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
      try {
        await fetchWithAuth(`/aftercool/sync-metadata/${factoryId}?account=${account}&dataset=lister`, { method: "POST" });
      } catch (e) {
        console.warn("Metadata sync failed, exporting with available data", e);
      }
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

  // Client-side processing (in-place index caching, zero object allocations)
  const enrichedProducts = useMemo(() => {
    for (let i = 0; i < products.length; i++) {
      const p = products[i];
      if (!p._searchIndex) {
        const row = p.row || {};
        const title = row.Artikelbeschreibung || p.name || "";
        const ean = String(p.ean || row.EAN || "");
        p._searchIndex = `${title} ${ean}`.toLowerCase();
      }
    }
    return products;
  }, [products]);

  const processed = useMemo(() => {
    const hasSearch = Boolean(deferredSearch.trim());
    const hasCategory = Boolean(categoryFilter.trim());
    const hasStatus = Boolean(statusFilter.trim());
    const hasSort = Boolean(sort.col);
    const hasOver70 = onlyOver70;

    if (!hasSearch && !hasCategory && !hasStatus && !hasSort && !hasOver70) {
      return enrichedProducts;
    }

    let arr = enrichedProducts;
    if (hasOver70) {
      arr = arr.filter(p => {
        const ean = p.ean || (p.row || {}).EAN || "";
        const mapData = mappings[ean];
        const rawTitle = (mapData?.product_name || "").trim();
        const title = (rawTitle && rawTitle !== "Без названия" && rawTitle.toLowerCase() !== "null")
          ? rawTitle
          : (p.row?.Artikelbeschreibung || p.name || "");
        return Boolean(title && title.length > 70);
      });
    }
    if (hasSearch) {
      const q = deferredSearch.trim().toLowerCase();
      arr = arr.filter(p => {
        if (p._searchIndex && p._searchIndex.includes(q)) return true;
        const ean = p.ean || (p.row || {}).EAN;
        const mappedTitle = mappings[ean]?.product_name;
        return !!(mappedTitle && mappedTitle.toLowerCase().includes(q));
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
      const q = statusFilter.toLowerCase().trim();
      arr = arr.filter(p => {
        const ean = p.ean || (p.row || {}).EAN || "";
        const mapData = mappings[ean];
        const stKey = getProductStatusKey(p, mapData);
        if (q === "unmapped") return stKey === "unmapped";
        if (q === "need") return stKey === "need";
        if (q === "ready") return stKey === "ready";
        if (q === "error") return stKey === "error";
        if (q === "published") return stKey === "published";
        if (q === "in progress" || q === "processing") return stKey === "in progress";
        return stKey.includes(q);
      });
    }
    if (sort.col) {
      const isAsc = sort.dir === "asc";
      if (sort.col === "price") {
        const mapped = arr.map((item) => ({
          item,
          price: getItemNumericPrice(item, mappings)
        }));

        mapped.sort((a, b) => {
          const pA = a.price;
          const pB = b.price;
          const hasA = pA > 0;
          const hasB = pB > 0;

          if (!hasA && !hasB) return 0;
          if (!hasA) return 1;
          if (!hasB) return -1;

          return isAsc ? pA - pB : pB - pA;
        });

        arr = mapped.map((m) => m.item);
      } else {
        const mapped = arr.map((item) => {
          let key = "";
          const row = item.row || {};
          const ean = item.ean || row.EAN || "";
          if (sort.col === "name") {
            key = row.Artikelbeschreibung || item.name || "";
          } else if (sort.col === "category") {
            key = mappings[ean]?.category_name || mappings[ean]?.category || "";
          } else if (sort.col === "status") {
            const ean = item.ean || row.EAN || "";
            const mapData = mappings[ean];
            key = getProductStatusKey(item, mapData);
          } else {
            key = ean;
          }
          return { item, key };
        });

        mapped.sort((a, b) => {
          if (a.key === b.key) return 0;
          if (!a.key) return 1;
          if (!b.key) return -1;
          const cmp = deCollator.compare(a.key, b.key);
          return isAsc ? cmp : -cmp;
        });

        arr = mapped.map((m) => m.item);
      }
    }
    return arr;
  }, [enrichedProducts, deferredSearch, sort, categoryFilter, statusFilter, onlyOver70, mappings]);

  // Virtualizer with lean overscan for max FPS
  const rowVirtualizer = useVirtualizer({
    count: processed.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100, // 100px row height for larger images

    overscan: 4,
  });

  // Keep refs in sync
  processedRef.current = processed;
  selectedIdsRef.current = selectedIds;

  const toggleSort = (col: string) => {
    startTransition(() => {
      setSort(prev => ({ col, dir: prev.col === col && prev.dir === "asc" ? "desc" : "asc" }));
    });
    parentRef.current?.scrollTo({ top: 0 });
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

  const handleInlineCategorySaved = useCallback((ean: string, categoryId: string, categoryName: string) => {
    setMappings(prev => {
      const oldMapping = prev[ean] || { category: "", status: "Not Mapped" };
      const isChanged = oldMapping.category !== categoryId;
      return {
        ...prev,
        [ean]: {
          ...oldMapping,
          category: categoryId,
          category_name: categoryName,
          status: isChanged ? "Need Attributes" : oldMapping.status
        }
      };
    });
  }, []);

  const handleInlineTitleSaved = useCallback((ean: string, newTitle: string) => {
    startTransition(() => {
      setMappings(prev => {
        const oldMapping = prev[ean];
        if (oldMapping?.product_name === newTitle) return prev;

        const prodList = productsRef.current;
        const p = prodList.find(prod => (prod.ean || prod.row?.EAN) === ean);
        const fallbackCat = p?.category_id || (p?.row && p?.row?.category_id) || p?.category || "";
        const fallbackCatName = p?.category_name || (p?.row && p?.row?.category_name) || fallbackCat;

        const finalCategory = oldMapping?.category || fallbackCat || "";
        const finalCategoryName = oldMapping?.category_name || fallbackCatName || "";
        const finalStatus = oldMapping?.status || (finalCategory ? "Need Attributes" : "Not Mapped");

        return {
          ...prev,
          [ean]: {
            ...(oldMapping || {}),
            category: finalCategory,
            category_name: finalCategoryName,
            status: finalStatus,
            product_name: newTitle,
            has_ai_title: true
          }
        };
      });
    });
    // Mutate in-place without replacing the products array reference to avoid
    // re-triggering expensive O(N log N) sorting and full-table re-renders.
    setProducts(prev => {
      const target = prev.find(p => (p.ean || p.row?.EAN) === ean);
      if (target) {
        target.name = newTitle;
        if (target.row) {
          target.row.Artikelbeschreibung = newTitle;
        }
        if (target._searchIndex) {
          target._searchIndex = `${newTitle} ${ean}`.toLowerCase();
        }
      }
      return prev;
    });
  }, []);

  const handleCloseDrawer = useCallback(() => {
    setActiveProduct(null);
  }, []);

  const handleSaveDrawer = useCallback((
    ean: string,
    cat: string,
    catName: string,
    attrs: Record<string, string>,
    status: string,
    savedName?: string,
    savedPrice?: string
  ) => {
    setMappings(prev => {
      const old = prev[ean] || { category: "", status: "Not Mapped" };
      const finalName = (savedName && savedName.trim()) ? savedName.trim() : old.product_name;
      const finalPrice = (savedPrice && savedPrice.trim()) ? savedPrice.trim() : old.price;
      return {
        ...prev,
        [ean]: {
          ...old,
          category: cat,
          category_name: catName,
          status: status || "Need Attributes",
          ...(finalName ? { product_name: finalName } : {}),
          ...(finalPrice ? { price: finalPrice } : {})
        }
      };
    });
    if (savedName) {
      setProducts(prev => {
        const idx = prev.findIndex(p => (p.ean || p.row?.EAN) === ean);
        if (idx === -1) return prev;
        const target = prev[idx];
        if ((target.row || {}).Artikelbeschreibung === savedName) return prev;
        const copy = [...prev];
        copy[idx] = {
          ...target,
          name: savedName,
          row: {
            ...(target.row || {}),
            Artikelbeschreibung: savedName
          }
        };
        return copy;
      });
    }
  }, []);

  const handleAiTitlesUpdated = useCallback((updated: Record<string, string>) => {
    const eans = Object.keys(updated);
    if (eans.length === 0) return;

    startTransition(() => {
      setMappings(prev => {
        const next = { ...prev };
        const prodList = productsRef.current;
        for (const [ean, newTitle] of Object.entries(updated)) {
          const oldMapping = prev[ean];
          const p = prodList.find(prod => (prod.ean || prod.row?.EAN) === ean);
          const fallbackCat = p?.category_id || (p?.row && p?.row?.category_id) || p?.category || "";
          const fallbackCatName = p?.category_name || (p?.row && p?.row?.category_name) || fallbackCat;

          const finalCategory = oldMapping?.category || fallbackCat || "";
          const finalCategoryName = oldMapping?.category_name || fallbackCatName || "";
          const finalStatus = oldMapping?.status || (finalCategory ? "Need Attributes" : "Not Mapped");

          next[ean] = {
            ...(oldMapping || {}),
            category: finalCategory,
            category_name: finalCategoryName,
            status: finalStatus,
            product_name: newTitle,
            has_ai_title: true
          };
        }
        return next;
      });
    });

    setProducts(prev => {
      const eanMap = new Map(Object.entries(updated));
      for (const p of prev) {
        const pEan = p.ean || p.row?.EAN;
        if (pEan && eanMap.has(pEan)) {
          const newTitle = eanMap.get(pEan)!;
          p.name = newTitle;
          if (p.row) {
            p.row.Artikelbeschreibung = newTitle;
          }
          if (p._searchIndex) {
            p._searchIndex = `${newTitle} ${pEan}`.toLowerCase();
          }
        }
      }
      return prev;
    });
  }, []);

  const handleInlinePriceSaved = useCallback((ean: string, newPrice: string) => {
    startTransition(() => {
      setMappings(prev => {
        const oldMapping = prev[ean] || { category: "", status: "Not Mapped" };
        if (oldMapping.price === newPrice) return prev;
        return {
          ...prev,
          [ean]: {
            ...oldMapping,
            price: newPrice
          }
        };
      });
    });
    // Mutate in-place without replacing the products array reference
    setProducts(prev => {
      const target = prev.find(p => (p.ean || p.row?.EAN) === ean);
      if (target && target.row) {
        target.row.Startpreis = newPrice;
        target.row.SofortkaufenPreis = newPrice;
      }
      return prev;
    });
  }, []);

  const handlePricesUpdated = useCallback((updated: Record<string, string>) => {
    const eans = Object.keys(updated);
    if (eans.length === 0) return;

    startTransition(() => {
      setMappings(prev => {
        const next = { ...prev };
        for (const [ean, newPrice] of Object.entries(updated)) {
          const oldMapping = prev[ean] || { category: "", status: "Not Mapped" };
          next[ean] = {
            ...oldMapping,
            price: newPrice,
            has_manual_price: true,
          };
        }
        return next;
      });
    });

    setProducts(prev => {
      const eanMap = new Map(Object.entries(updated));
      for (const p of prev) {
        const pEan = p.ean || p.row?.EAN;
        if (pEan && eanMap.has(pEan)) {
          const newPrice = eanMap.get(pEan)!;
          p.price = newPrice;
          if (p.row) {
            p.row.Startpreis = newPrice;
            p.row.SofortkaufenPreis = newPrice;
          }
        }
      }
      return prev;
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
      if (isDragging.current) {
        setTimeout(() => {
          isDragging.current = false;
        }, 50);
      } else {
        isDragging.current = false;
      }
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


  // ── COMBINED SINGLE-PASS CATALOG STATS (75% FASTER) ──
  const { dataQualityStats, over70Count, xlAlreadyGeneratedCount } = useMemo(() => {
    const counts = { ready: 0, needAttrs: 0, error: 0, unmapped: 0, published: 0 };
    let over70 = 0;
    let xlGen = 0;
    const isJv = account === "JV";

    for (let i = 0; i < products.length; i++) {
      const p = products[i];
      const ean = p.ean || (p.row || {}).EAN || "";
      const mapData = mappings[ean];

      // 1. Data quality stats
      const stKey = getProductStatusKey(p, mapData);
      if (stKey === "published") counts.published++;
      else if (stKey === "ready") counts.ready++;
      else if (stKey === "need") counts.needAttrs++;
      else if (stKey === "error") counts.error++;
      else counts.unmapped++;

      // 2. Title length and tokens
      const rawTitle = (mapData?.product_name || "").trim();
      const title = (rawTitle && rawTitle !== "Без названия" && rawTitle.toLowerCase() !== "null")
        ? rawTitle
        : (p.row?.Artikelbeschreibung || p.name || "");
      if (title && title.length > 70) {
        over70++;
      }
      if (!isJv && mapData?.has_ai_title) {
        xlGen++;
      }
    }
    return { dataQualityStats: counts, over70Count: over70, xlAlreadyGeneratedCount: xlGen };
  }, [products, mappings, account]);

  const xlNeedCount = useMemo(() => {
    return Math.max(0, products.length - xlAlreadyGeneratedCount);
  }, [products.length, xlAlreadyGeneratedCount]);

  // ── MEMOIZED CATEGORY OPTIONS ──
  const categoryOptionsRef = useRef<string[]>([]);
  const categoryOptions = useMemo(() => {
    const cats = new Set<string>();
    for (const m of Object.values(mappings)) {
      const c = m.category_name || m.category;
      if (c) cats.add(c);
    }
    const newArr = Array.from(cats).sort();
    const prevArr = categoryOptionsRef.current;
    if (prevArr.length === newArr.length && prevArr.every((v, i) => v === newArr[i])) {
      return prevArr;
    }
    categoryOptionsRef.current = newArr;
    return newArr;
  }, [mappings]);

  // ── LAZY-EVALUATED SELECTION METRICS (ZERO OVERHEAD DURING DRAG / CHECKBOX CLICK) ──
  const selectedEansArray = useMemo(() => {
    if (selectedIds.size === 0) return [];
    if (!isAiTitleModalOpen && !isPriceEndingModalOpen && !isMassEditOpen && !isQueueModalOpen) return [];
    const eans: string[] = [];
    for (let i = 0; i < processed.length; i++) {
      if (selectedIds.has(getRowKey(processed[i], i))) {
        const e = processed[i].ean || processed[i].row?.EAN;
        if (e) eans.push(e);
      }
    }
    return eans;
  }, [isAiTitleModalOpen, isPriceEndingModalOpen, isMassEditOpen, isQueueModalOpen, selectedIds, processed]);

  const productTitlesMap = useMemo(() => {
    if (!isAiTitleModalOpen) return {};
    const map: Record<string, string> = {};
    for (let i = 0; i < products.length; i++) {
      const p = products[i];
      const ean = p.ean || p.row?.EAN;
      if (!ean) continue;
      const mapData = mappings[ean];
      const rawTitle = (mapData?.product_name || "").trim();
      const title = (rawTitle && rawTitle !== "Без названия" && rawTitle.toLowerCase() !== "null")
        ? rawTitle
        : (p.row?.Artikelbeschreibung || p.name || "");
      if (title && title.trim()) {
        map[ean] = title.trim();
      }
    }
    return map;
  }, [isAiTitleModalOpen, products, mappings]);

  const selectedOver70Count = useMemo(() => {
    if (!isAiTitleModalOpen || selectedIds.size === 0) return 0;
    let count = 0;
    for (let i = 0; i < processed.length; i++) {
      if (selectedIds.has(getRowKey(processed[i], i))) {
        const p = processed[i];
        const ean = p.ean || p.row?.EAN || "";
        const mapData = mappings[ean];
        const rawTitle = (mapData?.product_name || "").trim();
        const title = (rawTitle && rawTitle !== "Без названия" && rawTitle.toLowerCase() !== "null")
          ? rawTitle
          : (p.row?.Artikelbeschreibung || p.name || "");
        if (title && title.length > 70) {
          count++;
        }
      }
    }
    return count;
  }, [isAiTitleModalOpen, selectedIds, processed, mappings]);

  const selectedXlAlreadyGeneratedCount = useMemo(() => {
    if (!isAiTitleModalOpen || selectedIds.size === 0) return 0;
    let count = 0;
    for (let i = 0; i < processed.length; i++) {
      if (selectedIds.has(getRowKey(processed[i], i))) {
        const p = processed[i];
        const ean = p.ean || p.row?.EAN || "";
        const mapData = mappings[ean];
        if (mapData?.has_ai_title) {
          count++;
        }
      }
    }
    return count;
  }, [isAiTitleModalOpen, selectedIds, processed, mappings]);

  const selectedXlNeedCount = useMemo(() => {
    if (!isAiTitleModalOpen || selectedIds.size === 0) return 0;
    return Math.max(0, selectedEansArray.length - selectedXlAlreadyGeneratedCount);
  }, [isAiTitleModalOpen, selectedIds.size, selectedEansArray.length, selectedXlAlreadyGeneratedCount]);

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
              disabled={loading || isBackgroundLoading}
              variant="outline"
              size="sm"
              className="gap-1.5 h-9 rounded-lg border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium hover:bg-slate-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading || isBackgroundLoading ? "animate-spin" : ""}`} />
              <span>{t("common.refresh")}</span>
            </Button>
          </div>
        </div>
      </div>

      {/* ── COMPACT SEGMENTED STATUS STRIP ── */}
      <div className="bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 shadow-xs divide-y sm:divide-y-0 sm:divide-x divide-slate-100 dark:divide-slate-800 grid grid-cols-2 sm:grid-cols-6">
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
            "p-3 text-left transition-colors",
            statusFilter === "unmapped" ? "bg-slate-100/80 dark:bg-slate-850" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">{t("workspace.unmapped")}</span>
          <span className="text-lg font-bold text-slate-500 dark:text-slate-400 tracking-tight mt-0.5 block">
            {dataQualityStats.unmapped.toLocaleString()}
          </span>
        </button>

        <button
          type="button"
          onClick={() => setStatusFilter(statusFilter === "published" ? "" : "published")}
          className={cn(
            "p-3 text-left transition-colors",
            statusFilter === "published" ? "bg-indigo-50/80 dark:bg-indigo-950/30" : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
          )}
        >
          <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">Опубликованы</span>
          <span className="text-lg font-bold text-indigo-600 dark:text-indigo-400 tracking-tight mt-0.5 block">
            {dataQualityStats.published.toLocaleString()}
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
            <Button
              size="sm"
              variant="outline"
              onClick={() => setIsQueueModalOpen(true)}
              className="h-8 text-xs border-indigo-200 text-indigo-700 bg-indigo-50/50 hover:bg-indigo-50 dark:border-indigo-800/60 dark:text-indigo-300 gap-1 font-semibold"
              title="Посмотреть список товаров этого плана"
            >
              <Layers className="w-3.5 h-3.5" />
              <span>Список товаров</span>
            </Button>

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

          {/* Drip Feed Queue & History */}
          <Button
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 border-slate-200 dark:border-slate-800 bg-white hover:bg-slate-50 dark:bg-card dark:hover:bg-slate-800/60 text-slate-700 dark:text-slate-200 font-semibold text-xs"
            onClick={() => setIsQueueModalOpen(true)}
            title="Посмотреть товары в очереди и историю выгрузок Drip Feed"
          >
            <Layers className="w-3.5 h-3.5 text-slate-500 dark:text-slate-400" />
            <span>Очередь Drip Feed</span>
            {activePlan ? (
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" title="Активный план выполняется" />
            ) : hasAnyPlan ? (
              <span className="w-1.5 h-1.5 rounded-full bg-slate-400" title="Есть завершенные планы" />
            ) : null}
          </Button>

          {/* Cross-Account Transfer */}
          <Button
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 border-purple-200 dark:border-purple-900/60 bg-purple-50/50 hover:bg-purple-50 dark:bg-purple-950/30 dark:hover:bg-purple-950/50 text-purple-700 dark:text-purple-300 font-semibold text-xs"
            onClick={() => handleTransferToAccount()}
            disabled={products.length === 0 || isTransferring}
            title={`Скопировать категории и атрибуты всех товаров в ${account === "JV" ? "XL" : "JV"} с адаптацией брендинга`}
          >
            {isTransferring ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-purple-600 dark:text-purple-400" />
            ) : (
              <ArrowRightLeft className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
            )}
            <span>Скопировать в {account === "JV" ? "XL" : "JV"}</span>
          </Button>

          {/* AI Title Generation */}
          <Button
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 border-amber-200 dark:border-amber-900/60 bg-amber-50/50 hover:bg-amber-50 dark:bg-amber-950/30 dark:hover:bg-amber-950/50 text-amber-700 dark:text-amber-300 font-semibold text-xs"
            onClick={() => setIsAiTitleModalOpen(true)}
            disabled={products.length === 0}
            title={
              account === "JV"
                ? "Сгенерировать естественные названия до 70 символов для товаров длиннее 70 символов"
                : "Сгенерировать новые названия до 70 символов для всех товаров"
            }
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
            <span>
              {selectedIds.size > 0
                ? `ИИ-название (выбрано ${selectedIds.size})`
                : account === "JV"
                ? "ИИ-название (>70 симв.)"
                : "ИИ-название (все товары)"}
            </span>
            {account === "JV" && selectedIds.size === 0 && over70Count > 0 && (
              <span className="px-1.5 py-0.2 text-[10px] bg-amber-200/80 dark:bg-amber-900 text-amber-900 dark:text-amber-100 rounded-full font-mono">
                {over70Count}
              </span>
            )}
            {account === "XL" && selectedIds.size === 0 && (
              <span
                className={cn(
                  "px-1.5 py-0.2 text-[10px] rounded-full font-mono font-semibold",
                  xlNeedCount > 0
                    ? "bg-amber-200/80 dark:bg-amber-900 text-amber-900 dark:text-amber-100"
                    : "bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-300/60 dark:border-emerald-800/60"
                )}
                title={xlNeedCount > 0 ? `Осталось сгенерировать: ${xlNeedCount} товаров` : "Все товары фабрики уже имеют готовые названия до 70 симв."}
              >
                {xlNeedCount > 0 ? xlNeedCount : "✓"}
              </span>
            )}
          </Button>

          {/* Price Ending ,99 Button */}
          <Button
            size="sm"
            variant="outline"
            className="h-9 gap-1.5 border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/50 hover:bg-emerald-50 dark:bg-emerald-950/30 dark:hover:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 font-semibold text-xs cursor-pointer shadow-xs"
            onClick={() => setIsPriceEndingModalOpen(true)}
            disabled={products.length === 0}
            title={
              selectedIds.size > 0
                ? `Применить окончание цены (,99) к ${selectedIds.size} выбранным товарам`
                : "Применить окончание цены (,99) ко всем товарам фабрики"
            }
          >
            <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">,99</span>
            <span>
              {selectedIds.size > 0
                ? `(выбрано ${selectedIds.size})`
                : "(все товары)"}
            </span>
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
          <div className="flex-1 px-4 min-w-0 flex items-center justify-between gap-2">
            <span
              className="cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors flex items-center gap-1 font-semibold truncate"
              onClick={() => toggleSort("name")}
            >
              {t("workspace.th_name")}
              {sort.col === "name" && (
                <span className="text-indigo-600 dark:text-indigo-400 font-bold">
                  {sort.dir === "asc" ? "↑" : "↓"}
                </span>
              )}
            </span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOnlyOver70((prev) => !prev);
                parentRef.current?.scrollTo({ top: 0 });
              }}
              title={
                onlyOver70
                  ? "Сбросить фильтр: показать все товары"
                  : "Показать только товары с длиной названия более 70 символов"
              }
              className={cn(
                "h-6 px-2 rounded-md text-[11px] font-mono font-semibold border transition-all flex items-center gap-1 select-none shrink-0 cursor-pointer",
                onlyOver70
                  ? "bg-rose-500 text-white border-rose-600 dark:bg-rose-600 dark:border-rose-500 shadow-xs hover:bg-rose-600 dark:hover:bg-rose-700"
                  : "bg-slate-100 hover:bg-slate-200/80 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-700/80"
              )}
            >
              <span>&gt;70</span>
              {over70Count > 0 && (
                <span
                  className={cn(
                    "px-1.5 py-0.2 text-[10px] rounded-full font-mono font-bold leading-none",
                    onlyOver70
                      ? "bg-rose-700 text-white"
                      : "bg-rose-100 text-rose-700 dark:bg-rose-950/80 dark:text-rose-300 border border-rose-200 dark:border-rose-800/60"
                  )}
                >
                  {over70Count}
                </span>
              )}
              {onlyOver70 && <span className="text-[10px] ml-0.5 opacity-80 leading-none">✕</span>}
            </button>
          </div>
          <div className="w-48 px-4 shrink-0 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors" onClick={() => toggleSort("ean")}>
            {t("workspace.th_ean")}
          </div>
          <div className="w-28 px-3 shrink-0 flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span
                className="cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors flex items-center gap-1 font-semibold"
                onClick={() => toggleSort("price")}
              >
                {t("workspace.th_price")}
                {sort.col === "price" && (
                  <span className="text-indigo-600 dark:text-indigo-400 font-bold">
                    {sort.dir === "asc" ? "↑" : "↓"}
                  </span>
                )}
              </span>
            </div>
            <div className="flex items-center gap-1 h-7">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  startTransition(() => {
                    setSort({ col: "price", dir: "asc" });
                  });
                  parentRef.current?.scrollTo({ top: 0 });
                }}
                title="По возрастанию (дешевые сверху)"
                className={cn(
                  "flex-1 h-full rounded text-[10px] font-bold border transition-colors flex items-center justify-center gap-0.5",
                  sort.col === "price" && sort.dir === "asc"
                    ? "bg-indigo-50 border-indigo-300 text-indigo-700 dark:bg-indigo-950/60 dark:border-indigo-700 dark:text-indigo-300 shadow-2xs"
                    : "border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                )}
              >
                ↑ Min
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  startTransition(() => {
                    setSort({ col: "price", dir: "desc" });
                  });
                  parentRef.current?.scrollTo({ top: 0 });
                }}
                title="По убыванию (дорогие сверху)"
                className={cn(
                  "flex-1 h-full rounded text-[10px] font-bold border transition-colors flex items-center justify-center gap-0.5",
                  sort.col === "price" && sort.dir === "desc"
                    ? "bg-indigo-50 border-indigo-300 text-indigo-700 dark:bg-indigo-950/60 dark:border-indigo-700 dark:text-indigo-300 shadow-2xs"
                    : "border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                )}
              >
                ↓ Max
              </button>
            </div>
          </div>
          <div className="w-64 px-4 shrink-0 flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <span className="cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors" onClick={() => toggleSort("category")}>
                {t("workspace.th_category")}
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  toggleSort("category");
                }}
                className={cn(
                  "px-1.5 py-0.5 rounded text-[10px] font-bold border transition-colors flex items-center gap-0.5",
                  sort.col === "category"
                    ? "bg-indigo-50 border-indigo-300 text-indigo-700 dark:bg-indigo-950/60 dark:border-indigo-700 dark:text-indigo-300"
                    : "border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                )}
                title="Сортировка категорий по алфавиту: A-Z"
              >
                A-Z {sort.col === "category" ? (sort.dir === "asc" ? "↑" : "↓") : ""}
              </button>
            </div>
            <AttributeCombobox
              name="Filter"
              value={categoryFilter}
              onChange={(val) => setCategoryFilter(val)}
              options={categoryOptions}
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
              <option value="published">{t("workspace.status_published")}</option>
              <option value="in progress">{t("workspace.status_in_progress")}</option>
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
                isDraggingRef={isDragging}
                handleRowMouseDown={handleRowMouseDown}
                toggleSelection={toggleSelection}
                setActiveProduct={setActiveProduct}
                onImageHover={handleImageHover}
                onImageLeave={handleImageLeave}
                isMirrored={account === "XL"}
                factoryId={factoryId}
                account={account}
                onCategorySaved={handleInlineCategorySaved}
                onTitleSaved={handleInlineTitleSaved}
                onPriceSaved={handleInlinePriceSaved}
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
                    {t("workspace.under_loading_count", { current: streamedLoadedCount || products.length, total: totalCatalogCount || streamedLoadedCount || products.length })}
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
                    style={{ width: `${Math.min(100, Math.round(((streamedLoadedCount || products.length) / totalCatalogCount) * 100))}%` }}
                  />
                </div>
              )}
              <span className="text-xs font-bold text-indigo-600 dark:text-indigo-400 tabular-nums">
                {totalCatalogCount && totalCatalogCount > 0
                  ? `${Math.round(((streamedLoadedCount || products.length) / totalCatalogCount) * 100)}%`
                  : t("common.loading")}
              </span>
            </div>
          </div>
        )}

        {/* ── CONTEXTUAL SELECTION BAR ── */}
        {(selectedIds.size > 0 || taskProgress) && (
          <div
            data-no-drag
            onMouseDown={(e) => e.stopPropagation()}
            className="selection-popup fixed z-50 flex flex-col justify-center bg-white dark:bg-card text-slate-900 dark:text-white px-4 py-3 rounded-xl border border-slate-200/90 dark:border-slate-800 shadow-2xl shadow-indigo-950/10 animate-in fade-in slide-in-from-bottom-4 duration-200 bottom-8 left-1/2 -translate-x-1/2 min-w-[380px]"
          >
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
                  disabled={isAutoMapping || isSyncingOkb || isApplyingDefaults}
                  className="h-8 px-2.5 text-xs font-medium border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/60 hover:bg-indigo-50 dark:bg-indigo-950/40 dark:hover:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 gap-1.5"
                >
                  <Layers className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
                  <span>Mass Edit</span>
                </Button>

                {/* Apply Defaults */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => handleApplyDefaultsBulk()}
                  disabled={isAutoMapping || isSyncingOkb || isApplyingDefaults}
                  className="h-8 px-2.5 text-xs font-medium border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/60 hover:bg-emerald-50 dark:bg-emerald-950/40 dark:hover:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 gap-1.5"
                  title="Заполнить стандартные константы и брендовые атрибуты для выделенных товаров"
                >
                  {isApplyingDefaults ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-600 dark:text-emerald-400" />
                  ) : (
                    <Sparkles className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                  )}
                  <span>⚡ Defaults</span>
                </Button>

                {/* AI Titles for selection */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsAiTitleModalOpen(true)}
                  className="h-8 px-2.5 text-xs font-medium border-amber-200 dark:border-amber-900/60 bg-amber-50/60 hover:bg-amber-50 dark:bg-amber-950/40 dark:hover:bg-amber-950/60 text-amber-700 dark:text-amber-300 gap-1.5"
                  title={`Сгенерировать ИИ-названия для ${selectedIds.size} выбранных товаров`}
                >
                  <Sparkles className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
                  <span>
                    ИИ-название ({selectedIds.size})
                  </span>
                </Button>

                {/* Price Ending ,99 for selection */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsPriceEndingModalOpen(true)}
                  className="h-8 px-2.5 text-xs font-medium border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/60 hover:bg-emerald-50 dark:bg-emerald-950/40 dark:hover:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 gap-1.5 cursor-pointer"
                  title={`Применить окончание цены (,99) для ${selectedIds.size} выбранных товаров`}
                >
                  <span className="font-mono font-bold">,99</span>
                  <span>({selectedIds.size})</span>
                </Button>

                {/* Transfer to other account */}
                <Button
                  variant="outline"
                  size="sm"
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    const selectedEans = processed
                      .filter((p, i) => {
                        const key = getRowKey(p, i);
                        const ean = p.ean || p.row?.EAN;
                        const id = p.id || p.row?.id || p.row?.ItemID;
                        return selectedIds.has(key) || (ean && selectedIds.has(String(ean))) || (id && selectedIds.has(String(id)));
                      })
                      .map(p => p.ean || p.row?.EAN || "")
                      .filter(Boolean);
                    handleTransferToAccount(selectedEans.length > 0 ? selectedEans : undefined);
                  }}
                  disabled={isTransferring}
                  className="h-8 px-2.5 text-xs font-medium border-purple-200 dark:border-purple-900/60 bg-purple-50/60 hover:bg-purple-50 dark:bg-purple-950/40 dark:hover:bg-purple-950/60 text-purple-700 dark:text-purple-300 gap-1.5"
                  title={`Скопировать ${selectedIds.size} выбранных товаров в аккаунт ${account === "JV" ? "XL" : "JV"} с адаптацией брендинга`}
                >
                  {isTransferring ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-purple-600 dark:text-purple-400" />
                  ) : (
                    <ArrowRightLeft className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
                  )}
                  <span>В {account === "JV" ? "XL" : "JV"}</span>
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
                    handleAutoMap(true, "attributes");
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

      {activeProduct && (() => {
        const row = activeProduct.row || {};
        const activeEan = activeProduct.ean || row.EAN || activeProduct.product_ean || "";
        const currentMapping = activeEan ? mappings[activeEan] : null;
        return (
          <ProductDrawer
            product={activeProduct}
            factoryId={factoryId}
            account={account}
            initialCategory={currentMapping?.category || ""}
            initialCategoryName={currentMapping?.category_name || currentMapping?.category || ""}
            initialProductName={currentMapping?.product_name || ""}
            initialPrice={currentMapping?.price || ""}
            onClose={handleCloseDrawer}
            onTitleSaved={handleInlineTitleSaved}
            onSave={handleSaveDrawer}
          />
        );
      })()}

      {/* ── DRIP SCHEDULE MODAL ── */}
      {isDripModalOpen && (
        <DripScheduleModal
          factoryId={factoryId}
          factoryName={factoryName}
          isOpen={isDripModalOpen}
          onClose={() => setIsDripModalOpen(false)}
          onSuccess={() => {
            fetchActivePlan();
            fetchMappings();
          }}
          selectedEans={selectedEansArray.length > 0 ? selectedEansArray : undefined}
        />
      )}

      {/* ── DRIP QUEUE & HISTORY MODAL ── */}
      {isQueueModalOpen && (
        <DripQueueModal
          factoryId={factoryId}
          factoryName={factoryName}
          isOpen={isQueueModalOpen}
          onClose={() => setIsQueueModalOpen(false)}
          catalogProducts={products}
          onPlanUpdated={() => {
            fetchActivePlan();
            fetchMappings();
          }}
        />
      )}

      {/* ── MASS EDIT DRAWER ── */}
      {isMassEditOpen && (
        <MassEditDrawer
          selectedEans={selectedEansArray}
          account={account}
          onClose={() => setIsMassEditOpen(false)}
          onSaved={() => {
            setIsMassEditOpen(false);
            fetchMappings();
          }}
        />
      )}

      {/* ── AI TITLE MODAL ── */}
      {isAiTitleModalOpen && (
        <AiTitleModal
          isOpen={isAiTitleModalOpen}
          onClose={() => setIsAiTitleModalOpen(false)}
          account={account}
          factoryId={factoryId}
          factoryName={factoryName}
          totalProductsCount={products.length}
          over70Count={over70Count}
          selectedIdsCount={selectedIds.size}
          selectedEans={selectedEansArray}
          selectedOver70Count={selectedOver70Count}
          xlAlreadyGeneratedCount={selectedIds.size > 0 ? selectedXlAlreadyGeneratedCount : xlAlreadyGeneratedCount}
          xlNeedCount={selectedIds.size > 0 ? selectedXlNeedCount : xlNeedCount}
          productTitles={productTitlesMap}
          onTitlesUpdated={handleAiTitlesUpdated}
        />
      )}

      {/* ── PRICE ENDING MODAL ── */}
      {isPriceEndingModalOpen && (
        <PriceEndingModal
          isOpen={isPriceEndingModalOpen}
          onClose={() => setIsPriceEndingModalOpen(false)}
          account={account}
          factoryId={factoryId}
          factoryName={factoryName}
          totalProductsCount={products.length}
          selectedCount={selectedIds.size}
          selectedEans={selectedEansArray}
          items={(selectedIds.size > 0
            ? processed.filter((p, i) => selectedIds.has(getRowKey(p, i)))
            : processed
          ).map((p) => ({
            ean: p.ean || (p.row && p.row.EAN) || "",
            price: mappings[p.ean || (p.row && p.row.EAN) || ""]?.price || p.price || (p.row && (p.row.Startpreis || p.row.SofortkaufenPreis)),
          }))}
          onPricesUpdated={handlePricesUpdated}
        />
      )}

      {/* ── IMAGE PREVIEW HOVER PORTAL ── */}
      <ImageHoverPreview preview={previewImage} />

    </div>
  );
}

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
import { toast } from "sonner";

import {
  Download, RefreshCw, Search, ArrowUpDown, ArrowUp, ArrowDown, Database, Wifi, X, Sparkles, Loader2, ChevronLeft, Upload, FileDown, FileArchive, Bot
} from "lucide-react";
import { useAccount } from "@/lib/useAccount";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { AttributeCombobox } from "@/components/AttributeCombobox";

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
  isDraggingCurrent, handleRowMouseDown, toggleSelection, setActiveProduct
}: {
  index: number; top: number; height: number; product: any; ean: string; rowKey: string; img: string; price: string; mapData: any;
  isSelected: boolean; isDraggingCurrent: boolean;
  handleRowMouseDown: (index: number, rowKey: string, e: React.MouseEvent) => void;
  toggleSelection: (rowKey: string) => void;
  setActiveProduct: (p: any) => void;
}) => {
  const row = product.row || {};
  return (
    <div
      data-row-index={index}
      className={cn(
        "absolute top-0 left-0 w-full flex items-center border-b border-slate-100 dark:border-slate-800/50 hover:bg-slate-50 dark:hover:bg-[#1A1D2B] cursor-pointer transition-colors text-sm select-none",
        isSelected && "bg-indigo-50/50 hover:bg-indigo-50 dark:bg-indigo-900/10 dark:hover:bg-indigo-900/20"
      )}
      style={{
        height: `${height}px`,
        transform: `translateY(${top}px)`,
      }}
      onMouseDown={(e) => handleRowMouseDown(index, rowKey, e)}
      onClick={(e) => { if (!isDraggingCurrent) setActiveProduct(product); }}
    >
      <div className="w-12 px-4" data-no-drag onClick={(e) => e.stopPropagation()}>
        <Checkbox checked={isSelected} onCheckedChange={() => toggleSelection(rowKey)} />
      </div>
      <div className="w-24 px-4">
        <div className="w-20 h-20 rounded-md border border-slate-200 bg-white flex items-center justify-center p-0.5 overflow-hidden shadow-sm">
          {img ? <img src={img} className="w-full h-full object-contain" /> : "-"}
        </div>
      </div>
      <div className="flex-1 px-4 truncate font-medium text-slate-700 dark:text-white">{row.Artikelbeschreibung || product.name || ""}</div>
      <div className="w-48 px-4 font-mono text-xs text-slate-500 dark:text-white">{ean}</div>
      <div className="w-24 px-4 font-medium text-slate-700 dark:text-white">{price !== "-" ? `${price} €` : "-"}</div>
      <div className="w-64 px-4 truncate text-slate-600 dark:text-white">
        {mapData?.category
          ? <span className={mapData.status === "Error" ? "text-slate-500 dark:text-white" : ""}>{mapData.category_name || mapData.category}</span>
          : <span className="text-slate-400 dark:text-white italic">Not mapped</span>}
      </div>
      <div className="w-40 px-4">
        {mapData?.status === "Ready" ? <Badge className="bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-900/30">Ready</Badge> :
          mapData?.status === "Need Attributes" ? <Badge className="bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-900/30">Needs Attrs</Badge> :
            mapData?.status === "Error" ? <Badge className="bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30">Error</Badge> :
              <Badge variant="outline" className="text-slate-400 dark:border-slate-700 dark:text-slate-500">Unmapped</Badge>}
      </div>
    </div>
  );
});

export default function FactoryWorkspace() {
  const params = useParams();
  const factoryId = params?.factory_id as string;
  const { account } = useAccount();

  const [products, setProducts] = useState<any[]>([]);
  const [factoryName, setFactoryName] = useState("");
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sort, setSort] = useState({ col: "", dir: "asc" });
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [popupPos, setPopupPos] = useState<{ x: number, y: number } | null>(null);

  const [activeProduct, setActiveProduct] = useState<any | null>(null);
  const [isAutoMapping, setIsAutoMapping] = useState(false);
  const [isSyncingOkb, setIsSyncingOkb] = useState(false);
  const isSyncCancelled = useRef(false);
  const isAutoMappingCancelled = useRef(false);
  const [isPublishing, setIsPublishing] = useState(false);

  const [taskProgress, setTaskProgress] = useState<{
    title: string;
    current?: number;
    total?: number;
    isIndeterminate?: boolean;
    colorClass?: string;
    onCancel?: () => void;
  } | null>(null);

  // Drag & Drop overlay state
  const [isDragOver, setIsDragOver] = useState(false);
  const [dragFileType, setDragFileType] = useState<"csv" | "zip" | "unknown" | null>(null);
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
      const mapRes = await fetchWithAuth(`/mapping/factory/${factoryId}`);
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
  }, [factoryId]);

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
      if (!confirm("Внимание! У некоторых выбранных товаров уже заполнены атрибуты. Синхронизация с OKB удалит их и заменит на данные из кэша. Продолжить?")) {
        return;
      }
    }

    setIsSyncingOkb(true);
    isSyncCancelled.current = false;
    setTaskProgress({
      title: "Загрузка данных с OKB...",
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

      try {
        const res = await fetchWithAuth("/mapping/sync-okb-bulk", {
          method: "POST",
          body: JSON.stringify({
            product_eans: chunk,
            factory_id: factoryId,
            account: account
          })
        });
        if (res.ok) {
          const data = await res.json();
          for (const r of (data.results || [])) {
            if (r.status === "success") ok++;
            else if (r.status === "not_found") notFound++;
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
        alert(`OKB Sync done. Found: ${ok}, Not Found: ${notFound}, Error: ${error}`);
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
      const cached = await getCachedFactory(factoryId);
      if (cached?.products.length) {
        setProducts(cached.products);
        setLoading(false);
      } else {
        setLoading(true);
      }

      // 2. Fetch from network if not cached or if it looks truncated (exactly 500)
      let all = cached?.products || [];
      if (!all.length || all.length === 500) {
        try {
          const res = await fetchWithAuth(`/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=10000&include_row=1`);
          if (res.ok) {
            const data = await res.json();
            all = data?.items || data || [];
            await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() });
            setProducts(all);
          }
        } catch (e) {
          console.error(e);
        } finally {
          setLoading(false);
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
    setLoading(true);
    try {
      const res = await fetchWithAuth(`/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&limit=10000&include_row=1`);
      if (res.ok) {
        const data = await res.json();
        const all = data?.items || data || [];
        await setCachedFactory({ factoryId, products: all, total: all.length, fetchedAt: Date.now() });
        setProducts(all);
        await fetchMappings();
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  const handleGlobalExport = () => {
    if (products.length === 0) return;

    // Format according to Otto Market Products V5 API (Create/Update Product Variations)
    const payload = products.map(p => {
      const ean = p.ean || (p.row || {}).EAN || "";
      const name = p.name || (p.row || {})["Artikelbeschreibung"] || "";

      const rawPrice = p.price || (p.row || {}).Startpreis || (p.row || {}).SofortkaufenPreis || "0";
      const priceStr = String(rawPrice).replace(',', '.').replace(/[^0-9.]/g, '');
      const price = parseFloat(priceStr) || 0;

      const mapping = mappings[ean] || {};
      const catName = mapping.category_name || mapping.category || "";
      const attributes = mapping.attributes || {};

      // Convert flat attributes map to arrays of strings (typical for Otto API)
      const formattedAttributes: Record<string, string[]> = {};
      for (const [key, value] of Object.entries(attributes)) {
        if (value !== null && value !== "") {
          formattedAttributes[key] = [String(value)];
        }
      }

      const row = p.row || {};
      const img1 = row.GalleryURL || row.PictureURL || "";
      const img2 = row.PictureURLs || "";
      const allImgs = [img1, ...img2.split('|')].map(u => u.trim()).filter(Boolean);
      const imageUrls = Array.from(new Set(allImgs));

      return {
        sku: ean,
        ean: ean,
        productReference: ean, // groups variations into a single product
        category: catName,
        pricing: {
          standardPrice: {
            amount: price,
            currency: "EUR"
          }
        },
        productDescription: {
          title: name,
          description: (p.row || {})["Beschreibung"] || name
        },
        media: {
          images: imageUrls.filter(u => u).map((url: string) => ({
            url: url.trim(),
            type: "IMAGE"
          }))
        },
        attributes: formattedAttributes,
        delivery: {
          type: "PARCEL",
          deliveryTime: 3
        }
      };
    });

    const jsonContent = JSON.stringify(payload, null, 2);

    const blob = new Blob([jsonContent], { type: "application/json;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `Factory_${factoryName || factoryId}_Otto_V5.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handlePublishToOtto = async () => {
    const readyProducts = processed.filter(p => mappings[p.ean || p.row?.EAN]?.status === "Ready");
    if (readyProducts.length === 0) {
      alert("No 'Ready' products available to publish.");
      return;
    }
    if (!confirm(`Are you sure you want to publish ${readyProducts.length} products to Otto?`)) {
      return;
    }

    setIsPublishing(true);
    setTaskProgress({
      title: "Publishing to Otto...",
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
      title: "Нейросеть обрабатывает товары...",
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
            toast.error("Отсутствует ключ ChatGPT", {
              id: "openai-error-toast",
              description: "Перейдите в настройки и укажите ваш OpenAI API ключ.",
              duration: 7000,
            });
          } else if (errorCode === "OPENAI_API_ERROR") {
            toast.error("Ошибка API ChatGPT", {
              id: "openai-error-toast",
              description: "Ошибка при обращении к OpenAI — проверьте баланс или лимиты.",
              duration: 7000,
            });
          } else {
            toast.error("Ошибка нейромаппинга", { id: "openai-error-toast" });
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
    setTaskProgress({ title: "Синхронизация данных товаров...", isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
    try {
      // First sync product names and prices from Aftercool so they appear in the CSV
      await fetchWithAuth(`/aftercool/sync-metadata/${factoryId}?account=${account}&dataset=lister`, { method: "POST" });
    } catch (e) {
      console.warn("Metadata sync failed, exporting with available data", e);
    }

    setTaskProgress({ title: "Экспорт категорий...", isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
    try {
      const selectedProducts = processed.filter((p, i) => selectedIds.has(getRowKey(p, i)));
      const eans = Array.from(new Set(selectedProducts.map(p => p.ean || p.row?.EAN).filter(Boolean)));
      const options: any = { method: "GET" };
      if (eans.length > 0) {
        options.method = "POST";
        options.body = JSON.stringify({ product_eans: eans });
      }

      const res = await fetchWithAuth(`/csv/export/categories/${factoryId}`, options);
      if (!res.ok) { setTimeout(() => alert("Export failed: " + res.statusText), 100); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Categories_${factoryName || factoryId}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { console.error(e); setTimeout(() => alert("Export failed."), 100); }
    finally { setTaskProgress(null); }
  };

  const handleExportAttributes = async () => {
    setTaskProgress({ title: "Экспорт атрибутов...", isIndeterminate: true, colorClass: "from-blue-500 to-indigo-500" });
    try {
      const selectedProducts = processed.filter((p, i) => selectedIds.has(getRowKey(p, i)));
      const eans = Array.from(new Set(selectedProducts.map(p => p.ean || p.row?.EAN).filter(Boolean)));
      const options: any = { method: "GET" };
      if (eans.length > 0) {
        options.method = "POST";
        options.body = JSON.stringify({ product_eans: eans });
      }

      const res = await fetchWithAuth(`/csv/export/attributes/${factoryId}`, options);
      if (!res.ok) { setTimeout(() => alert("Export failed: " + res.statusText), 100); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Attributes_${factoryName || factoryId}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { console.error(e); setTimeout(() => alert("Export failed."), 100); }
    finally { setTaskProgress(null); }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    setTaskProgress({ title: "Загрузка и обработка CSV...", isIndeterminate: true, colorClass: "from-amber-500 to-orange-500" });
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
        if (data.updated_categories) msgParts.push(`Категории: ${data.updated_categories}`);
        if (data.updated_attributes) msgParts.push(`Атрибуты: ${data.updated_attributes}`);
        if (data.skipped_foreign_eans) msgParts.push(`Пропущено (чужие EAN): ${data.skipped_foreign_eans}`);
        
        let finalAlert = "";
        if ((data.updated_categories || 0) > 0 || (data.updated_attributes || 0) > 0) {
          finalAlert = "✅ Импорт успешно завершен!\n" + msgParts.join(" | ");
        } else if ((data.skipped_foreign_eans || 0) > 0) {
          finalAlert = `⚠️ Данные не обновлены.\nВсе EAN оказались чужими (пропущено: ${data.skipped_foreign_eans}).`;
        } else {
          finalAlert = "ℹ️ Данные не обновлены (совпадают с текущими или файл пуст).";
        }

        setTaskProgress(null);
        setTimeout(() => alert(finalAlert), 100);
        await fetchMappings();
      } else {
        const err = await res.json().catch(() => ({}));
        setTaskProgress(null);
        setTimeout(() => alert("Ошибка импорта: " + (err.detail || res.statusText)), 100);
      }
    } catch (err) {
      console.error(err);
      setTaskProgress(null);
      setTimeout(() => alert("Ошибка при отправке файла."), 100);
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
        // We can't read filename on dragenter in all browsers, use type hint
        const type = fileItem.type;
        if (type === "application/zip" || type === "application/x-zip-compressed") setDragFileType("zip");
        else if (type === "text/csv" || type === "application/csv") setDragFileType("csv");
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

    if (!isZip && !isCsv) {
      alert("Неподдерживаемый формат файла. Перетяните ZIP (атрибуты) или CSV (категории).");
      return;
    }

    // ── Cross-factory protection ──
    const factoryIdInFile = file.name.match(/(?:Categories|Attributes)_([\d]+)/i)?.[1];
    if (factoryIdInFile && factoryIdInFile !== factoryId) {
      const confirmed = window.confirm(
        `⚠️ Внимание! Файл "${file.name}" принадлежит другой фабрике (ID: ${factoryIdInFile}), ` +
        `а вы сейчас находитесь в фабрике ${factoryName || factoryId} (ID: ${factoryId}).\n\n` +
        `Импорт из чужой фабрики применит данные только к товарам, которые совпадают по EAN.\n` +
        `Чужие EAN будут пропущены.\n\nПродолжить?`
      );
      if (!confirmed) return;
    }

    setIsUploading(true);
    const label = isZip ? "Импорт атрибутов из ZIP..." : "Импорт категорий из CSV...";
    setTaskProgress({ title: label, isIndeterminate: true, colorClass: isZip ? "from-purple-500 to-pink-500" : "from-amber-500 to-orange-500" });

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetchWithAuth(`/csv/import/${factoryId}`, { method: "POST", body: formData });
      if (res.ok) {
        const data = await res.json();
        const msgParts = [];
        if (data.updated_categories) msgParts.push(`Категории: ${data.updated_categories}`);
        if (data.updated_attributes) msgParts.push(`Атрибуты: ${data.updated_attributes}`);
        if (data.skipped_foreign_eans) msgParts.push(`Пропущено (чужие EAN): ${data.skipped_foreign_eans}`);
        
        let finalAlert = "";
        if ((data.updated_categories || 0) > 0 || (data.updated_attributes || 0) > 0) {
          finalAlert = "✅ Импорт успешно завершен!\n" + msgParts.join(" | ");
        } else if ((data.skipped_foreign_eans || 0) > 0) {
          finalAlert = `⚠️ Данные не обновлены.\nВсе EAN оказались чужими (пропущено: ${data.skipped_foreign_eans}).`;
        } else {
          finalAlert = "ℹ️ Данные не обновлены (совпадают с текущими или файл пуст).";
        }

        setTaskProgress(null);
        setTimeout(() => alert(finalAlert), 100);
        await fetchMappings();
      } else {
        const err = await res.json().catch(() => ({}));
        setTaskProgress(null);
        setTimeout(() => alert("❌ Ошибка импорта: " + (err.detail || res.statusText)), 100);
      }
    } catch (err) {
      console.error(err);
      setTaskProgress(null);
      setTimeout(() => alert("❌ Ошибка при отправке файла."), 100);
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

  if (loading) return <div className="p-10 text-slate-400 flex items-center gap-3"><Loader2 className="w-5 h-5 animate-spin" /> Loading products...</div>;

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
            ) : (
              <Upload className="w-14 h-14 text-indigo-400" />
            )}
            <p className="text-xl font-bold text-slate-800 dark:text-white">
              {dragFileType === "zip" ? "Отпустите для импорта атрибутов (ZIP)" :
               dragFileType === "csv" ? "Отпустите для импорта категорий (CSV)" :
               "Отпустите файл для импорта"}
            </p>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Поддерживается: <span className="font-semibold text-amber-600">CSV</span> (категории) и{" "}
              <span className="font-semibold text-purple-600">ZIP</span> (атрибуты)
            </p>
          </div>
        </div>
      )}
      {/* ── HEADER ── */}
      <div className="flex items-center justify-between gap-4">
        <Link href="/dashboard/factories">
          <Button variant="outline" size="sm" className="dark:bg-transparent dark:border-slate-800 dark:hover:bg-slate-900">
            <ChevronLeft className="w-4 h-4 mr-1" /> Назад
          </Button>
        </Link>

        <div className="flex flex-col items-center">
          <h1 className="text-xl font-bold text-slate-900 dark:text-white leading-tight">{factoryName || factoryId}</h1>
          <p className="text-xs text-slate-500 dark:text-slate-400">{products.length.toLocaleString()} товаров загружено</p>
        </div>

        <Button onClick={handleSync} disabled={loading} variant="outline" size="sm" className="dark:bg-transparent dark:border-slate-800 dark:hover:bg-slate-900">
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} /> Обновить
        </Button>
      </div>

      {/* ── DATA QUALITY STRIP ── */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "Ready", value: dataQualityStats.ready, color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-50 dark:bg-emerald-900/20", border: "border-emerald-200 dark:border-emerald-800/50", filter: "ready" },
          { label: "Need Attrs", value: dataQualityStats.needAttrs, color: "text-amber-600 dark:text-amber-400", bg: "bg-amber-50 dark:bg-amber-900/20", border: "border-amber-200 dark:border-amber-800/50", filter: "need" },
          { label: "Error", value: dataQualityStats.error, color: "text-red-600 dark:text-red-400", bg: "bg-red-50 dark:bg-red-900/20", border: "border-red-200 dark:border-red-800/50", filter: "error" },
          { label: "Unmapped", value: dataQualityStats.unmapped, color: "text-slate-500 dark:text-slate-400", bg: "bg-slate-50 dark:bg-slate-800/40", border: "border-slate-200 dark:border-slate-700/50", filter: "unmapped" },
        ].map((s) => (
          <button
            key={s.label}
            onClick={() => setStatusFilter(statusFilter === s.filter ? "" : s.filter)}
            className={`${s.bg} ${s.border} border rounded-xl p-3 text-left transition-all hover:shadow-sm hover:scale-[1.01] ${statusFilter === s.filter ? "ring-2 ring-offset-1 ring-indigo-500" : ""}`}
          >
            <p className={`text-xl font-bold ${s.color}`}>{s.value.toLocaleString()}</p>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{s.label}</p>
          </button>
        ))}
      </div>

      {/* ── UNIFIED SMART TOOLBAR ── */}
      <div className="flex items-center gap-3 bg-white dark:bg-[#0B0F19] p-3 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
        {/* Search */}
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск товаров..." className="pl-9 h-9 dark:text-white" />
        </div>

        <div className="w-px h-6 bg-slate-200 dark:bg-slate-700" />

        {/* CSV Import / Export */}
        <input type="file" ref={fileInputRef} className="hidden" accept=".csv, .zip" onChange={handleFileUpload} />
        <Button size="sm" variant="outline" className="h-9 border-slate-200 dark:border-slate-700" onClick={() => fileInputRef.current?.click()} disabled={isUploading}>
          {isUploading ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Upload className="w-4 h-4 mr-1.5" />}
          Импорт CSV
        </Button>
        <Button size="sm" variant="outline" className="h-9 border-slate-200 dark:border-slate-700" onClick={handleExportCategories} disabled={taskProgress !== null}>
          {taskProgress?.title === "Экспорт категорий..." ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <FileDown className="w-4 h-4 mr-1.5" />}
          Категории CSV
        </Button>
        <Button size="sm" variant="outline" className="h-9 border-slate-200 dark:border-slate-700" onClick={handleExportAttributes} disabled={taskProgress !== null}>
          {taskProgress?.title === "Экспорт атрибутов..." ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <FileArchive className="w-4 h-4 mr-1.5" />}
          Атрибуты ZIP
        </Button>

        <div className="w-px h-6 bg-slate-200 dark:bg-slate-700" />

        {/* Export to Otto JSON (global) */}
        <Button size="sm" variant="outline" className="h-9 border-slate-200 dark:border-slate-700" onClick={handleGlobalExport} disabled={products.length === 0}>
          <Download className="w-4 h-4 mr-1.5" /> Otto JSON
        </Button>
      </div>

      {/* Virtual Table */}
      <div
        ref={parentRef}
        className="flex-1 overflow-auto bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-xl shadow-sm relative rounded-b-none"
      >

        {/* Header */}
        <div className="sticky top-0 bg-slate-50 dark:bg-[#0B0F19] border-b border-slate-200 dark:border-slate-800 z-10 shadow-sm flex items-start text-sm font-semibold text-slate-500 dark:text-white py-3">
          <div className="w-12 px-4 mt-2"><Checkbox onCheckedChange={(c) => setSelectedIds(c ? new Set(processed.map((p, i) => getRowKey(p, i))) : new Set())} /></div>
          <div className="w-24 px-4 text-xs mt-2">IMG</div>
          <div className="flex-1 px-4 mt-2 cursor-pointer hover:text-slate-800" onClick={() => toggleSort("name")}>Product Name</div>
          <div className="w-48 px-4 mt-2 cursor-pointer hover:text-slate-800" onClick={() => toggleSort("ean")}>EAN</div>
          <div className="w-24 px-4 mt-2">Price</div>
          <div className="w-64 px-4 flex flex-col gap-1">
            <span className="cursor-pointer hover:text-slate-800" onClick={() => toggleSort("category")}>Category</span>
            <AttributeCombobox
              name="Filter"
              value={categoryFilter}
              onChange={(val) => setCategoryFilter(val)}
              options={Array.from(new Set(Object.values(mappings).map(m => m.category_name || m.category).filter(Boolean))).sort()}
              placeholder="Filter..."
              className="w-full h-7 text-xs border rounded-md px-2 pr-6 outline-none transition-all border-slate-400 dark:border-slate-700 bg-white dark:bg-[#0B0F19] text-slate-900 dark:text-slate-100 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          <div className="w-40 px-4 flex flex-col gap-1">
            <span className="cursor-pointer hover:text-slate-800" onClick={() => toggleSort("status")}>Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="h-7 text-xs px-1 py-0 border-slate-400 dark:border-slate-700 border-solid border bg-white dark:bg-[#0B0F19] rounded-md shadow-sm focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 cursor-pointer"
            >
              <option value="">All Statuses</option>
              <option value="ready">Ready (Exportable)</option>
              <option value="need">Need Attrs (Missing Data)</option>
              <option value="error">Error (Validation Failed)</option>
              <option value="unmapped">Unmapped (No Category)</option>
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
              />
            );
          })}
        </div>

        {/* ── CONTEXTUAL SELECTION BAR ── */}
        {(selectedIds.size > 0 || taskProgress) && (
          <div className="selection-popup fixed z-50 flex flex-col justify-center bg-slate-900/95 backdrop-blur-md text-white px-4 py-3 rounded-2xl border border-slate-700/50 shadow-2xl shadow-indigo-900/30 animate-in fade-in slide-in-from-bottom-4 duration-200 bottom-8 left-1/2 -translate-x-1/2 min-w-[380px]">
            {taskProgress ? (
              /* ── PROGRESS STATE ── */
              <div className="w-full flex flex-col gap-2.5">
                <div className="flex justify-between items-center">
                  <span className="flex items-center gap-2 text-sm font-medium text-slate-200">
                    <Loader2 className={`w-4 h-4 animate-spin ${taskProgress.colorClass ? taskProgress.colorClass.split(" ")[0].replace("from-", "text-") : "text-indigo-400"}`} />
                    {taskProgress.title}
                  </span>
                  <div className="flex items-center gap-3">
                    {!taskProgress.isIndeterminate && taskProgress.current !== undefined && taskProgress.total !== undefined && (
                      <span className="text-sm font-bold text-slate-300 tabular-nums">
                        {taskProgress.current} <span className="text-slate-500 font-normal">/ {taskProgress.total}</span>
                      </span>
                    )}
                    {taskProgress.onCancel && (
                      <button onClick={taskProgress.onCancel} className="text-xs text-red-400 hover:text-red-300 transition-colors px-2 py-0.5 rounded-lg hover:bg-red-500/10">Отмена</button>
                    )}
                  </div>
                </div>
                <div className="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                  {taskProgress.isIndeterminate ? (
                    <div className={`h-full w-full bg-gradient-to-r ${taskProgress.colorClass || "from-indigo-500 to-purple-500"} animate-pulse rounded-full`} />
                  ) : (
                    <div
                      className={`h-full bg-gradient-to-r ${taskProgress.colorClass || "from-indigo-500 to-purple-500"} rounded-full transition-all duration-500 ease-out`}
                      style={{ width: `${((taskProgress.current || 0) / Math.max(1, (taskProgress.total || 1))) * 100}%` }}
                    />
                  )}
                </div>
              </div>
            ) : (
              /* ── ACTIONS STATE ── */
              <div className="flex items-center gap-3">
                {/* Selection scope indicator */}
                <div className="flex items-center gap-2 shrink-0">
                  <div className="w-7 h-7 rounded-full bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-xs font-bold text-indigo-300">{selectedIds.size}</div>
                  <div className="text-xs leading-tight">
                    <div className="text-slate-200 font-medium">{selectedIds.size} выбрано</div>
                    <div className="text-slate-500">из {processed.length} в таблице</div>
                  </div>
                </div>

                <div className="w-px h-8 bg-slate-700/70 shrink-0" />

                {/* OKB Sync */}
                <button
                  onClick={handleOkbSync}
                  disabled={isSyncingOkb}
                  className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-xl bg-slate-800 hover:bg-emerald-600 text-slate-300 hover:text-white transition-all disabled:opacity-50"
                >
                  {isSyncingOkb ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
                  OKB Sync
                </button>

                <div className="w-px h-8 bg-slate-700/70 shrink-0" />

                {/* AI Categories */}
                <button
                  onClick={() => {
                    if (!confirm(`Запустить AI категоризацию для ${selectedIds.size} товаров?`)) return;
                    handleAutoMap(false, "categories");
                  }}
                  disabled={isAutoMapping}
                  className="flex items-center gap-1.5 h-8 px-3 text-xs font-medium rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white transition-all disabled:opacity-50"
                >
                  <Sparkles className="w-3.5 h-3.5 text-indigo-400" /> AI Categories
                </button>

                {/* AI Attributes */}
                <button
                  onClick={() => {
                    if (!confirm(`Запустить AI для заполнения атрибутов у ${selectedIds.size} товаров? Уже заполненные вручную поля не перезапишутся.`)) return;
                    handleAutoMap(false, "attributes");
                  }}
                  disabled={isAutoMapping}
                  className="flex items-center gap-1.5 h-8 px-3 text-xs font-bold rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white transition-all shadow-md shadow-indigo-500/30 disabled:opacity-50"
                >
                  <Sparkles className="w-3.5 h-3.5" /> AI Attrs
                </button>

                {/* Close */}
                <button
                  onClick={() => { setSelectedIds(new Set()); setPopupPos(null); }}
                  className="ml-1 p-1.5 text-slate-500 hover:text-white transition-colors rounded-lg hover:bg-slate-800"
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


    </div>
  );
}

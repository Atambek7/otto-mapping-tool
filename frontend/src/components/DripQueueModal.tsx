"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useAccount } from "@/lib/useAccount";
import { fetchWithAuth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Clock,
  Calendar,
  AlertCircle,
  CheckCircle2,
  Hourglass,
  Loader2,
  X,
  RotateCw,
  Search,
  ChevronLeft,
  ChevronRight,
  Layers,
  Play,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { ProductDrawer } from "@/components/ProductDrawer";

interface DripQueueModalProps {
  factoryId: string;
  factoryName?: string;
  isOpen: boolean;
  onClose: () => void;
  catalogProducts?: any[];
  onPlanUpdated?: () => void;
}

interface QueueItem {
  id: number;
  product_ean: string;
  product_name?: string;
  image_url?: string;
  price?: string;
  category_id?: string;
  category_name?: string;
  scheduled_day: number;
  scheduled_date: string;
  status: "pending" | "processing" | "published" | "failed";
  attempts: number;
  error_message?: string;
  missing_attributes?: string[];
  published_at?: string;
}

interface EnrichedQueueItem extends QueueItem {
  displayTitle: string;
  displayImage: string;
  formattedDate: string;
  searchCorpus: string;
}

interface PlanDetails {
  id: number;
  factory_id: string;
  factory_name?: string;
  account_name: string;
  total_products: number;
  total_days: number;
  current_day: number;
  daily_time: string;
  status: "active" | "paused" | "completed" | "cancelled";
  executor_type: string;
  created_at: string;
  progress?: {
    published: number;
    processing?: number;
    failed: number;
    pending: number;
    percentage: number;
  };
}

const PAGE_SIZE = 50;

export default function DripQueueModal({
  factoryId,
  factoryName,
  isOpen,
  onClose,
  catalogProducts = [],
  onPlanUpdated,
}: DripQueueModalProps) {
  const { account } = useAccount();

  const [plans, setPlans] = useState<PlanDetails[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null);
  const [loadingPlans, setLoadingPlans] = useState<boolean>(false);

  const [items, setItems] = useState<QueueItem[]>([]);
  const [loadingItems, setLoadingItems] = useState<boolean>(false);
  const [retrying, setRetrying] = useState<boolean>(false);
  const [triggeringNow, setTriggeringNow] = useState(false);

  const [statusTab, setStatusTab] = useState<"all" | "pending" | "processing" | "published" | "failed">("all");
  const [searchInput, setSearchInput] = useState<string>("");
  const [debouncedSearch, setDebouncedSearch] = useState<string>("");
  const [selectedDayFilter, setSelectedDayFilter] = useState<number | "all">("all");
  const [currentPage, setCurrentPage] = useState<number>(1);

  // Drawer state for inspecting / editing a product directly from queue
  const [activeDrawerProduct, setActiveDrawerProduct] = useState<any | null>(null);
  const [activeDrawerMeta, setActiveDrawerMeta] = useState<{
    category?: string;
    categoryName?: string;
    productName?: string;
    price?: string;
  } | null>(null);

  // Debounce search input by 200ms to eliminate typing lag
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchInput);
      setCurrentPage(1);
    }, 200);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Reset page when filters change
  useEffect(() => {
    setCurrentPage(1);
  }, [statusTab, selectedDayFilter]);

  // Quick lookup map for catalog products by EAN to enrich names/images
  const catalogMap = useMemo(() => {
    const map = new Map<string, any>();
    for (const p of catalogProducts) {
      const ean = p.ean || p.row?.EAN;
      if (ean) {
        map.set(ean, p);
      }
    }
    return map;
  }, [catalogProducts]);

  const handleOpenProduct = useCallback((item: EnrichedQueueItem) => {
    const catProd = catalogMap.get(item.product_ean);
    const initialProd = catProd || {
      ean: item.product_ean,
      name: item.displayTitle || item.product_name,
      img: item.displayImage || item.image_url,
      price: item.price,
      row: {
        EAN: item.product_ean,
        Artikelbeschreibung: item.displayTitle || item.product_name,
        PicMain: item.displayImage || item.image_url,
        StartPrice: item.price,
      },
    };
    setActiveDrawerProduct(initialProd);
    setActiveDrawerMeta({
      category: item.category_id || "",
      categoryName: item.category_name || "",
      productName: item.displayTitle || item.product_name || "",
      price: item.price || "",
    });

    // If catalogProduct didn't have full row data, fetch from Aftercool
    if (!catProd?.row || Object.keys(catProd.row).length <= 1) {
      fetchWithAuth(
        `/aftercool/products?factory_id=${factoryId}&account=${account}&dataset=lister&q=${item.product_ean}&limit=1&include_row=1`
      )
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          const found = (data?.items || data || [])[0];
          if (found && found.row) {
            setActiveDrawerProduct((prev: any) => ({
              ...prev,
              row: found.row,
              name: found.row.Artikelbeschreibung || found.name || prev?.name,
              img: found.row.GalleryURL || found.img || prev?.img,
              price: found.row.StartPrice || found.price || prev?.price,
            }));
          }
        })
        .catch(() => {});
    }
  }, [catalogMap, factoryId, account]);

  // Fetch plans for this factory & account
  const fetchPlans = useCallback(async () => {
    if (!factoryId || !account) return;
    setLoadingPlans(true);
    try {
      const res = await fetchWithAuth(`/pipeline/plans?account_name=${account}&factory_id=${factoryId}`);
      if (res.ok) {
        const data: PlanDetails[] = await res.json();
        setPlans(data);
        if (data.length > 0) {
          const active = data.find((p) => p.status === "active" || p.status === "paused");
          setSelectedPlanId((prev) => (prev && data.some((p) => p.id === prev) ? prev : (active ? active.id : data[0].id)));
        } else {
          setSelectedPlanId(null);
        }
      }
    } catch (err) {
      console.error("Failed to fetch plans", err);
    } finally {
      setLoadingPlans(false);
    }
  }, [account, factoryId]);

  // Fetch items for selected plan
  const fetchPlanItems = useCallback(async (planId: number, background = false) => {
    if (!background) setLoadingItems(true);
    try {
      const res = await fetchWithAuth(`/pipeline/plan/${planId}/items`);
      if (res.ok) {
        const data = await res.json();
        setItems(data.items || []);
      } else if (!background) {
        toast.error("Не удалось загрузить товары очереди");
      }
    } catch (err) {
      if (!background) {
        console.error("Failed to fetch plan items", err);
        toast.error("Сетевая ошибка при загрузке очереди");
      }
    } finally {
      if (!background) setLoadingItems(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchPlans();
    }
  }, [isOpen, fetchPlans]);

  useEffect(() => {
    if (selectedPlanId && isOpen) {
      fetchPlanItems(selectedPlanId);
    } else {
      setItems([]);
    }
  }, [selectedPlanId, isOpen, fetchPlanItems]);

  const hasProcessing = useMemo(() => items.some((it) => it.status === "processing"), [items]);

  // Gentle background poll ONLY if items are actively in "processing" state
  useEffect(() => {
    if (!selectedPlanId || !isOpen || !hasProcessing) return;

    const interval = setInterval(() => {
      fetchPlanItems(selectedPlanId, true);
    }, 5000);

    return () => clearInterval(interval);
  }, [selectedPlanId, isOpen, hasProcessing, fetchPlanItems]);

  const selectedPlan = useMemo(() => {
    return plans.find((p) => p.id === selectedPlanId) || null;
  }, [plans, selectedPlanId]);

  // Stats calculation
  const stats = useMemo(() => {
    let published = 0;
    let processing = 0;
    let failed = 0;
    let pending = 0;
    for (const it of items) {
      if (it.status === "published") published++;
      else if (it.status === "processing") processing++;
      else if (it.status === "failed") failed++;
      else pending++;
    }
    return {
      total: items.length,
      published,
      processing,
      failed,
      pending,
    };
  }, [items]);

  // Memoize enriched items with display attributes and pre-computed search string
  const enrichedItems = useMemo<EnrichedQueueItem[]>(() => {
    return items.map((it) => {
      const catProd = catalogMap.get(it.product_ean);
      const title =
        it.product_name && it.product_name !== "Без названия" && it.product_name !== "None"
          ? it.product_name
          : catProd?.name || catProd?.row?.Artikelbeschreibung || it.product_ean;

      let rawImg =
        it.image_url ||
        catProd?.image ||
        catProd?.PicMain ||
        catProd?.row?.PictureURLs ||
        catProd?.row?.PictureURL ||
        catProd?.row?.GalleryURL ||
        catProd?.row?.BILD_URL;

      if (typeof rawImg === "string" && (rawImg.includes("|") || rawImg.includes(","))) {
        rawImg = rawImg.split(/[|,]/)[0].trim();
      }

      const formattedDate = it.scheduled_date
        ? new Date(it.scheduled_date).toLocaleString("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
          })
        : "—";

      const searchCorpus = `${it.product_ean} ${title} ${it.category_name || ""}`.toLowerCase();

      return {
        ...it,
        displayTitle: title,
        displayImage: rawImg || "",
        formattedDate,
        searchCorpus,
      };
    });
  }, [items, catalogMap]);

  // Fast filtered items using pre-computed search corpus
  const filteredItems = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    return enrichedItems.filter((it) => {
      if (statusTab !== "all" && it.status !== statusTab) {
        return false;
      }
      if (selectedDayFilter !== "all" && it.scheduled_day !== selectedDayFilter) {
        return false;
      }
      if (q && !it.searchCorpus.includes(q)) {
        return false;
      }
      return true;
    });
  }, [enrichedItems, statusTab, selectedDayFilter, debouncedSearch]);

  // Paginated items: renders maximum 50 DOM nodes instead of 1000!
  const totalPages = Math.max(1, Math.ceil(filteredItems.length / PAGE_SIZE));
  const paginatedItems = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return filteredItems.slice(start, start + PAGE_SIZE);
  }, [filteredItems, currentPage]);

  // Available days for filter
  const availableDays = useMemo(() => {
    const daysSet = new Set<number>();
    for (const it of items) {
      daysSet.add(it.scheduled_day);
    }
    return Array.from(daysSet).sort((a, b) => a - b);
  }, [items]);

  const handleTriggerNow = async () => {
    if (!selectedPlanId) return;
    setTriggeringNow(true);
    try {
      const res = await fetchWithAuth(`/pipeline/plan/${selectedPlanId}/trigger-now`, {
        method: "POST",
      });
      if (res.ok) {
        toast.success("Выгрузка очереди успешно запущена!");
        await fetchPlans();
        await fetchPlanItems(selectedPlanId);
        onPlanUpdated?.();
      } else {
        toast.error("Не удалось запустить выгрузку");
      }
    } catch {
      toast.error("Ошибка при отправке очереди");
    } finally {
      setTriggeringNow(false);
    }
  };

  const handleRetryFailed = async () => {
    if (!selectedPlanId) return;
    setRetrying(true);
    try {
      const res = await fetchWithAuth(`/pipeline/plan/${selectedPlanId}/retry-failed`, {
        method: "POST",
      });
      if (res.ok) {
        toast.success("Ошибки успешно отправлены на повторную выгрузку!");
        await fetchPlans();
        await fetchPlanItems(selectedPlanId);
        onPlanUpdated?.();
      } else {
        toast.error("Не удалось запустить повторную выгрузку");
      }
    } catch {
      toast.error("Ошибка при отправке на повтор");
    } finally {
      setRetrying(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-xs p-3 sm:p-4 overflow-y-auto">
      <div className="bg-white dark:bg-card border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-5xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh]">
        {/* Modal Header */}
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-card">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 flex items-center justify-center shrink-0">
              <Layers className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="text-base font-bold text-slate-900 dark:text-white tracking-tight">
                  Очередь выгрузки Drip Feed
                </h3>
                <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200/80 dark:border-indigo-900/60">
                  {account} Möbel
                </span>
                {selectedPlan && (
                  <span
                    className={cn(
                      "px-2 py-0.5 rounded-md text-[11px] font-semibold uppercase tracking-wider",
                      selectedPlan.status === "active"
                        ? "bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-950/40 dark:text-emerald-400"
                        : selectedPlan.status === "paused"
                        ? "bg-amber-50 text-amber-700 border border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-400"
                        : selectedPlan.status === "completed"
                        ? "bg-blue-50 text-blue-700 border border-blue-200/80 dark:bg-blue-950/40 dark:text-blue-400"
                        : "bg-slate-100 text-slate-600 border border-slate-200 dark:bg-slate-800 dark:text-slate-400"
                    )}
                  >
                    {selectedPlan.status === "active"
                      ? "Активен"
                      : selectedPlan.status === "paused"
                      ? "На паузе"
                      : selectedPlan.status === "completed"
                      ? "Завершен"
                      : "Отменен"}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                Фабрика: <span className="font-medium text-slate-700 dark:text-slate-300">{factoryName || factoryId}</span>
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Plan Selector & Quick Stats Bar */}
        <div className="px-6 py-3 border-b border-slate-100 dark:border-slate-800 bg-slate-50/30 dark:bg-slate-900/20 flex flex-col md:flex-row md:items-center justify-between gap-3">
          {/* Plan Dropdown */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 shrink-0">План выгрузки:</span>
            {loadingPlans ? (
              <Loader2 className="w-4 h-4 animate-spin text-indigo-600" />
            ) : plans.length > 0 ? (
              <select
                value={selectedPlanId || ""}
                onChange={(e) => setSelectedPlanId(Number(e.target.value))}
                className="bg-white dark:bg-card border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer shadow-2xs"
              >
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>
                    План #{p.id} ({p.status === "active" ? "Активен" : p.status === "paused" ? "На паузе" : p.status === "completed" ? "Завершен" : "Отменен"}) — {p.total_products} товаров, {p.total_days} дн. ({new Date(p.created_at).toLocaleDateString()})
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-xs text-slate-400 italic">Нет созданных планов</span>
            )}

            <button
              onClick={() => {
                fetchPlans();
                if (selectedPlanId) fetchPlanItems(selectedPlanId);
              }}
              title="Обновить"
              className="p-1.5 rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
            >
              <RotateCw className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Metric Counters */}
          {selectedPlan && (
            <div className="flex items-center gap-3 text-xs flex-wrap">
              <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
                <span className="font-semibold text-slate-900 dark:text-white">{stats.total}</span> всего
              </div>
              <div className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 font-medium">
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{stats.published} опубликовано</span>
              </div>
              <div className="flex items-center gap-1.5 text-indigo-600 dark:text-indigo-400 font-medium">
                <Hourglass className="w-3.5 h-3.5" />
                <span>{stats.pending} в очереди</span>
              </div>
              {stats.processing > 0 && (
                <div className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400 font-medium">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>{stats.processing} в обработке OTTO</span>
                </div>
              )}
              {stats.pending > 0 && (
                <Button
                  size="sm"
                  onClick={handleTriggerNow}
                  disabled={triggeringNow}
                  className="h-7 text-[11px] px-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium shadow-2xs gap-1.5 cursor-pointer"
                >
                  {triggeringNow ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3 fill-current" />}
                  Выгрузить сейчас
                </Button>
              )}
              {stats.failed > 0 && (
                <div className="flex items-center gap-2">
                  <span className="flex items-center gap-1 text-rose-600 font-semibold">
                    <AlertCircle className="w-3.5 h-3.5" />
                    <span>{stats.failed} ошибок</span>
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleRetryFailed}
                    disabled={retrying}
                    className="h-7 text-[11px] px-2.5 border-rose-200 text-rose-600 hover:bg-rose-50 dark:border-rose-800/60 dark:text-rose-400 shadow-2xs gap-1 cursor-pointer"
                  >
                    {retrying ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCw className="w-3 h-3" />}
                    Повторить ошибки
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Filter and Search Bar */}
        <div className="px-6 py-3 border-b border-slate-100 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white dark:bg-card">
          {/* Status Tabs */}
          <div className="flex items-center gap-1 bg-slate-100/80 dark:bg-slate-800/80 p-0.5 rounded-lg text-xs self-start flex-wrap">
            <button
              onClick={() => setStatusTab("all")}
              className={cn(
                "px-3 py-1 rounded-md font-medium transition-colors cursor-pointer",
                statusTab === "all"
                  ? "bg-white dark:bg-slate-900 text-slate-900 dark:text-white shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              )}
            >
              Все ({stats.total})
            </button>
            <button
              onClick={() => setStatusTab("pending")}
              className={cn(
                "px-3 py-1 rounded-md font-medium transition-colors flex items-center gap-1 cursor-pointer",
                statusTab === "pending"
                  ? "bg-white dark:bg-slate-900 text-indigo-600 dark:text-indigo-400 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              )}
            >
              В очереди ({stats.pending})
            </button>
            <button
              onClick={() => setStatusTab("processing")}
              className={cn(
                "px-3 py-1 rounded-md font-medium transition-colors flex items-center gap-1 cursor-pointer",
                statusTab === "processing"
                  ? "bg-white dark:bg-slate-900 text-amber-600 dark:text-amber-400 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              )}
            >
              Обработка ({stats.processing})
            </button>
            <button
              onClick={() => setStatusTab("published")}
              className={cn(
                "px-3 py-1 rounded-md font-medium transition-colors flex items-center gap-1 cursor-pointer",
                statusTab === "published"
                  ? "bg-white dark:bg-slate-900 text-emerald-600 dark:text-emerald-400 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              )}
            >
              Опубликованы ({stats.published})
            </button>
            <button
              onClick={() => setStatusTab("failed")}
              className={cn(
                "px-3 py-1 rounded-md font-medium transition-colors flex items-center gap-1 cursor-pointer",
                statusTab === "failed"
                  ? "bg-white dark:bg-slate-900 text-rose-600 dark:text-rose-400 shadow-2xs font-semibold"
                  : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
              )}
            >
              Ошибки ({stats.failed})
            </button>
          </div>

          {/* Day Filter & Search Input */}
          <div className="flex items-center gap-2">
            {availableDays.length > 1 && (
              <select
                value={selectedDayFilter}
                onChange={(e) => setSelectedDayFilter(e.target.value === "all" ? "all" : Number(e.target.value))}
                className="bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-700 dark:text-slate-300 focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
              >
                <option value="all">Все дни</option>
                {availableDays.map((d) => (
                  <option key={d} value={d}>
                    День {d}
                  </option>
                ))}
              </select>
            )}

            <div className="relative w-full sm:w-56">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder="Поиск по EAN или названию..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="w-full bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-lg pl-8 pr-3 py-1.5 text-xs text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>
        </div>

        {/* Table / List Body */}
        <div className="overflow-y-auto flex-1 p-0">
          {loadingItems ? (
            <div className="py-20 flex flex-col items-center justify-center gap-2 text-slate-400 text-xs">
              <Loader2 className="w-6 h-6 animate-spin text-indigo-600" />
              <span>Загрузка товаров очереди...</span>
            </div>
          ) : filteredItems.length === 0 ? (
            <div className="py-20 text-center text-xs text-slate-400">
              {items.length === 0 ? "В этом плане нет товаров" : "Нет товаров, соответствующих фильтрам"}
            </div>
          ) : (
            <table className="w-full text-left text-xs border-collapse">
              <thead className="bg-slate-50/80 dark:bg-slate-900/80 sticky top-0 border-b border-slate-200 dark:border-slate-800 text-slate-500 font-semibold z-10 backdrop-blur-xs">
                <tr>
                  <th className="py-2.5 px-4 w-14">Фото</th>
                  <th className="py-2.5 px-4">Название товара</th>
                  <th className="py-2.5 px-4 w-36">EAN</th>
                  <th className="py-2.5 px-4 w-44">Категория</th>
                  <th className="py-2.5 px-4 w-32">День выгрузки</th>
                  <th className="py-2.5 px-4 w-36">Статус</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60 bg-white dark:bg-card">
                {paginatedItems.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => handleOpenProduct(item)}
                    className="hover:bg-slate-50/80 dark:hover:bg-slate-800/50 cursor-pointer transition-colors group select-none"
                    title="Нажмите на товар, чтобы открыть карточку и редактировать"
                  >
                    {/* Image */}
                    <td className="py-2.5 px-4">
                      <div className="w-10 h-10 rounded-lg border border-slate-200 dark:border-slate-800 overflow-hidden bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0">
                        {item.displayImage ? (
                          <img
                            src={item.displayImage}
                            alt={item.displayTitle}
                            className="w-full h-full object-cover"
                            loading="lazy"
                            onError={(e) => {
                              (e.target as HTMLElement).style.display = "none";
                            }}
                          />
                        ) : (
                          <span className="text-[10px] text-slate-400 font-medium">Нет</span>
                        )}
                      </div>
                    </td>

                    {/* Product Name & Error Details */}
                    <td className="py-2.5 px-4 max-w-sm">
                      <div className="font-semibold text-slate-900 dark:text-white line-clamp-2" title={item.displayTitle}>
                        {item.displayTitle}
                      </div>
                      {item.error_message && (
                        <div
                          className={cn(
                            "mt-1.5 p-2 rounded border text-[11px] leading-tight",
                            item.status === "failed"
                              ? "bg-rose-50 dark:bg-rose-950/40 border-rose-200/80 dark:border-rose-900/60 text-rose-700 dark:text-rose-300 font-mono break-all"
                              : "bg-blue-50 dark:bg-blue-950/40 border-blue-200/80 dark:border-blue-900/60 text-blue-700 dark:text-blue-300"
                          )}
                        >
                          {item.error_message}
                        </div>
                      )}
                      {item.missing_attributes && item.missing_attributes.length > 0 && (
                        <div className="mt-1.5 p-1.5 rounded bg-amber-50 dark:bg-amber-950/40 border border-amber-200/80 dark:border-amber-900/60 text-[11px] text-amber-700 dark:text-amber-300">
                          <strong>Отсутствуют:</strong> {item.missing_attributes.join(", ")}
                        </div>
                      )}
                      {item.published_at && (
                        <div className="text-[11px] text-emerald-600 dark:text-emerald-400 mt-0.5">
                          Опубликован: {new Date(item.published_at).toLocaleString("ru-RU")}
                        </div>
                      )}
                    </td>

                    {/* EAN */}
                    <td className="py-2.5 px-4 font-mono text-slate-700 dark:text-slate-300 select-text cursor-text">
                      {item.product_ean}
                    </td>

                    {/* Category */}
                    <td className="py-2.5 px-4">
                      <span className="inline-block px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[11px] text-slate-700 dark:text-slate-300 truncate max-w-[170px]" title={item.category_name || item.category_id}>
                        {item.category_name || item.category_id || "Uncategorized"}
                      </span>
                    </td>

                    {/* Scheduled Day */}
                    <td className="py-2.5 px-4">
                      <div className="font-bold text-indigo-600 dark:text-indigo-400">
                        День {item.scheduled_day}
                      </div>
                      <div className="text-[11px] text-slate-400 mt-0.5">
                        {item.formattedDate}
                      </div>
                    </td>

                    {/* Status */}
                    <td className="py-2.5 px-4">
                      {item.status === "published" ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200/80 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800/60">
                          <CheckCircle2 className="w-3 h-3" /> Опубликован
                        </span>
                      ) : item.status === "processing" ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-50 text-amber-700 border border-amber-200/80 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800/60">
                          <Loader2 className="w-3 h-3 animate-spin" /> Обработка OTTO
                        </span>
                      ) : item.status === "failed" ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-rose-50 text-rose-700 border border-rose-200/80 dark:bg-rose-950/40 dark:text-rose-400 dark:border-rose-800/60">
                          <AlertCircle className="w-3 h-3" /> Ошибка
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200/80 dark:bg-indigo-950/40 dark:text-indigo-400 dark:border-indigo-900/60">
                          <Clock className="w-3 h-3" /> В очереди
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Modal Footer with Pagination Controls */}
        <div className="px-6 py-3 border-t border-slate-100 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-slate-50/50 dark:bg-card">
          <div className="text-xs text-slate-400">
            {filteredItems.length > 0 ? (
              <>
                Показано {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filteredItems.length)} из {filteredItems.length}
                {filteredItems.length < items.length && ` (отфильтровано из ${items.length})`}
              </>
            ) : (
              `0 товаров`
            )}
          </div>

          {/* Pagination */}
          <div className="flex items-center gap-2 self-end sm:self-auto">
            {totalPages > 1 && (
              <div className="flex items-center gap-1 mr-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage <= 1}
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  className="h-7 w-7 p-0 cursor-pointer"
                  title="Предыдущая страница"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </Button>
                <span className="text-xs px-2 text-slate-600 dark:text-slate-300 font-medium">
                  {currentPage} / {totalPages}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage >= totalPages}
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  className="h-7 w-7 p-0 cursor-pointer"
                  title="Следующая страница"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </Button>
              </div>
            )}

            <Button
              size="sm"
              variant="outline"
              onClick={onClose}
              className="border-slate-200 dark:border-slate-800 text-xs cursor-pointer"
            >
              Закрыть
            </Button>
          </div>
        </div>
      </div>

      {/* Product Drawer for editing product directly from Queue */}
      {activeDrawerProduct && (
        <ProductDrawer
          product={activeDrawerProduct}
          factoryId={factoryId}
          account={account}
          initialCategory={activeDrawerMeta?.category || ""}
          initialCategoryName={activeDrawerMeta?.categoryName || ""}
          initialProductName={activeDrawerMeta?.productName || activeDrawerProduct.name || ""}
          initialPrice={activeDrawerMeta?.price || activeDrawerProduct.price || ""}
          onClose={() => {
            setActiveDrawerProduct(null);
            setActiveDrawerMeta(null);
          }}
          onSave={() => {
            if (selectedPlanId) {
              fetchPlanItems(selectedPlanId, true);
            }
            onPlanUpdated?.();
          }}
        />
      )}
    </div>
  );
}

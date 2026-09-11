"use client";

import React, { useState, useEffect, useMemo } from "react";
import { fetchWithAuth } from "@/lib/api";
import {
  X,
  ExternalLink,
  Search,
  Calendar,
  ShoppingBag,
  Euro,
  Package,
  Clock,
  CheckCircle2,
  Truck,
  AlertCircle,
  Loader2,
  Tag,
  Store,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ImageHoverPreview, HoverImagePreviewState } from "@/components/ImageHoverPreview";

interface OrderItem {
  id: number;
  product_title: string;
  price: number;
  currency: string;
  sku?: string;
  ean?: string;
  article_number?: string;
  fulfillment_status?: string;
  shop_url?: string;
  image_url?: string;
}

interface OrderData {
  id: number;
  order_number: string;
  order_date: string;
  total_revenue: number;
  items_count: number;
  items: OrderItem[];
}

interface WeekdayOrdersResponse {
  weekday: number;
  label: string;
  account_name: string;
  period_from: string;
  period_to: string;
  total_orders: number;
  total_items: number;
  total_revenue: number;
  orders: OrderData[];
}

interface WeekdayOrdersModalProps {
  isOpen: boolean;
  onClose: () => void;
  account: string;
  weekday: number | null;
  weekdayLabel: string;
  weeks?: number;
  customRange?: { from: string; to: string } | null;
}

const FULL_DAY_NAMES: Record<number, string> = {
  0: "Понедельник",
  1: "Вторник",
  2: "Среда",
  3: "Четверг",
  4: "Пятница",
  5: "Суббота",
  6: "Воскресенье",
};

export default function WeekdayOrdersModal({
  isOpen,
  onClose,
  account,
  weekday,
  weekdayLabel,
  weeks = 12,
  customRange,
}: WeekdayOrdersModalProps) {
  const [data, setData] = useState<WeekdayOrdersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedStatus, setSelectedStatus] = useState<string>("all");
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(null);

  useEffect(() => {
    if (!isOpen || weekday === null) {
      setData(null);
      setSearchQuery("");
      setSelectedStatus("all");
      setError(null);
      setImgPreview(null);
      return;
    }

    const fetchOrders = async () => {
      setLoading(true);
      setError(null);
      try {
        let url = `/analytics/weekday-orders?account_name=${account}&weekday=${weekday}`;
        if (customRange?.from && customRange?.to) {
          url += `&from_date=${customRange.from}&to_date=${customRange.to}`;
        } else {
          url += `&weeks=${weeks}`;
        }

        const res = await fetchWithAuth(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json: WeekdayOrdersResponse = await res.json();
        setData(json);
      } catch (err: any) {
        setError(err.message || "Не удалось загрузить заказы");
      } finally {
        setLoading(false);
      }
    };

    fetchOrders();
  }, [isOpen, weekday, account, weeks, customRange]);

  // Handle ESC key to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Filter orders by search & status
  const filteredOrders = useMemo(() => {
    if (!data?.orders) return [];

    return data.orders.filter((order) => {
      // Status filter
      if (selectedStatus !== "all") {
        const hasStatus = order.items.some(
          (it) => it.fulfillment_status === selectedStatus
        );
        if (!hasStatus) return false;
      }

      // Search query
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase().trim();

      const matchOrderNum = order.order_number.toLowerCase().includes(q);
      const matchItems = order.items.some(
        (it) =>
          it.product_title?.toLowerCase().includes(q) ||
          it.sku?.toLowerCase().includes(q) ||
          it.ean?.toLowerCase().includes(q) ||
          it.article_number?.toLowerCase().includes(q)
      );

      return matchOrderNum || matchItems;
    });
  }, [data, searchQuery, selectedStatus]);

  if (!isOpen || weekday === null) return null;

  const fullDayName = FULL_DAY_NAMES[weekday] || weekdayLabel;
  const avgOrderValue =
    data && data.total_orders > 0 ? data.total_revenue / data.total_orders : 0;

  const renderStatusBadge = (status?: string) => {
    switch (status) {
      case "SENT":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-50 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-400 border border-emerald-200/80 dark:border-emerald-900/50">
            <Truck className="w-3 h-3" /> Отправлен
          </span>
        );
      case "PROCESSABLE":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-sky-50 dark:bg-sky-950/50 text-sky-700 dark:text-sky-400 border border-sky-200/80 dark:border-sky-900/50">
            <Clock className="w-3 h-3" /> В обработке
          </span>
        );
      case "RETURNED":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-amber-50 dark:bg-amber-950/50 text-amber-700 dark:text-amber-400 border border-amber-200/80 dark:border-amber-900/50">
            <AlertCircle className="w-3 h-3" /> Возврат
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-medium bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
            {status || "Не указан"}
          </span>
        );
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-xs p-3 sm:p-5 overflow-y-auto animate-in fade-in duration-150">
      <div className="bg-white dark:bg-[#111827] border border-slate-200 dark:border-slate-800 rounded-2xl w-full max-w-4xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh] text-slate-800 dark:text-slate-200">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 dark:border-slate-800/80 flex items-center justify-between bg-slate-50/70 dark:bg-slate-900/50">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-600/10 dark:bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 flex items-center justify-center shrink-0 border border-indigo-200/50 dark:border-indigo-800/50">
              <Calendar className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base sm:text-lg font-bold text-slate-900 dark:text-white tracking-tight">
                  Заказы: {fullDayName}
                </h3>
                <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200/80 dark:border-indigo-900/60">
                  {account} Möbel
                </span>
              </div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                Период:{" "}
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  {data?.period_from || customRange?.from || "—"} —{" "}
                  {data?.period_to || customRange?.to || "—"}
                </span>{" "}
                ({customRange ? "Произвольный период" : `${weeks} недель`})
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-2 rounded-xl hover:bg-slate-200/60 dark:hover:bg-slate-800/80 transition-colors cursor-pointer"
            title="Закрыть (Esc)"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* KPI Strip */}
        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 sm:px-6 bg-slate-100/50 dark:bg-slate-900/30 border-b border-slate-100 dark:border-slate-800/60 text-xs">
            <div className="bg-white dark:bg-slate-800/60 p-3 rounded-xl border border-slate-200/60 dark:border-slate-700/50">
              <div className="text-slate-400 dark:text-slate-400 text-[11px] mb-1 flex items-center gap-1.5">
                <ShoppingBag className="w-3.5 h-3.5 text-indigo-500" /> Всего заказов
              </div>
              <div className="text-lg font-bold text-slate-900 dark:text-white">
                {data.total_orders.toLocaleString()}
              </div>
            </div>

            <div className="bg-white dark:bg-slate-800/60 p-3 rounded-xl border border-slate-200/60 dark:border-slate-700/50">
              <div className="text-slate-400 dark:text-slate-400 text-[11px] mb-1 flex items-center gap-1.5">
                <Package className="w-3.5 h-3.5 text-indigo-500" /> Куплено товаров
              </div>
              <div className="text-lg font-bold text-slate-900 dark:text-white">
                {data.total_items.toLocaleString()}
              </div>
            </div>

            <div className="bg-white dark:bg-slate-800/60 p-3 rounded-xl border border-slate-200/60 dark:border-slate-700/50">
              <div className="text-slate-400 dark:text-slate-400 text-[11px] mb-1 flex items-center gap-1.5">
                <Euro className="w-3.5 h-3.5 text-emerald-500" /> Выручка за {weekdayLabel}
              </div>
              <div className="text-lg font-bold text-emerald-600 dark:text-emerald-400">
                {data.total_revenue.toLocaleString("de-DE", {
                  style: "currency",
                  currency: "EUR",
                  maximumFractionDigits: 0,
                })}
              </div>
            </div>

            <div className="bg-white dark:bg-slate-800/60 p-3 rounded-xl border border-slate-200/60 dark:border-slate-700/50">
              <div className="text-slate-400 dark:text-slate-400 text-[11px] mb-1 flex items-center gap-1.5">
                <Tag className="w-3.5 h-3.5 text-amber-500" /> Средний чек
              </div>
              <div className="text-lg font-bold text-slate-900 dark:text-white">
                {avgOrderValue.toLocaleString("de-DE", {
                  style: "currency",
                  currency: "EUR",
                  maximumFractionDigits: 0,
                })}
              </div>
            </div>
          </div>
        )}

        {/* Search & Filter Toolbar */}
        <div className="p-4 sm:px-6 border-b border-slate-100 dark:border-slate-800/80 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-white dark:bg-[#111827]">
          {/* Search Box */}
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              placeholder="Поиск по номеру заказа, названию товара, артикулу или EAN..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full h-9 pl-9 pr-8 text-xs bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-700/80 rounded-xl text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Status Filter Chips */}
          <div className="flex items-center gap-1.5 shrink-0 overflow-x-auto pb-1 sm:pb-0 text-xs">
            <button
              onClick={() => setSelectedStatus("all")}
              className={cn(
                "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                selectedStatus === "all"
                  ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                  : "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700"
              )}
            >
              Все
            </button>
            <button
              onClick={() => setSelectedStatus("SENT")}
              className={cn(
                "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                selectedStatus === "SENT"
                  ? "bg-emerald-600 text-white"
                  : "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-100"
              )}
            >
              Отправленные
            </button>
            <button
              onClick={() => setSelectedStatus("PROCESSABLE")}
              className={cn(
                "px-2.5 py-1 rounded-lg text-xs font-medium transition-colors cursor-pointer",
                selectedStatus === "PROCESSABLE"
                  ? "bg-sky-600 text-white"
                  : "bg-sky-50 dark:bg-sky-950/40 text-sky-700 dark:text-sky-400 hover:bg-sky-100"
              )}
            >
              В обработке
            </button>
          </div>
        </div>

        {/* Scrollable Orders List */}
        <div
          className="p-4 sm:p-6 overflow-y-auto flex-1 space-y-4"
          onScroll={() => setImgPreview(null)}
        >
          {loading && (
            <div className="py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
              <Loader2 className="w-8 h-8 animate-spin text-indigo-500" />
              <div className="text-xs font-medium">Загрузка заказов из Otto...</div>
            </div>
          )}

          {error && (
            <div className="p-4 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-900/60 text-rose-700 dark:text-rose-300 text-xs flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                <div className="font-semibold">Ошибка загрузки</div>
                <div className="mt-0.5">{error}</div>
              </div>
            </div>
          )}

          {!loading && !error && filteredOrders.length === 0 && (
            <div className="py-16 text-center text-slate-400 dark:text-slate-500">
              <ShoppingBag className="w-10 h-10 mx-auto mb-2 text-slate-300 dark:text-slate-600" />
              <div className="text-sm font-semibold text-slate-700 dark:text-slate-300">
                Заказы не найдены
              </div>
              <div className="text-xs mt-1">
                {searchQuery
                  ? "Попробуйте изменить параметры поиска или фильтр статуса"
                  : `В выбранном периоде нет подтвержденных заказов за ${fullDayName.toLowerCase()}`}
              </div>
            </div>
          )}

          {!loading &&
            !error &&
            filteredOrders.map((order) => {
              const orderDateObj = new Date(order.order_date);
              const formattedDate = orderDateObj.toLocaleDateString("ru-RU", {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
              });
              const formattedTime = orderDateObj.toLocaleTimeString("ru-RU", {
                hour: "2-digit",
                minute: "2-digit",
              });

              return (
                <div
                  key={order.id}
                  className="bg-white dark:bg-slate-900/60 border border-slate-200/80 dark:border-slate-800 rounded-xl overflow-hidden shadow-2xs transition-all hover:border-slate-300 dark:hover:border-slate-700"
                >
                  {/* Order Header */}
                  <div className="px-4 py-3 bg-slate-50/80 dark:bg-slate-800/40 border-b border-slate-100 dark:border-slate-800/80 flex flex-wrap items-center justify-between gap-2 text-xs">
                    <div className="flex items-center gap-2.5">
                      <span className="font-mono font-bold text-slate-900 dark:text-white bg-slate-200/70 dark:bg-slate-700/60 px-2 py-0.5 rounded text-[11px]">
                        #{order.order_number}
                      </span>
                      <span className="text-slate-500 dark:text-slate-400 text-[11px]">
                        {formattedDate} в {formattedTime}
                      </span>
                    </div>

                    <div className="flex items-center gap-3">
                      <span className="text-slate-500 dark:text-slate-400 text-[11px]">
                        Позиций: <span className="font-semibold text-slate-700 dark:text-slate-300">{order.items_count}</span>
                      </span>
                      <span className="font-bold text-slate-900 dark:text-white text-sm">
                        {order.total_revenue.toLocaleString("de-DE", {
                          style: "currency",
                          currency: "EUR",
                        })}
                      </span>
                    </div>
                  </div>

                  {/* Order Items List */}
                  <div className="divide-y divide-slate-100 dark:divide-slate-800/60">
                    {order.items.map((item) => (
                      <div
                        key={item.id}
                        className="p-3 sm:px-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/20 transition-colors"
                      >
                        {/* Product Image & Info */}
                        <div className="flex items-start gap-3 flex-1 min-w-0">
                          {/* Thumbnail with Hover Zoom */}
                          <div
                            className="relative shrink-0 cursor-pointer group/thumb"
                            onMouseEnter={(e) => {
                              if (!item.image_url) return;
                              const rect = e.currentTarget.getBoundingClientRect();
                              setImgPreview({
                                url: item.image_url,
                                name: item.product_title,
                                price: item.price ? `${item.price.toFixed(2)} €` : undefined,
                                isMirrored: account === "XL",
                                rect: {
                                  top: rect.top,
                                  left: rect.left,
                                  right: rect.right,
                                  bottom: rect.bottom,
                                  height: rect.height,
                                },
                              });
                            }}
                            onMouseLeave={() => setImgPreview(null)}
                          >
                            {item.image_url ? (
                              <img
                                src={item.image_url}
                                alt={item.product_title}
                                className={cn(
                                  "w-12 h-12 object-cover rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 transition-all duration-150 group-hover/thumb:scale-105 group-hover/thumb:border-indigo-400 dark:group-hover/thumb:border-indigo-500 group-hover/thumb:shadow-md",
                                  account === "XL" && "-scale-x-100"
                                )}
                                loading="lazy"
                              />
                            ) : (
                              <div className="w-12 h-12 rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 flex items-center justify-center text-slate-400">
                                <Package className="w-5 h-5" />
                              </div>
                            )}
                          </div>

                          {/* Titles & Meta */}
                          <div className="flex-1 min-w-0">
                            <h4
                              className="text-xs font-semibold text-slate-900 dark:text-white line-clamp-2 leading-snug"
                              title={item.product_title}
                            >
                              {item.product_title}
                            </h4>

                            <div className="flex flex-wrap items-center gap-2 mt-1 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
                              {item.ean && <span>EAN: {item.ean}</span>}
                              {item.sku && <span>SKU: {item.sku}</span>}
                              {item.article_number && (
                                <span className="text-slate-400">
                                  Арт: {item.article_number}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* Price, Status & External Link */}
                        <div className="flex items-center justify-between sm:justify-end gap-3 shrink-0 self-end sm:self-center border-t sm:border-t-0 pt-2 sm:pt-0 border-slate-100 dark:border-slate-800/60 w-full sm:w-auto">
                          {renderStatusBadge(item.fulfillment_status)}

                          <div className="text-right">
                            <div className="font-bold text-slate-900 dark:text-white text-sm">
                              {item.price.toLocaleString("de-DE", {
                                style: "currency",
                                currency: item.currency || "EUR",
                              })}
                            </div>
                          </div>

                          {item.shop_url && (
                            <a
                              href={item.shop_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-100 dark:hover:bg-indigo-900/60 border border-indigo-200/70 dark:border-indigo-800/60 transition-colors shadow-2xs"
                              title="Открыть товар на Otto.de"
                            >
                              <Store className="w-3.5 h-3.5" />
                              <span className="hidden sm:inline">На Otto.de</span>
                              <ExternalLink className="w-3 h-3 ml-0.5" />
                            </a>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-slate-100 dark:border-slate-800/80 bg-slate-50/70 dark:bg-slate-900/50 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
          <div>
            Показано заказов:{" "}
            <span className="font-semibold text-slate-700 dark:text-slate-300">
              {filteredOrders.length}
            </span>{" "}
            из {data?.total_orders || 0}
          </div>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-slate-200/80 hover:bg-slate-300/80 dark:bg-slate-800 dark:hover:bg-slate-700 font-medium text-slate-700 dark:text-slate-300 transition-colors cursor-pointer"
          >
            Закрыть
          </button>
        </div>
      </div>

      {/* Hover Zoom Card Preview (rendered via portal) */}
      <ImageHoverPreview preview={imgPreview} />
    </div>
  );
}

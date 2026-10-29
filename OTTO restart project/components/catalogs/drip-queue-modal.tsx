"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import {
  Clock,
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
  Calendar,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import {
  dripFeedService,
  type DripPlan,
  type DripQueueItem,
} from "@/features/catalogs/drip-feed-service";
import { catalogDemoService } from "@/features/catalogs/service";
import type { CatalogProductItem } from "@/features/catalogs/types";
import { ProductDrawer } from "./product-drawer";

interface DripQueueModalProps {
  factoryId: string;
  factoryName?: string;
  isOpen: boolean;
  onClose: () => void;
  onPlanUpdated?: () => void;
}

interface EnrichedQueueItem extends DripQueueItem {
  displayTitle: string;
  displayImage: string;
  formattedDate: string;
  searchCorpus: string;
}

const PAGE_SIZE = 50;

export function DripQueueModal({
  factoryId,
  factoryName,
  isOpen,
  onClose,
  onPlanUpdated,
}: DripQueueModalProps) {
  const { account } = useRestartAccount();

  const [plans, setPlans] = useState<DripPlan[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<number | null>(null);
  const [loadingPlans, setLoadingPlans] = useState<boolean>(false);

  const [items, setItems] = useState<DripQueueItem[]>([]);
  const [loadingItems, setLoadingItems] = useState<boolean>(false);
  const [retrying, setRetrying] = useState<boolean>(false);
  const [triggeringNow, setTriggeringNow] = useState<boolean>(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const [statusTab, setStatusTab] = useState<
    "all" | "pending" | "processing" | "published" | "failed" | "existing"
  >("all");
  const [searchInput, setSearchInput] = useState<string>("");
  const [debouncedSearch, setDebouncedSearch] = useState<string>("");
  const [selectedDayFilter, setSelectedDayFilter] = useState<number | "all">("all");
  const [currentPage, setCurrentPage] = useState<number>(1);

  // Drawer state for inspecting / editing a product directly from queue
  const [activeDrawerProduct, setActiveDrawerProduct] = useState<CatalogProductItem | null>(null);

  function showToast(msg: string) {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3000);
  }

  // Debounce search input by 200ms
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

  // Fetch plans
  const fetchPlans = useCallback(() => {
    if (!factoryId || !account) return;
    setLoadingPlans(true);
    try {
      const data = dripFeedService.getPlans(factoryId, account);
      setPlans([...data]);
      if (data.length > 0) {
        const active = data.find((p) => p.status === "active" || p.status === "paused");
        setSelectedPlanId((prev) =>
          prev && data.some((p) => p.id === prev) ? prev : active ? active.id : data[0].id
        );
      } else {
        setSelectedPlanId(null);
      }
    } finally {
      setLoadingPlans(false);
    }
  }, [account, factoryId]);

  // Fetch items for selected plan
  const fetchPlanItems = useCallback((planId: number) => {
    setLoadingItems(true);
    try {
      const data = dripFeedService.getPlanItems(planId);
      setItems([...data]);
    } finally {
      setLoadingItems(false);
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

  const selectedPlan = useMemo(() => {
    return plans.find((p) => p.id === selectedPlanId) || null;
  }, [plans, selectedPlanId]);

  // Stats calculation
  const stats = useMemo(() => {
    let published = 0;
    let existing = 0;
    let processing = 0;
    let failed = 0;
    let pending = 0;
    for (const it of items) {
      if (it.status === "failed" && it.otto_product_exists) existing++;
      else if (it.status === "published") published++;
      else if (it.status === "processing") processing++;
      else if (it.status === "failed") failed++;
      else pending++;
    }
    return {
      total: items.length,
      published,
      existing,
      processing,
      failed,
      pending,
    };
  }, [items]);

  // Memoize enriched items with display attributes and pre-computed search string
  const enrichedItems = useMemo<EnrichedQueueItem[]>(() => {
    return items.map((it) => {
      const title =
        it.product_name && it.product_name !== "Без названия" && it.product_name !== "None"
          ? it.product_name
          : it.product_ean;

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
        displayImage: it.image_url || "",
        formattedDate,
        searchCorpus,
      };
    });
  }, [items]);

  // Filtered items
  const filteredItems = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    return enrichedItems.filter((it) => {
      const displayStatus =
        it.status === "failed" && it.otto_product_exists ? "existing" : it.status;
      if (statusTab !== "all" && displayStatus !== statusTab) {
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

  // Paginated items
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

  const handleOpenProduct = useCallback((item: EnrichedQueueItem) => {
    if (item.originalProduct) {
      setActiveDrawerProduct(item.originalProduct);
      return;
    }

    // Synthesize product for drawer if not linked
    const synth: CatalogProductItem = {
      id: `queue-${item.id}`,
      sourceType: "AFTERBUY",
      factoryId,
      factoryName: factoryName || factoryId,
      account,
      ean: item.product_ean,
      sku: item.product_ean,
      title: item.displayTitle,
      imageUrl: item.displayImage,
      category: item.category_name || "Sessel & Relaxsessel",
      oldStandardPriceCents: 99900,
      period: "3M",
      errorCode: "602016 - Nonseller",
      status: item.displayTitle.length <= 70 ? "READY" : "NEEDS_AI",
      existsInOtto: Boolean(item.otto_product_exists),
      attributes: {
        Marke: "Home Styling Collection",
        Farbe: "Grau",
        Material: "Stoff / Holz",
      },
    };
    setActiveDrawerProduct(synth);
  }, [factoryId, factoryName, account]);

  const handleTriggerNow = () => {
    if (!selectedPlanId) return;
    setTriggeringNow(true);
    setTimeout(() => {
      dripFeedService.triggerNow(selectedPlanId);
      fetchPlans();
      fetchPlanItems(selectedPlanId);
      onPlanUpdated?.();
      setTriggeringNow(false);
      showToast("✓ Выгрузка очереди Drip Feed успешно запущена!");
    }, 400);
  };

  const handleRetryFailed = () => {
    if (!selectedPlanId) return;
    setRetrying(true);
    setTimeout(() => {
      dripFeedService.retryFailed(selectedPlanId);
      fetchPlans();
      fetchPlanItems(selectedPlanId);
      onPlanUpdated?.();
      setRetrying(false);
      showToast("✓ Товары с ошибками отправлены на повторную выгрузку!");
    }, 400);
  };

  const handleSaveDrawerProduct = (updated: CatalogProductItem) => {
    catalogDemoService.updateProduct(updated);
    dripFeedService.syncProductUpdate(updated);
    if (selectedPlanId) {
      fetchPlanItems(selectedPlanId);
    }
    setActiveDrawerProduct(null);
    onPlanUpdated?.();
    showToast(`✓ Товар сохранён: ${updated.ean}`);
  };

  if (!isOpen) return null;

  return (
    <div
      className="drip-queue-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="drip-queue-modal-title"
    >
      <div className="drip-queue-modal-container">
        {/* Modal Header */}
        <div className="drip-queue-modal-header">
          <div className="drip-queue-title-wrap">
            <div className="drip-queue-icon-badge">
              <Layers size={18} />
            </div>
            <div>
              <div className="drip-queue-header-heading">
                <h3 id="drip-queue-modal-title">Очередь выгрузки Drip Feed</h3>
                <span className="drip-queue-account-badge">{account} Möbel</span>
                {selectedPlan && (
                  <span
                    className={`drip-queue-status-pill ${
                      selectedPlan.status === "active"
                        ? "status-active"
                        : selectedPlan.status === "paused"
                        ? "status-paused"
                        : selectedPlan.status === "completed"
                        ? "status-completed"
                        : "status-cancelled"
                    }`}
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
              <p className="drip-queue-subtitle">
                Фабрика: <strong>{factoryName || factoryId}</strong>
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="drip-queue-close-btn"
            aria-label="Закрыть окно"
          >
            <X size={18} />
          </button>
        </div>

        {/* Plan Selector & Quick Stats Bar */}
        <div className="drip-queue-toolbar">
          {/* Plan Dropdown */}
          <div className="drip-queue-plan-select-wrap">
            <span className="drip-queue-label">План выгрузки:</span>
            {loadingPlans ? (
              <Loader2 size={14} className="animate-spin text-accent" />
            ) : plans.length > 0 ? (
              <select
                value={selectedPlanId || ""}
                onChange={(e) => setSelectedPlanId(Number(e.target.value))}
                className="drip-queue-plan-dropdown"
              >
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>
                    План #{p.id} ({p.status === "active" ? "Активен" : p.status === "paused" ? "На паузе" : p.status === "completed" ? "Завершен" : "Отменен"}) — {p.total_products} товаров, {p.total_days} дн.
                  </option>
                ))}
              </select>
            ) : (
              <span className="drip-queue-empty-text">Нет созданных планов</span>
            )}

            <button
              type="button"
              onClick={() => {
                fetchPlans();
                if (selectedPlanId) fetchPlanItems(selectedPlanId);
              }}
              title="Обновить список планов"
              className="drip-queue-refresh-btn"
            >
              <RotateCw size={13} />
            </button>
          </div>

          {/* Metric Counters & Triggers */}
          {selectedPlan ? (
            <div className="drip-queue-metrics-bar">
              <div className="drip-metric-item">
                <strong>{stats.total}</strong> всего
              </div>
              <div className="drip-metric-item text-published">
                <CheckCircle2 size={13} />
                <span>{stats.published} опубликовано</span>
              </div>
              <div className="drip-metric-item text-pending">
                <Hourglass size={13} />
                <span>{stats.pending} в очереди</span>
              </div>
              {stats.processing > 0 && (
                <div className="drip-metric-item text-processing">
                  <Loader2 size={13} className="animate-spin" />
                  <span>{stats.processing} в обработке OTTO</span>
                </div>
              )}
              {stats.pending > 0 && (
                <button
                  type="button"
                  onClick={handleTriggerNow}
                  disabled={triggeringNow}
                  className="drip-queue-action-btn btn-trigger-now"
                >
                  {triggeringNow ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Play size={12} style={{ fill: "currentColor" }} />
                  )}
                  <span>Выгрузить сейчас</span>
                </button>
              )}
              {stats.failed > 0 && (
                <div className="drip-queue-errors-wrap">
                  <span className="drip-metric-item text-failed">
                    <AlertCircle size={13} />
                    <span>{stats.failed} ошибок</span>
                  </span>
                  <button
                    type="button"
                    onClick={handleRetryFailed}
                    disabled={retrying}
                    className="drip-queue-action-btn btn-retry-failed"
                  >
                    {retrying ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <RotateCw size={12} />
                    )}
                    <span>Повторить ошибки</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="drip-queue-metrics-bar">
              <span className="drip-queue-empty-text">Drip Feed не активен — 0 товаров в очереди</span>
            </div>
          )}
        </div>

        {/* Filter and Search Bar */}
        <div className="drip-queue-filters-bar">
          {/* Status Tabs */}
          <div className="drip-queue-status-tabs">
            <button
              type="button"
              onClick={() => setStatusTab("all")}
              className={`status-tab ${statusTab === "all" ? "active" : ""}`}
            >
              Все ({stats.total})
            </button>
            <button
              type="button"
              onClick={() => setStatusTab("pending")}
              className={`status-tab tab-pending ${statusTab === "pending" ? "active" : ""}`}
            >
              В очереди ({stats.pending})
            </button>
            <button
              type="button"
              onClick={() => setStatusTab("processing")}
              className={`status-tab tab-processing ${statusTab === "processing" ? "active" : ""}`}
            >
              Обработка ({stats.processing})
            </button>
            <button
              type="button"
              onClick={() => setStatusTab("published")}
              className={`status-tab tab-published ${statusTab === "published" ? "active" : ""}`}
            >
              Опубликованы ({stats.published})
            </button>
            {stats.existing > 0 && (
              <button
                type="button"
                onClick={() => setStatusTab("existing")}
                className={`status-tab tab-existing ${statusTab === "existing" ? "active" : ""}`}
              >
                Есть в OTTO ({stats.existing})
              </button>
            )}
            <button
              type="button"
              onClick={() => setStatusTab("failed")}
              className={`status-tab tab-failed ${statusTab === "failed" ? "active" : ""}`}
            >
              Ошибки ({stats.failed})
            </button>
          </div>

          {/* Day Filter & Search Input */}
          <div className="drip-queue-right-filters">
            {availableDays.length > 1 && (
              <select
                value={selectedDayFilter}
                onChange={(e) =>
                  setSelectedDayFilter(e.target.value === "all" ? "all" : Number(e.target.value))
                }
                className="drip-queue-day-select"
                aria-label="Фильтр по дню"
              >
                <option value="all">Все дни</option>
                {availableDays.map((d) => (
                  <option key={d} value={d}>
                    День {d}
                  </option>
                ))}
              </select>
            )}

            <div className="drip-queue-search-wrap">
              <Search size={13} className="drip-queue-search-icon" />
              <input
                type="text"
                placeholder="Поиск по EAN или названию..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="drip-queue-search-input"
              />
            </div>
          </div>
        </div>

        {/* Table / List Body */}
        <div className="drip-queue-table-body">
          {loadingItems ? (
            <div className="drip-queue-empty-state">
              <Loader2 size={24} className="animate-spin text-accent" />
              <span>Загрузка товаров очереди...</span>
            </div>
          ) : filteredItems.length === 0 ? (
            <div className="drip-queue-empty-state">
              <div className="drip-queue-empty-icon-box">
                <Layers size={36} style={{ color: "var(--muted)", opacity: 0.5 }} />
              </div>
              <div className="drip-queue-empty-title">
                {plans.length === 0
                  ? "Очередь выгрузки пуста"
                  : "Нет товаров в выбранном плане"}
              </div>
              <div className="drip-queue-empty-desc">
                {plans.length === 0
                  ? "В данный момент для этой фабрики нет запущенных планов Drip Feed. Карточки товаров появятся здесь после настройки расписания."
                  : "Нет товаров, соответствующих выбранным фильтрам."}
              </div>
            </div>
          ) : (
            <table className="drip-queue-table">
              <thead>
                <tr>
                  <th style={{ width: "48px" }}>Фото</th>
                  <th>Название товара</th>
                  <th style={{ width: "140px" }}>EAN</th>
                  <th style={{ width: "180px" }}>Категория</th>
                  <th style={{ width: "130px" }}>День выгрузки</th>
                  <th style={{ width: "150px" }}>Статус</th>
                </tr>
              </thead>
              <tbody>
                {paginatedItems.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => handleOpenProduct(item)}
                    title="Нажмите на товар, чтобы открыть карточку и редактировать"
                    className="drip-queue-row"
                  >
                    {/* Image */}
                    <td>
                      <div className="drip-queue-img-box">
                        {item.displayImage ? (
                          <img
                            src={item.displayImage}
                            alt={item.displayTitle}
                            className="drip-queue-thumb"
                            loading="lazy"
                            onError={(e) => {
                              (e.target as HTMLElement).style.display = "none";
                            }}
                          />
                        ) : (
                          <span className="no-img-label">Нет</span>
                        )}
                      </div>
                    </td>

                    {/* Title & Details */}
                    <td>
                      <div className="drip-queue-item-title" title={item.displayTitle}>
                        {item.displayTitle}
                      </div>

                      {item.otto_product_exists && item.otto_checked_at && (
                        <div className="drip-queue-info-notice notice-existing">
                          <CheckCircle2 size={12} />
                          <span>
                            Карточка найдена в OTTO ({new Date(item.otto_checked_at).toLocaleDateString()}). Доступность покупки активна.
                          </span>
                        </div>
                      )}

                      {!item.otto_product_exists && item.error_message && (
                        <div
                          className={`drip-queue-error-box ${
                            item.status === "failed" ? "box-error" : "box-info"
                          }`}
                        >
                          {item.error_message}
                        </div>
                      )}

                      {!item.otto_product_exists &&
                        item.missing_attributes &&
                        item.missing_attributes.length > 0 && (
                          <div className="drip-queue-missing-attrs">
                            <strong>Отсутствуют:</strong> {item.missing_attributes.join(", ")}
                          </div>
                        )}

                      {item.published_at && (
                        <div className="drip-queue-pub-time">
                          Опубликован: {new Date(item.published_at).toLocaleString("ru-RU")}
                        </div>
                      )}
                    </td>

                    {/* EAN */}
                    <td className="font-mono text-xs">{item.product_ean}</td>

                    {/* Category */}
                    <td>
                      <span className="drip-queue-category-chip" title={item.category_name || item.category_id}>
                        {item.category_name || item.category_id || "Без категории"}
                      </span>
                    </td>

                    {/* Scheduled Day */}
                    <td>
                      <div className="drip-queue-day-text">День {item.scheduled_day}</div>
                      <div className="drip-queue-day-sub">{item.formattedDate}</div>
                    </td>

                    {/* Status Pill */}
                    <td>
                      {item.status === "failed" && item.otto_product_exists ? (
                        <span className="drip-status-pill status-existing">
                          <CheckCircle2 size={12} /> Есть в OTTO
                        </span>
                      ) : item.status === "published" ? (
                        <span className="drip-status-pill status-published">
                          <CheckCircle2 size={12} /> Опубликован
                        </span>
                      ) : item.status === "processing" ? (
                        <span className="drip-status-pill status-processing">
                          <Loader2 size={12} className="animate-spin" /> В обработке OTTO
                        </span>
                      ) : item.status === "failed" ? (
                        <span className="drip-status-pill status-failed">
                          <AlertCircle size={12} /> Ошибка
                        </span>
                      ) : (
                        <span className="drip-status-pill status-pending">
                          <Clock size={12} /> В очереди
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Modal Footer with Pagination */}
        <div className="drip-queue-modal-footer">
          <div className="drip-queue-footer-info">
            {filteredItems.length > 0 ? (
              <>
                Показано {(currentPage - 1) * PAGE_SIZE + 1}–
                {Math.min(currentPage * PAGE_SIZE, filteredItems.length)} из{" "}
                {filteredItems.length}
                {filteredItems.length < items.length && ` (из ${items.length})`}
              </>
            ) : (
              "0 товаров"
            )}
          </div>

          <div className="drip-queue-footer-controls">
            {totalPages > 1 && (
              <div className="drip-queue-pagination">
                <button
                  type="button"
                  disabled={currentPage <= 1}
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  className="page-nav-btn"
                  title="Предыдущая страница"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="page-counter">
                  {currentPage} / {totalPages}
                </span>
                <button
                  type="button"
                  disabled={currentPage >= totalPages}
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  className="page-nav-btn"
                  title="Следующая страница"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            )}

            <button
              type="button"
              onClick={onClose}
              className="drip-queue-btn-close"
            >
              Закрыть
            </button>
          </div>
        </div>
      </div>

      {/* Product Drawer for editing product directly from Queue */}
      {activeDrawerProduct && (
        <ProductDrawer
          product={activeDrawerProduct}
          onClose={() => setActiveDrawerProduct(null)}
          onSaveProduct={handleSaveDrawerProduct}
        />
      )}

      {/* Toast Notification */}
      {toastMessage && (
        <div className="drip-queue-toast" role="status">
          {toastMessage}
        </div>
      )}
    </div>
  );
}

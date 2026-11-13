"use client";

import { useMemo, useState, useTransition, useCallback, useEffect } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Copy,
  Layers,
  Search,
  SlidersHorizontal,
  Sparkles,
  Tag,
  X,
  Zap,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { useTranslation } from "@/features/i18n";
import {
  calculateRestartPriceCents,
  formatPriceEur,
  getProductComputedStatus,
} from "@/features/catalogs/service";
import type {
  CatalogProductItem,
  FactoryProductsMetrics,
  CatalogServerFilters,
  CatalogSyncResult,
} from "@/features/catalogs/types";
import { titleCodePointLength } from "@/features/catalogs/title-policy";
import { ProductSelectionBar } from "./product-selection-bar";
import { ProductionBulkActions } from "./production-bulk-actions";
import type { CatalogBulkAction } from "@/features/catalogs/bulk-actions";
import { ProductDrawer } from "./product-drawer";
import {
  ImageHoverPreview,
  type HoverImagePreviewState,
} from "./image-hover-preview";
import {
  InlineTitleCell,
  InlinePriceCell,
  InlineCategoryCell,
} from "./inline-cells";
import { DripFeedButton } from "./drip-feed-button";
import { DripQueueModal } from "./drip-queue-modal";
import { DripScheduleModal, isDripCandidate } from "./drip-schedule-modal";

export type StatusFilterType =
  | ""
  | "READY"
  | "3M"
  | "12M"
  | "ERROR"
  | "NO_ATTRIBUTES"
  | "NO_CATEGORY"
  | "PUBLISHED";

interface CatalogProductsTableProps {
  demoMode?: boolean;
  sourceType: "AFTERBUY" | "DIRECT_OTTO";
  factoryId?: string;
  factoryName?: string;
  items: CatalogProductItem[];
  metrics: FactoryProductsMetrics;
  onRefresh: () => void;
  onTitleSave: (id: string, newTitle: string) => void;
  onPriceSave: (id: string, newOldPriceCents: number) => void;
  onCategorySave: (id: string, newCategory: string) => void;
  onDrawerSave: (updated: CatalogProductItem) => void;
  onAssignDemoEan?: (selectedIds: string[]) => void;
  onApplyAiTitles: (selectedIds?: string[]) => void;
  onApplyAiAttributes: (selectedIds?: string[]) => void;
  onApplyAiCategories: (selectedIds?: string[]) => void;
  onSyncFromOtto: (
    selectedIds: string[],
    reportProgress: (current: number, total: number) => void,
  ) => Promise<CatalogSyncResult>;
  showToast: (msg: string) => void;
  serverPage?: { page: number; totalPages: number; total: number; over70Count: number; categories: string[] };
  serverLoading?: boolean;
  onServerFiltersChange?: (filters: CatalogServerFilters) => void;
  onServerPageChange?: (page: number) => void;
}

export function CatalogProductsTable({
  demoMode = false,
  sourceType,
  factoryId,
  factoryName,
  items,
  metrics,
  onRefresh,
  onTitleSave,
  onPriceSave,
  onCategorySave,
  onDrawerSave,
  onAssignDemoEan,
  onApplyAiTitles,
  onApplyAiAttributes,
  onApplyAiCategories,
  onSyncFromOtto,
  showToast,
  serverPage,
  serverLoading = false,
  onServerFiltersChange,
  onServerPageChange,
}: CatalogProductsTableProps) {
  const { t } = useTranslation();
  const { account } = useRestartAccount();
  const [, startTransition] = useTransition();

  // Filter & Search states
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilterType>("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [onlyOver70, setOnlyOver70] = useState(false);

  // Sorting state
  const [sort, setSort] = useState<{
    col: "name" | "ean" | "price" | "category" | "status" | null;
    dir: "asc" | "desc";
  }>({ col: null, dir: "asc" });

  useEffect(() => {
    if (demoMode || !onServerFiltersChange) return;
    const timer = setTimeout(() => onServerFiltersChange({
      query: search.trim(),
      period: statusFilter === "3M" || statusFilter === "12M" ? statusFilter : undefined,
      status: statusFilter && statusFilter !== "3M" && statusFilter !== "12M" ? statusFilter : undefined,
      category: categoryFilter === "Без категории" ? "__EMPTY__" : categoryFilter || undefined,
      titleOver70: onlyOver70,
      sortBy: sort.col || undefined,
      sortDir: sort.dir,
    }), 250);
    return () => clearTimeout(timer);
  }, [demoMode, onServerFiltersChange, search, statusFilter, categoryFilter, onlyOver70, sort]);

  // Selection & UI states
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [bulkAction, setBulkAction] = useState<CatalogBulkAction | null>(null);
  useEffect(() => { setSelectedIds([]); }, [serverPage?.page, search, statusFilter, categoryFilter, onlyOver70, sort]);
  const [copiedEan, setCopiedEan] = useState<string | null>(null);
  const [selectedProductForDrawer, setSelectedProductForDrawer] =
    useState<CatalogProductItem | null>(null);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(
    null
  );
  const [isQueueOpen, setIsQueueOpen] = useState(false);
  const [isDripScheduleOpen, setIsDripScheduleOpen] = useState(false);
  const [dripSelection, setDripSelection] = useState<CatalogProductItem[] | null>(null);

  // Unique categories list for combobox filter
  const categoryOptions = useMemo(() => {
    if (!demoMode) return serverPage?.categories || [];
    const set = new Set<string>();
    items.forEach((p) => {
      if (p.category && p.category !== "Без категории") {
        set.add(p.category);
      }
    });
    return Array.from(set).sort();
  }, [items, demoMode, serverPage?.categories]);

  // Count items with title > 70
  const over70Count = useMemo(() => {
    return demoMode ? items.filter((p) => titleCodePointLength(p.title) > 70).length : serverPage?.over70Count || 0;
  }, [items, demoMode, serverPage?.over70Count]);

  const displayedStatus = useCallback((item: CatalogProductItem): "READY" | "3M" | "12M" | "ERROR" | "NO_ATTRIBUTES" | "NO_CATEGORY" | "PUBLISHED" | "UNKNOWN" => {
    if (demoMode) return getProductComputedStatus(item);
    if (["READY", "ERROR", "NO_ATTRIBUTES", "NO_CATEGORY", "PUBLISHED"].includes(item.status)) return item.status as "READY" | "ERROR" | "NO_ATTRIBUTES" | "NO_CATEGORY" | "PUBLISHED";
    return "UNKNOWN";
  }, [demoMode]);

  // Filter & Sort pipeline
  const filteredItems = useMemo(() => {
    if (!demoMode) return items;
    return items.filter((product) => {
      // 1. Search text (name or EAN)
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        const matchesName = product.title.toLowerCase().includes(q);
        const matchesEan = product.ean.toLowerCase().includes(q);
        if (!matchesName && !matchesEan) return false;
      }

      // 2. >70 filter
      if (onlyOver70 && titleCodePointLength(product.title) <= 70) {
        return false;
      }

      // 3. Category filter
      if (categoryFilter) {
        if (categoryFilter === "Без категории") {
          if (product.category && product.category !== "Без категории")
            return false;
        } else if (product.category !== categoryFilter) {
          return false;
        }
      }

      // 4. Status filter (the 5 target statuses)
      if (statusFilter) {
        const computed = displayedStatus(product);
        if (statusFilter === "3M" && product.period !== "3M") return false;
        if (statusFilter === "12M" && product.period !== "12M") return false;
        if (statusFilter === "READY" && computed !== "READY") return false;
        if (statusFilter === "ERROR" && computed !== "ERROR") return false;
        if (statusFilter === "NO_ATTRIBUTES" && computed !== "NO_ATTRIBUTES") return false;
        if (statusFilter === "NO_CATEGORY" && computed !== "NO_CATEGORY") return false;
        if (statusFilter === "PUBLISHED" && computed !== "PUBLISHED")
          return false;
      }

      return true;
    });
  }, [items, demoMode, displayedStatus, search, onlyOver70, categoryFilter, statusFilter]);

  const sortedItems = useMemo(() => {
    if (!demoMode) return filteredItems;
    if (!sort.col) return filteredItems;

    return [...filteredItems].sort((a, b) => {
      let comp = 0;
      if (sort.col === "name") {
        comp = a.title.localeCompare(b.title, "de");
      } else if (sort.col === "ean") {
        comp = a.ean.localeCompare(b.ean);
      } else if (sort.col === "price") {
        comp = a.oldStandardPriceCents - b.oldStandardPriceCents;
      } else if (sort.col === "category") {
        comp = (a.category || "").localeCompare(b.category || "", "de");
      } else if (sort.col === "status") {
        comp = displayedStatus(a).localeCompare(
          displayedStatus(b)
        );
      }
      return sort.dir === "asc" ? comp : -comp;
    });
  }, [filteredItems, sort, demoMode, displayedStatus]);

  // Handle KPI Tile clicks
  function handleKpiTileClick(targetStatus: StatusFilterType) {
    if (statusFilter === targetStatus) {
      setStatusFilter(""); // toggle off
    } else {
      setStatusFilter(targetStatus);
    }
  }

  // Handle Sort Toggle
  function toggleSort(col: "name" | "ean" | "price" | "category" | "status") {
    startTransition(() => {
      setSort((prev) => {
        if (prev.col === col) {
          return { col, dir: prev.dir === "asc" ? "desc" : "asc" };
        }
        return { col, dir: "asc" };
      });
    });
  }

  // Handle Price Sort Buttons (↑ Min / ↓ Max)
  function handlePriceSort(dir: "asc" | "desc", e: React.MouseEvent) {
    e.stopPropagation();
    startTransition(() => {
      setSort({ col: "price", dir });
    });
  }

  // Toggle Selection
  const allFilteredSelected =
    sortedItems.length > 0 &&
    sortedItems.every((item) => selectedIds.includes(item.id));

  function toggleSelectAll() {
    if (serverLoading) return;
    if (allFilteredSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(sortedItems.map((item) => item.id));
    }
  }

  function toggleItemSelection(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (serverLoading) return;
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  }

  // Copy EAN with feedback
  function handleCopyEan(ean: string, e: React.MouseEvent) {
    e.stopPropagation();
    navigator.clipboard.writeText(ean);
    setCopiedEan(ean);
    showToast(`EAN скопирован: ${ean}`);
    setTimeout(() => setCopiedEan(null), 2000);
  }

  // Drawer helper: ensure drawer always opens when clicked, with fresh key to prevent stale state
  const [drawerKey, setDrawerKey] = useState(0);

  function handleProductClick(product: CatalogProductItem) {
    if (serverLoading) return;
    setDrawerKey((k) => k + 1);
    setSelectedProductForDrawer({ ...product });
  }

  // Image Hover Preview
  function handleImageHover(
    product: CatalogProductItem,
    e: React.MouseEvent<HTMLElement>
  ) {
    const rect = e.currentTarget.getBoundingClientRect();
    setImgPreview({
      url: product.imageUrl,
      name: product.title,
      oldPrice: product.sourcePriceAvailable ? formatPriceEur(product.oldStandardPriceCents) : "Цена не подтверждена",
      ean: product.ean,
      category: product.category,
      rect: {
        top: rect.top,
        left: rect.left,
        right: rect.right,
        bottom: rect.bottom,
        height: rect.height,
      },
    });
  }

  function handleImageLeave() {
    setImgPreview(null);
  }

  return (
    <div className="ottoflow-catalog-container">
      {/* 1. TOP KPI SUMMARY TILES (Photo 2) */}
      <section
        className="ottoflow-kpi-strip"
        aria-label="Сводка статусов каталога"
      >
        {/* Tile 1: Все товары */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("")}
          className={`ottoflow-kpi-tile ${statusFilter === "" ? "kpi-active" : ""}`}
        >
          <span className="kpi-label">{t("products.totalCandidates")}</span>
          <span className="kpi-value text-white tabular-nums">{metrics.total}</span>
        </button>

        {/* Tile 2: Готовы */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("READY")}
          className={`ottoflow-kpi-tile ${statusFilter === "READY" ? "kpi-active border-emerald-500" : ""}`}
        >
          <span className="kpi-label">{t("products.statusReady")}</span>
          <span className="kpi-value text-emerald-400 tabular-nums">{metrics.ready}</span>
        </button>

        {/* Tile 3: 3 месяца */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("3M")}
          className={`ottoflow-kpi-tile ${statusFilter === "3M" ? "kpi-active border-amber-500" : ""}`}
        >
          <span className="kpi-label">{t("products.activeRestrictions3M")}</span>
          <span className="kpi-value text-amber-400 tabular-nums">{metrics.count3m}</span>
        </button>

        {/* Tile 4: 12 месяцев */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("12M")}
          className={`ottoflow-kpi-tile ${statusFilter === "12M" ? "kpi-active border-orange-500" : ""}`}
        >
          <span className="kpi-label">{t("products.activeRestrictions12M")}</span>
          <span className="kpi-value text-orange-400 tabular-nums">{metrics.count12m}</span>
        </button>

        {/* Tile 5: Ошибки */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("ERROR")}
          className={`ottoflow-kpi-tile ${statusFilter === "ERROR" ? "kpi-active border-rose-500" : ""}`}
        >
          <span className="kpi-label">{t("products.errors")}</span>
          <span className="kpi-value text-rose-400 tabular-nums">{metrics.errors}</span>
        </button>

        {/* Tile 6: Опубликованы */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("PUBLISHED")}
          className={`ottoflow-kpi-tile ${statusFilter === "PUBLISHED" ? "kpi-active border-teal-500" : ""}`}
        >
          <span className="kpi-label">{t("products.published")}</span>
          <span className="kpi-value text-teal-400 tabular-nums">{metrics.published}</span>
        </button>
      </section>

      {/* 2. MAIN TOOLBAR (Photo 2) */}
      <div className="ottoflow-toolbar">
        {/* Search input on the left */}
        <div className="ottoflow-search-wrap">
          <Search size={16} className="search-icon" />
          <input
            type="text"
            className="ottoflow-search-input"
            placeholder={t("products.searchProducts")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button
              type="button"
              className="search-clear-btn"
              onClick={() => setSearch("")}
              title="Очистить поиск"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Action buttons on the right (Strictly NO ,99, NO в XL, NO Импорт, NO Экспорт) */}
        <div className="ottoflow-toolbar-actions">
          {/* Drip Feed Button */}
          <DripFeedButton
            onClick={() => { setDripSelection(null); setIsDripScheduleOpen(true); }}
          />

          {/* Drip Queue Button */}
          {demoMode ? <button
            type="button"
            className="ottoflow-action-btn btn-drip-queue"
            onClick={() => setIsQueueOpen(true)}
            title="Очередь выгрузки Drip Feed"
          >
            <Layers size={14} />
            <span>{t("products.dripQueue")}</span>
          </button> : null}

          {/* AI Title Button */}
          {demoMode ? <button
            type="button"
            className="ottoflow-action-btn btn-ai-titles"
            onClick={() => {
              onApplyAiTitles(selectedIds.length > 0 ? selectedIds : undefined);
              if (selectedIds.length > 0) setSelectedIds([]);
            }}
            title={
              selectedIds.length > 0
                ? `Оптимизировать названия для ${selectedIds.length} выбранных товаров`
                : t("products.aiTitle")
            }
          >
            <Sparkles size={14} className="text-amber-400" />
            <span>
              {selectedIds.length > 0
                ? `${t("products.aiTitle")} (${selectedIds.length})`
                : t("products.aiTitle")}
            </span>
            {selectedIds.length === 0 && over70Count > 0 && (
              <span className="badge-counter-amber">{over70Count}</span>
            )}
          </button> : null}
        </div>
      </div>

      {/* 3. 7-COLUMN TABLE WITH INDIVIDUAL HEADER FILTERS (Photo 3) */}
      <div className="ottoflow-table-wrapper">
        <table className="ottoflow-table">
          <thead>
            <tr>
              {/* Col 1: Checkbox */}
              <th className="th-checkbox">
                <input
                  type="checkbox"
                  className="table-checkbox"
                  checked={allFilteredSelected}
                  onChange={toggleSelectAll}
                  aria-label="Выбрать все товары на странице"
                />
              </th>

              {/* Col 2: Фото */}
              <th className="th-photo">{t("products.colPhoto")}</th>

              {/* Col 3: Название товара + >70 Filter Button */}
              <th className="th-name">
                <div className="th-name-inner">
                  <span
                    className="th-sortable-label"
                    onClick={() => toggleSort("name")}
                    title="Сортировать по названию"
                  >
                    <span>{t("products.colTitle")}</span>
                    {sort.col === "name" && (
                      <span className="sort-arrow">
                        {sort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </span>

                  {/* >70 Filter Button */}
                  <button
                    type="button"
                    onClick={() => setOnlyOver70((prev) => !prev)}
                    className={`th-filter-btn-70 ${onlyOver70 ? "btn-70-active" : ""}`}
                    title={
                      onlyOver70
                        ? "Показаны только товары с длиной названия > 70. Нажмите для сброса."
                        : "Показать только товары с длиной названия более 70 символов"
                    }
                  >
                    <span>&gt;70</span>
                    {over70Count > 0 && (
                      <span className="btn-70-counter">{over70Count}</span>
                    )}
                    {onlyOver70 && <span className="btn-70-close">✕</span>}
                  </button>
                </div>
              </th>

              {/* Col 4: EAN */}
              <th
                className="th-ean th-sortable-label"
                onClick={() => toggleSort("ean")}
                title="Сортировать по EAN"
              >
                <span>EAN</span>
                {sort.col === "ean" && (
                  <span className="sort-arrow">
                    {sort.dir === "asc" ? "↑" : "↓"}
                  </span>
                )}
              </th>

              {/* Col 5: Цена + Min/Max Sort Buttons */}
              <th className="th-price">
                <div className="th-price-stack">
                  <div
                    className="th-sortable-label"
                    onClick={() => toggleSort("price")}
                  >
                    <span>{t("products.colPrice")}</span>
                    {sort.col === "price" && (
                      <span className="sort-arrow">
                        {sort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </div>
                  <div className="th-price-sort-group">
                    <button
                      type="button"
                      className={`price-sort-btn ${sort.col === "price" && sort.dir === "asc" ? "price-sort-active" : ""}`}
                      onClick={(e) => handlePriceSort("asc", e)}
                      title="Min"
                    >
                      ↑ {t("products.minPrice")}
                    </button>
                    <button
                      type="button"
                      className={`price-sort-btn ${sort.col === "price" && sort.dir === "desc" ? "price-sort-active" : ""}`}
                      onClick={(e) => handlePriceSort("desc", e)}
                      title="Max"
                    >
                      ↓ {t("products.maxPrice")}
                    </button>
                  </div>
                </div>
              </th>

              {/* Col 6: Категория + A-Z + Combobox Filter */}
              <th className="th-category">
                <div className="th-category-stack">
                  <div className="th-category-header-row">
                    <span
                      className="th-sortable-label"
                      onClick={() => toggleSort("category")}
                    >
                      <span>{t("products.colCategory")}</span>
                    </span>
                    <button
                      type="button"
                      className={`th-az-btn ${sort.col === "category" ? "az-active" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSort("category");
                      }}
                      title="A-Z"
                    >
                      A-Z{" "}
                      {sort.col === "category"
                        ? sort.dir === "asc"
                          ? "↑"
                          : "↓"
                        : ""}
                    </button>
                  </div>

                  {/* Category Combobox / Selector */}
                  <div className="th-category-select-wrap">
                    <select
                      className="th-category-select"
                      value={categoryFilter}
                      onChange={(e) => setCategoryFilter(e.target.value)}
                      aria-label={t("products.colCategory")}
                    >
                      <option value="">{t("products.filterCategoryPlaceholder")}</option>
                      <option value="Без категории">Без категории</option>
                      {categoryOptions.map((cat) => (
                        <option key={cat} value={cat}>
                          {cat}
                        </option>
                      ))}
                    </select>
                    <ChevronDown size={12} className="select-dropdown-chevron" />
                  </div>
                </div>
              </th>

              {/* Col 7: Статус + Status Dropdown Filter */}
              <th className="th-status">
                <div className="th-status-stack">
                  <span
                    className="th-sortable-label"
                    onClick={() => toggleSort("status")}
                  >
                    <span>{t("products.colStatus")}</span>
                    {sort.col === "status" && (
                      <span className="sort-arrow">
                        {sort.dir === "asc" ? "↑" : "↓"}
                      </span>
                    )}
                  </span>

                  {/* Status Dropdown with 5 target statuses */}
                  <div className="th-status-select-wrap">
                    <select
                      className="th-status-select"
                      value={statusFilter}
                      onChange={(e) =>
                        setStatusFilter(e.target.value as StatusFilterType)
                      }
                      aria-label={t("products.colStatus")}
                    >
                      <option value="">{t("products.filterAllStatuses")}</option>
                      <option value="READY">{t("products.statusReady")}</option>
                      <option value="NO_ATTRIBUTES">Нет атрибутов</option>
                      <option value="NO_CATEGORY">Нет категории</option>
                      <option value="ERROR">{t("products.errors")}</option>
                      <option value="PUBLISHED">{t("products.published")}</option>
                    </select>
                    <ChevronDown size={12} className="select-dropdown-chevron" />
                  </div>
                </div>
              </th>
            </tr>
          </thead>

          <tbody>
            {sortedItems.length === 0 ? (
              <tr>
                <td colSpan={7} className="td-empty-state">
                  <div className="empty-message-box">
                    <span>Товары не найдены по заданным фильтрам</span>
                    {(search || statusFilter || categoryFilter || onlyOver70) && (
                      <button
                        type="button"
                        className="empty-reset-btn"
                        onClick={() => {
                          setSearch("");
                          setStatusFilter("");
                          setCategoryFilter("");
                          setOnlyOver70(false);
                        }}
                      >
                        Сбросить все фильтры
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ) : (
              sortedItems.map((product) => {
                const isSelected = selectedIds.includes(product.id);
                const computedStatus = displayedStatus(product);
                return (
                  <tr
                    key={product.id}
                    className={`ottoflow-table-row ${isSelected ? "row-selected" : ""}`}
                    onClick={() => handleProductClick(product)}
                  >
                    {/* Col 1: Checkbox */}
                    <td
                      className="td-checkbox"
                      onClick={(e) => toggleItemSelection(product.id, e)}
                    >
                      <input
                        type="checkbox"
                        className="table-checkbox"
                        checked={isSelected}
                        onChange={() => {}}
                        aria-label={`Выбрать ${product.title}`}
                      />
                    </td>

                    {/* Col 2: ФОТО */}
                    <td
                      className="td-photo"
                      onClick={() => handleProductClick(product)}
                    >
                      <div
                        className="photo-thumb-container"
                        onMouseEnter={(e) => handleImageHover(product, e)}
                        onMouseLeave={handleImageLeave}
                      >
                        {product.imageUrl ? (
                          <img
                            src={product.imageUrl}
                            alt=""
                            className="photo-thumb-img"
                            loading="lazy"
                            onError={(e) => {
                              (e.target as HTMLElement).style.display = "none";
                            }}
                          />
                        ) : (
                          <span className="photo-placeholder">—</span>
                        )}
                      </div>
                    </td>

                    {/* Col 3: Название товара + (len/70) Badge */}
                    <td
                      className="td-name"
                      onClick={(e) => {
                        // Let inline cell handle its editing, row handles open
                      }}
                    >
                      <div className="name-cell-wrapper">
                        <div className="title-inline-edit-area">
                          <InlineTitleCell
                            title={product.title}
                            titleOrigin={product.titleOrigin}
                            readOnly={!demoMode}
                            onSave={(newTitle) =>
                              onTitleSave(product.id, newTitle)
                            }
                            onOpenDrawer={() =>
                              handleProductClick(product)
                            }
                          />
                        </div>
                      </div>
                    </td>

                    {/* Col 4: EAN */}
                    <td
                      className="td-ean"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="ean-cell-stack">
                        <button
                          type="button"
                          className={`ean-badge-btn ${product.hasNewEan ? "ean-badge-new" : ""}`}
                          onClick={(e) => handleCopyEan(product.ean, e)}
                          title={
                            product.hasNewEan && product.sourceEan
                              ? `Новый EAN: ${product.ean} (исходный Aftercool EAN: ${product.sourceEan}). Нажмите, чтобы скопировать.`
                              : "Нажмите, чтобы скопировать EAN"
                          }
                        >
                          <span className="ean-text tabular-nums">{product.ean}</span>
                          {copiedEan === product.ean ? (
                            <Check size={12} className="text-emerald-400" />
                          ) : (
                            <Copy size={12} className="ean-copy-icon" />
                          )}
                        </button>
                        {product.hasNewEan && product.sourceEan && (
                          <div className="ean-origin-row">
                            <span className="ean-new-tag">Новый EAN</span>
                            <span className="ean-origin-text tabular-nums" title={`Исходный Aftercool EAN: ${product.sourceEan}`}>
                              ← {product.sourceEan}
                            </span>
                          </div>
                        )}
                      </div>
                    </td>

                    {/* Col 5: Цена — ручное редактирование без скидочной подписи */}
                    <td
                      className="td-price"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <InlinePriceCell
                        oldStandardPriceCents={product.oldStandardPriceCents}
                        priceAvailable={product.sourcePriceAvailable}
                        account={account}
                        readOnly={!demoMode}
                        onSavePriceCents={(newCents) =>
                          onPriceSave(product.id, newCents)
                        }
                      />
                    </td>

                    {/* Col 6: Категория — ручной выбор и поиск по клику */}
                    <td
                      className="td-category"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <InlineCategoryCell
                        category={product.category}
                        readOnly={!demoMode}
                        isSyncedWithOtto={product.isSyncedWithOtto}
                        hasAiCategory={product.hasAiCategory}
                        categoriesList={categoryOptions.length > 0 ? categoryOptions : undefined}
                        onSaveCategory={(newCat) =>
                          onCategorySave(product.id, newCat)
                        }
                      />
                    </td>

                    {/* Col 7: Статус */}
                    <td
                      className="td-status"
                      onClick={() => handleProductClick(product)}
                    >
                      {computedStatus === "READY" && (
                        <span className="status-pill status-ready">
                          <Check size={12} />
                          <span>{t("products.statusReady")}</span>
                        </span>
                      )}

                      {computedStatus === "3M" && (
                        <span className="status-pill status-3m">
                          <span>{t("products.activeRestrictions3M")}</span>
                        </span>
                      )}

                      {computedStatus === "12M" && (
                        <span className="status-pill status-12m">
                          <span>{t("products.activeRestrictions12M")}</span>
                        </span>
                      )}

                      {computedStatus === "ERROR" && (
                        <span
                          className="status-pill status-error"
                          title={
                            product.errorCode ||
                            t("products.statusError")
                          }
                        >
                          <span>{t("products.errors")}</span>
                        </span>
                      )}
                      {computedStatus === "NO_ATTRIBUTES" && <span className="status-pill status-no-attributes">Нет атрибутов</span>}
                      {computedStatus === "NO_CATEGORY" && <span className="status-pill status-no-category">Нет категории</span>}

                      {computedStatus === "PUBLISHED" && (
                        <span
                          className="status-pill status-published"
                          title={t("products.statusPublished")}
                        >
                          <Check size={12} />
                          <span>{t("products.published")}</span>
                        </span>
                      )}

                      {computedStatus === "UNKNOWN" && (
                        <span className="status-pill status-no-category">Проверка статуса</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {!demoMode && serverPage && <div className="catalog-pagination" role="navigation" aria-label="Страницы товаров">
        <button type="button" className="catalog-action-btn" disabled={serverLoading || serverPage.page <= 1} onClick={() => onServerPageChange?.(serverPage.page - 1)}>Назад</button>
        <span>{serverLoading ? "Загрузка…" : `Страница ${serverPage.page} из ${Math.max(1, serverPage.totalPages)} · всего ${serverPage.total}`}</span>
        <button type="button" className="catalog-action-btn" disabled={serverLoading || serverPage.page >= serverPage.totalPages} onClick={() => onServerPageChange?.(serverPage.page + 1)}>Далее</button>
      </div>}

      {/* 4. FLOATING SELECTION BAR (Photo 4) */}
      <ProductSelectionBar
        demoMode={demoMode}
        disabled={serverLoading}
        onProductionAction={demoMode ? undefined : setBulkAction}
        selectedCount={selectedIds.length}
        totalCount={items.length}
        onClearSelection={() => setSelectedIds([])}
        onDripFeed={() => {
          setDripSelection(items.filter((item) => selectedIds.includes(item.id)));
          setIsDripScheduleOpen(true);
        }}
        onSelectAll={() => { if (!serverLoading) setSelectedIds(sortedItems.map((i) => i.id)); }}
        onApplyDefaults={() => {
          showToast(
            `DEMO: локальный preview стандартных параметров для ${selectedIds.length} товаров; данные не сохранены.`
          );
        }}
        onAssignDemoEan={onAssignDemoEan ? () => {
          onAssignDemoEan(selectedIds);
          setSelectedIds([]);
        } : undefined}
        onApplyAiTitles={() => {
          onApplyAiTitles(selectedIds);
          setSelectedIds([]);
        }}
        onSyncFromOtto={async (reportProgress) => {
          try {
            await onSyncFromOtto(selectedIds, reportProgress);
          } finally {
            setSelectedIds([]);
          }
        }}
        onApplyAiCategories={() => {
          onApplyAiCategories(selectedIds);
          setSelectedIds([]);
        }}
        onApplyAiAttributes={() => {
          onApplyAiAttributes(selectedIds);
          setSelectedIds([]);
        }}
      />

      {!demoMode && bulkAction && selectedIds.length > 0 ? <ProductionBulkActions
        action={bulkAction}
        account={account}
        products={items.filter((item) => selectedIds.includes(item.id))}
        onClose={() => setBulkAction(null)}
        onSaved={onRefresh}
      /> : null}

      {/* 5. PRODUCT DRAWER (Full side panel) */}
      {selectedProductForDrawer && (
        <ProductDrawer
          key={`${selectedProductForDrawer.id}-${drawerKey}`}
          product={selectedProductForDrawer}
          onClose={() => setSelectedProductForDrawer(null)}
          demoMode={demoMode}
          onSyncProduct={async (id) => {
            const result = await onSyncFromOtto([id], () => {});
            if (!result.succeededIds.includes(id)) throw new Error(result.failures[0] || "Синхронизация не выполнена.");
          }}
          onProductSaved={demoMode ? undefined : () => onRefresh()}
          onSaveProduct={demoMode ? (updated: CatalogProductItem) => {
            onDrawerSave(updated);
          } : undefined}
        />
      )}

      {/* 6. HOVER IMAGE PREVIEW */}
      <ImageHoverPreview preview={imgPreview} />

      {/* 7. DRIP FEED SCHEDULE MODAL */}
      <DripScheduleModal
        isOpen={isDripScheduleOpen}
        onClose={() => setIsDripScheduleOpen(false)}
        factoryName={factoryName}
        totalProductsCount={demoMode ? (dripSelection ?? items).length : (dripSelection ?? items).filter(isDripCandidate).length}
        products={dripSelection ?? items}
        selectionCount={dripSelection?.length}
        onScheduleCreated={(days, batchName) => {
          showToast(`План Drip Feed (${batchName || "Batch"}) успешно запущен на ${days} дней!`);
        }}
      />

      {/* 8. DRIP FEED QUEUE MODAL */}
      <DripQueueModal
        demoMode={demoMode}
        isOpen={isQueueOpen}
        onClose={() => setIsQueueOpen(false)}
        factoryId={factoryId || "direct-otto"}
        factoryName={factoryName}
      />
    </div>
  );
}

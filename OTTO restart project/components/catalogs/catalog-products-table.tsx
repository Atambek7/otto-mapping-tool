"use client";

import { useMemo, useState, useTransition, useCallback } from "react";
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
import {
  calculateRestartPriceCents,
  formatPriceEur,
  getProductComputedStatus,
} from "@/features/catalogs/service";
import type {
  CatalogProductItem,
  FactoryProductsMetrics,
} from "@/features/catalogs/types";
import { ProductSelectionBar } from "./product-selection-bar";
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
import { DripScheduleModal } from "./drip-schedule-modal";

export type StatusFilterType =
  | ""
  | "READY"
  | "3M"
  | "12M"
  | "ERROR"
  | "PUBLISHED";

interface CatalogProductsTableProps {
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
  onApplyAiTitles: (selectedIds?: string[]) => void;
  onApplyAiAttributes: (selectedIds?: string[]) => void;
  onApplyAiCategories: (selectedIds?: string[]) => void;
  onSyncFromOtto: (selectedIds?: string[]) => void;
  showToast: (msg: string) => void;
}

export function CatalogProductsTable({
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
  onApplyAiTitles,
  onApplyAiAttributes,
  onApplyAiCategories,
  onSyncFromOtto,
  showToast,
}: CatalogProductsTableProps) {
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

  // Selection & UI states
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [copiedEan, setCopiedEan] = useState<string | null>(null);
  const [selectedProductForDrawer, setSelectedProductForDrawer] =
    useState<CatalogProductItem | null>(null);
  const [imgPreview, setImgPreview] = useState<HoverImagePreviewState | null>(
    null
  );
  const [isQueueOpen, setIsQueueOpen] = useState(false);
  const [isDripScheduleOpen, setIsDripScheduleOpen] = useState(false);

  // Unique categories list for combobox filter
  const categoryOptions = useMemo(() => {
    const set = new Set<string>();
    items.forEach((p) => {
      if (p.category && p.category !== "Без категории") {
        set.add(p.category);
      }
    });
    return Array.from(set).sort();
  }, [items]);

  // Count items with title > 70
  const over70Count = useMemo(() => {
    return items.filter((p) => p.title.length > 70).length;
  }, [items]);

  // Filter & Sort pipeline
  const filteredItems = useMemo(() => {
    return items.filter((product) => {
      // 1. Search text (name or EAN)
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        const matchesName = product.title.toLowerCase().includes(q);
        const matchesEan = product.ean.toLowerCase().includes(q);
        if (!matchesName && !matchesEan) return false;
      }

      // 2. >70 filter
      if (onlyOver70 && product.title.length <= 70) {
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
        const computed = getProductComputedStatus(product);
        if (statusFilter === "3M" && product.period !== "3M") return false;
        if (statusFilter === "12M" && product.period !== "12M") return false;
        if (statusFilter === "READY" && computed !== "READY") return false;
        if (statusFilter === "ERROR" && computed !== "ERROR") return false;
        if (statusFilter === "PUBLISHED" && computed !== "PUBLISHED")
          return false;
      }

      return true;
    });
  }, [items, search, onlyOver70, categoryFilter, statusFilter]);

  const sortedItems = useMemo(() => {
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
        comp = getProductComputedStatus(a).localeCompare(
          getProductComputedStatus(b)
        );
      }
      return sort.dir === "asc" ? comp : -comp;
    });
  }, [filteredItems, sort]);

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
    if (allFilteredSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(sortedItems.map((item) => item.id));
    }
  }

  function toggleItemSelection(id: string, e: React.MouseEvent) {
    e.stopPropagation();
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
      oldPrice: formatPriceEur(product.oldStandardPriceCents),
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
        {/* Tile 1: ВСЕ ТОВАРЫ */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("")}
          className={`ottoflow-kpi-tile ${statusFilter === "" ? "kpi-active" : ""}`}
        >
          <span className="kpi-label">ВСЕ ТОВАРЫ</span>
          <span className="kpi-value text-white">{metrics.total}</span>
        </button>

        {/* Tile 2: ГОТОВЫ */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("READY")}
          className={`ottoflow-kpi-tile ${statusFilter === "READY" ? "kpi-active border-emerald-500" : ""}`}
        >
          <span className="kpi-label">ГОТОВЫ</span>
          <span className="kpi-value text-emerald-400">{metrics.ready}</span>
        </button>

        {/* Tile 3: 3 МЕСЯЦА */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("3M")}
          className={`ottoflow-kpi-tile ${statusFilter === "3M" ? "kpi-active border-amber-500" : ""}`}
        >
          <span className="kpi-label">3 МЕСЯЦА</span>
          <span className="kpi-value text-amber-400">{metrics.count3m}</span>
        </button>

        {/* Tile 4: 12 МЕСЯЦЕВ */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("12M")}
          className={`ottoflow-kpi-tile ${statusFilter === "12M" ? "kpi-active border-orange-500" : ""}`}
        >
          <span className="kpi-label">12 МЕСЯЦЕВ</span>
          <span className="kpi-value text-orange-400">{metrics.count12m}</span>
        </button>

        {/* Tile 5: ОШИБКИ */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("ERROR")}
          className={`ottoflow-kpi-tile ${statusFilter === "ERROR" ? "kpi-active border-rose-500" : ""}`}
        >
          <span className="kpi-label">ОШИБКИ</span>
          <span className="kpi-value text-rose-400">{metrics.errors}</span>
        </button>

        {/* Tile 6: ОПУБЛИКОВАНЫ */}
        <button
          type="button"
          onClick={() => handleKpiTileClick("PUBLISHED")}
          className={`ottoflow-kpi-tile ${statusFilter === "PUBLISHED" ? "kpi-active border-teal-500" : ""}`}
        >
          <span className="kpi-label">ОПУБЛИКОВАНЫ</span>
          <span className="kpi-value text-teal-400">{metrics.published}</span>
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
            placeholder="Поиск товаров по названию или EAN..."
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
            onClick={() => setIsDripScheduleOpen(true)}
          />

          {/* Drip Queue Button */}
          <button
            type="button"
            className="ottoflow-action-btn btn-drip-queue"
            onClick={() => setIsQueueOpen(true)}
            title="Очередь выгрузки Drip Feed"
          >
            <Layers size={14} />
            <span>Очередь Drip Feed</span>
          </button>

          {/* AI Title Button */}
          <button
            type="button"
            className="ottoflow-action-btn btn-ai-titles"
            onClick={() => {
              onApplyAiTitles(selectedIds.length > 0 ? selectedIds : undefined);
              if (selectedIds.length > 0) setSelectedIds([]);
            }}
            title={
              selectedIds.length > 0
                ? `Оптимизировать названия для ${selectedIds.length} выбранных товаров`
                : "Оптимизировать названия товаров > 70 символов"
            }
          >
            <Sparkles size={14} className="text-amber-400" />
            <span>
              {selectedIds.length > 0
                ? `ИИ-название (выбрано ${selectedIds.length})`
                : "ИИ-название (>70 симв.)"}
            </span>
            {selectedIds.length === 0 && over70Count > 0 && (
              <span className="badge-counter-amber">{over70Count}</span>
            )}
          </button>
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

              {/* Col 2: ФОТО */}
              <th className="th-photo">ФОТО</th>

              {/* Col 3: Название товара + >70 Filter Button */}
              <th className="th-name">
                <div className="th-name-inner">
                  <span
                    className="th-sortable-label"
                    onClick={() => toggleSort("name")}
                    title="Сортировать по названию"
                  >
                    <span>Название товара</span>
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
                    <span>Цена</span>
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
                      title="По возрастанию (дешевые сверху)"
                    >
                      ↑ Min
                    </button>
                    <button
                      type="button"
                      className={`price-sort-btn ${sort.col === "price" && sort.dir === "desc" ? "price-sort-active" : ""}`}
                      onClick={(e) => handlePriceSort("desc", e)}
                      title="По убыванию (дорогие сверху)"
                    >
                      ↓ Max
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
                      <span>Категория</span>
                    </span>
                    <button
                      type="button"
                      className={`th-az-btn ${sort.col === "category" ? "az-active" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSort("category");
                      }}
                      title="Сортировка категорий по алфавиту: A-Z"
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
                      aria-label="Фильтр по категории"
                    >
                      <option value="">Фильтр по категории...</option>
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
                    <span>Статус</span>
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
                      aria-label="Фильтр по статусу"
                    >
                      <option value="">Все статусы</option>
                      <option value="READY">Готово</option>
                      <option value="3M">3 месяца</option>
                      <option value="12M">12 месяцев</option>
                      <option value="ERROR">Ошибки</option>
                      <option value="PUBLISHED">Опубликованы</option>
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
                const computedStatus = getProductComputedStatus(product);
                const titleLen = product.title.length;
                const isOver70 = titleLen > 70;

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
                      <button
                        type="button"
                        className="ean-badge-btn"
                        onClick={(e) => handleCopyEan(product.ean, e)}
                        title="Нажмите, чтобы скопировать EAN"
                      >
                        <span className="ean-text">{product.ean}</span>
                        {copiedEan === product.ean ? (
                          <Check size={12} className="text-emerald-400" />
                        ) : (
                          <Copy size={12} className="ean-copy-icon" />
                        )}
                      </button>
                    </td>

                    {/* Col 5: Цена — ручное редактирование без скидочной подписи */}
                    <td
                      className="td-price"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <InlinePriceCell
                        oldStandardPriceCents={product.oldStandardPriceCents}
                        account={account}
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
                          <span>Готово</span>
                        </span>
                      )}

                      {computedStatus === "3M" && (
                        <span className="status-pill status-3m">
                          <span>3 месяца</span>
                        </span>
                      )}

                      {computedStatus === "12M" && (
                        <span className="status-pill status-12m">
                          <span>12 месяцев</span>
                        </span>
                      )}

                      {computedStatus === "ERROR" && (
                        <span
                          className="status-pill status-error"
                          title={
                            product.errorCode ||
                            "Не заполнены обязательные атрибуты"
                          }
                        >
                          <span>Ошибки</span>
                        </span>
                      )}

                      {computedStatus === "PUBLISHED" && (
                        <span
                          className="status-pill status-published"
                          title="Карточка опубликована в каталоге OTTO"
                        >
                          <Check size={12} />
                          <span>Опубликованы</span>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 4. FLOATING SELECTION BAR (Photo 4) */}
      <ProductSelectionBar
        selectedCount={selectedIds.length}
        totalCount={items.length}
        onClearSelection={() => setSelectedIds([])}
        onSelectAll={() => setSelectedIds(items.map((i) => i.id))}
        onApplyDefaults={() => {
          showToast(
            `✓ Применены стандартные параметры фабрики для ${selectedIds.length} товаров`
          );
        }}
        onApplyAiTitles={() => {
          onApplyAiTitles(selectedIds);
          setSelectedIds([]);
        }}
        onSyncFromOtto={() => {
          onSyncFromOtto(selectedIds);
          setSelectedIds([]);
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

      {/* 5. PRODUCT DRAWER (Full side panel) */}
      {selectedProductForDrawer && (
        <ProductDrawer
          key={`${selectedProductForDrawer.id}-${drawerKey}`}
          product={selectedProductForDrawer}
          onClose={() => setSelectedProductForDrawer(null)}
          onSaveProduct={(updated: CatalogProductItem) => {
            onDrawerSave(updated);
          }}
        />
      )}

      {/* 6. HOVER IMAGE PREVIEW */}
      <ImageHoverPreview preview={imgPreview} />

      {/* 7. DRIP FEED SCHEDULE MODAL */}
      <DripScheduleModal
        isOpen={isDripScheduleOpen}
        onClose={() => setIsDripScheduleOpen(false)}
        factoryName={factoryName}
        totalProductsCount={items.length}
        onScheduleCreated={(days) => {
          showToast(`✓ Drip Feed запущен: план на ${days} дней успешно сформирован`);
        }}
      />

      {/* 8. DRIP FEED QUEUE MODAL */}
      <DripQueueModal
        isOpen={isQueueOpen}
        onClose={() => setIsQueueOpen(false)}
        factoryId={factoryId || "direct-otto"}
        factoryName={factoryName}
      />
    </div>
  );
}

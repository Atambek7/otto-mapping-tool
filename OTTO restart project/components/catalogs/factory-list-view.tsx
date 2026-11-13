"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowUpDown,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  Clock,
  Database,
  Layers,
  LayoutGrid,
  List,
  Package,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService } from "@/features/catalogs/service";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import { useTranslation } from "@/features/i18n";
import type { RestartFactory, RestartFactoryListResponse } from "@/features/api/types";
import type { AfterbuyFactoryItem } from "@/features/catalogs/types";

const restartApi = new RestartApiClient();

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  } catch {
    return dateStr;
  }
}

function ProductionFactoryListView() {
  const router = useRouter();
  const { t } = useTranslation();
  const { account, ready: accountReady } = useRestartAccount();
  const [response, setResponse] = useState<RestartFactoryListResponse | null>(null);
  const [search, setSearch] = useState("");
  const [showOnlyCached, setShowOnlyCached] = useState(false);
  const [sortBy, setSortBy] = useState<"count-desc" | "new-first" | "count-asc" | "name">("count-desc");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!accountReady) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    restartApi
      .listFactories(account, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setResponse(data);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setResponse(null);
          setError(reason instanceof Error ? reason.message : "Backend фабрик недоступен.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [account, accountReady, refreshKey]);

  const cachedCount = useMemo(() => {
    return (response?.items ?? []).filter((item) => item.totalCandidates > 0).length;
  }, [response]);

  const items = useMemo(() => {
    let result = [...(response?.items ?? [])];
    if (showOnlyCached) {
      result = result.filter((item) => item.totalCandidates > 0);
    }
    const needle = search.trim().toLocaleLowerCase("ru-RU");
    if (needle) {
      result = result.filter((item) =>
        `${item.name} ${item.id}`.toLocaleLowerCase("ru-RU").includes(needle)
      );
    }
    const comparators: Record<typeof sortBy, (a: RestartFactory, b: RestartFactory) => number> = {
      "count-desc": (a, b) => b.totalCandidates - a.totalCandidates,
      "new-first": (a, b) => b.newCandidatesCount - a.newCandidatesCount || b.totalCandidates - a.totalCandidates,
      "count-asc": (a, b) => a.totalCandidates - b.totalCandidates,
      name: (a, b) => a.name.localeCompare(b.name),
    };
    return result.sort(comparators[sortBy]);
  }, [response, search, sortBy, showOnlyCached]);

  function toggleSelect(id: string, e?: React.MouseEvent) {
    if (e) e.stopPropagation();
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  }

  function toggleSelectAll() {
    if (selectedIds.length === items.length && items.length > 0) {
      setSelectedIds([]);
    } else {
      setSelectedIds(items.map((i) => i.id));
    }
  }

  if (!accountReady || (loading && !response)) {
    return <ApiState kind="loading" label="Загружаем подтверждённые данные фабрик…" />;
  }

  if (error && !response) {
    return (
      <ApiState
        kind="error"
        title="Фабрики недоступны"
        description={error}
        onRetry={() => setRefreshKey((value) => value + 1)}
      />
    );
  }

  const metrics = response;

  return (
    <div className="catalog-page">
      {/* Breadcrumb */}
      <nav className="catalog-breadcrumb" aria-label={t("factories.breadcrumbTitle")}>
        <Link href="/">{t("factories.breadcrumbHome")}</Link>
        <span className="breadcrumb-separator">/</span>
        <span className="current">{t("factories.breadcrumbTitle")}</span>
      </nav>

      {/* Header */}
      <div className="factory-header">
        <div className="factory-title-block">
          <h1>{t("factories.title")}</h1>
          <p>{t("factories.subtitle", { account })}</p>
        </div>
        <button
          type="button"
          className="catalog-action-btn"
          onClick={() => setRefreshKey((value) => value + 1)}
          disabled={loading}
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          <span>{t("factories.refreshList")}</span>
        </button>
      </div>

      {error ? <div className="api-state api-state-error" role="alert">{error}</div> : null}

      {/* 5-Column Compact Summary Strip (Matches Photo 2) */}
      <div className="factory-summary-strip">
        <div className="summary-strip-item">
          <span className="summary-strip-label">{t("factories.totalProducts")}</span>
          <span className="summary-strip-val val-accent tabular-nums">
            {(metrics?.totalCandidates ?? 0).toLocaleString("ru-RU")}
          </span>
        </div>
        <div className="summary-strip-item">
          <span className="summary-strip-label">{t("factories.factoriesCount")}</span>
          <span className="summary-strip-val val-white tabular-nums">
            {metrics?.totalCount ?? 0}
          </span>
        </div>
        <div className="summary-strip-item">
          <span className="summary-strip-label">{t("factories.complete")}</span>
          <span className="summary-strip-val val-emerald tabular-nums">
            {metrics?.completeCount ?? 0}
          </span>
        </div>
        <div className="summary-strip-item">
          <span className="summary-strip-label">{t("factories.notComplete")}</span>
          <span className="summary-strip-val val-amber tabular-nums">
            {metrics?.notCompleteCount ?? 0}
          </span>
        </div>
        <div className="summary-strip-item">
          <span className="summary-strip-label">{t("factories.inCache")}</span>
          <span className="summary-strip-val val-blue tabular-nums">
            {cachedCount.toLocaleString("ru-RU")}
          </span>
        </div>
      </div>

      {/* Toolbar (Matches Photo 2 Layout) */}
      <div className="catalog-toolbar">
        <div className="catalog-toolbar-left">
          <div className="catalog-search-wrap">
            <Search size={16} />
            <input
              type="text"
              className="catalog-search-input"
              aria-label={t("factories.searchPlaceholder")}
              placeholder={t("factories.searchPlaceholder")}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>

        <div className="catalog-toolbar-actions">
          {/* Cached Filter Button */}
          <button
            type="button"
            className={`catalog-action-btn ${showOnlyCached ? "btn-accent" : ""}`}
            onClick={() => setShowOnlyCached((prev) => !prev)}
          >
            <Database size={13} />
            <span>{t("factories.inCacheBtn")} ({cachedCount})</span>
          </button>

          {/* Sort Dropdown with icons */}
          <div className="catalog-sort-wrap">
            <ArrowUpDown size={13} className="catalog-sort-icon-left" />
            <select
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value as typeof sortBy)}
              aria-label="Сортировка фабрик"
            >
              <option value="count-desc">{t("factories.sortByCountDesc")}</option>
              <option value="new-first">{t("factories.sortByNew")}</option>
              <option value="count-asc">{t("factories.sortByCountAsc")}</option>
              <option value="name">{t("factories.sortByName")}</option>
            </select>
            <ChevronDown size={13} className="catalog-sort-icon-right" />
          </div>

          {/* Select All Button */}
          <button
            type="button"
            className="catalog-action-btn"
            onClick={toggleSelectAll}
          >
            <span>{selectedIds.length === items.length && items.length > 0 ? t("factories.deselectAll") : t("factories.selectAll")}</span>
          </button>

          {/* View Mode Toggle */}
          <div className="view-mode-toggle">
            <button
              type="button"
              className={`view-toggle-btn ${viewMode === "list" ? "active" : ""}`}
              onClick={() => setViewMode("list")}
              title="Список"
              aria-label="Список"
            >
              <List size={15} />
            </button>
            <button
              type="button"
              className={`view-toggle-btn ${viewMode === "grid" ? "active" : ""}`}
              onClick={() => setViewMode("grid")}
              title="Сетка"
              aria-label="Сетка"
            >
              <LayoutGrid size={15} />
            </button>
          </div>
        </div>
      </div>

      {loading && response ? <ApiState kind="loading" label="Обновляем состояние фабрик…" /> : null}

      {/* Main Content: 3-Column Grid vs List (Matches Photo 2) */}
      {items.length ? (
        viewMode === "grid" ? (
          <div className="factory-grid">
            {items.map((factory) => {
              const isSelected = selectedIds.includes(factory.id);
              const isCached = factory.totalCandidates > 0;
              return (
                <div
                  key={`${factory.account}:${factory.id}`}
                  onClick={() => router.push(`/afterbuy/${encodeURIComponent(factory.id)}`)}
                  className={`factory-card ${isSelected ? "selected" : ""}`}
                >
                  {/* Top Bar: Checkbox on left; Badges & Link Arrow on right (Photo 2) */}
                  <div className="factory-card-top">
                    <div className="checkbox-area" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        className="factory-card-checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelect(factory.id)}
                        aria-label={`Выбрать фабрику ${factory.name}`}
                      />
                    </div>

                    <div className="factory-card-top-right">
                      {isCached && (
                        <span className="factory-status-badge badge-cached">
                          <Database size={10} />
                          {t("factories.inCacheBtn")}
                        </span>
                      )}
                      {factory.status === "COMPLETE" && (
                        <span className="factory-status-badge badge-complete">
                          <CheckCircle2 size={11} />
                          COMPLETE
                        </span>
                      )}
                      {factory.status === "NOT_COMPLETE" && (
                        <span className="factory-status-badge badge-not-complete">
                          <AlertCircle size={11} />
                          NOT COMPLETE
                        </span>
                      )}
                      {factory.newCandidatesCount > 0 && (
                        <span className="factory-status-badge badge-new-candidates">
                          <Sparkles size={10} />
                          {t("factories.newN")} {factory.newCandidatesCount}
                        </span>
                      )}
                      <button
                        type="button"
                        className="factory-arrow-btn"
                        title={t("factories.open")}
                        onClick={(e) => {
                          e.stopPropagation();
                          router.push(`/afterbuy/${encodeURIComponent(factory.id)}`);
                        }}
                      >
                        <ArrowUpRight size={14} />
                      </button>
                    </div>
                  </div>

                  {/* Middle Info: Icon box + Name + ID (Photo 2) */}
                  <div className="factory-card-middle">
                    <div className="factory-icon-box">
                      <Layers size={18} />
                    </div>
                    <div className="factory-info">
                      <span className="factory-name" title={factory.name}>
                        {factory.name}
                      </span>
                      <span className="factory-id">ID: {factory.id}</span>
                    </div>
                  </div>

                  <div className="factory-card-divider" />

                  {/* Bottom Row: Product count & 3M/12M pills (Photo 2) */}
                  <div className="factory-card-bottom">
                    <div className="factory-items-count">
                      <Package size={14} />
                      <span>{factory.totalCandidates.toLocaleString("ru-RU")} {t("factories.products312M")}</span>
                    </div>

                    <div className="factory-period-pills">
                      {factory.period3mCount > 0 && (
                        <span className="period-pill pill-3m">3M: {factory.period3mCount}</span>
                      )}
                      {factory.period12mCount > 0 && (
                        <span className="period-pill pill-12m">12M: {factory.period12mCount}</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          /* List View (Dense Enterprise Row Mode) */
          <div className="factory-list-wrap">
            {items.map((factory) => {
              const isSelected = selectedIds.includes(factory.id);
              const isCached = factory.totalCandidates > 0;
              return (
                <div
                  key={`${factory.account}:${factory.id}`}
                  onClick={() => router.push(`/afterbuy/${encodeURIComponent(factory.id)}`)}
                  className={`factory-list-row ${isSelected ? "selected" : ""}`}
                >
                  <div className="factory-list-row-left">
                    <div className="checkbox-area" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        className="factory-card-checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelect(factory.id)}
                        aria-label={`Выбрать фабрику ${factory.name}`}
                      />
                    </div>
                    <div className="factory-icon-box sm">
                      <Layers size={16} />
                    </div>
                    <div className="factory-info">
                      <div className="factory-name-row">
                        <span className="factory-name" title={factory.name}>
                          {factory.name}
                        </span>
                        {isCached && (
                          <span className="factory-status-badge badge-cached">
                            <Database size={10} /> В кэше
                          </span>
                        )}
                        {factory.status === "COMPLETE" && (
                          <span className="factory-status-badge badge-complete">
                            <CheckCircle2 size={11} /> COMPLETE
                          </span>
                        )}
                        {factory.status === "NOT_COMPLETE" && (
                          <span className="factory-status-badge badge-not-complete">
                            <AlertCircle size={11} /> NOT COMPLETE
                          </span>
                        )}
                        {factory.newCandidatesCount > 0 && (
                          <span className="factory-status-badge badge-new-candidates">
                            +{factory.newCandidatesCount}
                          </span>
                        )}
                      </div>
                      <span className="factory-id">ID: {factory.id}</span>
                    </div>
                  </div>

                  <div className="factory-list-row-right">
                    <div className="factory-items-count">
                      <Package size={14} />
                      <span>{factory.totalCandidates.toLocaleString("ru-RU")} {t("factories.products312M")}</span>
                    </div>
                    <div className="factory-period-pills">
                      {factory.period3mCount > 0 && (
                        <span className="period-pill pill-3m">3M: {factory.period3mCount}</span>
                      )}
                      {factory.period12mCount > 0 && (
                        <span className="period-pill pill-12m">12M: {factory.period12mCount}</span>
                      )}
                    </div>
                    <button
                      type="button"
                      className="catalog-action-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        router.push(`/afterbuy/${encodeURIComponent(factory.id)}`);
                      }}
                    >
                      <span>{t("factories.open")}</span>
                      <ArrowUpRight size={13} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )
      ) : loading ? null : (
        <ApiState
          kind="empty"
          title={t("common.noData")}
          description={t("common.empty")}
        />
      )}

      {/* Floating Batch Action Bar */}
      {selectedIds.length > 0 && (
        <div className="batch-action-bar-floating">
          <div className="batch-action-count">
            <span className="batch-badge">{selectedIds.length}</span>
            <span>{t("factories.selectedCount", { count: selectedIds.length })}</span>
          </div>
          <div className="batch-divider" />
          <button
            type="button"
            className="catalog-action-btn btn-accent"
            onClick={() => {
              if (selectedIds.length === 1) {
                router.push(`/afterbuy/${encodeURIComponent(selectedIds[0])}`);
              }
            }}
          >
            <Clock size={13} />
            <span>{t("factories.startDripFeed")}</span>
          </button>
          <button
            type="button"
            className="catalog-action-btn"
            onClick={() => setSelectedIds([])}
          >
            <X size={13} />
            <span>{t("factories.deselectAll")}</span>
          </button>
        </div>
      )}
    </div>
  );
}

function DemoFactoryListView() {
  const router = useRouter();
  const { account } = useRestartAccount();
  const isDemo = isOttoRestartDemoMode("afterbuy");
  const [search, setSearch] = useState("");
  const [showOnlyCached, setShowOnlyCached] = useState(false);
  const [sortBy, setSortBy] = useState<"count-desc" | "new-first" | "count-asc" | "name">("count-desc");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const [asyncData, setAsyncData] = useState<{
    items: AfterbuyFactoryItem[];
    metrics: {
      totalCandidates: number;
      totalFactories: number;
      count3m: number;
      count12m: number;
      cachedCount: number;
      completeCount: number;
      notCompleteCount: number;
      unknownCount: number;
      totalNewCandidates: number;
    };
  } | null>(null);

  useEffect(() => {
    if (!isDemo) {
      setAsyncData(null);
      return;
    }
    const demo = catalogDemoService.getFactories(account, search);
    setAsyncData({
      items: demo.items,
      metrics: {
        ...demo.metrics,
        completeCount: 0,
        notCompleteCount: demo.items.length,
        unknownCount: 0,
        totalNewCandidates: 0,
      },
    });
  }, [account, search, sortBy, refreshTrigger, isDemo]);

  const { items, metrics } = useMemo(() => {
    if (!asyncData) {
      return {
        items: [],
        metrics: {
          totalCandidates: 0,
          totalFactories: 0,
          count3m: 0,
          count12m: 0,
          cachedCount: 0,
          completeCount: 0,
          notCompleteCount: 0,
          unknownCount: 0,
          totalNewCandidates: 0,
        },
      };
    }

    let sorted = [...asyncData.items];
    if (sortBy === "count-desc") {
      sorted.sort((a, b) => b.totalCandidates - a.totalCandidates);
    } else if (sortBy === "new-first") {
      sorted.sort((a, b) => {
        const aNew = a.newCandidatesCount || 0;
        const bNew = b.newCandidatesCount || 0;
        if (aNew !== bNew) return bNew - aNew;
        return b.totalCandidates - a.totalCandidates;
      });
    } else if (sortBy === "count-asc") {
      sorted.sort((a, b) => a.totalCandidates - b.totalCandidates);
    } else if (sortBy === "name") {
      sorted.sort((a, b) => a.name.localeCompare(b.name));
    }

    if (showOnlyCached) {
      sorted = sorted.filter((f) => f.isCached);
    }

    return { items: sorted, metrics: asyncData.metrics };
  }, [asyncData, sortBy, showOnlyCached]);

  function toggleSelectAll() {
    if (selectedIds.length === items.length && items.length > 0) {
      setSelectedIds([]);
    } else {
      setSelectedIds(items.map((i) => i.id));
    }
  }

  function toggleSelect(id: string, e?: React.MouseEvent) {
    if (e) e.stopPropagation();
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  }

  if (!isDemo) {
    return <RuntimeModeGate title="Каталог фабрик недоступен" demoEnabled={false} />;
  }

  return (
    <RuntimeModeGate title="Каталог фабрик недоступен" demoEnabled={isDemo} showDemoBanner={false}>
      <div className="catalog-page">
        {/* Breadcrumb */}
        <nav className="catalog-breadcrumb" aria-label="Хлебные крошки">
          <Link href="/">Добро пожаловать</Link>
          <span className="breadcrumb-separator">/</span>
          <span className="current">Фабрики</span>
        </nav>

        {/* Header */}
        <div className="factory-header">
          <div className="factory-title-block">
            <h1>Фабрики</h1>
            <p>Управление каталогами поставщиков и рабочими пространствами маппинга для {account}</p>
          </div>
          <button
            type="button"
            className="catalog-action-btn"
            onClick={() => setRefreshTrigger((n) => n + 1)}
          >
            <RefreshCw size={14} />
            <span>Обновить список</span>
          </button>
        </div>

        {/* 5-Column Compact Summary Strip (Matches Photo 2) */}
        <div className="factory-summary-strip">
          <div className="summary-strip-item">
            <span className="summary-strip-label">ВСЕГО ТОВАРОВ</span>
            <span className="summary-strip-val val-accent tabular-nums">
              {metrics.totalCandidates.toLocaleString("ru-RU")}
            </span>
          </div>
          <div className="summary-strip-item">
            <span className="summary-strip-label">ФАБРИК</span>
            <span className="summary-strip-val val-white tabular-nums">
              {metrics.totalFactories}
            </span>
          </div>
          <div className="summary-strip-item">
            <span className="summary-strip-label">COMPLETE</span>
            <span className="summary-strip-val val-emerald tabular-nums">
              {metrics.completeCount}
            </span>
          </div>
          <div className="summary-strip-item">
            <span className="summary-strip-label">NOT COMPLETE</span>
            <span className="summary-strip-val val-amber tabular-nums">
              {metrics.notCompleteCount}
            </span>
          </div>
          <div className="summary-strip-item">
            <span className="summary-strip-label">В КЭШЕ</span>
            <span className="summary-strip-val val-blue tabular-nums">
              {metrics.cachedCount.toLocaleString("ru-RU")}
            </span>
          </div>
        </div>

        {/* Toolbar (Matches Photo 2) */}
        <div className="catalog-toolbar">
          <div className="catalog-toolbar-left">
            <div className="catalog-search-wrap">
              <Search size={16} />
              <input
                type="text"
                className="catalog-search-input"
                placeholder="Поиск фабрики по названию или ID..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="catalog-toolbar-actions">
            <button
              type="button"
              className={`catalog-action-btn ${showOnlyCached ? "btn-accent" : ""}`}
              onClick={() => setShowOnlyCached((prev) => !prev)}
            >
              <Database size={13} />
              <span>В кэше ({metrics.cachedCount})</span>
            </button>

            <div className="catalog-sort-wrap">
              <ArrowUpDown size={13} className="catalog-sort-icon-left" />
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as any)}
                aria-label="Сортировка фабрик"
              >
                <option value="count-desc">Товары (по убыванию)</option>
                <option value="new-first">Сначала новые</option>
                <option value="count-asc">Товары (по возрастанию)</option>
                <option value="name">По названию (A-Z)</option>
              </select>
              <ChevronDown size={13} className="catalog-sort-icon-right" />
            </div>

            <button
              type="button"
              className="catalog-action-btn"
              onClick={toggleSelectAll}
            >
              <span>{selectedIds.length === items.length && items.length > 0 ? "Снять выбор" : "Выбрать все"}</span>
            </button>

            <div className="view-mode-toggle">
              <button
                type="button"
                className={`view-toggle-btn ${viewMode === "list" ? "active" : ""}`}
                onClick={() => setViewMode("list")}
                title="Список"
                aria-label="Список"
              >
                <List size={15} />
              </button>
              <button
                type="button"
                className={`view-toggle-btn ${viewMode === "grid" ? "active" : ""}`}
                onClick={() => setViewMode("grid")}
                title="Сетка"
                aria-label="Сетка"
              >
                <LayoutGrid size={15} />
              </button>
            </div>
          </div>
        </div>

        {/* Grid vs List */}
        {items.length ? (
          viewMode === "grid" ? (
            <div className="factory-grid">
              {items.map((factory) => {
                const isSelected = selectedIds.includes(factory.id);
                const status = factory.status || "UNKNOWN";

                return (
                  <div
                    key={factory.id}
                    onClick={() => router.push(`/afterbuy/${encodeURIComponent(factory.id)}`)}
                    className={`factory-card ${isSelected ? "selected" : ""}`}
                  >
                    <div className="factory-card-top">
                      <div className="checkbox-area" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          className="factory-card-checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(factory.id)}
                          aria-label={`Выбрать фабрику ${factory.name}`}
                        />
                      </div>

                      <div className="factory-card-top-right">
                        {factory.isCached && (
                          <span className="factory-status-badge badge-cached">
                            <Database size={10} />
                            В кэше
                          </span>
                        )}
                        {status === "COMPLETE" && (
                          <span className="factory-status-badge badge-complete">
                            <CheckCircle2 size={11} />
                            COMPLETE
                          </span>
                        )}
                        {status === "NOT_COMPLETE" && (
                          <span className="factory-status-badge badge-not-complete">
                            <AlertCircle size={11} />
                            NOT COMPLETE
                          </span>
                        )}
                        <button
                          type="button"
                          className="factory-arrow-btn"
                          title="Открыть фабрику"
                          onClick={(e) => {
                            e.stopPropagation();
                            router.push(`/afterbuy/${encodeURIComponent(factory.id)}`);
                          }}
                        >
                          <ArrowUpRight size={14} />
                        </button>
                      </div>
                    </div>

                    <div className="factory-card-middle">
                      <div className="factory-icon-box">
                        <Layers size={18} />
                      </div>
                      <div className="factory-info">
                        <span className="factory-name" title={factory.name}>
                          {factory.name}
                        </span>
                        <span className="factory-id">ID: {factory.id}</span>
                      </div>
                    </div>

                    <div className="factory-card-divider" />

                    <div className="factory-card-bottom">
                      <div className="factory-items-count">
                        <Package size={14} />
                        <span>{factory.totalCandidates.toLocaleString("ru-RU")} товаров 3/12M</span>
                      </div>

                      <div className="factory-period-pills">
                        <span className="period-pill pill-3m">3M: {factory.candidatesCount3m}</span>
                        <span className="period-pill pill-12m">12M: {factory.candidatesCount12m}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="factory-list-wrap">
              {items.map((factory) => {
                const isSelected = selectedIds.includes(factory.id);
                const status = factory.status || "UNKNOWN";

                return (
                  <div
                    key={factory.id}
                    onClick={() => router.push(`/afterbuy/${encodeURIComponent(factory.id)}`)}
                    className={`factory-list-row ${isSelected ? "selected" : ""}`}
                  >
                    <div className="factory-list-row-left">
                      <div className="checkbox-area" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          className="factory-card-checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(factory.id)}
                          aria-label={`Выбрать фабрику ${factory.name}`}
                        />
                      </div>
                      <div className="factory-icon-box sm">
                        <Layers size={16} />
                      </div>
                      <div className="factory-info">
                        <div className="factory-name-row">
                          <span className="factory-name" title={factory.name}>
                            {factory.name}
                          </span>
                          {factory.isCached && (
                            <span className="factory-status-badge badge-cached">
                              <Database size={10} /> В кэше
                            </span>
                          )}
                          {status === "COMPLETE" && (
                            <span className="factory-status-badge badge-complete">
                              <CheckCircle2 size={11} /> COMPLETE
                            </span>
                          )}
                          {status === "NOT_COMPLETE" && (
                            <span className="factory-status-badge badge-not-complete">
                              <AlertCircle size={11} /> NOT COMPLETE
                            </span>
                          )}
                        </div>
                        <span className="factory-id">ID: {factory.id}</span>
                      </div>
                    </div>

                    <div className="factory-list-row-right">
                      <div className="factory-items-count">
                        <Package size={14} />
                        <span>{factory.totalCandidates.toLocaleString("ru-RU")} товаров 3/12M</span>
                      </div>
                      <div className="factory-period-pills">
                        <span className="period-pill pill-3m">3M: {factory.candidatesCount3m}</span>
                        <span className="period-pill pill-12m">12M: {factory.candidatesCount12m}</span>
                      </div>
                      <button
                        type="button"
                        className="catalog-action-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          router.push(`/afterbuy/${encodeURIComponent(factory.id)}`);
                        }}
                      >
                        <span>Открыть</span>
                        <ArrowUpRight size={13} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )
        ) : (
          <div style={{ textAlign: "center", padding: "40px", color: "var(--muted)" }}>
            <p>Фабрики по выбранным критериям не найдены.</p>
          </div>
        )}

        {/* Floating Batch Action Bar */}
        {selectedIds.length > 0 && (
          <div className="batch-action-bar-floating">
            <div className="batch-action-count">
              <span className="batch-badge">{selectedIds.length}</span>
              <span>Выбрано фабрик: {selectedIds.length}</span>
            </div>
            <div className="batch-divider" />
            <button
              type="button"
              className="catalog-action-btn btn-accent"
              onClick={() => {
                if (selectedIds.length === 1) {
                  router.push(`/afterbuy/${encodeURIComponent(selectedIds[0])}`);
                }
              }}
            >
              <Clock size={13} />
              <span>Запустить Drip Feed</span>
            </button>
            <button
              type="button"
              className="catalog-action-btn"
              onClick={() => setSelectedIds([])}
            >
              <X size={13} />
              <span>Снять выбор</span>
            </button>
          </div>
        )}
      </div>
    </RuntimeModeGate>
  );
}

export function FactoryListView() {
  const mode = getOttoRestartMode();
  if (mode === "DEMO" || isOttoRestartDemoMode("afterbuy")) return <DemoFactoryListView />;
  if (mode === "PRODUCTION" || mode === "TEST") return <ProductionFactoryListView />;
  return <RuntimeModeGate title="Каталог фабрик недоступен" />;
}

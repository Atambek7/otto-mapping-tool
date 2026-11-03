"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  CheckSquare,
  Database,
  Factory,
  Grid,
  HelpCircle,
  List,
  Package,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService } from "@/features/catalogs/service";
import type { AfterbuyFactoryItem, FactoryCompletionStatus } from "@/features/catalogs/types";

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

export function FactoryListView() {
  const { account } = useRestartAccount();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "COMPLETE" | "NOT_COMPLETE">("ALL");
  const [cachedOnly, setCachedOnly] = useState(false);
  const [sortBy, setSortBy] = useState<"count-desc" | "new-first" | "count-asc" | "name">("count-desc");
  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
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

  // Load real PostgreSQL data via backend proxy with automatic fallback
  useEffect(() => {
    let active = true;
    catalogDemoService
      .getFactoriesAsync(account, search, statusFilter, sortBy === "new-first")
      .then((data) => {
        if (active) setAsyncData(data);
      })
      .catch(() => {
        if (active) {
          const fallback = catalogDemoService.getFactories(account, search);
          setAsyncData({
            items: fallback.items,
            metrics: {
              ...fallback.metrics,
              completeCount: 0,
              notCompleteCount: fallback.items.length,
              unknownCount: 0,
              totalNewCandidates: 0,
            },
          });
        }
      });
    return () => {
      active = false;
    };
  }, [account, search, statusFilter, sortBy, refreshTrigger]);

  const { items, metrics } = useMemo(() => {
    if (!asyncData) {
      const fallback = catalogDemoService.getFactories(account, search);
      return {
        items: fallback.items,
        metrics: {
          ...fallback.metrics,
          completeCount: 0,
          notCompleteCount: fallback.items.length,
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

    if (cachedOnly) {
      sorted = sorted.filter((f) => f.isCached);
    }

    return { items: sorted, metrics: asyncData.metrics };
  }, [asyncData, sortBy, cachedOnly, account, search]);

  function toggleSelectAll() {
    if (selectedIds.length === items.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(items.map((i) => i.id));
    }
  }

  function toggleSelect(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  }

  return (
    <div className="catalog-page">
      {/* Breadcrumbs */}
      <nav className="catalog-breadcrumb" aria-label="Хлебные крошки">
        <Link href="/">Добро пожаловать</Link>
        <span className="breadcrumb-separator">/</span>
        <span className="current">Товары Afterbuy</span>
      </nav>

      {/* Header */}
      <div className="factory-header">
        <div className="factory-title-block">
          <h1>Фабрики Afterbuy</h1>
          <p>
            Каталоги поставщиков Aftercool с контролем завершённости (COMPLETE / NOT COMPLETE) для аккаунта {account}
          </p>
        </div>
        <div className="factory-header-actions">
          <button
            type="button"
            className="catalog-action-btn"
            onClick={() => setRefreshTrigger((n) => n + 1)}
          >
            <RefreshCw size={14} />
            Обновить список
          </button>
        </div>
      </div>

      {/* 5 KPI Metric Cards */}
      <div className="catalog-metrics-row">
        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">Всего товаров 3/12M</span>
          <span className="catalog-metric-val color-blue tabular-nums">
            {metrics.totalCandidates.toLocaleString("ru-RU")}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">Фабрик (Завершено)</span>
          <span className="catalog-metric-val tabular-nums">
            <span style={{ color: "var(--color-accent)" }}>{metrics.completeCount}</span>
            <span style={{ fontSize: "14px", color: "var(--color-ink-2)", fontWeight: 500 }}>
              {" "}из {metrics.totalFactories}
            </span>
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">Новые кандидаты</span>
          <span className="catalog-metric-val color-amber tabular-nums">
            {metrics.totalNewCandidates}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">3 месяца (Nonseller)</span>
          <span className="catalog-metric-val color-amber tabular-nums">
            {metrics.count3m.toLocaleString("ru-RU")}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">12 месяцев (Low Performer)</span>
          <span className="catalog-metric-val color-rose tabular-nums">
            {metrics.count12m.toLocaleString("ru-RU")}
          </span>
        </div>
      </div>

      {/* Completion Filter Tabs */}
      <div className="factory-filter-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={statusFilter === "ALL"}
          className={`factory-filter-tab ${statusFilter === "ALL" ? "active" : ""}`}
          onClick={() => setStatusFilter("ALL")}
        >
          Все фабрики ({metrics.totalFactories})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={statusFilter === "COMPLETE"}
          className={`factory-filter-tab tab-complete ${statusFilter === "COMPLETE" ? "active" : ""}`}
          onClick={() => setStatusFilter("COMPLETE")}
        >
          <CheckCircle2 size={13} />
          COMPLETE ({metrics.completeCount})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={statusFilter === "NOT_COMPLETE"}
          className={`factory-filter-tab tab-not-complete ${statusFilter === "NOT_COMPLETE" ? "active" : ""}`}
          onClick={() => setStatusFilter("NOT_COMPLETE")}
        >
          <AlertCircle size={13} />
          NOT COMPLETE ({metrics.notCompleteCount})
        </button>
      </div>

      {/* Search & Filter Toolbar */}
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

          <button
            type="button"
            className={`catalog-action-btn ${cachedOnly ? "btn-accent" : ""}`}
            onClick={() => setCachedOnly((prev) => !prev)}
          >
            <Database size={13} />
            В кэше ({metrics.cachedCount})
          </button>

          <select
            className="catalog-select"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as any)}
            aria-label="Сортировка фабрик"
          >
            <option value="count-desc">Товары (по убыванию)</option>
            <option value="new-first">Сначала новые кандидаты</option>
            <option value="count-asc">Товары (по возрастанию)</option>
            <option value="name">По названию (A-Z)</option>
          </select>
        </div>

        <div className="catalog-toolbar-actions">
          <button
            type="button"
            className="catalog-action-btn"
            onClick={toggleSelectAll}
          >
            <CheckSquare size={14} />
            {selectedIds.length === items.length && items.length > 0
              ? "Снять выбор"
              : "Выбрать все"}
          </button>

          <div style={{ display: "flex", gap: "2px" }}>
            <button
              type="button"
              className={`catalog-action-btn ${viewMode === "grid" ? "btn-accent" : ""}`}
              onClick={() => setViewMode("grid")}
              style={{ padding: "0 8px" }}
              title="Сетка"
            >
              <Grid size={15} />
            </button>
            <button
              type="button"
              className={`catalog-action-btn ${viewMode === "table" ? "btn-accent" : ""}`}
              onClick={() => setViewMode("table")}
              style={{ padding: "0 8px" }}
              title="Список"
            >
              <List size={15} />
            </button>
          </div>
        </div>
      </div>

      {/* Grid of Factory Cards */}
      {viewMode === "grid" ? (
        <div className="factory-grid">
          {items.map((factory) => {
            const isSelected = selectedIds.includes(factory.id);
            const status = factory.status || "UNKNOWN";
            const newCount = factory.newCandidatesCount || 0;

            return (
              <Link
                key={factory.id}
                href={`/afterbuy/${factory.id}`}
                className={`factory-card ${status === "COMPLETE" ? "factory-card-complete" : ""}`}
              >
                <div className="factory-card-top">
                  <div className="factory-card-brand">
                    <input
                      type="checkbox"
                      className="factory-card-checkbox"
                      checked={isSelected}
                      onClick={(e) => toggleSelect(factory.id, e)}
                      onChange={() => {}}
                    />
                    <div className="factory-icon-box">
                      <Factory size={19} />
                    </div>
                    <div className="factory-info">
                      <span className="factory-name">{factory.name}</span>
                      <span className="factory-id">ID: {factory.id}</span>
                    </div>
                  </div>

                  <span className="factory-arrow-icon">
                    <ArrowUpRight size={17} />
                  </span>
                </div>

                <div className="factory-card-divider" />

                <div className="factory-card-bottom">
                  <div className="factory-card-bottom-main">
                    <div className="factory-items-count">
                      <Package size={14} />
                      <span>
                        {factory.totalCandidates.toLocaleString("ru-RU")} товаров 3/12M
                      </span>
                    </div>

                    <div className="factory-period-pills">
                      <span className="period-pill pill-3m">
                        3M: {factory.candidatesCount3m}
                      </span>
                      <span className="period-pill pill-12m">
                        12M: {factory.candidatesCount12m}
                      </span>
                    </div>
                  </div>

                  {/* Status, New Candidates, and Completion Date directly under "X товаров 3/12M" */}
                  <div className="factory-card-status-row">
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
                    {status === "UNKNOWN" && (
                      <span className="factory-status-badge badge-unknown">
                        <HelpCircle size={11} />
                        UNKNOWN
                      </span>
                    )}

                    {newCount > 0 && (
                      <span className="factory-status-badge badge-new-candidates">
                        <Sparkles size={10} />
                        Новые {newCount}
                      </span>
                    )}

                    {/* Completion Date Subline */}
                    {status === "COMPLETE" && factory.lastCompletedAt && (
                      <span className="factory-completion-subline complete">
                        ✓ Завершено {formatDate(factory.lastCompletedAt)}
                      </span>
                    )}
                    {factory.wasPreviouslyCompleted && factory.lastCompletedAt && (
                      <span className="factory-completion-subline previously-complete">
                        Ранее завершено {formatDate(factory.lastCompletedAt)}
                      </span>
                    )}
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      ) : (
        /* Table View */
        <div className="product-table-card">
          <div className="product-table-wrap">
            <table className="catalog-product-table">
              <thead>
                <tr>
                  <th style={{ width: "40px" }}>
                    <input
                      type="checkbox"
                      checked={selectedIds.length === items.length && items.length > 0}
                      onChange={toggleSelectAll}
                    />
                  </th>
                  <th>Фабрика</th>
                  <th>ID</th>
                  <th>Статус</th>
                  <th>Новые</th>
                  <th>Кандидатов 3/12M</th>
                  <th>3M (Nonseller)</th>
                  <th>12M (Low Performer)</th>
                  <th>Завершено</th>
                  <th style={{ textAlign: "right" }}>Действие</th>
                </tr>
              </thead>
              <tbody>
                {items.map((factory) => {
                  const status = factory.status || "UNKNOWN";
                  const newCount = factory.newCandidatesCount || 0;
                  return (
                    <tr key={factory.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(factory.id)}
                          onChange={() => {
                            setSelectedIds((prev) =>
                              prev.includes(factory.id)
                                ? prev.filter((i) => i !== factory.id)
                                : [...prev, factory.id]
                            );
                          }}
                        />
                      </td>
                      <td>
                        <Link
                          href={`/afterbuy/${factory.id}`}
                          style={{ color: "var(--ink)", fontWeight: 700, textDecoration: "none" }}
                        >
                          {factory.name}
                        </Link>
                      </td>
                      <td>
                        <span className="ean-code-pill">{factory.id}</span>
                      </td>
                      <td>
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
                        {status === "UNKNOWN" && (
                          <span className="factory-status-badge badge-unknown">
                            UNKNOWN
                          </span>
                        )}
                      </td>
                      <td>
                        {newCount > 0 ? (
                          <span className="factory-status-badge badge-new-candidates">
                            +{newCount}
                          </span>
                        ) : (
                          <span style={{ color: "var(--muted)" }}>0</span>
                        )}
                      </td>
                      <td>
                        <strong style={{ color: "var(--accent)" }}>
                          {factory.totalCandidates}
                        </strong>
                      </td>
                      <td>
                        <span className="period-pill pill-3m">{factory.candidatesCount3m}</span>
                      </td>
                      <td>
                        <span className="period-pill pill-12m">{factory.candidatesCount12m}</span>
                      </td>
                      <td>
                        {factory.lastCompletedAt ? (
                          <span style={{ fontSize: "12px", color: "var(--color-ink-2)" }}>
                            {formatDate(factory.lastCompletedAt)}
                          </span>
                        ) : (
                          <span style={{ color: "var(--muted)" }}>—</span>
                        )}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <Link
                          href={`/afterbuy/${factory.id}`}
                          className="catalog-action-btn"
                          style={{ display: "inline-flex" }}
                        >
                          Открыть
                          <ArrowUpRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {items.length === 0 && (
        <div style={{ textAlign: "center", padding: "40px", color: "var(--muted)" }}>
          <p>Фабрики по выбранным критериям не найдены.</p>
        </div>
      )}
    </div>
  );
}

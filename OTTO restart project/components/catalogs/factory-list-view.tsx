"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowUpRight,
  Boxes,
  CheckSquare,
  Database,
  Factory,
  Grid,
  List,
  Package,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService } from "@/features/catalogs/service";
import type { AfterbuyFactoryItem } from "@/features/catalogs/types";

export function FactoryListView() {
  const { account } = useRestartAccount();
  const [search, setSearch] = useState("");
  const [cachedOnly, setCachedOnly] = useState(false);
  const [sortBy, setSortBy] = useState<"count-desc" | "count-asc" | "name">("count-desc");
  const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const { items, metrics } = useMemo(() => {
    const res = catalogDemoService.getFactories(account, search, cachedOnly);
    let sorted = [...res.items];
    if (sortBy === "count-desc") {
      sorted.sort((a, b) => b.totalCandidates - a.totalCandidates);
    } else if (sortBy === "count-asc") {
      sorted.sort((a, b) => a.totalCandidates - b.totalCandidates);
    } else if (sortBy === "name") {
      sorted.sort((a, b) => a.name.localeCompare(b.name));
    }
    return { items: sorted, metrics: res.metrics };
  }, [account, search, cachedOnly, sortBy, refreshTrigger]);

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
      {/* Breadcrumbs matching Screenshot 1 */}
      <nav className="catalog-breadcrumb" aria-label="Хлебные крошки">
        <Link href="/">Панель управления</Link>
        <span className="breadcrumb-separator">/</span>
        <span className="current">Фабрики 3/12M (Afterbuy)</span>
      </nav>

      {/* Header */}
      <div className="factory-header">
        <div className="factory-title-block">
          <h1>Фабрики</h1>
          <p>
            Каталоги поставщиков Aftercool с выявленными товарами 3/12 месяцев для {account}
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

      {/* 5 KPI Metric Cards matching Screenshot 1 */}
      <div className="catalog-metrics-row">
        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">
            Всего товаров 3/12M
            <Boxes size={14} />
          </span>
          <span className="catalog-metric-val color-blue">
            {metrics.totalCandidates.toLocaleString("ru-RU")}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">
            Фабрик
            <Factory size={14} />
          </span>
          <span className="catalog-metric-val">
            {metrics.totalFactories}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">
            3M (Nonseller)
            <Sparkles size={14} />
          </span>
          <span className="catalog-metric-val color-amber">
            {metrics.count3m.toLocaleString("ru-RU")}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">
            12M (Low Performer)
            <Sparkles size={14} />
          </span>
          <span className="catalog-metric-val color-rose">
            {metrics.count12m.toLocaleString("ru-RU")}
          </span>
        </div>

        <div className="catalog-metric-tile">
          <span className="catalog-metric-label">
            В кэше
            <Database size={14} />
          </span>
          <span className="catalog-metric-val color-green">
            {metrics.cachedCount}
          </span>
        </div>
      </div>

      {/* Search & Filter Toolbar matching Screenshot 1 */}
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

      {/* Grid of Factory Cards matching Screenshot 1 */}
      {viewMode === "grid" ? (
        <div className="factory-grid">
          {items.map((factory) => {
            const isSelected = selectedIds.includes(factory.id);
            return (
              <Link
                key={factory.id}
                href={`/afterbuy/${factory.id}`}
                className="factory-card"
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

                  <div className="factory-card-badges">
                    {factory.isCached && (
                      <span className="cached-badge">
                        <Database size={10} />
                        В кэше
                      </span>
                    )}
                    <span className="factory-arrow-icon">
                      <ArrowUpRight size={17} />
                    </span>
                  </div>
                </div>

                <div className="factory-card-divider" />

                <div className="factory-card-bottom">
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
                  <th>Товаров в каталоге</th>
                  <th>Кандидатов 3/12M</th>
                  <th>3M (Nonseller)</th>
                  <th>12M (Low Performer)</th>
                  <th>Кэш</th>
                  <th style={{ textAlign: "right" }}>Действие</th>
                </tr>
              </thead>
              <tbody>
                {items.map((factory) => (
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
                    <td>{factory.totalProductsCatalog.toLocaleString("ru-RU")}</td>
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
                      {factory.isCached ? (
                        <span className="cached-badge">В кэше</span>
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
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {items.length === 0 && (
        <div style={{ textAlign: "center", padding: "40px", color: "var(--muted)" }}>
          <p>Фабрики с выявленными товарами 3/12M не найдены.</p>
        </div>
      )}
    </div>
  );
}

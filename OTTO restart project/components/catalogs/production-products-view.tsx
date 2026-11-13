"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { RefreshCw, Search } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import type { RestartPeriod, RestartProduct, RestartProductListResponse } from "@/features/api/types";
import { useRestartAccount } from "@/features/state/restart-account";

const api = new RestartApiClient();
const PAGE_SIZE = 50;

interface ProductionProductsViewProps {
  title: string;
  description: string;
  factoryId?: string;
  source?: RestartProduct["source"];
  backHref?: string;
  backLabel?: string;
}

function displayValue(value: string | null | undefined): string {
  return value && value.trim() ? value : "—";
}

function displayPrice(priceCents: number | null): string {
  if (priceCents === null) return "—";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(priceCents / 100);
}

function displayDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU");
}

function OttoSourceDetails({ product }: { product: RestartProduct }) {
  const [open, setOpen] = useState(false);
  const [sourceData, setSourceData] = useState<Record<string, unknown> | null>(
    Object.keys(product.sourceData ?? {}).length ? product.sourceData : null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || sourceData) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void api.getProduct(product.candidateEpisodeId, product.account, controller.signal)
      .then((detail) => {
        if (!controller.signal.aborted) setSourceData(detail.sourceData);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "Не удалось загрузить данные OTTO.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, product.account, product.candidateEpisodeId, sourceData]);

  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Данные OTTO · атрибутов {product.attributes.length}</summary>
      {open ? (
        loading ? <small>Загружаем исходные данные…</small>
          : error ? <small role="alert">{error}</small>
            : <pre className="production-source-data">{JSON.stringify(sourceData ?? {}, null, 2)}</pre>
      ) : null}
    </details>
  );
}

export function ProductionProductsView({
  title,
  description,
  factoryId,
  source,
  backHref,
  backLabel,
}: ProductionProductsViewProps) {
  const { account, ready: accountReady } = useRestartAccount();
  const [result, setResult] = useState<RestartProductListResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [period, setPeriod] = useState<RestartPeriod | "">("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const metricsCacheRef = useRef(new Map<string, {
    refreshVersion: number;
    metrics: NonNullable<RestartProductListResponse["metrics"]>;
  }>());
  const metricsScope = `${account}|${source ?? ""}|${factoryId ?? ""}`;

  useEffect(() => {
    if (!accountReady) return;
    const controller = new AbortController();
    let current = true;
    const cachedMetrics = metricsCacheRef.current.get(metricsScope);
    const includeMetrics = cachedMetrics?.refreshVersion !== refreshVersion;
    setLoading(true);
    setError(null);
    setResult(null);

    const timer = window.setTimeout(() => {
      void api.listProducts(account, {
        page,
        pageSize: PAGE_SIZE,
        period: period || undefined,
        status: status.trim() || undefined,
        source,
        factoryId,
        query: query.trim() || undefined,
        includeMetrics,
        signal: controller.signal,
      }).then((response) => {
        if (!current) return;
        if (response.metrics) {
          metricsCacheRef.current.set(metricsScope, { refreshVersion, metrics: response.metrics });
          if (metricsCacheRef.current.size > 32) {
            const oldestScope = metricsCacheRef.current.keys().next().value;
            if (oldestScope) metricsCacheRef.current.delete(oldestScope);
          }
        }
        setResult({ ...response, metrics: response.metrics ?? metricsCacheRef.current.get(metricsScope)?.metrics ?? null });
        setLoading(false);
      }).catch((requestError: unknown) => {
        if (!current || controller.signal.aborted) return;
        setError(requestError);
        setLoading(false);
      });
    }, query ? 250 : 0);

    return () => {
      current = false;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [account, accountReady, factoryId, metricsScope, page, period, query, refreshVersion, source, status]);

  const productRows = useMemo(() => result?.items ?? [], [result]);
  const pageInfo = result?.page;
  const accountMetrics = result?.metrics;
  const resolvedTitle = factoryId
    ? productRows.find((item) => item.factoryName)?.factoryName ?? title
    : title;
  const apiError = error instanceof RestartApiError ? error : null;
  const permissionDenied = apiError?.status === 401 || apiError?.status === 403;

  return (
    <div className="catalog-page production-products-view">
      {backHref && backLabel ? (
        <nav className="catalog-breadcrumb" aria-label="Хлебные крошки">
          <Link href={backHref}>{backLabel}</Link>
          <span className="breadcrumb-separator">/</span>
          <span className="current">{resolvedTitle}</span>
        </nav>
      ) : null}

      <div className="factory-header">
        <div className="factory-title-block">
          <h1>{resolvedTitle}</h1>
          <p className="tabular-nums">{description} (аккаунт {account})</p>
        </div>
        <div className="factory-header-actions">
          <button
            type="button"
            className="catalog-action-btn"
            onClick={() => setRefreshVersion((value) => value + 1)}
            disabled={loading}
            title="Обновить проверенные данные backend"
          >
            <RefreshCw size={14} />
            <span>Обновить</span>
          </button>
        </div>
      </div>

      <section className="ottoflow-kpi-strip" aria-label="Сводка API по аккаунту">
        <div className="ottoflow-kpi-tile">
          <span className="kpi-label">Открытые кандидаты · аккаунт</span>
          <span className="kpi-value text-white tabular-nums">{accountMetrics?.total ?? "—"}</span>
        </div>
        <div className="ottoflow-kpi-tile">
          <span className="kpi-label">3 месяца · аккаунт</span>
          <span className="kpi-value text-amber-400 tabular-nums">{accountMetrics?.count3m ?? "—"}</span>
        </div>
        <div className="ottoflow-kpi-tile">
          <span className="kpi-label">12 месяцев · аккаунт</span>
          <span className="kpi-value text-orange-400 tabular-nums">{accountMetrics?.count12m ?? "—"}</span>
        </div>
      </section>

      <div className="ottoflow-toolbar" role="search">
        <label className="ottoflow-search-wrap">
          <span className="sr-only">Поиск по каталогу</span>
          <Search size={16} className="search-icon" />
          <input
            type="search"
            className="ottoflow-search-input"
            maxLength={200}
            placeholder="Поиск по SKU, EAN, MOIN, исходному названию или фабрике…"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setPage(1); }}
          />
        </label>
        <div className="ottoflow-toolbar-actions production-catalog-filters">
          <label>
            <span>Период</span>
            <select value={period} onChange={(event) => { setPeriod(event.target.value as RestartPeriod | ""); setPage(1); }}>
              <option value="">Все</option>
              <option value="3M">3M</option>
              <option value="12M">12M</option>
            </select>
          </label>
          <label>
            <span>Статус API</span>
            <input
              type="text"
              maxLength={50}
              placeholder="точное значение"
              value={status}
              onChange={(event) => { setStatus(event.target.value); setPage(1); }}
            />
          </label>
        </div>
      </div>

      {loading ? <ApiState kind="loading" label="Загружаем каталог из Restart API…" /> : null}
      {!loading && error ? (
        <ApiState
          kind={permissionDenied ? "permission-denied" : "error"}
          title={permissionDenied ? "Нет доступа к выбранному аккаунту" : "Каталог backend недоступен"}
          description={apiError?.message ?? "Не удалось получить данные каталога. Локальные/demo данные не подставлялись."}
          onRetry={() => setRefreshVersion((value) => value + 1)}
        />
      ) : null}

      {!loading && !error && result && result.items.length === 0 ? (
        <ApiState kind="empty" title="Backend не вернул кандидатов для этих фильтров" description="Это результат account-scoped запроса к production API; локальные данные не подставлялись." />
      ) : null}

      {!loading && !error && result && result.items.length > 0 ? (
        <>
          <div className="ottoflow-table-wrapper">
            <table className="ottoflow-table production-products-table">
              <thead>
                <tr>
                  <th>Период</th><th>SKU OTTO</th><th>EAN</th><th>Название</th><th>Фабрика</th>
                  <th>Цена OTTO</th><th>Категория / данные</th><th>Статус backend</th><th>Изменён OTTO</th><th>Ошибки</th>
                </tr>
              </thead>
              <tbody>
                {productRows.map((product) => (
                  <tr key={product.candidateEpisodeId}>
                    <td>{displayValue(product.period)}</td>
                    <td>
                      <span className="tabular-nums">{displayValue(product.sku)}</span>
                      {product.targetSku ? <small className="production-subvalue">Целевой SKU: {product.targetSku}</small> : null}
                    </td>
                    <td>
                      <span className="tabular-nums">{displayValue(product.ean)}</span>
                      {product.newEan ? <small className="production-subvalue">Целевой EAN: {product.newEan}</small> : null}
                    </td>
                    <td>
                      {product.imageUrl ? (
                        // Only the first validated OTTO media asset is imported and displayed.
                        <img className="production-product-image" src={product.imageUrl} alt="" loading="lazy" />
                      ) : null}
                      <span>{displayValue(product.title)}</span>
                    </td>
                    <td>{displayValue(product.factoryName ?? product.factoryId)}</td>
                    <td>{displayPrice(product.priceCents)}</td>
                    <td>
                      <span>{displayValue(product.category)}</span>
                      <OttoSourceDetails product={product} />
                    </td>
                    <td>
                      <span className="status-pill">{product.status}</span>
                      <small className="production-subvalue">{product.classification} · {product.origin}</small>
                    </td>
                    <td>{displayDate(product.lastModified)}</td>
                    <td>
                      {product.errorCodes.length || product.errorTitles.length ? (
                        <details>
                          <summary>Коды: {product.errorCodes.length} · тексты: {product.errorTitles.length}</summary>
                          {product.errorCodes.length ? <small className="production-error-detail">Коды: {product.errorCodes.join(", ")}</small> : null}
                          {product.errorTitles.length ? <small className="production-error-detail">Тексты: {product.errorTitles.join("; ")}</small> : null}
                        </details>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="production-catalog-pagination" aria-label="Пагинация каталога">
            <span>Всего после фильтрации: {pageInfo?.total ?? 0}</span>
            <div>
              <button type="button" className="catalog-action-btn" disabled={!pageInfo || pageInfo.page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>Назад</button>
              <span>Страница {pageInfo?.page ?? page} / {pageInfo?.totalPages ?? 0}</span>
              <button type="button" className="catalog-action-btn" disabled={!pageInfo || pageInfo.page >= pageInfo.totalPages || loading} onClick={() => setPage((value) => value + 1)}>Дальше</button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useRestartAccount } from "@/features/state/restart-account";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";
import { RestartApiClient } from "@/features/api/client";
import { catalogDemoService, formatPriceEur } from "@/features/catalogs/service";
import type { RestartProduct, RestartPeriod, RestartProductListResponse } from "@/features/api/types";
import type {
  CatalogProductItem,
  FactoryProductsMetrics,
  ProductRestartStatus,
  CatalogServerFilters,
} from "./types";
import { titleCodePointLength } from "./title-policy";

const restartApi = new RestartApiClient();
const requiredSchemaCache = new Map<string, string[] | null>();

function hasValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasValue);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return hasValue(record.value ?? record.name ?? "");
  }
  return value !== null && value !== undefined && String(value).trim() !== "";
}

function deriveCardStatus(item: CatalogProductItem, required: string[] | null): CatalogProductItem["status"] {
  if (item.operationStatus === "PUBLISHED" || item.status === "PUBLISHED") return "PUBLISHED";
  if (!item.category.trim() || item.category === "Без категории" || item.category === "None") return "NO_CATEGORY";
  const attrs = item.attributes ?? {};
  if (!Object.values(attrs).some(hasValue)) return "NO_ATTRIBUTES";
  if (!required) return "ERROR";
  const normalized = new Map(Object.entries(attrs).map(([name, value]) => [name.trim().toLocaleLowerCase(), value]));
  const missing = required.some((name) => !hasValue(normalized.get(name.trim().toLocaleLowerCase())));
  return missing ? "ERROR" : "READY";
}

export function toCatalogProductItem(p: RestartProduct): CatalogProductItem {
  const attrs: Record<string, string> = {};
  const renderAttributeValue = (value: unknown): string => {
    if (Array.isArray(value)) {
      return value.map((entry) => {
        if (entry && typeof entry === "object") {
          const record = entry as Record<string, unknown>;
          return String(record.value ?? record.name ?? JSON.stringify(entry));
        }
        return String(entry);
      }).join(", ");
    }
    if (value && typeof value === "object") return JSON.stringify(value);
    return value === null || value === undefined ? "" : String(value);
  };
  if (p.savedEdit) {
    Object.assign(attrs, p.savedEdit.attributes);
  } else if (Array.isArray(p.attributes)) {
    for (const a of p.attributes) {
      if (a && typeof a === "object" && a.name) {
        const rawValue = a.value ?? a.values;
        if (rawValue !== undefined && rawValue !== null) {
          attrs[String(a.name)] = renderAttributeValue(rawValue);
        }
      }
    }
  } else if (p.attributes && typeof p.attributes === "object") {
    for (const [k, v] of Object.entries(p.attributes)) {
      if (v !== undefined && v !== null) attrs[k] = renderAttributeValue(v);
    }
  }

  // Derive status
  let computedStatus: ProductRestartStatus = "UNKNOWN";
  const rawStatus = (p.status || p.operationStatus || "").toUpperCase();
  if (rawStatus === "READY") {
    computedStatus = "READY";
  } else if (rawStatus === "PUBLISHED") {
    computedStatus = "PUBLISHED";
  } else if (rawStatus === "SUBMITTED" || rawStatus === "QUEUED") {
    computedStatus = "QUEUED";
  } else if (rawStatus === "ERROR" || rawStatus === "FAILED" || rawStatus === "NEEDS_REVIEW") {
    computedStatus = "ERROR";
  } else if (rawStatus === "NO_ATTRIBUTES" || rawStatus === "NO_CATEGORY") {
    computedStatus = rawStatus;
  }

  const sourceTitle = p.sourceTitle ?? p.title ?? "";
  const draftTitle = p.draftTitle ?? p.savedEdit?.title ?? undefined;
  const preparedTitle = p.preparedTitle ?? undefined;
  const changedDraftTitle = draftTitle && draftTitle.trim() !== sourceTitle.trim() ? draftTitle : undefined;
  const effectiveTitle = changedDraftTitle ?? preparedTitle ?? sourceTitle;
  const ottoSourcePriceCents = p.ottoSourcePrice?.status === "CONFIRMED"
    && Number.isSafeInteger(p.ottoSourcePrice.amountCents) && (p.ottoSourcePrice.amountCents ?? 0) > 0
    ? p.ottoSourcePrice.amountCents! : null;
  const aftercoolSourcePriceCents = p.aftercoolSourcePrice?.status === "CONFIRMED"
    && Number.isSafeInteger(p.aftercoolSourcePrice.amountCents) && (p.aftercoolSourcePrice.amountCents ?? 0) > 0
    ? p.aftercoolSourcePrice.amountCents! : null;
  const sourcePriceAvailable = Boolean(p.pricePreview)
    || (p.ottoSourcePrice?.status === "CONFIRMED" && (p.ottoSourcePrice.amountCents ?? 0) > 0)
    || (p.aftercoolSourcePrice?.status === "CONFIRMED" && (p.aftercoolSourcePrice.amountCents ?? 0) > 0);
  const availableSourcePrices = [ottoSourcePriceCents, aftercoolSourcePriceCents]
    .filter((value): value is number => value !== null && value > 0);
  const displayBaseCents = p.pricePreview?.baseCents
    ?? (availableSourcePrices.length ? Math.max(...availableSourcePrices) : null)
    ?? 0;

  return {
    id: p.id || p.candidateEpisodeId,
    sourceType: p.source === "OTTO_DIRECT" ? "DIRECT_OTTO" : "AFTERBUY",
    factoryId: p.factoryId ?? undefined,
    factoryName: p.factoryName ?? undefined,
    account: p.account,
    ean: p.newEan || p.ean || "",
    sourceEan: p.ean ?? undefined,
    newEan: p.newEan ?? undefined,
    hasNewEan: Boolean(p.newEan),
    sku: p.targetSku || p.sku || "",
    title: effectiveTitle,
    sourceTitle,
    draftTitle,
    preparedTitle,
    titleOrigin: changedDraftTitle ? "DRAFT" : preparedTitle ? "PREPARED" : "SOURCE",
    imageUrl: p.imageUrl || "",
    category: p.savedEdit?.category ?? p.category ?? "",
    oldStandardPriceCents: displayBaseCents,
    priceAvailable: Boolean(p.pricePreview),
    sourcePriceAvailable,
    ottoSourcePriceCents,
    aftercoolSourcePriceCents,
    targetPriceStatus: p.targetPriceStatus ?? (p.pricePreview ? "CALCULATED" : sourcePriceAvailable ? "PRICE_REVIEW_REQUIRED" : "PRICE_UNAVAILABLE"),
    priceReviewReason: p.priceReviewReason ?? null,
    restartPriceCents: p.pricePreview?.targetCents,
    restartUvpCents: p.pricePreview?.uvpCents,
    priceSource: p.pricePreview?.selectedSource
      ?? (availableSourcePrices.length === 0 ? undefined
        : ottoSourcePriceCents !== null && ottoSourcePriceCents > 0
          && (aftercoolSourcePriceCents === null || ottoSourcePriceCents >= aftercoolSourcePriceCents)
          ? "OTTO_STANDARD_PRICE" : "AFTERCOOL_START_PRICE"),
    priceCapturedAt: p.pricePreview?.capturedAt ?? p.ottoSourcePrice?.capturedAt,
    period: (p.period as RestartPeriod) || "3M",
    errorCode: p.period === "12M" ? "602016 - LOW_PERFORMER" : "602016 - Nonseller",
    status: computedStatus,
    existsInOtto: true,
    operationId: p.operationId ?? undefined,
    operationStatus: p.operationStatus ?? undefined,
    operationVersion: p.operationVersion ?? undefined,
    attributes: attrs,
  };
}

export function useCatalogProducts({
  factoryId,
  source,
}: {
  factoryId?: string;
  source?: "AFTERBUY" | "OTTO_DIRECT";
}) {
  const { account, ready: accountReady } = useRestartAccount();
  const [storedItems, setItems] = useState<CatalogProductItem[]>([]);
  const [loadedScope, setLoadedScope] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [serverPage, setServerPage] = useState(1);
  const [serverData, setServerData] = useState<RestartProductListResponse | null>(null);
  const [filters, setFilters] = useState<CatalogServerFilters>({ query: "", titleOver70: false, sortDir: "asc" });
  const filtersRef = useRef(filters);
  const metricsCacheRef = useRef(new Map<string, {
    refreshTrigger: number;
    metrics: NonNullable<RestartProductListResponse["metrics"]>;
  }>());

  const mode = getOttoRestartMode();
  const demoTab = source === "OTTO_DIRECT" ? "direct-otto" : "afterbuy";
  const isDemo = isOttoRestartDemoMode(demoTab);
  const viewScope = `${account}|${source || ""}|${factoryId || ""}|${isDemo}`;
  const hasLoaded = loadedScope === viewScope;
  const items = hasLoaded ? storedItems : [];

  const showToast = useCallback((msg: string, durationMs = 3500) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), durationMs);
  }, []);

  // Fetch production data from backend API
  useEffect(() => {
    if (!accountReady) return;

    if (isDemo) {
      setLoading(false);
      if (source === "OTTO_DIRECT") {
        const res = catalogDemoService.getDirectOttoProducts(account, "", "", "", "");
        setItems(res.items);
      } else if (factoryId) {
        const res = catalogDemoService.getProductsByFactory(factoryId, account, "", "", "");
        setItems(res.items);
      }
      setLoadedScope(viewScope);
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const cachedMetrics = metricsCacheRef.current.get(viewScope);
    const includeMetrics = cachedMetrics?.refreshTrigger !== refreshTrigger;

    restartApi
      .listProducts(account, {
        factoryId: factoryId || undefined,
        source: source || undefined,
        page: serverPage,
        pageSize: 100,
        includeMetrics,
        ...filters,
        signal: controller.signal,
      })
      .then((data) => {
        if (!controller.signal.aborted) {
          if (data.metrics) {
            metricsCacheRef.current.set(viewScope, { refreshTrigger, metrics: data.metrics });
            if (metricsCacheRef.current.size > 32) {
              const oldestScope = metricsCacheRef.current.keys().next().value;
              if (oldestScope) metricsCacheRef.current.delete(oldestScope);
            }
          }
          const mapped = data.items.map(toCatalogProductItem);
          setItems(mapped);
          setServerData({ ...data, metrics: data.metrics ?? metricsCacheRef.current.get(viewScope)?.metrics ?? null });
          setLoadedScope(viewScope);
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить товары каталога.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [account, accountReady, factoryId, source, isDemo, refreshTrigger, serverPage, filters, viewScope]);

  useEffect(() => {
    if (isDemo || !items.length) return;
    let cancelled = false;
    void (async () => {
      const categories = [...new Set(items.map((item) => item.category.trim()).filter(Boolean))];
      await Promise.all(categories.map(async (category) => {
        const key = `${account}:${category.toLocaleLowerCase()}`;
        if (requiredSchemaCache.has(key)) return;
        try {
          const schema = await restartApi.getRequiredCategoryAttributes(account, category);
          requiredSchemaCache.set(key, schema.requiredAttributes.length ? schema.requiredAttributes : null);
        } catch {
          requiredSchemaCache.set(key, null);
        }
      }));
      if (cancelled) return;
      setItems((current) => {
        let changed = false;
        const next = current.map((item) => {
          const key = `${account}:${item.category.trim().toLocaleLowerCase()}`;
          const status = deriveCardStatus(item, item.category.trim() ? requiredSchemaCache.get(key) ?? null : []);
          if (status === item.status) return item;
          changed = true;
          return { ...item, status };
        });
        return changed ? next : current;
      });
    })();
    return () => { cancelled = true; };
  }, [account, isDemo, items]);

  const updateServerFilters = useCallback((next: CatalogServerFilters) => {
    if (JSON.stringify(filtersRef.current) === JSON.stringify(next)) return;
    filtersRef.current = next;
    setServerPage(1);
    setFilters(next);
  }, []);

  useEffect(() => {
    setServerPage(1);
    const initial: CatalogServerFilters = { query: "", titleOver70: false, sortDir: "asc" };
    filtersRef.current = initial;
    setFilters(initial);
  }, [account, factoryId, source, isDemo]);

  // Compute metrics
  const metrics: FactoryProductsMetrics = useMemo(() => {
    if (!isDemo && serverData?.metrics && hasLoaded) {
      const m = serverData.metrics;
      return {
        total: m.total, ready: m.statusCounts.READY || 0, count3m: m.count3m, count12m: m.count12m,
        needsAi: m.over70Count || 0,
        errors: m.statusCounts.ERROR || 0,
        published: m.statusCounts.PUBLISHED || 0, noCategory: 0, publishedOrExists: m.total,
      };
    }
    return {
      total: items.length,
      ready: items.filter((i) => i.status === "READY").length,
      count3m: items.filter((i) => i.period === "3M").length,
      count12m: items.filter((i) => i.period === "12M").length,
      needsAi: items.filter((i) => titleCodePointLength(i.title || "") > 70).length,
      errors: items.filter((i) => i.status === "ERROR").length,
      published: items.filter((i) => i.status === "PUBLISHED").length,
      noCategory: items.filter((i) => !i.category).length,
      publishedOrExists: items.length,
    };
  }, [items, isDemo, serverData, hasLoaded]);

  // Actions
  const handleAssignEans = useCallback(
    async (selectedIds: string[]) => {
      if (!isDemo) {
        showToast("Назначение EAN из каталога отключено. Используйте отдельный импорт EAN и проверенный workflow операции.");
        return;
      }
      if (isDemo) {
        const result = catalogDemoService.assignDemoEans(account, selectedIds);
        setRefreshTrigger((n) => n + 1);
        showToast(result.assignedCount ? `DEMO: коды назначены для ${result.assignedCount} карточек` : "Уже назначены");
        return;
      }
      try {
        const res = await restartApi.assignProductEans(account, selectedIds);
        setRefreshTrigger((n) => n + 1);
        showToast(`Назначен новый EAN и применена формула цены (-10€ JV / -20€ XL, затем ,99) для ${res.assignedCount} товаров.`);
      } catch (err: unknown) {
        showToast(`Ошибка назначения EAN: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [account, isDemo, showToast]
  );

  const handleApplyAiTitles = useCallback(
    async (selectedIds?: string[]) => {
      if (!isDemo) {
        showToast("Генерация названий в каталоге отключена: AI endpoint для этой операции не подключён.");
        return;
      }
      const targetIds = selectedIds && selectedIds.length > 0 ? selectedIds : items.map((p) => p.id);
      if (isDemo) {
        const count = catalogDemoService.applyAiTitles(targetIds);
        setRefreshTrigger((n) => n + 1);
        showToast(`DEMO: оптимизированы названия для ${count} товаров.`);
        return;
      }
      try {
        const res = await restartApi.generateProductTitles(account, targetIds);
        setRefreshTrigger((n) => n + 1);
        showToast(`ИИ-названия (≤70 симв.) сгенерированы для ${res.generatedCount} товаров.`);
      } catch (err: unknown) {
        showToast(`Ошибка генерации названий: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [account, isDemo, items, showToast]
  );

  const handleTitleSave = useCallback(
    async (id: string, newTitle: string) => {
      if (!isDemo) {
        showToast("Редактирование каталога недоступно: legacy write API закрыт.");
        return;
      }
      if (isDemo) {
        catalogDemoService.updateProductTitle(id, newTitle);
        setRefreshTrigger((n) => n + 1);
        showToast(`DEMO: название изменено (${newTitle.length}/70 симв.).`);
        return;
      }
      try {
        await restartApi.updateProduct(id, account, { title: newTitle });
        setRefreshTrigger((n) => n + 1);
        showToast(`Название сохранено (${newTitle.length}/70 симв.).`);
      } catch (err: unknown) {
        showToast(`Ошибка сохранения: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [account, isDemo, showToast]
  );

  const handlePriceSave = useCallback(
    async (id: string, newPriceCents: number) => {
      if (!isDemo) {
        showToast("Ручное изменение цены из каталога отключено. Цена должна обновляться через проверенную revision.");
        return;
      }
      if (isDemo) {
        catalogDemoService.updateProductPrice(id, newPriceCents);
        setRefreshTrigger((n) => n + 1);
        showToast(`DEMO: цена изменена: ${formatPriceEur(newPriceCents)}.`);
        return;
      }
      try {
        await restartApi.updateProduct(id, account, { priceCents: newPriceCents });
        setRefreshTrigger((n) => n + 1);
        showToast(`Цена обновлена: ${formatPriceEur(newPriceCents)}.`);
      } catch (err: unknown) {
        showToast(`Ошибка сохранения цены: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [account, isDemo, showToast]
  );

  const handleCategorySave = useCallback(
    async (id: string, newCat: string) => {
      if (!isDemo) {
        showToast("Изменение категории из каталога отключено. Категория должна быть подтверждена в revision.");
        return;
      }
      if (isDemo) {
        catalogDemoService.updateProductCategory(id, newCat);
        setRefreshTrigger((n) => n + 1);
        showToast(`DEMO: категория изменена: ${newCat}.`);
        return;
      }
      try {
        await restartApi.updateProduct(id, account, { category: newCat });
        setRefreshTrigger((n) => n + 1);
        showToast(`Категория сохранена: ${newCat}.`);
      } catch (err: unknown) {
        showToast(`Ошибка сохранения: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [account, isDemo, showToast]
  );

  const handleDrawerSave = useCallback(
    async (updated: CatalogProductItem) => {
      if (!isDemo) {
        showToast("Сохранение карточки через legacy API закрыто. Откройте production workflow операции.");
        return;
      }
      if (isDemo) {
        catalogDemoService.updateProduct(updated);
        setRefreshTrigger((n) => n + 1);
        showToast("DEMO: изменения сохранены.");
        return;
      }
      try {
        await restartApi.updateProduct(updated.id, account, {
          title: updated.title,
          priceCents: updated.oldStandardPriceCents,
          category: updated.category,
          attributes: updated.attributes,
        });
        setRefreshTrigger((n) => n + 1);
        showToast(`Карточка ${updated.sku || updated.ean} успешно обновлена.`);
      } catch (err: unknown) {
        showToast(`Ошибка сохранения: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    [account, isDemo, showToast]
  );

  const handleSyncFromOtto = useCallback(
    async (selectedIds: string[], onProgress?: (current: number, total: number) => void) => {
      if (loading || error) {
        showToast("Обновите страницу товаров перед синхронизацией.");
        return { succeededIds: [], failures: ["Обновите страницу товаров перед синхронизацией."] };
      }
      const targetIds = [...new Set(selectedIds)].filter((id) => id && items.some((item) => item.id === id));
      if (targetIds.length === 0) {
        showToast("Выберите товары для синхронизации.");
        return { succeededIds: [], failures: ["Выберите товары для синхронизации."] };
      }

      if (isDemo) {
        const count = catalogDemoService.syncFromOtto(targetIds);
        targetIds.forEach((_, index) => onProgress?.(index + 1, targetIds.length));
        setRefreshTrigger((n) => n + 1);
        showToast(`DEMO: синхронизированы ${count} выбранных товаров.`, 8000);
        return { succeededIds: count ? targetIds : [], failures: count ? [] : ["Не удалось синхронизировать выбранные товары."] };
      }

      let cursor = 0;
      let completed = 0;
      let succeeded = 0;
      const succeededIds: string[] = [];
      const failures: string[] = [];
      const warnings: string[] = [];
      const worker = async () => {
        while (true) {
          const index = cursor++;
          if (index >= targetIds.length) return;
          const id = targetIds[index];
          const item = items.find((candidate) => candidate.id === id);
          try {
            if (!item) throw new Error("Товар больше не находится в текущем каталоге.");
            const idempotencyKey = typeof crypto !== "undefined" && "randomUUID" in crypto
              ? crypto.randomUUID()
              : `sync-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 10)}`;
            const syncResult = await restartApi.syncProduct(id, account, idempotencyKey, item.operationVersion);
            succeeded += 1;
            succeededIds.push(id);
            if (syncResult.targetPriceStatus === "PRICE_REVIEW_REQUIRED") {
              const reason = syncResult.priceReviewReason === "AFTERCOOL_START_PRICE_UNVERIFIED"
                ? "не подтверждён Aftercool Startpreis"
                : syncResult.priceReviewReason === "AFTERCOOL_IDENTITY_UNVERIFIED"
                  ? "не подтверждена карточка Aftercool"
                  : "не хватает подтверждённых источников для расчёта";
              warnings.push(`${item.title || item.sku || id}: данные OTTO сохранены, цена Restart ожидает проверки (${reason}).`);
            }
          } catch (error: unknown) {
            const reason = error instanceof Error ? error.message : String(error);
            failures.push(`${item?.title || item?.sku || id}: ${reason}`);
          } finally {
            completed += 1;
            onProgress?.(completed, targetIds.length);
          }
        }
      };

      await Promise.all(Array.from({ length: Math.min(2, targetIds.length) }, () => worker()));
      setRefreshTrigger((n) => n + 1);
      const summary = `Синхронизация завершена: успешно ${succeeded} из ${targetIds.length}.`;
      const details = [
        failures.length ? `Ошибки: ${failures.join("; ")}` : "",
        warnings.length ? `Проверка цены: ${warnings.join("; ")}` : "",
      ].filter(Boolean).join(" ");
      showToast(details ? `${summary} ${details}` : summary, 12000);
      return { succeededIds, failures, warnings };
    },
    [account, isDemo, items, showToast, loading, error]
  );

  const onRefresh = useCallback(() => setRefreshTrigger((n) => n + 1), []);

  return {
    account,
    mode,
    isDemo,
    hasLoaded,
    items,
    metrics,
    loading,
    error,
    toastMessage,
    showToast,
    serverPage,
    serverData,
    setServerPage,
    updateServerFilters,
    onRefresh,
    handleAssignEans,
    handleApplyAiTitles,
    handleTitleSave,
    handlePriceSave,
    handleCategorySave,
    handleDrawerSave,
    handleSyncFromOtto,
  };
}

"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CalendarClock, ChevronLeft, ChevronRight, CirclePause, CirclePlay, Clock3, Layers, Loader2, RefreshCw, RotateCw, Search, SlidersHorizontal, X } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { ProductDrawer } from "@/components/catalogs/product-drawer";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import type { RestartAccount, RestartDripBatch, RestartDripBatchItem, RestartDripPlanningManifestItem, RestartDripPlanningRequestDetails, RestartDripPlanningRequestSummary, RestartSettings } from "@/features/api/types";
import { resolveDripApiFactoryId } from "@/features/catalogs/drip-banner-utils";
import type { CatalogProductItem } from "@/features/catalogs/types";
import { toCatalogProductItem } from "@/features/catalogs/use-catalog-products";
import { useRestartAccount } from "@/features/state/restart-account";
import "./production-drip-queue-modal.css";

const api = new RestartApiClient();
const PAGE_SIZE = 50;

type Props = {
  isOpen: boolean;
  onClose: () => void;
  factoryId?: string;
  factoryName?: string;
  sourceType?: "AFTERBUY" | "OTTO_DIRECT";
  initialRequestId?: string;
  initialRequestAccount?: RestartAccount;
  initialBatchId?: string;
  initialBatchAccount?: RestartAccount;
  onPlanUpdated?: () => void;
};

function errorText(error: unknown): string {
  if (error instanceof RestartApiError) return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : "Не удалось загрузить очередь OttoRestart.";
}

function key(action: string): string {
  if (!globalThis.crypto?.randomUUID) throw new Error("Браузер не поддерживает безопасные ключи повторного запроса.");
  return `drip-queue-${action}-${globalThis.crypto.randomUUID()}`;
}

function dateText(value: string | null | undefined, timezone?: string | null): string {
  if (!value) return "—";
  try { return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", ...(timezone ? { timeZone: timezone } : {}) }).format(new Date(value)); }
  catch { return value; }
}

const requestLabels: Record<string, string> = { PENDING: "Создание плана", BUILDING: "Формирование планов", COMPLETED: "Планы сформированы", FAILED: "Ошибка создания" };
const batchLabels: Record<string, string> = { DRAFT: "Черновик", SCHEDULED: "Запланирована", ACTIVE: "Активна", PAUSED: "Приостановлена", COMPLETED: "Завершена", CANCELLED: "Отменена" };
const itemLabels: Record<string, string> = {
  PENDING: "Ожидает обработчика", BUILDING: "Создание расписания",
  PREPARING: "Подготовка", NEEDS_REVIEW: "Нужна проверка", FAILED: "Ошибка", SCHEDULED: "Ожидает времени",
  READY_TO_SUBMIT: "Ожидает отправки", SUBMITTING: "Отправляется", OTTO_PROCESSING: "Обработка OTTO",
  AWAITING_ONLINE: "Опубликован · Модерация OTTO", PUBLISHED: "Опубликован · ONLINE", CANCELLED: "Отменено",
  DRAFT: "Черновик", QC_PENDING: "Ожидает QC", READY: "Готово", QUEUED: "В очереди",
  RECONCILIATION_REQUIRED: "Требует сверки",
};
const stageFilterOptions = [
  ["PREPARING", "Подготовка"], ["SCHEDULED", "Ожидает расписания"], ["READY_TO_SUBMIT", "Ожидает отправки"],
  ["NEEDS_REVIEW", "Нужна проверка"], ["SUBMITTING", "Отправляется"], ["OTTO_PROCESSING", "Обработка OTTO"],
  ["AWAITING_ONLINE", "Опубликован · Модерация OTTO"], ["PUBLISHED", "Опубликован · ONLINE"], ["FAILED", "Ошибка"], ["CANCELLED", "Отменено"],
] as const;

type DripQueueDisplayRequest = Omit<RestartDripPlanningRequestDetails, "status"> & {
  status: RestartDripPlanningRequestSummary["status"] | RestartDripBatch["status"];
  standaloneBatch?: boolean;
};

function statusClass(value: string): string {
  if (value === "errors") return "is-error";
  if (value === "published") return "is-success";
  if (["queue", "processing"].includes(value)) return "is-running";
  if (["FAILED", "NEEDS_REVIEW", "DEAD"].includes(value)) return "is-error";
  if (["COMPLETED", "PUBLISHED", "AWAITING_ONLINE", "ONLINE"].includes(value)) return "is-success";
  if (["ACTIVE", "BUILDING", "PENDING", "PREPARING", "SUBMITTING", "OTTO_PROCESSING", "READY_TO_SUBMIT"].includes(value)) return "is-running";
  return "is-muted";
}

function readableQueueError(value?: string | null): string {
  if (!value) return "";
  const labels: Record<string, string> = {
    REQUIRED_ATTRIBUTE_MISSING: "Не заполнено обязательное поле",
    REQUIRED_LEGAL_ATTRIBUTE_MISSING: "Не заполнено юридически обязательное поле",
    ATTRIBUTE_ENUM_INVALID: "Значение не соответствует списку категории",
    PREPARATION_GAP_REQUIRED_ATTRIBUTES_MISSING: "Не заполнены обязательные характеристики",
    SCHEDULE_START_NOT_FUTURE: "Время начала прошло. Создайте расписание с новым временем.",
    CURRENT_QC_NOT_PASSED: "Нужна повторная проверка карточки.",
    STALE_QC_OR_REVISION: "Карточка изменилась после проверки; повторите подготовку и QC.",
    PLAN_SCHEDULE_SNAPSHOT_INCOMPLETE: "Не удалось восстановить сохранённое распределение.",
    PLAN_CREATION_TEMPORARY: "Временная ошибка создания; задание можно повторить.",
    PLAN_START_EXPIRED: "Время запуска прошло. Создайте новое расписание с будущей датой.",
  };
  return labels[value] ?? value.toLowerCase().replaceAll("_", " ");
}

function availabilityLabel(value?: string): string {
  const labels: Record<string, string> = {
    NOT_STARTED: "Не начато", PREPARED: "Подготовлено", DISPATCHING: "Отправляется",
    ACCEPTED: "Принято · ждёт подтверждения", REJECTED: "Отклонено", UNKNOWN: "Результат неизвестен",
  };
  return value ? labels[value] ?? value : "Не начато";
}

function safeImage(value?: string | null): string | null {
  if (!value) return null;
  try { const url = new URL(value); return url.protocol === "https:" ? url.toString() : null; }
  catch { return null; }
}

export function ProductionDripQueueModal({ isOpen, onClose, factoryId, factoryName, sourceType, initialRequestId, initialRequestAccount, initialBatchId, initialBatchAccount, onPlanUpdated }: Props) {
  const { account, ready: accountReady } = useRestartAccount();
  const scopedFactoryId = (sourceType ? resolveDripApiFactoryId(sourceType, factoryId) : factoryId?.trim() || undefined) ?? "";
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [requestPage, setRequestPage] = useState(1);
  const [unlinkedBatchPage, setUnlinkedBatchPage] = useState(1);
  const [itemPage, setItemPage] = useState(1);
  const [itemStatusFilter, setItemStatusFilter] = useState("all");
  const [itemStageFilter, setItemStageFilter] = useState("");
  const [itemDayFilter, setItemDayFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"" | "AFTERBUY" | "OTTO_DIRECT">(sourceType ?? "");
  const [factoryFilter, setFactoryFilter] = useState(scopedFactoryId);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [requests, setRequests] = useState<RestartDripPlanningRequestSummary[]>([]);
  const [unlinkedBatches, setUnlinkedBatches] = useState<RestartDripBatch[]>([]);
  const [unlinkedBatchesPage, setUnlinkedBatchesPage] = useState<{ page: number; pageSize: number; totalItems: number; totalPages: number } | null>(null);
  const [selectedRequestId, setSelectedRequestId] = useState(initialRequestId ?? "");
  const [selectedUnlinkedBatchId, setSelectedUnlinkedBatchId] = useState("");
  const [request, setRequest] = useState<RestartDripPlanningRequestDetails | null>(null);
  const [unlinkedBatch, setUnlinkedBatch] = useState<RestartDripBatch | null>(null);
  const [unlinkedManifestItems, setUnlinkedManifestItems] = useState<RestartDripPlanningManifestItem[]>([]);
  const [unlinkedManifestPage, setUnlinkedManifestPage] = useState<{ page: number; pageSize: number; totalItems: number; totalPages: number } | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [batchItems, setBatchItems] = useState<RestartDripBatchItem[]>([]);
  const [batchItemsPage, setBatchItemsPage] = useState<{ page: number; pageSize: number; totalItems: number; totalPages: number } | null>(null);
  const [requestsPage, setRequestsPage] = useState<{ page: number; pageSize: number; totalItems: number; totalPages: number } | null>(null);
  const [settings, setSettings] = useState<RestartSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [drawerProduct, setDrawerProduct] = useState<CatalogProductItem | null>(null);
  const [openingProductId, setOpeningProductId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const forceSettingsRefresh = useRef(false);
  const hasLoadedOnce = useRef(false);
  const handleRefresh = () => {
    if (refreshing || busy) return;
    forceSettingsRefresh.current = true;
    setRefreshing(true); setNotice(""); setError(""); setActionError("");
    setRefreshVersion((value) => value + 1);
  };
  const currentAccount = useRef(account);
  currentAccount.current = account;
  const focusedRequestId = initialRequestAccount === account ? initialRequestId : undefined;
  const focusedBatchId = initialBatchAccount === account ? initialBatchId : undefined;

  useEffect(() => {
    const timer = setTimeout(() => { setDebouncedQuery(query.trim()); setRequestPage(1); setUnlinkedBatchPage(1); setItemPage(1); }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setRequests([]); setUnlinkedBatches([]); setUnlinkedBatchesPage(null); setRequest(null); setUnlinkedBatch(null); setUnlinkedManifestItems([]); setUnlinkedManifestPage(null);
    setBatchItems([]); setSelectedBatchId(""); setSelectedUnlinkedBatchId(""); setRequestPage(1); setUnlinkedBatchPage(1); setItemPage(1);
    setItemStatusFilter("all"); setItemStageFilter(""); setItemDayFilter("");
    setQuery(""); setDebouncedQuery(""); setStatusFilter(""); setCreatedFrom(""); setCreatedTo("");
    setSourceFilter(sourceType ?? ""); setFactoryFilter(scopedFactoryId); hasLoadedOnce.current = false; setLoading(true);
    setSelectedRequestId(focusedRequestId ?? ""); setSettings(null); setError(""); setActionError(""); setNotice("");
    setSelectedUnlinkedBatchId(focusedBatchId ?? ""); setSelectedBatchId(focusedBatchId ?? "");
    setRefreshing(false); setRefreshedAt(null); forceSettingsRefresh.current = false;
  }, [account, focusedRequestId, focusedBatchId, scopedFactoryId, sourceType]);

  const refresh = useCallback(async (signal: AbortSignal) => {
    if (!accountReady || signal.aborted) return;
    setError("");
    if (!hasLoadedOnce.current) setLoading(true);
    try {
      const [list, currentSettings, directBatches] = await Promise.all([
        api.listDripPlanningRequests(account, {
          page: requestPage, pageSize: PAGE_SIZE, factoryId: factoryFilter || undefined,
          sourceType: sourceFilter || undefined, status: statusFilter ? statusFilter as "PENDING" | "BUILDING" | "COMPLETED" | "FAILED" : undefined,
          query: debouncedQuery || undefined, createdFrom: createdFrom || undefined, createdTo: createdTo || undefined, signal,
        }),
        settings?.account === account && !forceSettingsRefresh.current ? Promise.resolve(settings) : api.getSettings(account, signal),
        api.listDripBatches(account, { page: unlinkedBatchPage, pageSize: PAGE_SIZE, factoryId: factoryFilter || undefined,
          sourceType: sourceFilter || undefined, query: debouncedQuery || undefined, unlinkedOnly: true, signal }),
      ]);
      if (signal.aborted || currentAccount.current !== account) return;
      forceSettingsRefresh.current = false;
      setRequests(list.items); setRequestsPage(list.page); setSettings(currentSettings);
      setUnlinkedBatches(directBatches.items); setUnlinkedBatchesPage(directBatches.page);

      if (selectedUnlinkedBatchId && directBatches.items.some((batch) => batch.id === selectedUnlinkedBatchId)) {
        const batch = await api.getDripBatch(selectedUnlinkedBatchId, account, signal);
        if (signal.aborted || currentAccount.current !== account || batch.account !== account) return;
        const items = await api.listDripBatchItems(batch.id, account, {
          page: itemPage, pageSize: PAGE_SIZE, query: debouncedQuery || undefined,
          status: itemStatusFilter === "all" ? undefined : itemStatusFilter,
          stage: itemStageFilter || undefined, day: itemDayFilter ? Number(itemDayFilter) : undefined, signal,
        });
        if (signal.aborted || currentAccount.current !== account) return;
        setUnlinkedBatch(batch); setBatchItems(items.items); setBatchItemsPage(items.page); setRequest(null);
        if (items.page.totalItems === 0 && batch.status === "DRAFT") {
          const manifest = await api.listDripBatchManifest(batch.id, account, {
            page: itemPage, pageSize: PAGE_SIZE, query: debouncedQuery || undefined,
            stage: itemStageFilter || undefined, signal,
          });
          if (signal.aborted || currentAccount.current !== account) return;
          setUnlinkedManifestItems(manifest.items); setUnlinkedManifestPage(manifest.page);
        } else {
          setUnlinkedManifestItems([]); setUnlinkedManifestPage(null);
        }
        setRefreshedAt(new Date());
        return;
      }
      if (selectedUnlinkedBatchId) {
        setSelectedUnlinkedBatchId(""); setUnlinkedBatch(null); setUnlinkedManifestItems([]); setUnlinkedManifestPage(null);
      }
      setUnlinkedBatch(null); setUnlinkedManifestItems([]); setUnlinkedManifestPage(null);

      let requestId = selectedRequestId;
      if (!requestId || (!list.items.some((entry) => entry.id === requestId) && !focusedRequestId)) {
        requestId = list.items[0]?.id ?? "";
        setSelectedRequestId(requestId);
      }
      if (!requestId) {
        setRequest(null); setBatchItems([]); setBatchItemsPage(null); setSelectedBatchId("");
        const firstUnlinked = directBatches.items[0];
        if (firstUnlinked) {
          setSelectedUnlinkedBatchId(firstUnlinked.id); setSelectedBatchId(firstUnlinked.id); setUnlinkedBatch(firstUnlinked); setLoading(true);
        }
        setRefreshedAt(new Date());
        return;
      }
      setSelectedUnlinkedBatchId("");
      if (requestId !== selectedRequestId) {
        setRequest(null); setBatchItems([]); setBatchItemsPage(null); setSelectedBatchId("");
        setLoading(true);
      }
      const detail = await api.getDripPlanning(account, requestId, signal, itemPage, PAGE_SIZE, {
        query: debouncedQuery || undefined, status: itemStatusFilter,
        stage: itemStageFilter || undefined, batchId: selectedBatchId || undefined,
      });
      if (signal.aborted || currentAccount.current !== account) return;
      setRequest(detail);
      let batchId = selectedBatchId;
      if (!batchId || !detail.batches.some((batch) => batch.id === batchId)) {
        batchId = (focusedBatchId && detail.batches.some((batch) => batch.id === focusedBatchId) ? focusedBatchId : undefined)
          ?? detail.batches[0]?.id ?? "";
        setSelectedBatchId(batchId);
      }
      if (batchId) {
        const [batch, items] = await Promise.all([
          api.getDripBatch(batchId, account, signal),
          api.listDripBatchItems(batchId, account, { page: itemPage, pageSize: PAGE_SIZE,
            query: debouncedQuery || undefined, status: itemStatusFilter === "all" ? undefined : itemStatusFilter,
            stage: itemStageFilter || undefined,
            day: itemDayFilter ? Number(itemDayFilter) : undefined, signal }),
        ]);
        if (signal.aborted || currentAccount.current !== account || batch.account !== account) return;
        setRequest((current) => current ? { ...current, batches: current.batches.map((entry) => entry.id === batch.id ? batch : entry) } : current);
        setBatchItems(items.items); setBatchItemsPage(items.page);
      } else {
        setBatchItems([]); setBatchItemsPage(null);
      }
      setRefreshedAt(new Date());
    } catch (cause) {
      if (!signal.aborted && currentAccount.current === account) setError(errorText(cause));
    } finally {
      if (!signal.aborted && currentAccount.current === account) {
        hasLoadedOnce.current = true;
        setLoading(false); setRefreshing(false);
      }
    }
  }, [account, accountReady, requestPage, factoryFilter, sourceFilter, statusFilter, debouncedQuery,
    createdFrom, createdTo, settings, selectedRequestId, focusedRequestId, focusedBatchId, itemPage, selectedBatchId, unlinkedBatchPage,
    itemStatusFilter, itemStageFilter, itemDayFilter, selectedUnlinkedBatchId]);

  useEffect(() => {
    if (!isOpen || !accountReady) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") await refresh(controller.signal);
      if (active && !controller.signal.aborted) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [isOpen, accountReady, refreshVersion, refresh]);

  const selectedBatch = useMemo(() => unlinkedBatch?.id === selectedBatchId ? unlinkedBatch
    : request?.batches.find((entry) => entry.id === selectedBatchId) ?? null, [request, selectedBatchId, unlinkedBatch]);
  const visibleRequests = requests.filter((entry) => entry.account === account);
  const visibleRequest: DripQueueDisplayRequest | null = request?.account === account ? request
    : unlinkedBatch?.account === account ? {
      id: unlinkedBatch.id, account: unlinkedBatch.account, name: unlinkedBatch.name, status: unlinkedBatch.status,
      itemCount: unlinkedBatch.itemCount, preparedCount: unlinkedBatch.itemCount, errorCode: null, jobId: null,
      jobState: null, jobAttempts: 0, jobMaxAttempts: 0, batchCount: 1,
      factoryIds: unlinkedBatch.factoryId ? [unlinkedBatch.factoryId] : [],
      startDate: unlinkedBatch.localStartDate ?? unlinkedBatch.createdAt.slice(0, 10),
      dailyTime: unlinkedBatch.dailyTime ?? "—", timezone: unlinkedBatch.timezone ?? "Europe/Berlin",
      totalDays: unlinkedBatch.totalDays ?? 1, createdAt: unlinkedBatch.createdAt, processingStartedAt: null, batches: [unlinkedBatch],
      items: unlinkedManifestItems, page: unlinkedManifestPage ?? { page: itemPage, pageSize: PAGE_SIZE, totalItems: unlinkedBatch.itemCount,
        totalPages: unlinkedBatch.itemCount ? Math.ceil(unlinkedBatch.itemCount / PAGE_SIZE) : 0 }, standaloneBatch: true,
    } : null;
  const showFallbackRequest = Boolean(
    visibleRequest && !visibleRequest.standaloneBatch && !visibleRequests.some((entry) => entry.id === visibleRequest.id)
  );
  const visibleRequestStatusLabel = visibleRequest
    ? (visibleRequest.standaloneBatch ? batchLabels[visibleRequest.status] : requestLabels[visibleRequest.status]) ?? visibleRequest.status
    : "";
  const selectedBatchItemCount = Object.values(selectedBatch?.itemCounts ?? {}).reduce((total, count) => total + count, 0);
  const showingManifest = !selectedBatch || (visibleRequest?.standaloneBatch && batchItems.length === 0)
    || selectedBatch.status === "DRAFT" || selectedBatchItemCount === 0;
  const manifestRows = showingManifest
    ? (visibleRequest?.items ?? []).filter((item) => visibleRequest?.standaloneBatch || !selectedBatch || (item.factoryId === selectedBatch.factoryId && item.period === selectedBatch.period))
    : batchItems;
  const itemPageInfo = showingManifest ? visibleRequest?.page ?? null : batchItemsPage;
  const planCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const batch of unlinkedBatch ? [unlinkedBatch] : request?.batches ?? []) {
      for (const [status, count] of Object.entries(batch.itemCounts ?? {})) counts[status] = (counts[status] ?? 0) + count;
    }
    return counts;
  }, [request, unlinkedBatch]);
  const queueGroupCounts = selectedBatch?.itemGroupCounts ?? visibleRequest?.itemGroupCounts ?? {};

  async function controlBatch(batch: RestartDripBatch, action: "start" | "pause" | "resume" | "cancel" | "retry" | "retry-errors") {
    if (busy || batch.account !== account) return;
    if (["start", "resume", "retry", "retry-errors"].includes(action) && settings?.account !== account) {
      setActionError("Не удалось подтвердить настройки сервера. Обновите очередь перед запуском или повтором."); return;
    }
    if (["start", "resume", "retry", "retry-errors"].includes(action)) {
      const writesEnabled = Boolean(settings?.mode === "PRODUCTION" && settings.otto.readEnabled && settings.publication.productWriteEnabled);
      const label = action === "start" ? "запустить"
        : action === "resume" ? "продолжить"
          : action === "retry-errors" ? "повторно подготовить ошибочные товары" : "повторить";
      if (!window.confirm(`Подтвердить действие: ${label} расписание «${batch.name}» (${account})? Worker повторно проверит поколение, QC и серверные флаги.${writesEnabled ? " По текущим флагам отправка товара в OTTO может быть разрешена после этих проверок." : " Отправка в OTTO сейчас заблокирована серверными флагами."}`)) return;
    }
    if (action === "cancel" && !window.confirm(`Отменить будущие задания расписания «${batch.name}»? Уже начатые внешние операции не затрагиваются.`)) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await api.controlDripBatch(batch.id, account, action, batch.version, key(action));
      setNotice(action === "start" ? "Запуск плана принят; подготовка и QC выполняются worker-ом." : `Состояние плана обновлено: ${batchLabels[batch.status] ?? batch.status}.`);
      setRefreshVersion((value) => value + 1);
      onPlanUpdated?.();
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function triggerNow(batch: RestartDripBatch) {
    if (busy || batch.account !== account) return;
    if (!window.confirm(`Запустить текущую дневную порцию расписания «${batch.name}» (${account})? До старта расписания будет выбрана первая порция. Остальные дни сохранятся. Worker повторно проверит QC и серверные флаги.`)) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      const result = await api.triggerDripBatchNow(batch.id, account, batch.version, key("trigger-now"));
      setNotice(["REQUESTED", "ALREADY_REQUESTED"].includes(result.status)
        ? `Дневная порция запрошена: ${result.selectedCount ?? 0}; ожидают подготовки: ${result.preparingCount ?? 0}; готовы: ${result.readyCount ?? 0}.`
        : result.status === "NO_ELIGIBLE_ITEMS" ? "Нет подходящих товаров: проверьте ошибки и уже выполняющиеся отправки."
        : result.status === "QUEUED" ? `Передано worker-у ${result.dueItemCount} наступивших заданий. Это не подтверждает отправку или публикацию.` : "Наступивших заданий нет; расписание не изменено.");
      setRefreshVersion((value) => value + 1);
      onPlanUpdated?.();
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function recheckOnline(operationId: string) {
    if (busy || !operationId) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await api.recheckOnlineStatus(account, operationId, key("online-recheck"));
      setNotice("Проверка ONLINE в OTTO поставлена в очередь (автоопрос до 6 часов).");
      setRefreshVersion((value) => value + 1);
      onPlanUpdated?.();
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function retryPlanning() {
    if (!request || request.status !== "FAILED" || busy) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await api.retryDripPlanning(account, request.id, key("plan-retry"));
      setNotice("Запрос на продолжение создания плана принят."); setRefreshVersion((value) => value + 1);
    } catch (cause) { setActionError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function openProductDrawer(candidateEpisodeId?: string | null) {
    if (!candidateEpisodeId || openingProductId) return;
    setOpeningProductId(candidateEpisodeId);
    setActionError("");
    try {
      const product = await api.getProduct(candidateEpisodeId, account);
      if (currentAccount.current !== account) return;
      setDrawerProduct(toCatalogProductItem(product));
    } catch (cause) {
      if (currentAccount.current === account) setActionError(errorText(cause));
    } finally {
      setOpeningProductId(null);
    }
  }

  if (!isOpen) return null;
  return <><div className="production-drip-queue-overlay" role="dialog" aria-modal="true" aria-labelledby="production-drip-queue-title" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="production-drip-queue-window">
      <header className="production-drip-queue-header">
        <div className="production-drip-queue-heading"><span className="production-drip-queue-icon"><Layers size={19} /></span><div>
          <h2 id="production-drip-queue-title">Очередь выгрузки Drip Feed <span className="production-drip-account-badge">{account} Möbel</span></h2>
          <p>Фабрика: {factoryName || factoryId || "Все фабрики"}{refreshing ? " · Обновляем…" : refreshedAt ? ` · Обновлено ${refreshedAt.toLocaleTimeString("ru-RU")}` : ""}</p>
        </div></div>
        <div className="production-drip-queue-header-actions"><button type="button" className="production-drip-icon-button" disabled={loading || refreshing || busy} onClick={handleRefresh} aria-label="Обновить очередь" title="Обновить данные очереди" aria-busy={refreshing}><RefreshCw className={refreshing ? "production-drip-spin" : undefined} size={16} /></button><button type="button" className="production-drip-icon-button" onClick={onClose} aria-label="Закрыть очередь"><X size={18} /></button></div>
      </header>

      {actionError ? <div className="production-drip-queue-alert" role="alert"><AlertTriangle size={16} /><span>{actionError}</span><button type="button" onClick={() => setActionError("")}>Скрыть</button></div> : null}
      {error ? <div className="production-drip-queue-alert" role="alert"><AlertTriangle size={16} /><span>{error}</span><button type="button" onClick={() => setRefreshVersion((value) => value + 1)}>Повторить</button></div> : null}
      {notice ? <p className={(planCounts.FAILED ?? 0) + (planCounts.NEEDS_REVIEW ?? 0) > 0 ? "production-drip-queue-alert" : "production-drip-queue-notice"} role="status">{(planCounts.FAILED ?? 0) + (planCounts.NEEDS_REVIEW ?? 0) > 0 ? "Подготовка обнаружила ошибки. Проверьте поля в подробностях товара." : notice}</p> : null}

      <div className="production-drip-queue-content">
        <div className="production-drip-plan-selector">
          <label htmlFor="drip-plan-select">План выгрузки:</label>
          <select id="drip-plan-select" value={selectedUnlinkedBatchId ? `batch:${selectedUnlinkedBatchId}` : selectedRequestId ? `request:${selectedRequestId}` : ""} onChange={(event) => {
            const [kind, id] = event.target.value.split(":");
            setRequest(null); setBatchItems([]); setBatchItemsPage(null); setUnlinkedManifestItems([]); setUnlinkedManifestPage(null);
            setSelectedBatchId(""); setItemPage(1); setItemStageFilter(""); setItemDayFilter(""); setLoading(true);
            if (kind === "batch") {
              setSelectedRequestId(""); setSelectedUnlinkedBatchId(id); setSelectedBatchId(id);
              setUnlinkedBatch(unlinkedBatches.find((entry) => entry.id === id) ?? null);
            } else {
              setSelectedUnlinkedBatchId(""); setUnlinkedBatch(null); setSelectedRequestId(id);
            }
          }}>
            {!visibleRequests.length && !unlinkedBatches.length && !showFallbackRequest ? <option value="">{loading ? "Загрузка планов…" : "Нет созданных планов"}</option> : null}
            {showFallbackRequest && visibleRequest ? <option value={`request:${visibleRequest.id}`}>{visibleRequest.name} · {requestLabels[visibleRequest.status] ?? visibleRequest.status}</option> : null}
            {visibleRequests.map((entry) => <option key={entry.id} value={`request:${entry.id}`}>{entry.name} · {requestLabels[entry.status] ?? entry.status}</option>)}
            {unlinkedBatches.map((entry) => <option key={entry.id} value={`batch:${entry.id}`}>{entry.name} · {batchLabels[entry.status] ?? entry.status}</option>)}
          </select>
          {visibleRequest ? <div className="production-drip-inline-stats"><span><b>{visibleRequest.itemCount}</b> всего</span><span className="is-success-text"><b>{(planCounts.PUBLISHED ?? 0) + (planCounts.AWAITING_ONLINE ?? 0)}</b> опубл.</span><span className="is-error-text"><b>{(planCounts.FAILED ?? 0) + (planCounts.NEEDS_REVIEW ?? 0)}</b> ошибок</span><span>{visibleRequest.totalDays} дн. · {visibleRequest.dailyTime.slice(0, 5)}</span></div> : null}
            {visibleRequest && visibleRequest.batches.length ? <div className="production-drip-batch-toolbar"><label hidden={visibleRequest.batches.length === 1}>Группа товаров<select value={selectedBatchId} disabled={visibleRequest.standaloneBatch} onChange={(event) => { setBatchItems([]); setBatchItemsPage(null); setLoading(true); setSelectedBatchId(event.target.value); setItemPage(1); setItemStageFilter(""); setItemDayFilter(""); }}>
              {visibleRequest.batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name} · {batch.factoryId || "Фабрика не указана"} · {batch.period} · {batchLabels[batch.status] ?? batch.status}</option>)}
            </select></label><span className={`production-drip-status ${statusClass(selectedBatch?.status ?? "")}`}>{batchLabels[selectedBatch?.status ?? ""] ?? selectedBatch?.status ?? "—"}</span>
              {selectedBatch ? <div className="production-drip-batch-actions">
                {selectedBatch.status === "DRAFT" ? <button type="button" className="production-drip-primary" disabled={busy} onClick={() => void controlBatch(selectedBatch, "start")}><CirclePlay size={16} /> Запустить план</button> : null}
                {selectedBatch.status === "ACTIVE" || selectedBatch.status === "SCHEDULED" ? <button type="button" disabled={busy} onClick={() => void controlBatch(selectedBatch, "pause")}><CirclePause size={14} /> Пауза</button> : null}
                {selectedBatch.status === "PAUSED" ? <button type="button" disabled={busy} onClick={() => void controlBatch(selectedBatch, "resume")}><CirclePlay size={14} /> Продолжить</button> : null}
                {["ACTIVE", "SCHEDULED"].includes(selectedBatch.status) ? <button type="button" disabled={busy} onClick={() => void triggerNow(selectedBatch)}><Clock3 size={14} /> Выгрузить сейчас</button> : null}
                {selectedBatch.itemCounts && (selectedBatch.itemCounts.FAILED || selectedBatch.itemCounts.NEEDS_REVIEW) ? <button type="button" disabled={busy} onClick={() => void controlBatch(selectedBatch, "retry-errors")}><RotateCw size={14} /> Повторить ошибки</button> : null}
                {["DRAFT", "SCHEDULED", "ACTIVE", "PAUSED"].includes(selectedBatch.status) ? <button type="button" disabled={busy} onClick={() => void controlBatch(selectedBatch, "cancel")}>Отменить план</button> : null}
              </div> : null}
            </div> : null}
          {requestsPage && requestsPage.totalPages > 1 ? <div className="production-drip-pagination"><button type="button" aria-label="Предыдущие планы" disabled={requestPage <= 1 || loading} onClick={() => setRequestPage((value) => value - 1)}><ChevronLeft size={14} /></button><span>{requestPage}/{requestsPage.totalPages}</span><button type="button" aria-label="Следующие планы" disabled={requestPage >= requestsPage.totalPages || loading} onClick={() => setRequestPage((value) => value + 1)}><ChevronRight size={14} /></button></div> : null}
          {unlinkedBatchesPage && unlinkedBatchesPage.totalPages > 1 ? <div className="production-drip-pagination"><button type="button" aria-label="Предыдущие расписания" disabled={unlinkedBatchPage <= 1 || loading} onClick={() => { setSelectedUnlinkedBatchId(""); setUnlinkedBatch(null); setUnlinkedBatchPage((value) => value - 1); }}><ChevronLeft size={14} /></button><span>{unlinkedBatchesPage.page}/{unlinkedBatchesPage.totalPages}</span><button type="button" aria-label="Следующие расписания" disabled={unlinkedBatchPage >= unlinkedBatchesPage.totalPages || loading} onClick={() => { setSelectedUnlinkedBatchId(""); setUnlinkedBatch(null); setUnlinkedBatchPage((value) => value + 1); }}><ChevronRight size={14} /></button></div> : null}
        </div>
        <main className="production-drip-request-detail">
            <div className="production-drip-flow-toolbar">
            <div className="production-drip-status-tabs" aria-label="Состояние товаров">
              {([["all", "Все"], ["queue", "В очереди"], ["processing", "Обработка OTTO"], ["published", "Опубликованы"], ["errors", "Ошибки"]]).map(([value, label]) => <button type="button" key={value} aria-pressed={itemStatusFilter === value} className={`production-drip-status-tab ${statusClass(value)}`} onClick={() => { setItemStatusFilter(value); setItemStageFilter(""); setItemPage(1); }}>{label}<span>{queueGroupCounts[value] ?? 0}</span></button>)}
            </div>
            <div className="production-drip-flow-search">
              <label>День<select value={itemDayFilter} disabled={showingManifest} onChange={(event) => { setItemDayFilter(event.target.value); setItemPage(1); }}>
                <option value="">Все дни</option>{Array.from({ length: Math.min(60, Math.max(1, (selectedBatch?.totalDays ?? visibleRequest?.totalDays ?? 1) + 2)) }, (_, index) => index + 1).map((day) => <option key={day} value={day}>День {day}</option>)}
              </select></label>
        <label className="production-drip-queue-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="План, EAN, SKU, товар…" aria-label="Поиск планов и товаров" /></label>
        <button type="button" className="production-drip-filter-toggle" aria-expanded={filtersOpen} aria-controls="drip-queue-extra-filters" onClick={() => setFiltersOpen((value) => !value)}><SlidersHorizontal size={15} /> Фильтры{(createdFrom || createdTo || sourceFilter !== (sourceType ?? "") || factoryFilter !== (factoryId ?? "")) ? <span className="production-drip-filter-dot" /> : null}</button>
            </div>
            </div>
      <div id="drip-queue-extra-filters" className="production-drip-extra-filters" hidden={!filtersOpen}>
        <label className="production-drip-exact-stage">Точный этап<select aria-label="Точный этап товара" value={itemStageFilter} onChange={(event) => { setItemStageFilter(event.target.value); setItemStatusFilter("all"); setItemPage(1); }}><option value="">Все этапы</option>{stageFilterOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <select aria-label="Фильтр по статусу" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setRequestPage(1); }}><option value="">Все статусы</option><option value="PENDING">Создание плана</option><option value="BUILDING">Формирование</option><option value="COMPLETED">Сформированы</option><option value="FAILED">Ошибки</option></select>
        <select aria-label="Фильтр по источнику" value={sourceFilter} onChange={(event) => { setSourceFilter(event.target.value as typeof sourceFilter); setRequestPage(1); setUnlinkedBatchPage(1); }}><option value="">Все источники</option><option value="AFTERBUY">Afterbuy</option><option value="OTTO_DIRECT">OTTO Direct</option></select>
        <input aria-label="Фильтр по фабрике" value={factoryFilter} onChange={(event) => { setFactoryFilter(event.target.value); setRequestPage(1); setUnlinkedBatchPage(1); }} placeholder="Фабрика ID" />
        <label className="production-drip-date-filter"><CalendarClock size={14} /><input aria-label="Создано с даты" type="date" value={createdFrom} onChange={(event) => { setCreatedFrom(event.target.value); setRequestPage(1); }} /></label>
        <label className="production-drip-date-filter"><CalendarClock size={14} /><input aria-label="Создано по дату" type="date" value={createdTo} onChange={(event) => { setCreatedTo(event.target.value); setRequestPage(1); }} /></label>
      </div>

          {!visibleRequest && loading ? <ApiState kind="loading" label="Загружаем состояние запроса…" /> : null}
          {!visibleRequest && !loading ? <ApiState kind="empty" title="Выберите расписание" description="Выберите план в списке сверху, чтобы увидеть товары и ход публикации." /> : null}
          {visibleRequest && visibleRequest.account === account ? <>
            {!visibleRequest.standaloneBatch && visibleRequest.status === "FAILED" && visibleRequest.errorCode !== "SCHEDULE_START_NOT_FUTURE" && visibleRequest.errorCode !== "PLAN_START_EXPIRED" ? <button type="button" className="production-drip-action-button" disabled={busy} onClick={() => void retryPlanning()}><RotateCw size={14} /> Продолжить создание</button> : null}
            {selectedBatch?.status === "DRAFT" ? <div className="production-drip-draft-hint"><CirclePlay size={20} /><div><strong>Расписание сохранено. Товары ещё не отправлялись.</strong><p>Нажмите «Запустить план», чтобы начать подготовку и проверку товаров перед публикацией.</p></div></div> : null}
            {visibleRequest.errorCode ? <p className="production-drip-queue-alert"><AlertTriangle size={15} /> Ошибка планирования: {readableQueueError(visibleRequest.errorCode)}</p> : null}
            {visibleRequest.batches.length ? null : <div className="production-drip-queue-info"><Loader2 size={15} className={visibleRequest.status === "PENDING" || visibleRequest.status === "BUILDING" ? "production-drip-spin" : ""} />
              {visibleRequest.status === "PENDING" ? (visibleRequest.jobAttempts === 0 ? `Запрос сохранён. Задание ещё не начато (${visibleRequest.jobState ?? "состояние недоступно"}); ${visibleRequest.itemCount} товар(ов) сохранено. Можно закрыть окно — состояние останется в очереди.` : `Задание ожидает повтора (${visibleRequest.jobState ?? "состояние недоступно"}). Обработано ${visibleRequest.preparedCount} из ${visibleRequest.itemCount}.`) : visibleRequest.status === "BUILDING" ? `Создание плана: ${visibleRequest.preparedCount} из ${visibleRequest.itemCount} товаров. Подготовка карточек ещё не означает QC или публикацию.` : visibleRequest.status === "FAILED" ? "План не завершён. Сохранённые товары и прогресс показаны ниже." : "Запрос завершён, но batch пока не найден; это не считается публикацией."}
            </div>}

            {loading && manifestRows.length === 0 ? <ApiState kind="loading" label="Обновляем список товаров…" /> : manifestRows.length ? <div className="production-drip-items-wrap"><table className="production-drip-items-table"><thead><tr><th>Фото</th><th>Название товара</th><th>EAN</th><th>День выгрузки</th><th>Статус</th></tr></thead><tbody>
              {manifestRows.map((item) => {
                const batchItem = showingManifest ? null : item as RestartDripBatchItem;
                const manifestItem = showingManifest ? item as RestartDripPlanningRequestDetails["items"][number] : null;
                const stage = batchItem?.status ?? manifestItem?.queueStatus
                  ?? (!visibleRequest.standaloneBatch && ["PENDING", "BUILDING", "FAILED"].includes(visibleRequest.status)
                    ? visibleRequest.status : manifestItem?.operationStatus ?? visibleRequest.status);
                const online = batchItem?.status === "PUBLISHED" ? "ONLINE подтверждён" : batchItem?.status === "AWAITING_ONLINE" ? "Ожидает подтверждения" : batchItem?.onlineStatus ? batchItem.onlineStatus : "Нет подтверждения";
                const image = safeImage(batchItem?.imageUrl || manifestItem?.imageUrl);
                const rawPreparationError = batchItem?.preparationErrors?.[0];
                const preparationError = typeof rawPreparationError === "string" ? rawPreparationError
                  : rawPreparationError?.message || [readableQueueError(rawPreparationError?.code), rawPreparationError?.field].filter(Boolean).join(": ");
                const rowError = preparationError || readableQueueError(batchItem?.lastErrorCode || batchItem?.processingErrorCode || batchItem?.submissionErrorCode || manifestItem?.queueErrorCode);
                const category = batchItem?.category || manifestItem?.category;
                const candidateEpisodeId = batchItem?.candidateEpisodeId || manifestItem?.candidateEpisodeId || null;
                const isOpeningThis = Boolean(candidateEpisodeId && openingProductId === candidateEpisodeId);
                const keyValue = batchItem ? `item-${batchItem.id}` : `manifest-${manifestItem!.rowNumber}`;
                return <Fragment key={keyValue}><tr>
                  <td className="production-drip-photo">{candidateEpisodeId ? <button type="button" className="production-drip-photo-button" disabled={Boolean(openingProductId)} onClick={() => void openProductDrawer(candidateEpisodeId)} title="Открыть карточку товара">{image ? <img src={image} alt="" loading="lazy" /> : <span className="production-drip-image-fallback"><Layers size={15} /></span>}</button> : (image ? <img src={image} alt="" loading="lazy" /> : <span className="production-drip-image-fallback"><Layers size={15} /></span>)}</td><td><div className="production-drip-product-cell"><span>{candidateEpisodeId ? <button type="button" className="production-drip-product-title-btn" disabled={Boolean(openingProductId)} onClick={() => void openProductDrawer(candidateEpisodeId)} title="Открыть карточку товара">{batchItem?.title || manifestItem?.title || "Без названия"}{isOpeningThis ? <Loader2 size={13} className="production-drip-spin" /> : null}</button> : <strong>{batchItem?.title || manifestItem?.title || "Без названия"}</strong>}<small>{batchItem?.factoryName || manifestItem?.factoryName || factoryName || "Фабрика не указана"} · {batchItem?.sourceType || manifestItem?.sourceType || "—"}</small>{category ? <small>Категория: {category}</small> : null}{rowError ? <small className="is-error-text">{rowError}</small> : null}</span></div></td>
                  <td><span className="production-drip-ean">{manifestItem?.newEan || batchItem?.targetSku || manifestItem?.targetSku || "—"}</span><small>Исходный: {batchItem?.sourceEan || manifestItem?.sourceEan || "—"}</small><details className="production-drip-identifiers"><summary>SKU и операция</summary><small>Исходный SKU: {batchItem?.sourceSku || manifestItem?.sourceSku || "—"}</small><small>Новый SKU: {batchItem?.targetSku || manifestItem?.targetSku || manifestItem?.newEan || "—"}</small><small>Операция: {batchItem?.operationId || manifestItem?.operationId || "—"}</small></details></td>
                  <td>{batchItem ? <><small>{dateText(batchItem.scheduledAt, selectedBatch?.timezone)} · день {batchItem.scheduledDay ?? "—"}</small><small>Попытки: {batchItem.totalAttempts} · ревизия {batchItem.revisionNumber ?? "—"}</small></> : <><small>{manifestItem?.scheduledAt ? dateText(manifestItem.scheduledAt, visibleRequest.timezone) : visibleRequest.standaloneBatch ? `Черновик · старт ${dateText(selectedBatch?.scheduledStartAt, selectedBatch?.timezone)}` : "Ожидает формирования плана"}</small></>}</td>
                  <td><span className={`production-drip-status ${statusClass(stage)}`}>{itemLabels[stage] ?? stage}</span><small>{rowError ? "Требует внимания" : ""}</small></td>
                </tr>
                {batchItem ? <tr className="production-drip-details-row"><td colSpan={5}><details><summary>Подробности · QC, отправка, ONLINE и наличие</summary><div className="production-drip-details-grid">
                  <div><strong>Подготовка и QC</strong><span>Операция: {batchItem.operationId}</span><span>Ревизия: {batchItem.revisionNumber ?? "—"} · QC: {batchItem.qcResultId ?? "—"}</span><span>Готово: {dateText(batchItem.readyAt)}</span><span>QC: {batchItem.qcOutcome ?? "Ожидает результата"}{batchItem.qcCreatedAt ? ` · ${dateText(batchItem.qcCreatedAt)}` : ""}</span>
                    {(batchItem.preparationErrors ?? []).map((entry, index) => {
                      const text = typeof entry === "string" ? readableQueueError(entry) : [entry.message || readableQueueError(entry.code), entry.field, entry.value ? `Значение: ${entry.value}` : "", entry.allowedValues?.length ? `Допустимо: ${entry.allowedValues.join(", ")}` : ""].filter(Boolean).join(" · ");
                      return <span className="is-error-text" key={`prep-${index}`}>{text}</span>;
                    })}
                    {(batchItem.qcIssues ?? []).map((entry, index) => <span className="is-error-text" key={`${entry.code ?? "qc"}-${index}`}>{entry.code ?? "Замечание QC"} · {entry.outcome ?? ""}</span>)}
                  </div>
                  <div><strong>Отправка и обработка OTTO</strong><span>Попытка: {batchItem.submissionAttemptNumber ?? "—"} · состояние: {batchItem.submissionStatus ?? "не отправлялось"}</span>
                    <span>HTTP: {batchItem.submissionHttpStatus ?? "—"} · задача OTTO: {batchItem.taskState ?? batchItem.processingState ?? "—"}</span>
                    <span>Последняя проверка обработки: {dateText(batchItem.processingUpdatedAt)}</span>
                    <span>Начало: {dateText(batchItem.submissionStartedAt)} · ответ: {dateText(batchItem.submissionCompletedAt)}</span>
                    {batchItem.submissionErrorCode || batchItem.processingErrorCode ? <span className="is-error-text">{batchItem.processingErrorCode ?? batchItem.submissionErrorCode}</span> : null}
                  </div>
                  <div><strong>Подтверждение и наличие</strong><span>{online} · ONLINE: {batchItem.onlineStatus ?? "Нет наблюдения"} · {dateText(batchItem.onlineObservedAt)}</span>
                    <span>Stock: {batchItem.stockVerified ? "Применено в OTTO" : availabilityLabel(batchItem.stockStatus)} · delivery: {batchItem.deliveryVerified ? "Применено в OTTO" : availabilityLabel(batchItem.deliveryStatus)}</span>
                    {batchItem.operationId && batchItem.operationStatus === "AWAITING_ONLINE" && batchItem.submissionState === "CONFIRMED" && batchItem.onlineStatus !== "ONLINE" && batchItem.status !== "PUBLISHED"
                      ? <button type="button" className="production-drip-inline-button" disabled={busy} onClick={() => void recheckOnline(batchItem.operationId)}><RefreshCw size={12} /> Проверить ONLINE</button>
                      : null}
                  </div>
                </div></details></td></tr> : null}
                </Fragment>;
              })}
            </tbody></table></div> : <ApiState kind="empty" title="Товаров пока нет" description={visibleRequest.status === "PENDING" || visibleRequest.status === "BUILDING" ? "Сохранённый запрос появится в таблице после чтения manifest." : visibleRequest.standaloneBatch ? "В сохранённом черновике нет доступных операций." : "Для этого запроса backend не вернул элементы."} />}
            {itemPageInfo && itemPageInfo.totalPages > 1 ? <div className="production-drip-pagination production-drip-item-pagination"><span>Страница {itemPageInfo.page} из {itemPageInfo.totalPages} · товаров {itemPageInfo.totalItems.toLocaleString("ru-RU")}</span><button type="button" disabled={itemPage <= 1 || loading} onClick={() => setItemPage((value) => value - 1)} aria-label="Предыдущая страница товаров"><ChevronLeft size={16} /></button><button type="button" disabled={itemPage >= itemPageInfo.totalPages || loading} onClick={() => setItemPage((value) => value + 1)} aria-label="Следующая страница товаров"><ChevronRight size={16} /></button></div> : null}
          </> : null}
        </main>
      </div>
      {busy ? <div className="production-drip-queue-busy" role="status"><Loader2 size={17} className="production-drip-spin" /> Сохраняем действие…</div> : null}
      <footer className="production-drip-queue-footer"><span>{itemPageInfo ? `Товаров: ${itemPageInfo.totalItems.toLocaleString("ru-RU")}` : "Выберите план выгрузки"} · {account}</span><button type="button" className="production-drip-action-button" onClick={onClose}>Закрыть</button></footer>
    </section>
  </div>
  {drawerProduct ? <div className="production-drip-drawer-layer"><ProductDrawer product={drawerProduct} onClose={() => setDrawerProduct(null)} demoMode={false} onProductSaved={() => { setRefreshVersion((value) => value + 1); onPlanUpdated?.(); }} /></div> : null}
  </>;
}

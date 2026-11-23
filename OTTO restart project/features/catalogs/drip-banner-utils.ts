import type {
  RestartDripBatch,
  RestartDripPlanningRequestSummary,
} from "@/features/api/types";

export interface DripBannerViewModel {
  kind: "batch" | "planning";
  id: string;
  batch?: RestartDripBatch;
  planningRequest?: RestartDripPlanningRequestSummary;
  title: string;
  statusText: string;
  statusTone: "running" | "scheduled" | "paused" | "building";
  isPaused: boolean;
  isAnimated: boolean;
  currentDay: number;
  totalDays: number;
  dailyTime: string;
  timezoneLabel: string;
  totalItems: number;
  publishedCount: number;
  processingCount: number;
  errorsCount: number;
  preparedCount: number;
  percentage: number;
}

const PROCESSING_STATES = new Set([
  "PREPARING",
  "READY_TO_SUBMIT",
  "SUBMITTING",
  "OTTO_PROCESSING",
  "AWAITING_ONLINE",
]);

const ERROR_STATES = new Set(["NEEDS_REVIEW", "FAILED"]);

const TIMEZONE_LABELS: Record<string, string> = {
  "Europe/Berlin": "Берлин",
  "Europe/Moscow": "Москва",
  UTC: "UTC",
};

function parseYmdUtcMs(ymd: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd.trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  return Date.UTC(year, month - 1, day);
}

export function formatTimezoneLabel(timezone?: string | null): string {
  if (!timezone) return "Берлин";
  return TIMEZONE_LABELS[timezone] ?? timezone;
}

export function computeDripCurrentDay(
  localStartDate?: string | null,
  totalDays?: number | null,
  timezone?: string | null,
  now: Date = new Date(),
): number {
  const safeTotalDays = Math.max(1, totalDays ?? 1);
  if (!localStartDate) return 1;
  const startMs = parseYmdUtcMs(localStartDate);
  if (startMs === null) return 1;

  let todayYmd: string;
  try {
    todayYmd = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone || "Europe/Berlin",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
  } catch {
    todayYmd = now.toISOString().slice(0, 10);
  }

  const todayMs = parseYmdUtcMs(todayYmd);
  if (todayMs === null) return 1;

  const elapsedDays = Math.floor((todayMs - startMs) / 86_400_000) + 1;
  return Math.min(safeTotalDays, Math.max(1, elapsedDays));
}

export function pickBannerBatch(batches: RestartDripBatch[]): RestartDripBatch | null {
  const priority: Array<RestartDripBatch["status"]> = ["ACTIVE", "SCHEDULED", "PAUSED"];
  for (const status of priority) {
    const match = batches.find((batch) => batch.status === status);
    if (match) return match;
  }
  return null;
}

export function pickBannerPlanningRequest(
  requests: RestartDripPlanningRequestSummary[],
): RestartDripPlanningRequestSummary | null {
  return (
    requests.find((req) => req.status === "BUILDING") ??
    requests.find((req) => req.status === "PENDING") ??
    null
  );
}

function readGroupCounts(batch: RestartDripBatch) {
  const groups = batch.itemGroupCounts;
  if (groups) {
    return {
      total: Math.max(batch.itemCount, groups.all ?? 0),
      published: groups.published ?? 0,
      processing: groups.processing ?? 0,
      errors: groups.errors ?? 0,
    };
  }

  const itemCounts = batch.itemCounts ?? {};
  let published = 0;
  let processing = 0;
  let errors = 0;
  for (const [state, count] of Object.entries(itemCounts)) {
    if (state === "PUBLISHED") published += count;
    else if (PROCESSING_STATES.has(state)) processing += count;
    else if (ERROR_STATES.has(state)) errors += count;
  }

  return {
    total: batch.itemCount,
    published,
    processing,
    errors,
  };
}

export function buildBatchBannerViewModel(
  batch: RestartDripBatch,
  now: Date = new Date(),
): DripBannerViewModel {
  const totalDays = Math.max(1, batch.totalDays ?? 1);
  const currentDay = computeDripCurrentDay(
    batch.localStartDate,
    totalDays,
    batch.timezone,
    now,
  );
  const counts = readGroupCounts(batch);
  const percentage =
    counts.total > 0
      ? Math.min(100, Math.max(0, Math.round((counts.published / counts.total) * 100)))
      : 0;

  const isPaused = batch.status === "PAUSED";
  const statusTone =
    batch.status === "ACTIVE"
      ? "running"
      : batch.status === "SCHEDULED"
        ? "scheduled"
        : "paused";
  const statusText =
    batch.status === "ACTIVE"
      ? "ВЫПОЛНЯЕТСЯ"
      : batch.status === "SCHEDULED"
        ? "ЗАПЛАНИРОВАН"
        : "НА ПАУЗЕ";

  return {
    kind: "batch",
    id: batch.id,
    batch,
    title: `Активный Drip Feed: День ${currentDay} из ${totalDays}`,
    statusText,
    statusTone,
    isPaused,
    isAnimated: batch.status === "ACTIVE",
    currentDay,
    totalDays,
    dailyTime: (batch.dailyTime || "09:00").slice(0, 5),
    timezoneLabel: formatTimezoneLabel(batch.timezone),
    totalItems: counts.total,
    publishedCount: counts.published,
    processingCount: counts.processing,
    errorsCount: counts.errors,
    preparedCount: counts.total,
    percentage,
  };
}

export function buildPlanningBannerViewModel(
  request: RestartDripPlanningRequestSummary,
): DripBannerViewModel {
  const totalDays = Math.max(1, request.totalDays || 1);
  const totalItems = Math.max(0, request.itemCount || 0);
  const preparedCount = Math.min(totalItems, Math.max(0, request.preparedCount || 0));
  const percentage =
    totalItems > 0 ? Math.min(100, Math.max(0, Math.round((preparedCount / totalItems) * 100))) : 0;

  return {
    kind: "planning",
    id: request.id,
    planningRequest: request,
    title: `Активный Drip Feed: Формирование расписания (${totalDays} дн.)`,
    statusText: request.status === "BUILDING" ? "СОЗДАЁТСЯ ПЛАН" : "В ОЧЕРЕДИ",
    statusTone: "building",
    isPaused: false,
    isAnimated: true,
    currentDay: 1,
    totalDays,
    dailyTime: (request.dailyTime || "09:00").slice(0, 5),
    timezoneLabel: formatTimezoneLabel(request.timezone),
    totalItems,
    publishedCount: 0,
    processingCount: 0,
    errorsCount: 0,
    preparedCount,
    percentage,
  };
}

/**
 * Strip the demo-only `"direct-otto"` sentinel when querying production OTTO_DIRECT
 * Drip endpoints where `source_products.factory_id` is NULL.
 */
export function resolveDripApiFactoryId(
  sourceType: "AFTERBUY" | "DIRECT_OTTO" | "OTTO_DIRECT",
  factoryId?: string | null,
): string | undefined {
  const normalized = factoryId?.trim();
  if (!normalized) return undefined;
  if ((sourceType === "DIRECT_OTTO" || sourceType === "OTTO_DIRECT") && normalized === "direct-otto") {
    return undefined;
  }
  return normalized;
}

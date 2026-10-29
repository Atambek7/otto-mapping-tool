export type RestartAccount = "JV" | "XL";
export type RestartPeriod = "3M" | "12M";

export type RestartBatchStatus =
  | "IMPORTED"
  | "PREVIEWED"
  | "NEEDS_REVIEW"
  | "READY"
  | "CANCELLED";

export type RestartEanStatus =
  | "AVAILABLE"
  | "RESERVED"
  | "SUBMITTED"
  | "USED"
  | "QUARANTINED";

export type RestartQueueStatus = "IDLE" | "PAUSED" | "RUNNING" | "NEEDS_ATTENTION";

export interface RestartPageInfo {
  page: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
}

export interface RestartPage<T> {
  items: T[];
  page: RestartPageInfo;
}

export interface RestartBatch {
  id: string;
  account: RestartAccount;
  period: RestartPeriod;
  name: string;
  status: RestartBatchStatus;
  itemCount: number;
  createdAt: string;
}

export interface RestartBatchItem {
  id: number;
  batchId: string;
  account: RestartAccount;
  sourceEan: string;
  rowNumber: number;
}

export interface RestartEvent {
  id: string;
  account: RestartAccount;
  eventType: string;
  entityType: string;
  entityId: string;
  summary: string;
  metadata: Record<string, unknown>;
  createdAt: string;
}

export interface RestartCreateBatchRequest {
  account: RestartAccount;
  period: RestartPeriod;
  name: string;
  sourceEans: string[];
}

export interface RestartBatchPreview {
  account: RestartAccount;
  period?: RestartPeriod | null;
  inputCount: number;
  uniqueCount: number;
  duplicateCount: number;
  duplicates: string[];
  invalidEans: string[];
  warnings: string[];
  sourceEans: string[];
  dataSourcesChecked?: Array<"LOCAL_FORMAT_ONLY">;
}

export interface RestartOverview {
  account: RestartAccount;
  counts: {
    candidates: number;
    awaitingReview: number;
    needsAttention: number;
    queued: number;
    onlineConfirmed: number;
    availableEans: number;
  };
  generatedAt: string;
}

export interface RestartEanEntry {
  ean: string;
  account: RestartAccount;
  status: RestartEanStatus;
  batchId: string | null;
  importedAt: string;
}

export interface RestartEanImportResult {
  importedCount: number;
  account: RestartAccount;
  status: "AVAILABLE";
  importedAt: string;
}

export interface RestartQueueEntry {
  id: string;
  batchId: string;
  state: "WAITING" | "PROCESSING" | "ACCEPTED" | "FAILED" | "NEEDS_RECONCILE";
  createdAt: string;
}

export interface RestartQueuePage extends RestartPage<RestartQueueEntry> {
  account: RestartAccount;
  status: RestartQueueStatus;
}

export type RestartApiErrorCode =
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "VALIDATION_ERROR"
  | "CONFLICT"
  | "NOT_FOUND"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR";

export interface RestartApiErrorBody {
  code: RestartApiErrorCode;
  message: string;
  requestId: string;
  details?: Record<string, unknown>;
}

export interface RestartListOptions {
  page?: number;
  pageSize?: number;
  signal?: AbortSignal;
}

export interface RestartBatchListOptions extends RestartListOptions {
  status?: RestartBatchStatus;
  query?: string;
}

export interface RestartEanListOptions extends RestartListOptions {
  status?: RestartEanStatus;
}

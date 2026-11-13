import type {
  RestartAccount,
  RestartAiStatus,
  RestartSettings,
  RestartContentDefaults,
  RestartApiErrorBody,
  RestartBatch,
  RestartBatchItem,
  RestartBatchListOptions,
  RestartBatchPreview,
  RestartCreateBatchRequest,
  RestartDiscoveryCandidatesResponse,
  RestartDiscoveryRun,
  RestartDiscoverySchedule,
  RestartDripBatch,
  RestartDripBatchItem,
  RestartDripBatchStatus,
  RestartDripSchedulePreview,
  RestartDripScheduleRequest,
  RestartEanEntry,
  RestartEanImportResult,
  RestartEanPoolPreview,
  RestartEanListOptions,
  RestartFactoryListResponse,
  RestartFactoryRecalculation,
  RestartFactoryStatus,
  RestartImportCommit,
  RestartImportPreview,
  RestartImportRun,
  RestartImportRunRowsResponse,
  RestartOverview,
  RestartEvent,
  RestartPage,
  RestartQueuePage,
  RestartListOptions,
  RestartLibraryLinkListOptions,
  RestartLibraryLinkListResponse,
  RestartProduct,
  RestartProductListOptions,
  RestartProductListResponse,
  RestartProductSyncResult,
  RestartOperation,
  RestartPreparationResult,
  RestartOperationQcResult,
  RestartTitleSuggestion,
  RestartCategorySuggestion,
  RestartAttributeSuggestion,
  RestartAvailabilityAction,
} from "./types";
import { getCsrfToken as issueCsrfToken } from "@/features/auth/csrf-client";

export class RestartApiConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RestartApiConfigurationError";
  }
}

export class RestartApiError extends Error {
  readonly status: number;
  readonly code: RestartApiErrorBody["code"] | "NETWORK_ERROR" | "INVALID_RESPONSE";
  readonly requestId?: string;
  readonly details?: Record<string, unknown>;

  constructor(args: {
    message: string;
    status: number;
    code: RestartApiError["code"];
    requestId?: string;
    details?: Record<string, unknown>;
  }) {
    super(args.message);
    this.name = "RestartApiError";
    this.status = args.status;
    this.code = args.code;
    this.requestId = args.requestId;
    this.details = args.details;
  }
}

export interface RestartApiClientOptions {
  /** Only the authenticated same-origin BFF path is accepted. */
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  csrfToken?: () => string | undefined;
}

export interface RestartApiHealth {
  status: "ok";
  service: "ottorestart-local-api";
}

export interface RestartProductEdit {
  title: string;
  category: string;
  attributes: Record<string, string>;
  version: number;
  updatedBy: string;
  updatedAt: string;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  account?: RestartAccount;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
  idempotencyKey?: string;
};

function apiBasePath(override?: string): string {
  const basePath = override ?? "/restart/api/backend";
  if (basePath !== "/restart/api/backend") {
    throw new RestartApiConfigurationError("OttoRestart requests must use the authenticated same-origin BFF.");
  }
  return basePath;
}

function isApiErrorBody(value: unknown): value is RestartApiErrorBody {
  if (!value || typeof value !== "object") return false;
  const body = value as Partial<RestartApiErrorBody>;
  return typeof body.code === "string" && typeof body.message === "string" && typeof body.requestId === "string";
}

export class RestartApiClient {
  private readonly basePath: string;
  private readonly fetchImpl: typeof fetch;
  private readonly csrfToken?: () => string | undefined;
  private csrfValue?: string;

  constructor(options: RestartApiClientOptions = {}) {
    this.basePath = apiBasePath(options.baseUrl);
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
    this.csrfToken = options.csrfToken;
  }

  getOverview(account: RestartAccount, signal?: AbortSignal): Promise<RestartOverview> {
    return this.request("/overview", { account, signal });
  }

  listProducts(
    account: RestartAccount,
    options: RestartProductListOptions = {},
  ): Promise<RestartProductListResponse> {
    return this.request("/products", {
      account,
      signal: options.signal,
      query: {
        page: options.page,
        pageSize: options.pageSize,
        period: options.period,
        status: options.status,
        source: options.source,
        factoryId: options.factoryId,
        query: options.query,
        category: options.category,
        titleOver70: options.titleOver70 ? "true" : undefined,
        sortBy: options.sortBy,
        sortDir: options.sortDir,
        includeMetrics: options.includeMetrics === false ? "false" : undefined,
      },
    });
  }

  listFactories(
    account: RestartAccount,
    options: { status?: RestartFactoryStatus | "ALL"; sortByNew?: boolean; signal?: AbortSignal } = {},
  ): Promise<RestartFactoryListResponse> {
    return this.request("/factories", {
      account,
      signal: options.signal,
      query: { status: options.status, sort_by_new: options.sortByNew ? "true" : "false" },
    });
  }

  recalculateFactory(
    factoryId: string,
    account: RestartAccount,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartFactoryRecalculation> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Factory recalculation requires an idempotency key of at least 8 characters.");
    }
    return this.request(`/factories/${encodeURIComponent(factoryId)}/complete`, {
      account,
      body: { account, factoryId },
      idempotencyKey,
      signal,
    });
  }

  getProduct(
    candidateEpisodeId: string,
    account: RestartAccount,
    signal?: AbortSignal,
  ): Promise<RestartProduct> {
    return this.request(`/products/${encodeURIComponent(candidateEpisodeId)}`, { account, signal });
  }

  getProductEdit(candidateId: string, account: RestartAccount, signal?: AbortSignal): Promise<{ edit: RestartProductEdit | null }> {
    return this.request(`/products/${encodeURIComponent(candidateId)}/edit`, { account, signal });
  }

  saveProductEdit(candidateId: string, account: RestartAccount, edit: {
    title: string; category: string; attributes: Record<string, string>; expectedVersion: number;
  }, idempotencyKey: string): Promise<{ edit: RestartProductEdit }> {
    return this.request(`/products/${encodeURIComponent(candidateId)}/edit`, {
      method: "PUT", account, body: { account, ...edit }, idempotencyKey,
    });
  }

  getAiStatus(account: RestartAccount, signal?: AbortSignal): Promise<RestartAiStatus> {
    return this.request("/ai/status", { account, signal });
  }

  getSettings(account: RestartAccount, signal?: AbortSignal): Promise<RestartSettings> {
    return this.request("/settings", { account, signal });
  }

  getContentDefaults(account: RestartAccount, category: string, signal?: AbortSignal): Promise<RestartContentDefaults> {
    return this.request("/defaults", { account, query: { category }, signal });
  }

  reserveOperationEan(account: RestartAccount, operationId: string, expectedVersion: number, idempotencyKey: string, signal?: AbortSignal): Promise<{ ean: string; status: string; version: number; reused: boolean }> {
    return this.request(`/operations/${encodeURIComponent(operationId)}/reserve-ean`, {
      account, body: { account, expectedVersion }, idempotencyKey, signal,
    });
  }

  suggestTitle(
    account: RestartAccount,
    sourceTitle: string,
    signal?: AbortSignal,
  ): Promise<RestartTitleSuggestion> {
    return this.request("/ai/title-suggestion", {
      account,
      body: { account, sourceTitle },
      signal,
    });
  }

  suggestCategory(candidateEpisodeId: string, account: RestartAccount, signal?: AbortSignal): Promise<RestartCategorySuggestion> {
    return this.request("/ai/category-suggestion", {
      account, body: { account, candidateEpisodeId }, signal,
    });
  }

  suggestAttributes(
    candidateEpisodeId: string,
    account: RestartAccount,
    category: string,
    currentAttributes: Record<string, string>,
    signal?: AbortSignal,
  ): Promise<RestartAttributeSuggestion> {
    return this.request("/ai/attribute-suggestions", {
      account,
      body: {
        account, candidateEpisodeId, category,
        currentAttributes: Object.entries(currentAttributes).map(([name, value]) => ({ name, value })),
      },
      signal,
    });
  }

  createOperation(
    account: RestartAccount,
    candidateEpisodeId: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartOperation> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Operation creation requires an idempotency key of at least 8 characters.");
    }
    return this.request("/operations", {
      account,
      body: { account, candidateEpisodeId },
      idempotencyKey,
      signal,
    });
  }

  prepareOperation(
    account: RestartAccount,
    operationId: string,
    expectedVersion: number,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartPreparationResult> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Operation preparation requires an idempotency key of at least 8 characters.");
    }
    return this.request(`/operations/${encodeURIComponent(operationId)}/prepare`, {
      account,
      body: { account, expectedVersion },
      idempotencyKey,
      signal,
    });
  }

  runOperationQc(
    account: RestartAccount,
    operationId: string,
    expectedVersion: number,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartOperationQcResult> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Operation QC requires an idempotency key of at least 8 characters.");
    }
    return this.request(`/operations/${encodeURIComponent(operationId)}/qc`, {
      account,
      body: { account, expectedVersion },
      idempotencyKey,
      signal,
    });
  }

  retryAvailability(
    account: RestartAccount,
    operationId: string,
    action: RestartAvailabilityAction,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<{ operationId: string; attemptId: string; jobId: string; action: RestartAvailabilityAction; retryNumber: number; status: string }> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Availability retry requires an idempotency key of at least 8 characters.");
    }
    return this.request(`/operations/${encodeURIComponent(operationId)}/availability/${action}/retry`, {
      account, body: { account }, idempotencyKey, signal,
    });
  }

  resolveAvailability(
    account: RestartAccount,
    operationId: string,
    action: RestartAvailabilityAction,
    resolution: "APPLIED" | "NOT_APPLIED",
    evidenceReference: string,
    reason: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<{ operationId: string; attemptId: string; action: RestartAvailabilityAction; resolution: "APPLIED" | "NOT_APPLIED"; status: string; published: false }> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Availability resolution requires an idempotency key of at least 8 characters.");
    }
    return this.request(`/operations/${encodeURIComponent(operationId)}/availability/${action}/resolve`, {
      account, body: { account, resolution, evidenceReference, reason }, idempotencyKey, signal,
    });
  }

  syncProduct(
    candidateEpisodeId: string,
    account: RestartAccount,
    idempotencyKey: string,
    expectedVersion?: number,
    signal?: AbortSignal,
  ): Promise<RestartProductSyncResult> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Product synchronization requires an idempotency key of at least 8 characters.");
    }
    return this.request(`/products/${encodeURIComponent(candidateEpisodeId)}/sync`, {
      account,
      body: { account, expectedVersion },
      idempotencyKey,
      signal,
    });
  }

  getHealth(signal?: AbortSignal): Promise<RestartApiHealth> {
    return this.request("/health", { signal });
  }

  async getCsrfToken(signal?: AbortSignal): Promise<string> {
    try {
      this.csrfValue = await issueCsrfToken();
      return this.csrfValue;
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new RestartApiError({
        message: error instanceof Error ? error.message : "Could not obtain an OttoRestart CSRF token.",
        status: 403,
        code: "CSRF_REQUIRED",
      });
    }
  }

  listBatches(account: RestartAccount, options: RestartBatchListOptions = {}): Promise<RestartPage<RestartBatch>> {
    return this.request("/batches", {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, status: options.status, query: options.query },
    });
  }

  listDripBatches(
    account: RestartAccount,
    options: { page?: number; pageSize?: number; status?: RestartDripBatchStatus; signal?: AbortSignal } = {},
  ): Promise<RestartPage<RestartDripBatch>> {
    return this.request("/batches", {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, status: options.status },
    });
  }

  listDripBatchItems(
    batchId: string,
    account: RestartAccount,
    options: { page?: number; pageSize?: number; query?: string; signal?: AbortSignal } = {},
  ): Promise<RestartPage<RestartDripBatchItem>> {
    return this.request(`/batches/${encodeURIComponent(batchId)}/items`, {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, query: options.query },
    });
  }

  previewDripSchedule(
    request: RestartDripScheduleRequest,
    signal?: AbortSignal,
  ): Promise<RestartDripSchedulePreview> {
    return this.request("/batches/preview", { body: request, signal });
  }

  createDripBatch(
    request: RestartDripScheduleRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartDripBatch> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Drip Feed creation requires an idempotency key of at least 8 characters.");
    }
    return this.request("/batches", { body: request, idempotencyKey, signal });
  }

  controlDripBatch(
    batchId: string,
    account: RestartAccount,
    action: "start" | "pause" | "resume" | "cancel" | "retry",
    expectedVersion: number,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartDripBatch> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Drip Feed actions require an idempotency key of at least 8 characters.");
    }
    return this.request(`/batches/${encodeURIComponent(batchId)}/${action}`, {
      account,
      body: { account, expectedVersion },
      idempotencyKey,
      signal,
    });
  }

  getBatch(batchId: string, account: RestartAccount, signal?: AbortSignal): Promise<RestartBatch> {
    return this.request(`/batches/${encodeURIComponent(batchId)}`, { account, signal });
  }

  listBatchItems(
    batchId: string,
    account: RestartAccount,
    options: RestartBatchListOptions = {},
  ): Promise<RestartPage<RestartBatchItem>> {
    return this.request(`/batches/${encodeURIComponent(batchId)}/items`, {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, query: options.query },
    });
  }

  previewBatch(request: RestartCreateBatchRequest, signal?: AbortSignal): Promise<RestartBatchPreview> {
    return this.request("/batches/preview", { body: request, signal });
  }

  previewImportFile(
    file: File,
    account: RestartAccount,
    period?: "3M" | "12M",
    signal?: AbortSignal,
  ): Promise<RestartBatchPreview> {
    const formData = new FormData();
    formData.set("file", file);
    return this.request("/imports/preview", {
      formData,
      signal,
      query: { account, period },
    });
  }

  previewSourceImport(
    account: RestartAccount,
    rawText: string,
    filename = "paste_input.txt",
    signal?: AbortSignal,
  ): Promise<RestartImportPreview> {
    return this.request("/imports/paste", {
      body: { account, raw_text: rawText, filename, dryRun: true },
      signal,
    });
  }

  previewSourceImportFile(
    account: RestartAccount,
    file: File,
    signal?: AbortSignal,
  ): Promise<RestartImportPreview> {
    const formData = new FormData();
    formData.set("file", file);
    return this.request("/imports/upload", {
      formData,
      query: { account, dry_run: "true" },
      signal,
    });
  }

  commitSourceImport(
    account: RestartAccount,
    rawText: string,
    idempotencyKey: string,
    filename = "paste_input.txt",
    signal?: AbortSignal,
  ): Promise<RestartImportCommit> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Source import commits require an idempotency key of at least 8 characters.");
    }
    return this.request("/imports/paste", {
      body: { account, raw_text: rawText, filename, dryRun: false, runMode: "BASELINE" },
      idempotencyKey,
      signal,
    });
  }

  commitSourceImportFile(
    account: RestartAccount,
    file: File,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartImportCommit> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Source import commits require an idempotency key of at least 8 characters.");
    }
    const formData = new FormData();
    formData.set("file", file);
    return this.request("/imports/upload", {
      formData,
      query: { account, dry_run: "false", runMode: "BASELINE" },
      idempotencyKey,
      signal,
    });
  }

  getImportRun(runId: string, account: RestartAccount, signal?: AbortSignal): Promise<RestartImportRun> {
    return this.request(`/imports/runs/${encodeURIComponent(runId)}`, { account, signal });
  }

  getImportRunRows(
    runId: string,
    account: RestartAccount,
    options: { page?: number; pageSize?: number; signal?: AbortSignal } = {},
  ): Promise<RestartImportRunRowsResponse> {
    return this.request(`/imports/runs/${encodeURIComponent(runId)}/rows`, {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize },
    });
  }

  listImportRuns(account: RestartAccount, limit = 20, signal?: AbortSignal): Promise<{ items: RestartImportRun[]; count: number }> {
    return this.request("/imports/runs", { account, query: { limit }, signal });
  }

  createDiscoveryScan(
    account: RestartAccount,
    idempotencyKey: string,
    fromDate?: string,
    signal?: AbortSignal,
  ): Promise<RestartDiscoveryRun> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Discovery scans require an idempotency key of at least 8 characters.");
    }
    return this.request("/scans", {
      body: { account, ...(fromDate ? { fromDate } : {}) },
      idempotencyKey,
      signal,
    });
  }

  listDiscoveryRuns(
    account: RestartAccount,
    options: { limit?: number; offset?: number; signal?: AbortSignal } = {},
  ): Promise<{ items: RestartDiscoveryRun[]; count: number; limit: number; offset: number }> {
    return this.request("/scans", {
      account,
      signal: options.signal,
      query: { limit: options.limit, offset: options.offset },
    });
  }

  getDiscoveryRun(runId: string, account: RestartAccount, signal?: AbortSignal): Promise<RestartDiscoveryRun> {
    return this.request(`/scans/${encodeURIComponent(runId)}`, { account, signal });
  }

  getDiscoveryCandidates(
    runId: string,
    account: RestartAccount,
    options: { page?: number; pageSize?: number; signal?: AbortSignal } = {},
  ): Promise<RestartDiscoveryCandidatesResponse> {
    return this.request(`/scans/${encodeURIComponent(runId)}/candidates`, {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize },
    });
  }

  confirmDiscoveryBaseline(account: RestartAccount, runId: string, signal?: AbortSignal): Promise<{
    account: RestartAccount;
    baselineRunId: string;
    baselineConfirmedAt: string;
    cutoffAt: string;
    verifiedRows: number;
    candidateRows: number;
  }> {
    return this.request("/scans/baseline/confirm", {
      body: { account, runId },
      signal,
    });
  }

  getDiscoverySchedule(account: RestartAccount, signal?: AbortSignal): Promise<RestartDiscoverySchedule> {
    return this.request("/scans/schedule/config", { account, signal });
  }

  updateDiscoverySchedule(
    schedule: Pick<RestartDiscoverySchedule, "account" | "enabled" | "timezone" | "weekday" | "localTime">,
    signal?: AbortSignal,
  ): Promise<RestartDiscoverySchedule> {
    return this.request("/scans/schedule", { method: "PUT", body: schedule, signal });
  }

  createBatch(
    request: RestartCreateBatchRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<RestartBatch> {
    if (idempotencyKey.trim().length < 8) {
      throw new RestartApiConfigurationError("Batch creation requires an idempotency key of at least 8 characters.");
    }
    return this.request("/batches", { body: request, idempotencyKey, signal });
  }

  listEanPool(account: RestartAccount, options: RestartEanListOptions = {}): Promise<RestartPage<RestartEanEntry>> {
    return this.request("/ean-pool", {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, status: options.status, q: options.query },
    });
  }

  listLibraryLinks(
    account: RestartAccount,
    options: RestartLibraryLinkListOptions = {},
  ): Promise<RestartLibraryLinkListResponse> {
    return this.request("/library/links", {
      account,
      signal: options.signal,
      query: {
        page: options.page,
        pageSize: options.pageSize,
        status: options.status,
        q: options.query,
        factory_id: options.factoryId,
      },
    });
  }

  previewEanPoolFile(account: RestartAccount, file: File, signal?: AbortSignal): Promise<RestartEanPoolPreview> {
    const formData = new FormData();
    formData.set("file", file);
    return this.request("/ean-pool/preview", { account, formData, signal });
  }

  importEans(account: RestartAccount, eans: string[], idempotencyKey: string, signal?: AbortSignal): Promise<RestartEanImportResult> {
    if (idempotencyKey.trim().length < 8) throw new RestartApiConfigurationError("EAN import requires an idempotency key.");
    return this.request("/ean-pool/import", { account, body: { account, eans }, idempotencyKey, signal });
  }

  getQueue(account: RestartAccount, options: RestartListOptions = {}): Promise<RestartQueuePage> {
    return this.request("/queue", {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize },
    });
  }

  listEvents(account: RestartAccount, options: RestartListOptions = {}): Promise<RestartPage<RestartEvent>> {
    return this.request("/events", {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, q: options.query },
    });
  }

  assignProductEans(
    account: RestartAccount,
    productIds: string[],
    signal?: AbortSignal,
  ): Promise<{ assignedCount: number; items: RestartProduct[] }> {
    return this.request("/products/assign-ean", {
      account,
      body: { account, productIds },
      signal,
    });
  }

  generateProductTitles(
    account: RestartAccount,
    productIds: string[],
    signal?: AbortSignal,
  ): Promise<{ generatedCount: number; items: RestartProduct[] }> {
    return this.request("/products/generate-titles", {
      account,
      body: { account, productIds },
      signal,
    });
  }

  updateProduct(
    candidateEpisodeId: string,
    account: RestartAccount,
    updates: {
      title?: string;
      priceCents?: number;
      category?: string;
      attributes?: Record<string, any> | Array<Record<string, any>>;
    },
    signal?: AbortSignal,
  ): Promise<RestartProduct> {
    return this.request(`/products/${encodeURIComponent(candidateEpisodeId)}`, {
      account,
      body: { account, ...updates },
      signal,
    });
  }

  importEanPool(
    account: RestartAccount,
    eans: string[],
    idempotencyKey?: string,
    signal?: AbortSignal,
  ): Promise<{ importedCount: number; status: string }> {
    const key = idempotencyKey || (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `ean-import-${Date.now()}`);
    return this.request("/ean-pool/import", {
      account,
      body: { account, eans },
      idempotencyKey: key,
      signal,
    });
  }

  searchCategories(
    account: RestartAccount,
    query: string,
    signal?: AbortSignal,
  ): Promise<Array<{ id: number; name: string }>> {
    return this.request("/categories", {
      account,
      query: { q: query },
      signal,
    });
  }

  getCategoryAttributes(
    account: RestartAccount,
    categoryIdOrName: string,
    signal?: AbortSignal,
  ): Promise<Array<{
    id: string;
    name: string;
    type: string;
    unit?: string | null;
    required: boolean;
    options?: string[] | null;
    multiValue?: boolean;
  }>> {
    return this.request(`/categories/${encodeURIComponent(categoryIdOrName)}/attributes`, {
      account,
      signal,
    });
  }

  getRequiredCategoryAttributes(account: RestartAccount, category: string, signal?: AbortSignal): Promise<{ category: string; requiredAttributes: string[] }> {
    return this.request(`/categories/${encodeURIComponent(category)}/required-attributes`, { account, signal });
  }

  private async request<T>(path: string, options: RequestOptions): Promise<T> {
    const url = new URL(`${this.basePath}${path}`, typeof window === "undefined" ? "http://localhost" : window.location.origin);
    if (options.account) url.searchParams.set("account", options.account);
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }

    const headers = new Headers({ Accept: "application/json" });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const hasBody = options.body !== undefined || options.formData !== undefined;
    let csrf = hasBody ? this.csrfToken?.() ?? this.csrfValue : undefined;
    if (hasBody && !csrf) csrf = await this.getCsrfToken(options.signal);
    if (csrf) headers.set("X-CSRF-Token", csrf);
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method: options.method ?? (hasBody ? "POST" : "GET"),
        headers,
        body: options.formData ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
        credentials: "include",
        cache: "no-store",
        signal: options.signal,
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new RestartApiError({
        message: "Could not reach the OttoRestart API.",
        status: 0,
        code: "NETWORK_ERROR",
      });
    }

    const payload: unknown = await response.json().catch(() => undefined);
    if (!response.ok) {
      if (typeof window !== "undefined" && response.status === 401 && !window.location.pathname.includes("/login")) {
        const dest = window.location.pathname.replace(/^\/restart/, "") + window.location.search;
        window.location.href = `/restart/login?next=${encodeURIComponent(dest || "/")}`;
      }
      const apiError = isApiErrorBody(payload) ? payload : undefined;
      throw new RestartApiError({
        message: apiError?.message ?? `OttoRestart API returned HTTP ${response.status}.`,
        status: response.status,
        code: apiError?.code ?? (payload === undefined ? "INVALID_RESPONSE" : "INTERNAL_ERROR"),
        requestId: apiError?.requestId,
        details: apiError?.details,
      });
    }
    if (payload === undefined) {
      throw new RestartApiError({
        message: "OttoRestart API returned an unreadable JSON response.",
        status: response.status,
        code: "INVALID_RESPONSE",
      });
    }
    return payload as T;
  }
}

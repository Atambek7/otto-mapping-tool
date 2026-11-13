export type RestartAccount = "JV" | "XL";
export type RestartPeriod = "3M" | "12M";
export type RestartFactoryStatus = "COMPLETE" | "NOT_COMPLETE" | "UNKNOWN";

export interface RestartAiStatus {
  account: RestartAccount;
  enabled: boolean;
  configured: boolean;
  available: boolean;
  state: "READY" | "DISABLED" | "NOT_CONFIGURED" | "INVALID";
  model: string | null;
}

export interface RestartSettings {
  account: RestartAccount;
  mode: string;
  operator: { id: string; accounts: RestartAccount[] };
  otto: { configured: boolean; readEnabled: boolean };
  aftercool: { configured: boolean; readEnabled: boolean };
  ai: Omit<RestartAiStatus, "account">;
  publication: { productWriteEnabled: boolean; availabilityWriteEnabled: boolean; jobTypes: string[] };
  price: { discountCents: number; fractionCents: number; source: string; directOttoSource: string };
  fulfillment: { quantity: number; processingDays: number; shippingProfile: string; shippingProfileId: string };
  ean: { digits: number; globallyUnique: boolean };
}

export interface RestartContentDefaults {
  account: RestartAccount;
  category: string;
  attributes: Record<string, string>;
  accountAttributeNames: string[];
}

export interface RestartTitleSuggestion {
  account: RestartAccount;
  sourceTitle: string;
  title: string;
  manualReviewRequired: true;
  reviewReasons: string[];
  inputHash: string;
  model: string;
  promptVersion: string;
  schemaVersion: string;
}

export interface RestartCategorySuggestion {
  account: RestartAccount;
  candidateEpisodeId: string;
  categoryId: string;
  category: string;
  reason: string;
  manualReviewRequired: true;
}

export interface RestartAttributeSuggestion {
  account: RestartAccount;
  candidateEpisodeId: string;
  attributes: Array<{ name: string; value: string; previousValue: string }>;
  manualReviewRequired: true;
  notice: string;
}

export interface RestartOperation {
  id: string;
  account: RestartAccount;
  sourceProductId: string;
  candidateEpisodeId: string;
  period: RestartPeriod | null;
  attemptNumber: number;
  status: string;
  targetSku: string | null;
  newEan: string | null;
  version: number;
  revisionNumber: number;
  submissionState: string;
}

export interface RestartPreparationResult {
  id: string;
  operationId: string;
  account: RestartAccount;
  revisionNumber: number;
  payloadHash: string;
  gtin: string;
  targetPriceCents: number;
  preparationState: "PAYLOAD_PREPARED" | "PAYLOAD_PREPARED_WITH_GAPS";
  readinessGaps: string[];
}

export interface RestartOperationQcResult {
  id: string;
  operationId: string;
  account: RestartAccount;
  revisionId: string;
  revisionNumber: number;
  payloadHash: string;
  outcome: "PASSED_AUTO" | "MANUAL_REQUIRED" | "BLOCKED" | string;
  status: string;
  version: number;
  policyVersion: string;
  issues: Array<{ code: string; outcome: string }>;
}

export interface RestartDiscoveryRun {
  id?: string;
  runId?: string;
  jobId?: string;
  account: RestartAccount;
  runType: "INCREMENTAL_SCAN" | "KNOWN_SKU_RECONCILIATION";
  runState: string;
  windowFrom: string | null;
  cutoffAt: string;
  totalRows: number;
  coverage: Record<string, unknown>;
  errors: Array<Record<string, unknown>>;
  createdAt?: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  parameters?: Record<string, unknown>;
  httpStatus?: 202;
}

export interface RestartDiscoveryCandidate {
  requestedSku: string;
  classification: string;
  period: RestartPeriod | null;
  reductionState: string | null;
  episodeId: string | null;
  episodeState: string | null;
  origin: string | null;
  routingState: string | null;
  routingErrorCode: string | null;
  sourceProductId: string | null;
  sourceType: string | null;
  factoryId: string | null;
  factoryName: string | null;
  title: string | null;
  imageUrl: string | null;
}

export interface RestartDiscoveryCandidatesResponse {
  items: RestartDiscoveryCandidate[];
  count: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface RestartDiscoverySchedule {
  account: RestartAccount;
  baselineConfirmed: boolean;
  baselineRunId: string | null;
  baselineConfirmedAt: string | null;
  enabled: boolean;
  timezone: string;
  weekday: number;
  localTime: string;
  nextAt: string | null;
  lastScheduledSlotAt: string | null;
}

export interface RestartFactory {
  id: string;
  account: RestartAccount;
  name: string;
  status: RestartFactoryStatus;
  totalCandidates: number;
  publishedCount: number;
  readyCount: number;
  errorCount: number;
  newCandidatesCount: number;
  period3mCount: number;
  period12mCount: number;
  unresolvedCount: number;
  firstCompletedAt: string | null;
  lastCompletedAt: string | null;
  lastScannedAt: string | null;
  wasPreviouslyCompleted: boolean;
  candidateGeneration: number;
  coverageGeneration: number | null;
  coverageConfirmedAt: string | null;
  coverageRunId: string | null;
  coverageSource: string | null;
  coverageFresh: boolean;
}

export interface RestartFactoryListResponse {
  items: RestartFactory[];
  count: number;
  totalCount: number;
  completeCount: number;
  notCompleteCount: number;
  unknownCount: number;
  totalNewCandidates: number;
  totalCandidates: number;
  count3m: number;
  count12m: number;
}

export interface RestartFactoryRecalculation {
  factoryId: string;
  account: RestartAccount;
  status: RestartFactoryStatus;
  completionRecorded: boolean;
  totalCandidates: number;
  publishedCount: number;
  readyCount: number;
  errorCount: number;
  newCandidatesCount: number;
  period3mCount: number;
  period12mCount: number;
  unresolvedCount: number;
  firstCompletedAt: string | null;
  lastCompletedAt: string | null;
  lastScannedAt: string | null;
  wasPreviouslyCompleted: boolean;
  candidateGeneration: number;
  coverageGeneration: number | null;
  coverageConfirmedAt: string | null;
  coverageRunId: string | null;
  coverageSource: string | null;
  coverageFresh: boolean;
}

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

export interface RestartQueueCounts {
  pending: number;
  running: number;
  retryWait: number;
  succeeded: number;
  dead: number;
  cancelled: number;
  dispatchErrors: number;
}

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

export type RestartDripBatchStatus = "DRAFT" | "SCHEDULED" | "ACTIVE" | "PAUSED" | "COMPLETED" | "CANCELLED";

export interface RestartDripBatch {
  id: string;
  account: RestartAccount;
  period: RestartPeriod;
  name: string;
  status: RestartDripBatchStatus;
  itemCount: number;
  createdAt: string;
  factoryId: string | null;
  localStartDate: string | null;
  dailyTime: string | null;
  timezone: string | null;
  scheduledStartAt: string | null;
  totalDays: number | null;
  version: number;
  startedAt: string | null;
  pausedAt: string | null;
  cancelledAt: string | null;
  itemCounts?: Record<string, number> | null;
}

export interface RestartDripBatchItem {
  id: number;
  batchId: string;
  account: RestartAccount;
  sourceEan: string;
  rowNumber: number;
  operationId: string;
  revisionId: string;
  qcResultId: string;
  payloadHash: string;
  scheduledAt: string;
  status: string;
  releaseAttempts: number;
  lastErrorCode: string | null;
  readyAt: string | null;
  targetSku: string | null;
  sourceSku: string | null;
  revisionNumber: number;
  totalAttempts: number;
}

export interface RestartDripScheduleRequest {
  account: RestartAccount;
  name: string;
  operationIds: string[];
  startDate: string;
  dailyTime: string;
  timezone: string;
  totalDays: number;
}

export interface RestartDripSchedulePreview {
  account: RestartAccount;
  factoryId: string | null;
  period: RestartPeriod;
  timezone: string;
  totalDays: number;
  itemCount: number;
  distribution: Array<{
    dayNumber: number;
    scheduledAt: string;
    operationIds: string[];
    count: number;
  }>;
  externalServicesCalled: string[];
  publicationEnabled: false;
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

export interface RestartProduct {
  id: string;
  candidateEpisodeId: string;
  savedEdit?: { title: string; category: string; attributes: Record<string, string>; version: number } | null;
  sourceProductId: string;
  account: RestartAccount;
  dataScope: "LEGACY_UNVERIFIED" | "PRODUCTION";
  source: "AFTERCOOL" | "AFTERBUY" | "OTTO_DIRECT";
  factoryId: string | null;
  factoryName: string | null;
  sku: string | null;
  ean: string | null;
  moin: string | null;
  period: RestartPeriod | null;
  /** Persisted backend operation/episode status; the UI must not infer it. */
  status: string;
  classification: "EXACT_CODE" | "TITLE_ONLY";
  origin: "BASELINE" | "NEW";
  episodeState: "OPEN" | "CLOSED";
  operationId: string | null;
  operationStatus: string | null;
  operationVersion: number | null;
  currentRevisionNumber: number | null;
  targetSku: string | null;
  newEan: string | null;
  title: string | null;
  sourceTitle?: string | null;
  draftTitle?: string | null;
  preparedTitle?: string | null;
  priceCents: number | null;
  pricePreview?: { baseCents: number; targetCents: number; uvpCents: number; selectedSource: string; capturedAt: string | null } | null;
  ottoSourcePrice?: {
    source?: "OTTO";
    status: string;
    sku?: string | null;
    ean?: string | null;
    amount?: string | null;
    amountCents?: number | null;
    currency?: string | null;
    path?: string | null;
    capturedAt?: string | null;
  } | null;
  aftercoolSourcePrice?: {
    source?: "AFTERCOOL";
    status: string;
    amountCents?: number | null;
    currency?: string | null;
    path?: string | null;
  } | null;
  priceReviewReason?: string | null;
  targetPriceStatus?: "CALCULATED" | "PRICE_UNAVAILABLE" | "PRICE_REVIEW_REQUIRED";
  category: string | null;
  imageUrl: string | null;
  attributes: Array<Record<string, unknown>>;
  sourceData: Record<string, unknown>;
  errors: Array<Record<string, unknown>>;
  errorCodes: string[];
  errorTitles: string[];
  lastModified: string | null;
  lastCheckedAt: string | null;
}

export interface RestartProductListResponse {
  items: RestartProduct[];
  page: {
    page: number;
    pageSize: number;
    total: number;
    totalPages: number;
  };
  metrics: {
    scope: "ACCOUNT_OPEN_CANDIDATES" | "CATALOG_OPEN_CANDIDATES";
    total: number;
    count3m: number;
    count12m: number;
    statusCounts: Record<string, number>;
    over70Count: number;
    categories: string[];
  } | null;
}

export interface RestartProductSyncResult {
  account: RestartAccount;
  candidateEpisodeId: string;
  sourceProductId: string;
  operationId: string | null;
  operationVersion: number | null;
  revision: { id: string; revisionNumber: number; payloadHash: string } | null;
  category: string;
  attributes: Array<Record<string, unknown>>;
  targetPriceStatus: "CALCULATED" | "PRICE_REVIEW_REQUIRED";
  priceReviewReason?: string | null;
  priceSnapshot: {
    currency: "EUR";
    ottoStandardPriceCents: number;
    aftercoolStartPriceCents: number | null;
    originalPriceCents?: number;
    selectedSource?: "OTTO_STANDARD_PRICE" | "AFTERCOOL_START_PRICE";
    discountCents?: number;
    discountedPriceCents?: number;
    targetPriceCents?: number;
    uvpPriceCents?: number;
    uvpFormula?: string;
    targetPriceStatus?: "PRICE_REVIEW_REQUIRED";
    priceReviewReason?: string;
    formula?: "MAX_SOURCE_PRICE_MINUS_ACCOUNT_DISCOUNT_THEN_99";
    capturedAt: string;
  };
  syncedAt: string;
}

export interface RestartImportPreview {
  success: false;
  status: "UNVERIFIED";
  previewCompleted: true;
  dryRun: true;
  account: RestartAccount;
  filename: string;
  totalRows: number;
  counts: { valid: number; duplicate: number; invalid: number };
  items: Array<{
    rowNumber: number;
    rawIdentifier: string;
    sku: string | null;
    ean: string | null;
    periodHint: RestartPeriod | null;
    rowState: "VALID" | "DUPLICATE" | "INVALID";
    errorCode: string | null;
  }>;
  summary: Record<string, unknown>;
  warnings: string[];
  dataSourcesChecked: string[];
}

export interface RestartImportCommit {
  success: boolean;
  status: string;
  runId: string;
  jobId: string;
  httpStatus: number;
}

export interface RestartImportRun {
  id: string;
  account: RestartAccount;
  filename: string | null;
  runType: string;
  runMode: string | null;
  runState: string;
  totalRows: number;
  processedCount: number;
  afterbuyCount: number;
  directOttoCount: number;
  skippedCount: number;
  errorCount: number;
  status: string;
  summary: Record<string, unknown>;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface RestartImportRunRow {
  id: string;
  runId: string;
  rowNumber: number;
  rawIdentifier: string;
  normalizedIdentifier: string | null;
  periodHint: string | null;
  rowState: string;
  row: Record<string, unknown>;
  errors: Array<{ code: string }>;
}

export interface RestartImportRunRowsResponse {
  items: RestartImportRunRow[];
  count: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface RestartProductListOptions extends RestartListOptions {
  period?: RestartPeriod;
  status?: string;
  source?: RestartProduct["source"];
  factoryId?: string;
  query?: string;
  category?: string;
  titleOver70?: boolean;
  sortBy?: "name" | "ean" | "price" | "category" | "status";
  sortDir?: "asc" | "desc";
  includeMetrics?: boolean;
}

export interface RestartEanEntry {
  ean: string;
  account: RestartAccount;
  status: RestartEanStatus;
  batchId: string | null;
  importedAt: string | null;
  provenanceSource?: string;
  ownerReference?: string | null;
  eligibilityStatus?: "UNVERIFIED" | "ELIGIBLE" | "INELIGIBLE";
  importedBy?: string | null;
  verifiedBy?: string | null;
  verifiedAt?: string | null;
  reservationOperationId?: string | null;
  quarantinedAt?: string | null;
  quarantineReason?: string | null;
  version?: number;
  updatedAt?: string;
}

export type RestartLibraryLinkStatus = "ACTIVE" | "PENDING_PUBLISH" | "PUBLISHED" | "DEACTIVATED";
export type RestartAvailabilityAction = "QUANTITY" | "DELIVERY_INFORMATION";

export interface RestartLibraryLinkedIdentifier {
  ean: string;
  targetSku: string | null;
  linkId: string;
  linkStatus: RestartLibraryLinkStatus;
  linkedAt: string;
}

export interface RestartLibraryOnlineIdentifier {
  ean: string;
  sku: string;
  status: "ONLINE";
  checkedAt: string;
  observationId: string;
}

export interface RestartLibraryLink {
  id: string;
  account: RestartAccount;
  sourceProductId: string;
  sourceType: RestartProduct["source"];
  aftercoolProductId: string | null;
  aftercoolSku: string | null;
  sourceEan: string;
  newEan: string;
  factoryId: string | null;
  factoryName: string | null;
  title: string | null;
  imageUrl: string | null;
  status: RestartLibraryLinkStatus;
  createdAt: string;
  linkStatus: RestartLibraryLinkStatus;
  operationId: string | null;
  attemptNumber: number | null;
  operationStatus: string | null;
  revisionNumber: number | null;
  publicationState: string;
  targetSku: string | null;
  linkedIdentifier: RestartLibraryLinkedIdentifier;
  confirmedOnlineIdentifiers: RestartLibraryOnlineIdentifier[];
  latestMarketplaceObservation: {
    sku: string;
    responseSku: string | null;
    marketPlaceStatus: string | null;
    checkedAt: string;
    identityVerified: boolean;
  } | null;
  processingConfirmed: boolean;
  onlineConfirmed: boolean;
  availability: {
    stock: {
      status: string;
      attemptCount: number;
      lastAttemptAt: string | null;
      httpStatus: number | null;
      errorCode: string | null;
      manualResolution: "APPLIED" | "NOT_APPLIED" | null;
      target: number | null;
    };
    delivery: {
      status: string;
      attemptCount: number;
      lastAttemptAt: string | null;
      httpStatus: number | null;
      errorCode: string | null;
      manualResolution: "APPLIED" | "NOT_APPLIED" | null;
      target: {
        shippingProfile: string | null;
        shippingProfileName: string | null;
        processingTime: string | null;
        originClassification: string | null;
        classificationBasis: string | null;
      };
    };
  };
}

export interface RestartLibraryLinkListResponse {
  items: RestartLibraryLink[];
  page: RestartPageInfo;
  summary: {
    linkedRecords: number;
    confirmedOnlineOnPage: number;
    poolAvailable: number;
    poolTotal: number;
    eventsCount: number;
  };
}

export interface RestartEanImportResult {
  importedCount: number;
  account: RestartAccount;
  status: "AVAILABLE" | "PARTIAL_QUARANTINE";
  importedAt: string;
  unverifiedCount: number;
  ineligibleCount: number;
}

export interface RestartEanPoolPreview {
  account: RestartAccount;
  inputCount: number;
  uniqueCount: number;
  duplicateCount: number;
  duplicates: string[];
  invalidEans: string[];
  uniqueEans: string[];
  dryRun: true;
}

export interface RestartQueueEntry {
  id: string;
  jobType: string;
  state: "PENDING" | "RUNNING" | "RETRY_WAIT" | "SUCCEEDED" | "DEAD" | "CANCELLED";
  batchId: string | null;
  priority: number;
  attemptsInCycle: number;
  totalAttempts: number;
  maxAttempts: number;
  lastErrorCode: string | null;
  lastDispatchErrorCode: string | null;
  availableAt: string;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
}

export interface RestartQueuePage extends RestartPage<RestartQueueEntry> {
  account: RestartAccount;
  status: RestartQueueStatus;
  counts: RestartQueueCounts;
}

export type RestartApiErrorCode =
  | "UNAUTHORIZED"
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "ACCOUNT_FORBIDDEN"
  | "ACCOUNT_REQUIRED"
  | "INVALID_ACCOUNT"
  | "CSRF_REQUIRED"
  | "AUTH_CONFIGURATION_ERROR"
  | "BACKEND_CONFIGURATION_ERROR"
  | "BACKEND_UNAVAILABLE"
  | "BACKEND_TIMEOUT"
  | "READ_ONLY_MODE"
  | "NOT_IMPLEMENTED"
  | "VALIDATION_ERROR"
  | "VERSION_CONFLICT"
  | "OPERATION_NOT_EDITABLE"
  | "OPERATION_QUEUED"
  | "CONFLICT"
  | "NOT_FOUND"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR";

export interface RestartApiErrorBody {
  code: RestartApiErrorCode;
  message: string;
  requestId: string;
  retryable?: boolean;
  details?: Record<string, unknown>;
}

export interface RestartListOptions {
  page?: number;
  pageSize?: number;
  query?: string;
  signal?: AbortSignal;
}

export interface RestartBatchListOptions extends RestartListOptions {
  status?: RestartBatchStatus;
  query?: string;
}

export interface RestartEanListOptions extends RestartListOptions {
  status?: RestartEanStatus;
  query?: string;
}

export interface RestartLibraryLinkListOptions extends RestartListOptions {
  status?: RestartLibraryLinkStatus;
  query?: string;
  factoryId?: string;
}

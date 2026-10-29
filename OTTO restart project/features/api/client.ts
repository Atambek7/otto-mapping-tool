import type {
  RestartAccount,
  RestartApiErrorBody,
  RestartBatch,
  RestartBatchItem,
  RestartBatchListOptions,
  RestartBatchPreview,
  RestartCreateBatchRequest,
  RestartEanEntry,
  RestartEanImportResult,
  RestartEanListOptions,
  RestartOverview,
  RestartEvent,
  RestartPage,
  RestartQueuePage,
  RestartListOptions,
} from "./types";

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
  /** Explicit OttoRestart service URL. Never defaults to the OttoFlow origin. */
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  csrfToken?: () => string | undefined;
}

export interface RestartApiHealth {
  status: "ok";
  service: "ottorestart-local-api";
}

type RequestOptions = {
  account?: RestartAccount;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
  idempotencyKey?: string;
};

function apiBaseUrl(override?: string): URL {
  const configured = override ?? process.env.NEXT_PUBLIC_RESTART_API_URL;
  if (!configured) {
    throw new RestartApiConfigurationError(
      "OttoRestart API is not connected. Set NEXT_PUBLIC_RESTART_API_URL when the isolated API service is available.",
    );
  }

  let url: URL;
  try {
    url = new URL(configured);
  } catch {
    throw new RestartApiConfigurationError("NEXT_PUBLIC_RESTART_API_URL must be an absolute HTTP(S) URL.");
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new RestartApiConfigurationError("OttoRestart API URL must use HTTP or HTTPS.");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new RestartApiConfigurationError("OttoRestart API URL must not contain credentials, a query, or a fragment.");
  }
  if (url.pathname !== "/" && url.pathname !== "") {
    throw new RestartApiConfigurationError("NEXT_PUBLIC_RESTART_API_URL must be the OttoRestart API origin, without a path.");
  }

  url.pathname = "/api/restart";
  return url;
}

function isApiErrorBody(value: unknown): value is RestartApiErrorBody {
  if (!value || typeof value !== "object") return false;
  const body = value as Partial<RestartApiErrorBody>;
  return typeof body.code === "string" && typeof body.message === "string" && typeof body.requestId === "string";
}

export class RestartApiClient {
  private readonly baseUrl: URL;
  private readonly fetchImpl: typeof fetch;
  private readonly csrfToken?: () => string | undefined;
  private csrfValue?: string;

  constructor(options: RestartApiClientOptions = {}) {
    this.baseUrl = apiBaseUrl(options.baseUrl);
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.csrfToken = options.csrfToken;
  }

  getOverview(account: RestartAccount, signal?: AbortSignal): Promise<RestartOverview> {
    return this.request("/overview", { account, signal });
  }

  getHealth(signal?: AbortSignal): Promise<RestartApiHealth> {
    return this.request("/health", { signal });
  }

  async getCsrfToken(signal?: AbortSignal): Promise<string> {
    const response = await this.request<{ token: string }>("/csrf", { signal });
    if (!response.token) throw new RestartApiError({ message: "OttoRestart API returned no CSRF token.", status: 200, code: "INVALID_RESPONSE" });
    this.csrfValue = response.token;
    return response.token;
  }

  listBatches(account: RestartAccount, options: RestartBatchListOptions = {}): Promise<RestartPage<RestartBatch>> {
    return this.request("/batches", {
      account,
      signal: options.signal,
      query: { page: options.page, pageSize: options.pageSize, status: options.status, query: options.query },
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
      query: { page: options.page, pageSize: options.pageSize, status: options.status },
    });
  }

  importEans(account: RestartAccount, eans: string[], signal?: AbortSignal): Promise<RestartEanImportResult> {
    return this.request("/ean-pool/import", { body: { account, eans }, signal });
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
      query: { page: options.page, pageSize: options.pageSize },
    });
  }

  private async request<T>(path: string, options: RequestOptions): Promise<T> {
    const url = new URL(`${this.baseUrl.pathname}${path}`, this.baseUrl.origin);
    if (options.account) url.searchParams.set("account", options.account);
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }

    const headers = new Headers({ Accept: "application/json" });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const hasBody = options.body !== undefined || options.formData !== undefined;
    const csrf = hasBody ? this.csrfToken?.() ?? this.csrfValue : undefined;
    if (hasBody && !csrf) {
      throw new RestartApiConfigurationError("A CSRF token provider is required for OttoRestart API mutations.");
    }
    if (csrf) headers.set("X-CSRF-Token", csrf);
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method: hasBody ? "POST" : "GET",
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

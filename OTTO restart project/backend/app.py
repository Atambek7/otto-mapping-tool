from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import secrets
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from io import BytesIO, StringIO
from itertools import chain
from pathlib import Path, PurePath
from typing import Any, Literal
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Connection
from starlette.exceptions import HTTPException as StarletteHTTPException

from migration_runner import check_schema_head
from integrations.aftercool_client import AftercoolApiError, AftercoolCredentials
from integrations.otto_client import OttoApiError, OttoCredentials, OttoMarketClient
from integrations.openai_client import (
    OpenAIClientError,
    OpenAIResponsesClient,
    OpenAISettings,
    configuration_status as openai_configuration_status,
)
from runtime_mode import RuntimeConfig
from routers.jobs import router as jobs_router
from routers.scans import router as scans_router
from routers.settings import router as settings_router
from security import (
    AuthenticationFailure,
    Principal,
    SecuritySettings,
    authenticate_request,
    authorize_account,
)
from schemas.discovery import (
    ImportPreviewRequest,
    ImportRunDetailDTO,
    ImportRunListResponse,
    ImportRunRowsResponse,
    ProductListResponseDTO,
    ProductReadDTO,
)
from schemas.drip import (
    DripBatchActionInput,
    DripBatchDTO,
    DripBatchItemsResponse,
    DripBatchListResponse,
    DripScheduleInput,
    DripSchedulePreview,
)
from schemas.library import AftercoolLibraryLookupResponse, LibraryLinkListResponse
from services.factory_service import FactoryError, FactoryService, recalculate_factory
from services.ean_pool_service import EanPoolError, import_pool_entries, reserve_operation_ean
from services.gtin_service import (
    GtinEvidenceError,
    MAX_EVIDENCE_BYTES,
    get_gtin_evidence_artifact,
    list_gtin_evidence,
    review_gtin_evidence,
    submit_gtin_evidence,
    valid_ean13,
)
from services.import_pipeline import (
    MAX_IMPORT_BYTES,
    ImportConflict,
    ImportPipeline,
    ImportValidationError,
    parse_import_file,
)
from integrations.otto_client import OttoMarketClient
from services.qc_service import record_operation_qc
from services.availability_service import (
    AvailabilityError,
    resolve_unknown_availability_step,
    retry_rejected_availability_step,
)
from services.ai_service import (
    AISuggestionError,
    AISuggestionService,
    ATTRIBUTE_VISION_INSTRUCTIONS,
    CATEGORY_SUGGESTION_INSTRUCTIONS,
    SuggestionContext,
)

# Kept as an import alias for existing internal callers while the JSON
# contract now also carries an explicit BASELINE/INCREMENTAL run mode.
ImportPasteRequest = ImportPreviewRequest
from services.legacy_scope_service import legacy_conflict_report, legacy_rows, legacy_summary
from services.library_service import LibraryQueryError, list_links as list_production_library_links, lookup_aftercool as lookup_production_library_for_aftercool
from services.operation_service import (
    OperationError,
    append_operation_revision,
    cancel_operation,
    create_history_review,
    create_operation,
    create_retry_authorization,
    idempotent_operation_action,
)
from services.aftercool_lookup_service import AftercoolLookupService
from services.preparation_service import (
    PreparationError,
    TARGET_PAYLOAD_BUILDER_READY,
    load_preparation_context,
    prepare_operation_revision,
    price_snapshot_from_sources,
)
from services.product_sync_service import (
    ProductSyncError,
    apply_product_sync_snapshot,
    build_product_sync_snapshot,
    load_product_sync_context,
)
from services.otto_price_evidence import (
    is_missing_otto_price_error,
    normalize_otto_product_variation,
    resolve_otto_standard_price,
)
from services.product_catalog_service import ProductCatalogError, get_product, list_products
from services.product_edit_service import get_product_edit, save_product_edit
from services.queue_projection_service import list_queue_jobs
from services.drip_service import (
    DripError,
    control_batch,
    create_batch_draft,
    get_batch as get_drip_batch,
    list_batch_items as list_drip_batch_items,
    list_batches as list_drip_batches,
    preview_schedule,
    start_batch as start_drip_batch,
)
from services.qc_service import QCError, append_manual_revision, record_operation_qc

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get(
    "OTTORESTART_DATABASE_URL",
    "postgresql://restart_user:restart_secure_pass@127.0.0.1:5435/otto_restart_db",
)
RUNTIME_CONFIG = RuntimeConfig.from_environment(os.environ)
SECURITY_SETTINGS = SecuritySettings.from_environment(os.environ)
ACCOUNT = Literal["JV", "XL"]
PERIOD = Literal["3M", "12M"]
MAX_EANS_PER_REQUEST = 10_000

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_iso(val: object) -> str:
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


@contextmanager
def connect():
    with engine.connect() as connection:
        yield connection


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup is deliberately read-only: schema changes run only through
    # ``python migrate.py upgrade``.
    check_schema_head(engine)
    yield


def require_restart_principal(request: Request) -> Principal | None:
    if request.url.path == "/api/restart/health":
        return None
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "A valid Restart principal is required."})
    return principal


app = FastAPI(
    title="OttoRestart Local API",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_restart_principal)],
)
allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "OTTORESTART_ALLOWED_ORIGINS",
        "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3002,http://127.0.0.1:3002",
    ).split(",")
    if origin.strip()
]
app.state.database_engine = engine
app.state.allowed_origins = frozenset(allowed_origins)
app.state.runtime_config = RUNTIME_CONFIG
app.include_router(jobs_router)
app.include_router(scans_router)
app.include_router(settings_router)
if RUNTIME_CONFIG.mode.value == "PRODUCTION" and not os.environ.get("OTTORESTART_ALLOWED_ORIGINS", "").strip():
    raise RuntimeError("Production requires at least one OTTORESTART_ALLOWED_ORIGINS entry.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Request-ID"],
)


def _error_response(
    status: int,
    code: str,
    message: str,
    request_id: str,
    *,
    retryable: bool = False,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "requestId": request_id,
            "retryable": retryable,
            "details": details or {},
        },
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


@app.middleware("http")
async def authenticate_restart_api(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/restart/") or request.method.upper() == "OPTIONS":
        return await call_next(request)
    if path == "/api/restart/health":
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", str(uuid4()))
        return response

    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    bearer_token = credential.strip() if scheme.casefold() == "bearer" else None
    try:
        principal = authenticate_request(
            bearer_token,
            SECURITY_SETTINGS,
            method=request.method,
            path=path,
        )
        accounts = list(request.query_params.getlist("account"))
        content_type = request.headers.get("content-type", "").casefold()
        if "application/json" in content_type or "+json" in content_type:
            raw_body = await request.body()
            if raw_body:
                try:
                    body = json.loads(raw_body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                if isinstance(body, dict) and "account" in body:
                    accounts.append(body["account"])
        if path != "/api/restart/csrf" and not accounts:
            raise AuthenticationFailure(422, "ACCOUNT_REQUIRED", "An account is required for this Restart request.")
        for account in accounts:
            if not isinstance(account, str) or account not in {"JV", "XL"}:
                raise AuthenticationFailure(422, "INVALID_ACCOUNT", "Account must be JV or XL.")
            authorize_account(principal, account)
    except AuthenticationFailure as error:
        request_id = str(uuid4())
        response = _error_response(error.status, error.code, error.message, request_id)
        return response

    request.state.principal = principal
    request.state.request_id = principal.request_id or str(uuid4())
    response = await call_next(request)
    response.headers.setdefault("X-Request-ID", request.state.request_id)
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/api/restart/health")
def health():
    with connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "service": "ottorestart-local-api", "database": "postgresql"}


class BatchInput(BaseModel):
    account: ACCOUNT
    period: PERIOD
    name: str = Field(min_length=1, max_length=160)
    sourceEans: list[str] = Field(min_length=1, max_length=MAX_EANS_PER_REQUEST)


class EanImportInput(BaseModel):
    account: ACCOUNT
    eans: list[str] = Field(min_length=1, max_length=MAX_EANS_PER_REQUEST)


class GtinEvidenceReviewInput(BaseModel):
    account: ACCOUNT
    decision: Literal["APPROVE", "REJECT_EVIDENCE", "QUARANTINE_GTIN"]
    reviewBasis: str = Field(min_length=20, max_length=2000)


class RestartHistoryReviewInput(BaseModel):
    account: ACCOUNT
    sourceProductId: str
    candidateEpisodeId: str
    decision: Literal["CONFIRMED_TEST", "CONFIRMED_PRODUCTION", "INSUFFICIENT"]
    confirmedPriorAttemptCount: int = Field(ge=0)
    reviewedThroughOperationId: str | None = None
    reason: str = Field(min_length=20, max_length=2000)
    expiresAt: str


class RestartRetryAuthorizationInput(BaseModel):
    account: ACCOUNT
    sourceProductId: str
    candidateEpisodeId: str
    historyReviewId: str
    reason: str = Field(min_length=20, max_length=2000)
    expiresAt: str


class RestartOperationCreateInput(BaseModel):
    account: ACCOUNT
    candidateEpisodeId: str
    historyReviewId: str | None = None
    retryAuthorizationId: str | None = None


class RestartOperationPrepareInput(BaseModel):
    account: ACCOUNT
    expectedVersion: int = Field(ge=1)


class RestartOperationCancelInput(BaseModel):
    account: ACCOUNT
    expectedVersion: int = Field(ge=1)


class RestartOperationQCInput(BaseModel):
    account: ACCOUNT
    expectedVersion: int = Field(ge=1)


class RestartAvailabilityRetryInput(BaseModel):
    account: ACCOUNT


class RestartAvailabilityResolutionInput(BaseModel):
    account: ACCOUNT
    resolution: Literal["APPLIED", "NOT_APPLIED"]
    evidenceReference: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=20, max_length=2000)


class RestartTitleSuggestionInput(BaseModel):
    account: ACCOUNT
    sourceTitle: str = Field(min_length=1, max_length=1000)


class RestartCategorySuggestionInput(BaseModel):
    account: ACCOUNT
    candidateEpisodeId: str = Field(min_length=1, max_length=64)


class RestartAttributeSuggestionInput(BaseModel):
    account: ACCOUNT
    candidateEpisodeId: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=200)
    currentAttributes: list[dict[str, str]] = Field(default_factory=list, max_length=200)


class RestartManualRevisionInput(BaseModel):
    account: ACCOUNT
    expectedVersion: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=200)
    attributes: list[dict[str, Any]] = Field(max_length=200)
    images: list[dict[str, Any]] = Field(max_length=100)
    confirmations: list[dict[str, str]] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=20, max_length=2000)


class ProductSyncInput(BaseModel):
    account: ACCOUNT
    expectedVersion: int | None = Field(default=None, ge=1)


class ProductEditInput(BaseModel):
    account: ACCOUNT
    expectedVersion: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    category: str = Field(max_length=200)
    attributes: dict[str, str] = Field(max_length=1000)


def normalize_ean(raw: str) -> str:
    return raw.strip().replace("\ufeff", "")


def valid_ean(raw: str) -> bool:
    """Accept the user-approved 13-digit barcode format without checksum validation."""
    return valid_ean13(raw)


def inspect_eans(raw_eans: list[str]) -> dict:
    normalized = [normalize_ean(value) for value in raw_eans]
    seen: set[str] = set()
    duplicates: list[str] = []
    invalid: list[str] = []
    unique: list[str] = []
    for value in normalized:
        if not valid_ean(value):
            invalid.append(value)
            continue
        if value in seen:
            duplicates.append(value)
            continue
        seen.add(value)
        unique.append(value)
    return {
        "inputCount": len(raw_eans),
        "uniqueCount": len(unique),
        "duplicateCount": len(duplicates),
        "duplicates": duplicates,
        "invalidEans": invalid,
        "uniqueEans": unique,
    }


def parse_delimited_text(content: str) -> list[str]:
    nonempty_lines = [line for line in content.splitlines() if line.strip()]
    if not nonempty_lines:
        return []
    sample = "\n".join(nonempty_lines[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(StringIO("\n".join(nonempty_lines)), dialect))
    if not rows:
        return []
    header = [cell.strip().casefold() for cell in rows[0]]
    ean_headers = {"ean", "ean13", "gtin", "barcode", "штрихкод"}
    column = next((index for index, value in enumerate(header) if value in ean_headers), 0)
    start = 1 if any(value in ean_headers for value in header) else 0
    return [row[column].strip() for row in rows[start:] if len(row) > column and row[column].strip()]


def parse_xlsx(content: bytes) -> list[str]:
    try:
        from openpyxl import load_workbook
    except ImportError as error:
        raise ApiProblem(503, "INTERNAL_ERROR", "XLSX support is not installed in this OttoRestart backend.") from error
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        rows = sheet.iter_rows()
        header_row = next(rows, None)
    except Exception as error:
        raise ApiProblem(422, "VALIDATION_ERROR", "Could not read this XLSX file.") from error
    if header_row is None:
        workbook.close()
        return []
    header = [str(cell.value or "").strip().casefold() for cell in header_row]
    ean_headers = {"ean", "ean13", "gtin", "barcode", "штрихкод"}
    column = next((index for index, value in enumerate(header) if value in ean_headers), 0)
    has_header = any(value in ean_headers for value in header)
    output: list[str] = []
    data_rows = rows if has_header else chain([header_row], rows)
    for row_number, row in enumerate(data_rows, start=1):
        if row_number > MAX_EANS_PER_REQUEST:
            workbook.close()
            raise ApiProblem(422, "VALIDATION_ERROR", f"A file may contain at most {MAX_EANS_PER_REQUEST} EAN rows.")
        if len(row) <= column:
            continue
        cell = row[column]
        value = cell.value
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        text_val = str(value).strip()
        if isinstance(value, int) and re.fullmatch(r"0+", cell.number_format or ""):
            text_val = text_val.zfill(len(cell.number_format))
        output.append(text_val)
    workbook.close()
    return output


async def read_ean_upload(file: UploadFile) -> list[str]:
    filename = (file.filename or "").lower()
    content = await file.read(20_000_001)
    if len(content) > 20_000_000:
        raise ApiProblem(413, "VALIDATION_ERROR", "The import file exceeds the 20 MB limit.")
    if filename.endswith(".xlsx"):
        values = parse_xlsx(content)
    elif filename.endswith(".csv") or filename.endswith(".txt"):
        values = parse_delimited_text(content.decode("utf-8-sig", errors="replace"))
    else:
        raise ApiProblem(422, "VALIDATION_ERROR", "Use an .xlsx, .csv, or .txt file.")
    if len(values) > MAX_EANS_PER_REQUEST:
        raise ApiProblem(422, "VALIDATION_ERROR", f"A file may contain at most {MAX_EANS_PER_REQUEST} EAN rows.")
    return values


def page_info(page: int, page_size: int, total_items: int) -> dict:
    return {
        "page": page,
        "pageSize": page_size,
        "totalItems": total_items,
        "totalPages": (total_items + page_size - 1) // page_size if total_items > 0 else 0,
    }


def batch_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "account": row["account"],
        "period": row["period"],
        "name": row["name"],
        "status": row["status"],
        "itemCount": row["item_count"],
        "createdAt": format_iso(row["created_at"]),
    }


def add_event(
    conn: Connection,
    account: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    metadata: dict,
    data_scope: str = "LEGACY_UNVERIFIED",
):
    conn.execute(
        text(
            """
            INSERT INTO restart_events(
                id, account, event_type, entity_type, entity_id, summary, metadata_json, created_at, data_scope
            ) VALUES (
                :id, :account, :event_type, :entity_type, :entity_id, :summary, :metadata_json, CURRENT_TIMESTAMP, :data_scope
            )
            """
        ),
        {
            "id": str(uuid4()),
            "account": account,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "summary": summary,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "data_scope": data_scope,
        },
    )


def csrf_cookie(request: Request) -> str | None:
    return request.cookies.get("ottorestart_csrf")


def require_local_write(request: Request, csrf_header: str | None) -> None:
    origin = request.headers.get("origin")
    if origin not in allowed_origins:
        raise ApiProblem(403, "FORBIDDEN", "Write requests are limited to configured local origins.")
    principal: Principal | None = getattr(request.state, "principal", None)
    if not principal or principal.kind != "operator" or not principal.csrf_verified or not csrf_header:
        raise ApiProblem(403, "FORBIDDEN", "Missing or invalid OttoRestart CSRF token.")


def require_legacy_mutations_enabled() -> None:
    if not RUNTIME_CONFIG.legacy_mutations_allowed:
        raise ApiProblem(
            409,
            "READ_ONLY_MODE",
            "This legacy write operation is disabled in the current OttoRestart runtime mode.",
            {"mode": RUNTIME_CONFIG.mode.value},
        )


def _run_drip_action(
    *, request: Request, account: str, csrf_token: str | None, idempotency_key: str | None,
    action: str, request_body: dict[str, Any], execute,
) -> dict[str, Any]:
    principal = _require_operation_write(request, account, csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise ApiProblem(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain 8 to 128 characters.")
    try:
        with connect() as conn:
            with conn.begin():
                return idempotent_operation_action(
                    conn, account=account, actor=principal.subject, action=f"drip-{action}",
                    idempotency_key=idempotency_key, request_body=request_body,
                    execute=lambda: execute(conn, principal.subject),
                )
    except DripError as error:
        raise ApiProblem(error.status, error.code, str(error), error.details) from None
    except OperationError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None
    except AvailabilityError as error:
        raise ApiProblem(error.status_code, error.code, str(error)) from None
    except IntegrityError:
        raise ApiProblem(409, "DRIP_CONFLICT", "The Drip Feed action conflicts with current schedule or operation state.") from None


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@app.exception_handler(ApiProblem)
async def handle_api_problem(request: Request, error: ApiProblem):
    return _error_response(
        error.status,
        error.code,
        error.message,
        getattr(request.state, "request_id", str(uuid4())),
        retryable=error.status in {429, 502, 503, 504},
        details=error.details,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: RequestValidationError):
    errors = [
        {key: value for key, value in entry.items() if key in {"loc", "msg", "type"}}
        for entry in error.errors()
    ]
    return _error_response(
        422,
        "VALIDATION_ERROR",
        "The request did not match the OttoRestart API contract.",
        getattr(request.state, "request_id", str(uuid4())),
        details={"errors": jsonable_encoder(errors)},
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, error: StarletteHTTPException):
    code = "HTTP_ERROR"
    message = "The request could not be completed."
    if isinstance(error.detail, str):
        message = error.detail
    elif isinstance(error.detail, dict):
        code = str(error.detail.get("code", code))
        message = str(error.detail.get("message", message))
    return _error_response(
        error.status_code,
        code,
        message,
        getattr(request.state, "request_id", str(uuid4())),
        retryable=error.status_code in {429, 502, 503, 504},
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, _: Exception):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return _error_response(
        500,
        "INTERNAL_ERROR",
        "The OttoRestart request could not be completed.",
        request_id,
        retryable=False,
    )


@app.get("/api/restart/csrf")
def get_csrf(response: Response):
    token = secrets.token_urlsafe(32)
    response.set_cookie("ottorestart_csrf", token, httponly=False, samesite="strict", secure=False, path="/")
    return {"token": token}


@app.get("/api/restart/ai/status")
def get_restart_ai_status(account: ACCOUNT):
    """Expose only whether AI generation is configured; never expose credentials."""
    return {
        "account": account,
        **openai_configuration_status(os.environ, enabled=RUNTIME_CONFIG.openai_enabled),
    }


@app.post("/api/restart/ai/title-suggestion")
def suggest_restart_title(
    payload: RestartTitleSuggestionInput,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    """Return a title-only proposal after an explicit, authenticated operator action."""
    _require_operation_write(request, payload.account, x_csrf_token)
    if not RUNTIME_CONFIG.openai_enabled:
        raise ApiProblem(409, "OPENAI_DISABLED", "AI suggestions are disabled in this OttoRestart runtime.")
    try:
        settings = OpenAISettings.from_environment(os.environ)
        provider = OpenAIResponsesClient(settings, enabled=True)
        suggestion = AISuggestionService(provider, enabled=True).suggest(
            SuggestionContext(
                source_title=payload.sourceTitle,
                verified_facts=(),
                taxonomy=(),
                attribute_candidates=(),
            )
        )
    except OpenAIClientError as error:
        status_code = 409 if error.code.endswith("NOT_CONFIGURED") else 503
        raise ApiProblem(status_code, error.code, "AI title suggestion is unavailable.") from None
    except AISuggestionError as error:
        status_code = 422 if error.code.startswith("AI_SOURCE_TITLE") else 503
        raise ApiProblem(status_code, error.code, "AI title suggestion could not be generated.") from None
    return {
        "account": payload.account,
        "sourceTitle": payload.sourceTitle,
        "title": suggestion.title,
        "manualReviewRequired": suggestion.manual_review_required,
        "reviewReasons": list(suggestion.review_reasons),
        "inputHash": suggestion.input_hash,
        "model": suggestion.model,
        "promptVersion": suggestion.prompt_version,
        "schemaVersion": suggestion.schema_version,
    }


@app.post("/api/restart/ai/category-suggestion")
def suggest_restart_category(
    payload: RestartCategorySuggestionInput,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    """Offer a category proposal from the local Otto category catalogue."""
    _require_operation_write(request, payload.account, x_csrf_token)
    if not RUNTIME_CONFIG.openai_enabled:
        raise ApiProblem(409, "OPENAI_DISABLED", "AI suggestions are disabled in this OttoRestart runtime.")
    try:
        from services.categories_repo import CATEGORIES
        with connect() as conn:
            product = get_product(conn, account=payload.account, candidate_episode_id=payload.candidateEpisodeId)
    except ProductCatalogError as error:
        raise ApiProblem(404, error.code, str(error)) from None
    except Exception:
        raise ApiProblem(503, "AI_SOURCE_UNAVAILABLE", "Product source data could not be loaded.") from None
    title_tokens = set(re.findall(r"[\w]+", str(product.title or "").casefold(), flags=re.UNICODE))
    candidates: list[dict[str, str]] = []
    for category in CATEGORIES:
        category_id, name = str(category.get("categoryId") or ""), str(category.get("name") or "")
        if not category_id or not name:
            continue
        tokens = set(re.findall(r"[\w]+", name.casefold(), flags=re.UNICODE))
        score = len(title_tokens & tokens)
        candidates.append({"id": category_id, "name": name, "_score": str(score)})
    candidates.sort(key=lambda item: (-int(item["_score"]), item["name"].casefold()))
    candidate_set = candidates[:30]
    if not candidate_set:
        raise ApiProblem(503, "CATEGORY_CATALOG_EMPTY", "The local OTTO category catalogue is unavailable.")
    source_attributes = [
        {"name": str(item.get("name") or "")[:120], "value": str(item.get("value") or "")[:300]}
        for item in (product.attributes or []) if isinstance(item, dict)
    ][:100]
    schema = {"type": "object", "properties": {
        "categoryId": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["categoryId", "reason"], "additionalProperties": False}
    try:
        provider = OpenAIResponsesClient(OpenAISettings.from_environment(os.environ), enabled=True)
        raw = provider.create_structured_response(
            instructions=CATEGORY_SUGGESTION_INSTRUCTIONS,
            input_data={"sourceTitle": str(product.title or "")[:500],
                        "sourceCategory": str(product.category or "")[:200],
                        "sourceAttributes": source_attributes,
                        "categories": [
                            {"id": item["id"], "name": item["name"]} for item in candidate_set
                        ]},
            schema_name="restart_category_suggestion", schema=schema)
        result = json.loads(raw)
        selected = next((item for item in candidate_set if item["id"] == result.get("categoryId")), None)
        if selected is None:
            raise OpenAIClientError("OPENAI_OUTPUT_INVALID")
    except (OpenAIClientError, json.JSONDecodeError, AttributeError):
        raise ApiProblem(503, "AI_CATEGORY_UNAVAILABLE", "A validated category proposal could not be generated.") from None
    return {"account": payload.account, "candidateEpisodeId": payload.candidateEpisodeId,
            "categoryId": selected["id"], "category": selected["name"],
            "reason": str(result.get("reason") or ""), "manualReviewRequired": True}


@app.post("/api/restart/ai/attribute-suggestions")
def suggest_restart_attributes(
    payload: RestartAttributeSuggestionInput,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    """Return image-assisted attribute proposals; no edits are persisted."""
    _require_operation_write(request, payload.account, x_csrf_token)
    if not RUNTIME_CONFIG.openai_enabled:
        raise ApiProblem(409, "OPENAI_DISABLED", "AI suggestions are disabled in this OttoRestart runtime.")
    try:
        from services.categories_repo import get_category_schema_attributes
        with connect() as conn:
            product = get_product(conn, account=payload.account, candidate_episode_id=payload.candidateEpisodeId)
    except ProductCatalogError as error:
        raise ApiProblem(404, error.code, str(error)) from None
    except Exception:
        raise ApiProblem(503, "AI_SOURCE_UNAVAILABLE", "Product source data could not be loaded.") from None
    image_url = product.image_url
    if not isinstance(image_url, str) or not image_url.startswith("https://"):
        raise ApiProblem(409, "AI_IMAGE_UNAVAILABLE", "A verified HTTPS source image is required for AI Vision.")
    definitions = get_category_schema_attributes(payload.category)
    if not definitions:
        raise ApiProblem(409, "CATEGORY_SCHEMA_UNAVAILABLE", "Load a valid OTTO category schema before suggesting attributes.")
    allowed_names = {str(item["name"]) for item in definitions}
    response_schema = {"type": "object", "properties": {"attributes": {"type": "array", "items": {
        "type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
        "required": ["name", "value"], "additionalProperties": False}}},
        "required": ["attributes"], "additionalProperties": False}
    safe_existing = [{"name": str(item.get("name") or "")[:120], "value": str(item.get("value") or "")[:300]}
                     for item in (product.attributes or []) if isinstance(item, dict)][:100]
    current_values: dict[str, str] = {}
    for item in payload.currentAttributes:
        name = str(item.get("name") or "").strip()[:120]
        value = str(item.get("value") or "").strip()[:500]
        if name and value:
            current_values[name] = value
    # The editor snapshot has priority over source values. Send both as context
    # so the model can avoid duplicates, then reject suggestions for every
    # already-filled field regardless of what it returns.
    existing_values = {str(item["name"]): str(item["value"])
                       for item in safe_existing if str(item.get("name") or "").strip()}
    existing_values.update(current_values)
    safe_current = [{"name": name, "value": value} for name, value in current_values.items()]
    try:
        provider = OpenAIResponsesClient(OpenAISettings.from_environment(os.environ), enabled=True)
        raw = provider.create_structured_response(
            instructions=ATTRIBUTE_VISION_INSTRUCTIONS,
            input_data={"__image_url": image_url, "sourceTitle": str(product.title or "")[:500],
                        "category": payload.category, "allowedAttributeNames": sorted(allowed_names),
                        "attributeSchema": [
                            {"name": str(item["name"]), "required": bool(item.get("required")),
                             "legal": bool(item.get("legal")),
                             "multiValue": bool(item.get("multiValue")),
                             "allowedValues": item.get("allowedValues")
                             if isinstance(item.get("allowedValues"), list) else []}
                            for item in definitions
                        ],
                        "existingSourceAttributes": safe_existing,
                        "currentEditorAttributes": safe_current},
            schema_name="restart_vision_attributes", schema=response_schema)
        result = json.loads(raw)
        proposed = result.get("attributes") if isinstance(result, dict) else None
        if not isinstance(proposed, list):
            raise OpenAIClientError("OPENAI_OUTPUT_INVALID")
    except (OpenAIClientError, json.JSONDecodeError, AttributeError):
        raise ApiProblem(503, "AI_ATTRIBUTES_UNAVAILABLE", "Validated image attribute suggestions could not be generated.") from None
    source_values = existing_values
    filled_names = {name.casefold() for name, value in existing_values.items() if value.strip()}
    accepted: list[dict[str, str]] = []
    seen_names: set[str] = set()
    definitions_by_name = {str(item["name"]): item for item in definitions}
    for item in proposed:
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if (not isinstance(name, str) or name not in allowed_names or name in seen_names
                or name.casefold() in filled_names or not isinstance(value, str)):
            continue
        value = value.strip()[:500]
        if not value:
            continue
        choices = definitions_by_name[name].get("allowedValues")
        if isinstance(choices, list) and choices and value not in choices:
            continue
        seen_names.add(name)
        accepted.append({"name": name, "value": value, "previousValue": source_values.get(name, "")})
    return {"account": payload.account, "candidateEpisodeId": payload.candidateEpisodeId,
            "attributes": accepted, "manualReviewRequired": True,
            "notice": "AI proposals are not saved and do not count as verified facts until an operator reviews and saves them."}


@app.get("/api/restart/overview")
def get_overview(account: ACCOUNT):
    with connect() as conn:
        batches = conn.execute(
            text(
                "SELECT COALESCE(SUM(item_count), 0) FROM restart_batches "
                "WHERE account = :account AND data_scope = 'PRODUCTION' AND status != 'CANCELLED'"
            ),
            {"account": account},
        ).scalar() or 0
        eans = conn.execute(
            text("SELECT COUNT(*) FROM ean_pool "
                 "WHERE account = :account AND data_scope = 'PRODUCTION' "
                 "AND status = 'AVAILABLE' AND eligibility_status = 'ELIGIBLE'"),
            {"account": account},
        ).scalar() or 0
    return {
        "account": account,
        "counts": {
            "candidates": batches,
            "awaitingReview": 0,
            "needsAttention": 0,
            "queued": 0,
            "onlineConfirmed": 0,
            "availableEans": eans,
        },
        "generatedAt": utc_now(),
    }


@app.get("/api/restart/products", response_model=ProductListResponseDTO)
def list_restart_products(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    period: PERIOD | None = None,
    status: str | None = Query(None, max_length=50),
    source: Literal["AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"] | None = None,
    factoryId: str | None = Query(None, max_length=200),
    query: str | None = Query(None, max_length=200),
    category: str | None = Query(None, max_length=200),
    titleOver70: bool = False,
    sortBy: Literal["name", "ean", "price", "category", "status"] | None = None,
    sortDir: Literal["asc", "desc"] = "asc",
    includeMetrics: bool = True,
):
    try:
        with connect() as conn:
            return list_products(
                conn,
                account=account,
                page=page,
                page_size=pageSize,
                period=period,
                status=status,
                source=source,
                factory_id=factoryId,
                query=query,
                category=category, title_over_70=titleOver70, sort_by=sortBy, sort_dir=sortDir,
                include_metrics=includeMetrics,
            )
    except ProductCatalogError as error:
        raise ApiProblem(422, error.code, str(error)) from None


@app.get("/api/restart/products/{candidate_episode_id}", response_model=ProductReadDTO)
def get_restart_product(candidate_episode_id: str, account: ACCOUNT):
    try:
        with connect() as conn:
            return get_product(conn, account=account, candidate_episode_id=candidate_episode_id)
    except ProductCatalogError as error:
        status_code = 404 if error.code == "PRODUCT_NOT_FOUND" else 422
        raise ApiProblem(status_code, error.code, str(error)) from None


@app.get("/api/restart/products/{candidate_episode_id}/edit")
def get_restart_product_edit(candidate_episode_id: str, account: ACCOUNT):
    try:
        with connect() as conn:
            return get_product_edit(conn, account=account, candidate_id=candidate_episode_id)
    except OperationError as error:
        raise ApiProblem(404 if error.code == "PRODUCT_NOT_FOUND" else 422, error.code, str(error), error.details) from None


@app.put("/api/restart/products/{candidate_episode_id}/edit")
def put_restart_product_edit(
    candidate_episode_id: str, payload: ProductEditInput, request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    if not payload.title.strip() or any(
        not key.strip() or len(key) > 200 or len(value) > 4000
        for key, value in payload.attributes.items()
    ) or len(json.dumps(payload.attributes, ensure_ascii=False).encode("utf-8")) > 1_000_000:
        raise ApiProblem(422, "INVALID_PRODUCT_EDIT", "Title or attributes exceed the supported limits.")
    return _run_operation_action(
        request=request, account=payload.account, csrf_token=x_csrf_token,
        idempotency_key=idempotency_key, action=f"product-edit:{candidate_episode_id}",
        request_body={**payload.model_dump(mode="json"), "candidateEpisodeId": candidate_episode_id},
        execute=lambda conn, actor: save_product_edit(
            conn, account=payload.account, candidate_id=candidate_episode_id,
            expected_version=payload.expectedVersion, title=payload.title, category=payload.category,
            attributes=payload.attributes, actor=actor,
        ),
    )


@app.post("/api/restart/products/{candidate_episode_id}/sync")
def sync_restart_product(
    candidate_episode_id: str,
    payload: ProductSyncInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    principal = _require_operation_write(request, payload.account, x_csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key.strip()) <= 128:
        raise ApiProblem(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain 8 to 128 characters.")
    try:
        candidate_uuid = str(UUID(candidate_episode_id))
    except (TypeError, ValueError, AttributeError):
        raise ApiProblem(422, "INVALID_PRODUCT_ID", "Product ID must be a candidate episode UUID.") from None

    body = {"account": payload.account, "candidateEpisodeId": candidate_uuid,
            "expectedVersion": payload.expectedVersion}
    action = f"sync-product:{candidate_uuid}"
    request_hash = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    stored_key = "restart-op:" + hashlib.sha256(
        f"{payload.account}\0{principal.subject}\0{action}\0{idempotency_key.strip()}".encode("utf-8")
    ).hexdigest()
    with connect() as conn:
        previous = conn.execute(
            text("SELECT request_hash,response_body FROM restart_idempotency_keys "
                 "WHERE key=:key AND data_scope='PRODUCTION'"),
            {"key": stored_key},
        ).mappings().first()
    if previous is not None:
        if previous["request_hash"] != request_hash:
            raise ApiProblem(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was used for a different request.")
        if not previous["response_body"] or previous["response_body"] == "{}":
            raise ApiProblem(409, "IDEMPOTENCY_IN_PROGRESS", "The synchronization request has no committed result yet.")
        return json.loads(previous["response_body"])

    if not RUNTIME_CONFIG.otto_read_enabled:
        raise ApiProblem(409, "OTTO_READ_DISABLED", "OTTO source reads are disabled in this runtime.")
    try:
        with connect() as conn:
            context = load_product_sync_context(
                conn, account=payload.account, candidate_episode_id=candidate_uuid
            )
    except ProductSyncError as error:
        status = 404 if error.code == "PRODUCT_NOT_FOUND" else 409
        raise ApiProblem(status, error.code, str(error), error.details) from None

    if context.get("operation_id"):
        if payload.expectedVersion is None or payload.expectedVersion != context.get("operation_version"):
            raise ApiProblem(
                409, "VERSION_CONFLICT", "The editable operation version is required and must be current.",
                {"expectedVersion": payload.expectedVersion, "currentVersion": context.get("operation_version")},
            )
    elif payload.expectedVersion is not None:
        raise ApiProblem(409, "VERSION_CONFLICT", "The candidate has no Restart operation for this version.")
    source_sku = str(context["observed_sku"])
    try:
        otto = OttoMarketClient.from_environment(payload.account, runtime_config=RUNTIME_CONFIG)
        otto_product = otto.get_product(source_sku)
        normalized_product = normalize_otto_product_variation(
            otto_product, expected_sku=source_sku, expected_ean=str(context["confirmed_ean"]),
        )
        if normalized_product is None:
            raise ProductSyncError("OTTO_SKU_MISMATCH", "OTTO product did not confirm the candidate SKU and EAN.")
        otto_product = normalized_product
        nested_price = resolve_otto_standard_price(
            otto_product, None, expected_sku=source_sku, expected_ean=str(context["confirmed_ean"]),
        )
        otto_prices = nested_price.get("response") if nested_price["evidence"].get("status") in {"CONFIRMED", "ZERO"} else None
        if otto_prices is None:
            try:
                otto_prices = otto.get_product_prices(source_sku)
            except OttoApiError as error:
                if not is_missing_otto_price_error(error):
                    raise
                otto_prices = {"sku": source_sku}
        aftercool = None
        if context["source_type"] != "OTTO_DIRECT":
            aftercool = AftercoolLookupService(runtime_config=RUNTIME_CONFIG).lookup_product(
                payload.account, str(context["confirmed_ean"]), factory_hint=str(context["factory_id"])
            )
        snapshot = build_product_sync_snapshot(
            context, account=payload.account, otto_product=otto_product, otto_prices=otto_prices,
            aftercool=aftercool, captured_at=datetime.now(timezone.utc),
        )
    except ApiProblem:
        raise
    except OttoApiError as error:
        raise ApiProblem(503, "OTTO_READ_FAILED", "Required account-scoped OTTO source reads did not complete.",
                         {"reasonCode": error.code}) from None
    except ProductSyncError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None

    try:
        return _run_operation_action(
            request=request, account=payload.account, csrf_token=x_csrf_token,
            idempotency_key=idempotency_key, action=action, request_body=body,
            execute=lambda conn, actor: apply_product_sync_snapshot(
                conn, preflight_context=context, snapshot=snapshot,
                expected_version=payload.expectedVersion, actor=actor,
            ),
        )
    except ProductSyncError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None


@app.get("/api/restart/categories")
def search_restart_categories(query: str = Query("", alias="q", max_length=200)):
    from services.categories_repo import CATEGORIES
    q_clean = query.strip().lower()
    if not q_clean:
        return [
            {
                "id": str(c.get("categoryId", "")),
                "name": c.get("name", ""),
                "group": c.get("category_group", ""),
                "group_id": str(c.get("category_group_id", "")),
            }
            for c in CATEGORIES[:60]
        ]
    matches = [
        {
            "id": str(c.get("categoryId", "")),
            "name": c.get("name", ""),
            "group": c.get("category_group", ""),
            "group_id": str(c.get("category_group_id", "")),
        }
        for c in CATEGORIES
        if q_clean in str(c.get("name", "")).lower() or q_clean in str(c.get("category_group", "")).lower()
    ][:60]
    return matches


@app.get("/api/restart/categories/{category_id}/attributes")
def get_restart_category_attributes(category_id: str):
    from services.categories_repo import get_frontend_category_schema
    return get_frontend_category_schema(category_id)


@app.get("/api/restart/categories/{category_id}/required-attributes")
def get_restart_required_category_attributes(category_id: str):
    from services.categories_repo import get_category, get_frontend_category_schema
    if get_category(category_id) is None:
        raise ApiProblem(404, "CATEGORY_SCHEMA_NOT_FOUND", "No verified category schema exists for this category.")
    schema = get_frontend_category_schema(category_id)
    if not schema:
        raise ApiProblem(404, "CATEGORY_SCHEMA_NOT_FOUND", "No verified category schema exists for this category.")
    return {"category": category_id, "requiredAttributes": [item["name"] for item in schema if item.get("required") is True]}


class AssignProductEanInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account: ACCOUNT
    product_ids: list[str] = Field(default_factory=list, alias="productIds")


class GenerateTitlesInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account: ACCOUNT
    product_ids: list[str] = Field(default_factory=list, alias="productIds")


class UpdateProductInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    account: ACCOUNT
    title: str | None = None
    price_cents: int | None = Field(default=None, alias="priceCents")
    category: str | None = None
    attributes: list[dict[str, Any]] | dict[str, Any] | None = None


def _reject_legacy_product_write() -> None:
    raise ApiProblem(
        409,
        "NOT_IMPLEMENTED",
        "This legacy product write path is disabled because it fabricates preparation data. Use the canonical Restart operation workflow.",
    )


def _prepare_and_record_product(*args: Any, **kwargs: Any) -> None:
    """Reject the retired production shortcut that fabricated product revisions."""
    _reject_legacy_product_write()


@app.post("/api/restart/products/assign-ean")
def assign_products_ean(
    payload: AssignProductEanInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    _reject_legacy_product_write()
    require_local_write(request, x_csrf_token)
    principal: Principal | None = getattr(request.state, "principal", None)
    actor = principal.subject if principal else "operator"
    account = payload.account
    assigned_count = 0
    updated_items = []

    with connect() as conn:
        with conn.begin():
            for pid in payload.product_ids:
                try:
                    product = _prepare_and_record_product(
                        conn, account=account, episode_uuid=str(UUID(pid)), actor=actor,
                    )
                    if product:
                        assigned_count += 1
                        updated_items.append(product)
                except Exception as exc:
                    import logging
                    logging.exception("Failed to assign EAN for product %s: %s", pid, exc)
                    continue

    return {"assignedCount": assigned_count, "items": [p.model_dump(by_alias=True) for p in updated_items]}


@app.post("/api/restart/products/generate-titles")
def generate_product_titles(
    payload: GenerateTitlesInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    _reject_legacy_product_write()
    require_local_write(request, x_csrf_token)
    principal: Principal | None = getattr(request.state, "principal", None)
    actor = principal.subject if principal else "operator"
    account = payload.account
    generated_count = 0
    updated_items = []

    with connect() as conn:
        with conn.begin():
            for pid in payload.product_ids:
                try:
                    product = _prepare_and_record_product(
                        conn, account=account, episode_uuid=str(UUID(pid)), actor=actor,
                    )
                    if product:
                        generated_count += 1
                        updated_items.append(product)
                except Exception as exc:
                    import logging
                    logging.exception("Failed to generate title for product %s: %s", pid, exc)
                    continue

    return {"generatedCount": generated_count, "items": [p.model_dump(by_alias=True) for p in updated_items]}


@app.patch("/api/restart/products/{candidate_episode_id}")
def update_restart_product(
    candidate_episode_id: str,
    payload: UpdateProductInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    _reject_legacy_product_write()
    require_local_write(request, x_csrf_token)
    principal: Principal | None = getattr(request.state, "principal", None)
    actor = principal.subject if principal else "operator"
    try:
        episode_uuid = str(UUID(candidate_episode_id))
    except (ValueError, TypeError):
        raise ApiProblem(422, "INVALID_PRODUCT_ID", "Product ID must be a UUID.")

    with connect() as conn:
        with conn.begin():
            product = _prepare_and_record_product(
                conn,
                account=payload.account,
                episode_uuid=episode_uuid,
                actor=actor,
                new_title_override=payload.title,
                price_cents_override=payload.price_cents,
                category_override=payload.category,
                attributes_override=payload.attributes,
            )
            if not product:
                raise ApiProblem(404, "PRODUCT_NOT_FOUND", "Candidate episode was not found in this account.")
            return product


@app.get("/api/restart/batches", response_model=DripBatchListResponse)
def list_batches(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    status: str | None = None,
):
    try:
        with connect() as conn:
            return list_drip_batches(conn, account=account, page=page, page_size=pageSize, status=status)
    except DripError as error:
        raise ApiProblem(error.status, error.code, str(error), error.details) from None


@app.post("/api/restart/batches/preview", response_model=DripSchedulePreview)
def preview_batch(
    payload: DripScheduleInput,
    request: Request,
):
    del request
    try:
        with connect() as conn:
            return preview_schedule(
                conn, account=payload.account, operation_ids=payload.operation_ids,
                start_date=payload.start_date, daily_time=payload.daily_time,
                timezone_name=payload.timezone_name, total_days=payload.total_days,
            )
    except DripError as error:
        raise ApiProblem(error.status, error.code, str(error), error.details) from None


@app.post("/api/restart/imports/preview")
async def preview_import_file(
    request: Request,
    account: ACCOUNT = Query(...),
    period: PERIOD | None = Query(None),
    file: UploadFile = File(...),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    del period
    content = await file.read(MAX_IMPORT_BYTES + 1)
    filename = PurePath(file.filename or "upload.txt").name[:255]
    return _handle_import_request(
        request,
        account=account,
        filename=filename,
        content=content,
        dry_run=True,
        run_mode=None,
        idempotency_key=None,
        csrf_token=x_csrf_token,
    )


@app.post("/api/restart/batches", status_code=201)
def create_batch(
    payload: DripScheduleInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    body = payload.model_dump(mode="json", by_alias=True)
    return _run_drip_action(
        request=request, account=payload.account, csrf_token=x_csrf_token,
        idempotency_key=idempotency_key, action="create-draft", request_body=body,
        execute=lambda conn, actor: create_batch_draft(
            conn, account=payload.account, name=payload.name, operation_ids=payload.operation_ids,
            start_date=payload.start_date, daily_time=payload.daily_time, timezone_name=payload.timezone_name,
            total_days=payload.total_days, actor=actor,
        ),
    )


@app.post("/api/restart/batches/{batch_id}/start", response_model=DripBatchDTO)
def start_batch(
    batch_id: str, payload: DripBatchActionInput, request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    body = {"account": payload.account, "batchId": batch_id, "expectedVersion": payload.expected_version}
    return _run_drip_action(
        request=request, account=payload.account, csrf_token=x_csrf_token,
        idempotency_key=idempotency_key, action="start", request_body=body,
        execute=lambda conn, actor: start_drip_batch(
            conn, account=payload.account, batch_id=batch_id,
            expected_version=payload.expected_version, actor=actor,
        ),
    )


def _batch_control_route(
    batch_id: str, payload: DripBatchActionInput, request: Request, action: str,
    idempotency_key: str | None, x_csrf_token: str | None,
):
    body = {"account": payload.account, "batchId": batch_id, "expectedVersion": payload.expected_version}
    return _run_drip_action(
        request=request, account=payload.account, csrf_token=x_csrf_token,
        idempotency_key=idempotency_key, action=action, request_body=body,
        execute=lambda conn, actor: control_batch(
            conn, account=payload.account, batch_id=batch_id, action=action,
            expected_version=payload.expected_version, actor=actor,
        ),
    )


@app.post("/api/restart/batches/{batch_id}/pause", response_model=DripBatchDTO)
def pause_batch(batch_id: str, payload: DripBatchActionInput, request: Request,
                idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                x_csrf_token: str | None = Header(None, alias="X-CSRF-Token")):
    return _batch_control_route(batch_id, payload, request, "pause", idempotency_key, x_csrf_token)


@app.post("/api/restart/batches/{batch_id}/resume", response_model=DripBatchDTO)
def resume_batch(batch_id: str, payload: DripBatchActionInput, request: Request,
                 idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                 x_csrf_token: str | None = Header(None, alias="X-CSRF-Token")):
    return _batch_control_route(batch_id, payload, request, "resume", idempotency_key, x_csrf_token)


@app.post("/api/restart/batches/{batch_id}/cancel", response_model=DripBatchDTO)
def cancel_batch(batch_id: str, payload: DripBatchActionInput, request: Request,
                 idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                 x_csrf_token: str | None = Header(None, alias="X-CSRF-Token")):
    return _batch_control_route(batch_id, payload, request, "cancel", idempotency_key, x_csrf_token)


@app.post("/api/restart/batches/{batch_id}/retry", response_model=DripBatchDTO)
def retry_batch(batch_id: str, payload: DripBatchActionInput, request: Request,
                idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                x_csrf_token: str | None = Header(None, alias="X-CSRF-Token")):
    return _batch_control_route(batch_id, payload, request, "retry", idempotency_key, x_csrf_token)


@app.get("/api/restart/batches/{batch_id}", response_model=DripBatchDTO)
def get_batch(batch_id: str, account: ACCOUNT):
    try:
        with connect() as conn:
            return get_drip_batch(conn, account=account, batch_id=batch_id)
    except DripError as error:
        raise ApiProblem(error.status, error.code, str(error), error.details) from None


@app.get("/api/restart/batches/{batch_id}/items", response_model=DripBatchItemsResponse)
def list_batch_items(
    batch_id: str,
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    query: str | None = Query(None, max_length=100),
):
    try:
        with connect() as conn:
            return list_drip_batch_items(conn, account=account, batch_id=batch_id, page=page, page_size=pageSize, query=query)
    except DripError as error:
        raise ApiProblem(error.status, error.code, str(error), error.details) from None


@app.get("/api/restart/ean-pool")
def list_ean_pool(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    status: str | None = None,
    q: str | None = Query(None, max_length=100),
):
    where = ["account = :account", "data_scope = 'PRODUCTION'"]
    params: dict[str, object] = {"account": account}
    if status:
        where.append("status = :status")
        params["status"] = status
    if q and q.strip():
        where.append("ean ILIKE :query")
        params["query"] = f"%{q.strip()}%"
    where_sql = " AND ".join(where)

    with connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM ean_pool WHERE {where_sql}"),
            params,
        ).scalar() or 0

        params["limit"] = pageSize
        params["offset"] = (page - 1) * pageSize
        rows = conn.execute(
            text(
                f"""
                SELECT ean, account, status, batch_id, imported_at, created_at,
                       provenance_source, owner_reference, eligibility_status,
                       imported_by, verified_by, verified_at, reservation_operation_id,
                       quarantined_at, quarantine_reason, version, updated_at
                FROM ean_pool
                WHERE {where_sql}
                ORDER BY COALESCE(imported_at, created_at) DESC, ean
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()

    return {
        "items": [
            {
                "ean": row["ean"],
                "account": row["account"],
                "status": row["status"],
                "batchId": row["batch_id"],
                "importedAt": format_iso(row["imported_at"]) if row["imported_at"] else None,
                "provenanceSource": row["provenance_source"],
                "ownerReference": row["owner_reference"],
                "eligibilityStatus": row["eligibility_status"],
                "importedBy": row["imported_by"],
                "verifiedBy": row["verified_by"],
                "verifiedAt": format_iso(row["verified_at"]) if row["verified_at"] else None,
                "reservationOperationId": str(row["reservation_operation_id"]) if row["reservation_operation_id"] else None,
                "quarantinedAt": format_iso(row["quarantined_at"]) if row["quarantined_at"] else None,
                "quarantineReason": row["quarantine_reason"],
                "version": row["version"],
                "updatedAt": format_iso(row["updated_at"]),
            }
            for row in rows
        ],
        "page": page_info(page, pageSize, total),
    }


@app.post("/api/restart/ean-pool/preview")
async def preview_ean_pool_file(
    request: Request, account: ACCOUNT = Query(...), file: UploadFile = File(...),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    try:
        rows = parse_import_file(PurePath(file.filename or "upload.txt").name[:255], content)
    except ImportValidationError as error:
        raise ApiProblem(error.status_code, error.code, error.message) from None
    values = [str(row.get("ean") or row.get("raw_identifier") or "") for row in rows]
    return {"account": account, **inspect_eans(values), "dryRun": True}


@app.post("/api/restart/ean-pool/import", status_code=201)
def import_eans(
    payload: EanImportInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    inspection = inspect_eans(payload.eans)
    if inspection["invalidEans"] or inspection["duplicates"]:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "EAN import contains invalid or duplicate values.",
            {"invalidEans": inspection["invalidEans"], "duplicates": inspection["duplicates"]},
        )
    eans = inspection["uniqueEans"]
    principal: Principal | None = getattr(request.state, "principal", None)
    if not principal or principal.kind != "operator" or "restart:write" not in principal.permissions:
        raise ApiProblem(403, "FORBIDDEN", "An authorized operator is required to import EAN pool entries.")

    if RUNTIME_CONFIG.mode.value == "PRODUCTION":
        if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
            raise ApiProblem(422, "VALIDATION_ERROR", "Idempotency-Key is required and must be 8–128 characters.")
        data_scope = "PRODUCTION"
        stored_key = "ean-pool-import:" + hashlib.sha256(
            f"{payload.account}\0{principal.subject}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        request_hash = hashlib.sha256(
            json.dumps({"account": payload.account, "eans": sorted(eans)}, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    elif RUNTIME_CONFIG.legacy_mutations_allowed:
        data_scope = "LEGACY_UNVERIFIED"
        stored_key = None
        request_hash = None
    else:
        require_legacy_mutations_enabled()
        raise ApiProblem(409, "READ_ONLY_MODE", "EAN pool imports are disabled in this runtime mode.")

    try:
        with connect() as conn:
            with conn.begin():
                if stored_key:
                    inserted = conn.execute(
                        text(
                            """
                            INSERT INTO restart_idempotency_keys (key, request_hash, response_body, created_at, data_scope)
                            VALUES (:key, :request_hash, '{}', CURRENT_TIMESTAMP, 'PRODUCTION')
                            ON CONFLICT (key) DO NOTHING RETURNING key
                            """
                        ),
                        {"key": stored_key, "request_hash": request_hash},
                    ).scalar_one_or_none()
                    if inserted is None:
                        previous = conn.execute(
                            text("SELECT request_hash, response_body FROM restart_idempotency_keys WHERE key = :key FOR UPDATE"),
                            {"key": stored_key},
                        ).mappings().first()
                        if not previous or previous["request_hash"] != request_hash:
                            raise ApiProblem(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was used for a different request.")
                        return json.loads(previous["response_body"])

                result = import_pool_entries(
                    conn,
                    account=payload.account,
                    eans=eans,
                    actor=principal.subject,
                    data_scope=data_scope,
                )
                result["importedAt"] = utc_now()
                add_event(
                    conn,
                    payload.account,
                    "EAN_POOL_IMPORTED",
                    "EAN_POOL",
                    result["importedAt"],
                    f"Добавлено EAN в пул: {len(eans)}",
                    {"count": len(eans), "status": result["status"]},
                    data_scope=data_scope,
                )
                if stored_key:
                    conn.execute(
                        text("UPDATE restart_idempotency_keys SET response_body = :body WHERE key = :key"),
                        {"key": stored_key, "body": json.dumps(result, separators=(",", ":"))},
                    )
                return result
    except EanPoolError as error:
        status_code = 409 if error.code == "EAN_ALREADY_EXISTS" else 422
        raise ApiProblem(status_code, error.code, str(error), error.details) from None


def _require_gtin_evidence_write(request: Request, account: str, csrf_token: str | None) -> Principal:
    require_local_write(request, csrf_token)
    if RUNTIME_CONFIG.mode.value != "PRODUCTION":
        raise ApiProblem(
            409,
            "GTIN_EVIDENCE_NOT_IMPLEMENTED",
            "GTIN evidence changes are available only in the explicitly configured production runtime.",
        )
    principal: Principal | None = getattr(request.state, "principal", None)
    if (
        not principal
        or principal.kind != "operator"
        or "restart:write" not in principal.permissions
        or account not in principal.accounts
    ):
        raise ApiProblem(403, "FORBIDDEN", "An authorized account-scoped operator is required.")
    return principal


def _parse_expiry(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ApiProblem(422, "INVALID_EXPIRY", "Expiry must be an ISO-8601 timestamp with timezone.") from None
    if parsed.tzinfo is None:
        raise ApiProblem(422, "INVALID_EXPIRY", "Expiry must include a timezone.")
    return parsed


def _require_operation_write(request: Request, account: str, csrf_token: str | None) -> Principal:
    require_local_write(request, csrf_token)
    if RUNTIME_CONFIG.mode.value != "PRODUCTION":
        raise ApiProblem(
            409,
            "OPERATION_NOT_IMPLEMENTED",
            "Production Restart operations are unavailable outside the explicitly configured PRODUCTION runtime.",
        )
    principal: Principal | None = getattr(request.state, "principal", None)
    if (
        not principal
        or principal.kind != "operator"
        or "restart:write" not in principal.permissions
        or account not in principal.accounts
    ):
        raise ApiProblem(403, "FORBIDDEN", "An authorized account-scoped operator is required.")
    return principal


def _run_operation_action(
    *, request: Request, account: str, csrf_token: str | None, idempotency_key: str | None,
    action: str, request_body: dict[str, Any], execute,
) -> dict[str, Any]:
    principal = _require_operation_write(request, account, csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise ApiProblem(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain 8 to 128 characters.")
    try:
        with connect() as conn:
            with conn.begin():
                return idempotent_operation_action(
                    conn,
                    account=account,
                    actor=principal.subject,
                    action=action,
                    idempotency_key=idempotency_key,
                    request_body=request_body,
                    execute=lambda: execute(conn, principal.subject),
                )
    except OperationError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None
    except AvailabilityError as error:
        raise ApiProblem(error.status_code, error.code, str(error)) from None
    except IntegrityError:
        raise ApiProblem(409, "OPERATION_CONFLICT", "The operation conflicts with current account-scoped Restart history.") from None


@app.post("/api/restart/operations/history-reviews", status_code=201)
def record_restart_history_review(
    payload: RestartHistoryReviewInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    expiry = _parse_expiry(payload.expiresAt)
    if expiry is None:
        raise ApiProblem(422, "INVALID_EXPIRY", "Authorization expiry is required.")
    body = payload.model_dump(mode="json")
    return _run_operation_action(
        request=request,
        account=payload.account,
        csrf_token=x_csrf_token,
        idempotency_key=idempotency_key,
        action="history-review",
        request_body=body,
        execute=lambda conn, actor: create_history_review(
            conn,
            account=payload.account,
            source_product_id=payload.sourceProductId,
            candidate_episode_id=payload.candidateEpisodeId,
            decision=payload.decision,
            confirmed_prior_attempt_count=payload.confirmedPriorAttemptCount,
            reviewed_through_operation_id=payload.reviewedThroughOperationId,
            reason=payload.reason,
            actor=actor,
            expires_at=expiry,
        ),
    )


@app.post("/api/restart/operations/retry-authorizations", status_code=201)
def authorize_restart_retry(
    payload: RestartRetryAuthorizationInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    expiry = _parse_expiry(payload.expiresAt)
    if expiry is None:
        raise ApiProblem(422, "INVALID_EXPIRY", "Authorization expiry is required.")
    return _run_operation_action(
        request=request,
        account=payload.account,
        csrf_token=x_csrf_token,
        idempotency_key=idempotency_key,
        action="retry-authorization",
        request_body=payload.model_dump(mode="json"),
        execute=lambda conn, actor: create_retry_authorization(
            conn,
            account=payload.account,
            source_product_id=payload.sourceProductId,
            candidate_episode_id=payload.candidateEpisodeId,
            history_review_id=payload.historyReviewId,
            reason=payload.reason,
            actor=actor,
            expires_at=expiry,
        ),
    )


@app.post("/api/restart/operations", status_code=201)
def create_restart_operation(
    payload: RestartOperationCreateInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    return _run_operation_action(
        request=request,
        account=payload.account,
        csrf_token=x_csrf_token,
        idempotency_key=idempotency_key,
        action="create-operation",
        request_body=payload.model_dump(mode="json"),
        execute=lambda conn, actor: create_operation(
            conn,
            account=payload.account,
            candidate_episode_id=payload.candidateEpisodeId,
            actor=actor,
            history_review_id=payload.historyReviewId,
            retry_authorization_id=payload.retryAuthorizationId,
        ),
    )


@app.post("/api/restart/operations/{operation_id}/prepare", status_code=201)
def prepare_restart_operation(
    operation_id: str,
    payload: RestartOperationPrepareInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    principal = _require_operation_write(request, payload.account, x_csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key.strip()) <= 128:
        raise ApiProblem(422, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain 8 to 128 characters.")
    if not TARGET_PAYLOAD_BUILDER_READY:
        raise ApiProblem(
            409,
            "OTTO_TARGET_PAYLOAD_NOT_BUILT",
            "Preparation is unavailable until the complete OTTO target payload contract is implemented and validated. No source API was called and no EAN was reserved.",
            {"sourceReadsPerformed": False, "eanReserved": False},
        )
    body = {**payload.model_dump(mode="json"), "operationId": operation_id}
    action = f"prepare-operation:{operation_id}"
    request_hash = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    stored_key = "restart-op:" + hashlib.sha256(
        f"{payload.account}\0{principal.subject}\0{action}\0{idempotency_key.strip()}".encode("utf-8")
    ).hexdigest()
    with connect() as conn:
        previous = conn.execute(
            text("SELECT request_hash, response_body FROM restart_idempotency_keys "
                 "WHERE key=:key AND data_scope='PRODUCTION'"),
            {"key": stored_key},
        ).mappings().first()
    if previous is not None:
        if previous["request_hash"] != request_hash:
            raise ApiProblem(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was used for a different request.")
        if not previous["response_body"] or previous["response_body"] == "{}":
            raise ApiProblem(409, "IDEMPOTENCY_IN_PROGRESS", "The idempotent preparation has no committed result yet.")
        return json.loads(previous["response_body"])
    if not RUNTIME_CONFIG.otto_read_enabled:
        raise ApiProblem(409, "OTTO_READ_DISABLED", "OTTO source reads are disabled in this runtime.")
    try:
        with connect() as conn:
            context = load_preparation_context(
                conn,
                account=payload.account,
                operation_id=operation_id,
                expected_version=payload.expectedVersion,
            )
    except PreparationError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None

    if context["source_type"] != "OTTO_DIRECT" and not RUNTIME_CONFIG.aftercool_read_enabled:
        raise ApiProblem(409, "AFTERCOOL_READ_DISABLED", "Aftercool source verification is disabled in this runtime.")

    try:
        otto = OttoMarketClient.from_environment(payload.account, runtime_config=RUNTIME_CONFIG)
        source_sku = str(context["observed_sku"])
        otto_product = otto.get_product(source_sku)
        normalized_product = normalize_otto_product_variation(
            otto_product, expected_sku=source_sku, expected_ean=str(context["confirmed_ean"]),
        )
        if normalized_product is None:
            raise PreparationError("OTTO_SKU_MISMATCH", "OTTO product did not confirm the candidate SKU and EAN.")
        otto_product = normalized_product
        nested_price = resolve_otto_standard_price(
            otto_product, None, expected_sku=source_sku, expected_ean=str(context["confirmed_ean"]),
        )
        otto_prices = nested_price.get("response") if nested_price["evidence"].get("status") in {"CONFIRMED", "ZERO"} else None
        if otto_prices is None:
            try:
                otto_prices = otto.get_product_prices(source_sku)
            except OttoApiError as error:
                if not is_missing_otto_price_error(error):
                    raise
                otto_prices = {"sku": source_sku}
        fulfillment = otto.get_product_delivery_information(source_sku)
        aftercool = None
        if context["source_type"] != "OTTO_DIRECT":
            aftercool = AftercoolLookupService(runtime_config=RUNTIME_CONFIG).lookup_product(
                payload.account,
                str(context["confirmed_ean"]),
                factory_hint=str(context["factory_id"]),
            )
            if aftercool.get("status") != "FOUND":
                status_code = 409 if aftercool.get("status") == "AMBIGUOUS" else 503
                reason_code = str(aftercool.get("reason_code") or aftercool.get("status") or "LOOKUP_ERROR")
                raise ApiProblem(status_code, reason_code, "Aftercool source identity was not confirmed.")
        # Validate the same combined source-price policy used by sync, QC, and submission.
        price_snapshot_from_sources(
            payload.account,
            otto_prices,
            source_type=str(context["source_type"]),
            aftercool_price_cents=aftercool.get("price_cents") if aftercool is not None else None,
        )
    except OttoApiError as error:
        raise ApiProblem(503, "OTTO_READ_FAILED", "Required account-scoped OTTO source reads did not complete.", {"reasonCode": error.code}) from None
    except PreparationError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None

    try:
        return _run_operation_action(
            request=request,
            account=payload.account,
            csrf_token=x_csrf_token,
            idempotency_key=idempotency_key,
            action=action,
            request_body=body,
            execute=lambda conn, actor: prepare_operation_revision(
                conn,
                account=payload.account,
                operation_id=operation_id,
                expected_version=payload.expectedVersion,
                preflight_context=context,
                otto_product=otto_product,
                otto_prices=otto_prices,
                fulfillment=fulfillment,
                aftercool=aftercool,
                captured_at=datetime.now(timezone.utc),
                actor=actor,
                approved_evidence_types=RUNTIME_CONFIG.gtin_approved_evidence_types,
            ),
        )
    except PreparationError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None


@app.post("/api/restart/operations/{operation_id}/reserve-ean")
def reserve_restart_operation_ean(
    operation_id: str, payload: RestartOperationPrepareInput, request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    try:
        return _run_operation_action(
            request=request, account=payload.account, csrf_token=x_csrf_token,
            idempotency_key=idempotency_key, action=f"reserve-ean:{operation_id}",
            request_body={**payload.model_dump(mode="json"), "operationId": operation_id},
            execute=lambda conn, actor: reserve_operation_ean(
                conn, account=payload.account, operation_id=operation_id,
                expected_version=payload.expectedVersion, actor=actor,
            ),
        )
    except EanPoolError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None


@app.post("/api/restart/operations/{operation_id}/cancel")
def cancel_restart_operation(
    operation_id: str,
    payload: RestartOperationCancelInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    result = _run_operation_action(
        request=request,
        account=payload.account,
        csrf_token=x_csrf_token,
        idempotency_key=idempotency_key,
        action=f"cancel-operation:{operation_id}",
        request_body={**payload.model_dump(mode="json"), "operationId": operation_id},
        execute=lambda conn, actor: cancel_operation(
            conn,
            account=payload.account,
            operation_id=operation_id,
            expected_version=payload.expectedVersion,
            actor=actor,
            approved_evidence_types=RUNTIME_CONFIG.gtin_approved_evidence_types,
        ),
    )
    return JSONResponse(status_code=202 if not result.get("cancelled", True) else 200, content=result)


@app.post("/api/restart/operations/{operation_id}/manual-revision", status_code=201)
def create_restart_manual_revision(
    operation_id: str,
    payload: RestartManualRevisionInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    try:
        return _run_operation_action(
            request=request,
            account=payload.account,
            csrf_token=x_csrf_token,
            idempotency_key=idempotency_key,
            action=f"manual-revision:{operation_id}",
            request_body={**payload.model_dump(mode="json"), "operationId": operation_id},
            execute=lambda conn, actor: append_manual_revision(
                conn,
                account=payload.account,
                operation_id=operation_id,
                expected_version=payload.expectedVersion,
                title=payload.title,
                category=payload.category,
                attributes=payload.attributes,
                images=payload.images,
                confirmations=payload.confirmations,
                reason=payload.reason,
                actor=actor,
            ),
        )
    except QCError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None


@app.post("/api/restart/operations/{operation_id}/qc")
def run_restart_operation_qc(
    operation_id: str,
    payload: RestartOperationQCInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    try:
        return _run_operation_action(
            request=request,
            account=payload.account,
            csrf_token=x_csrf_token,
            idempotency_key=idempotency_key,
            action=f"qc:{operation_id}",
            request_body={**payload.model_dump(mode="json"), "operationId": operation_id},
            execute=lambda conn, actor: record_operation_qc(
                conn,
                account=payload.account,
                operation_id=operation_id,
                expected_version=payload.expectedVersion,
                actor=actor,
                approved_evidence_types=RUNTIME_CONFIG.gtin_approved_evidence_types,
            ),
        )
    except QCError as error:
        raise ApiProblem(409, error.code, str(error), error.details) from None


@app.post("/api/restart/operations/{operation_id}/availability/{action}/retry", status_code=202)
def retry_restart_availability_step(
    operation_id: str,
    action: Literal["QUANTITY", "DELIVERY_INFORMATION"],
    payload: RestartAvailabilityRetryInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    if not RUNTIME_CONFIG.availability_write_enabled:
        raise ApiProblem(409, "AVAILABILITY_WRITE_DISABLED", "Availability retries are disabled in this runtime.")
    return _run_operation_action(
        request=request, account=payload.account, csrf_token=x_csrf_token,
        idempotency_key=idempotency_key, action=f"availability-retry:{operation_id}:{action}",
        request_body={**payload.model_dump(mode="json"), "operationId": operation_id, "action": action},
        execute=lambda conn, actor: retry_rejected_availability_step(
            conn, account=payload.account, operation_id=operation_id, action=action, actor=actor,
        ),
    )


@app.post("/api/restart/operations/{operation_id}/availability/{action}/resolve")
def resolve_restart_availability_step(
    operation_id: str,
    action: Literal["QUANTITY", "DELIVERY_INFORMATION"],
    payload: RestartAvailabilityResolutionInput,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    return _run_operation_action(
        request=request, account=payload.account, csrf_token=x_csrf_token,
        idempotency_key=idempotency_key, action=f"availability-resolve:{operation_id}:{action}",
        request_body={**payload.model_dump(mode="json"), "operationId": operation_id, "action": action},
        execute=lambda conn, actor: resolve_unknown_availability_step(
            conn, account=payload.account, operation_id=operation_id, action=action,
            resolution=payload.resolution, evidence_reference=payload.evidenceReference,
            reason=payload.reason, actor=actor,
        ),
    )


@app.post("/api/restart/gtin-evidence", status_code=201)
async def upload_gtin_evidence(
    request: Request,
    account: ACCOUNT = Query(...),
    ean: str = Form(..., min_length=13, max_length=13),
    source_product_id: str = Form(..., alias="sourceProductId"),
    evidence_type: str = Form(..., alias="evidenceType"),
    source_name: str = Form(..., alias="sourceName"),
    claim_basis: str = Form(..., alias="claimBasis"),
    validity_kind: str = Form(..., alias="validityKind"),
    expires_at: str | None = Form(None, alias="expiresAt"),
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    principal = _require_gtin_evidence_write(request, account, x_csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise ApiProblem(422, "VALIDATION_ERROR", "Idempotency-Key is required and must be 8–128 characters.")
    content = await file.read(MAX_EVIDENCE_BYTES + 1)
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ApiProblem(413, "EVIDENCE_SIZE_INVALID", "Evidence artifact exceeds the 10 MB limit.")
    artifact_name = file.filename or "evidence"
    artifact_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    expires = _parse_expiry(expires_at)
    artifact_hash = hashlib.sha256(content).hexdigest()
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "account": account,
                "ean": ean,
                "sourceProductId": source_product_id,
                "evidenceType": evidence_type,
                "sourceName": source_name,
                "artifactFilename": artifact_name,
                "artifactMediaType": artifact_type,
                "artifactSha256": artifact_hash,
                "claimBasis": claim_basis,
                "validityKind": validity_kind,
                "expiresAt": expires.isoformat() if expires else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    stored_key = "gtin-evidence-upload:" + hashlib.sha256(
        f"{account}\0{principal.subject}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    try:
        with connect() as conn:
            with conn.begin():
                inserted = conn.execute(
                    text(
                        "INSERT INTO restart_idempotency_keys (key, request_hash, response_body, created_at, data_scope) "
                        "VALUES (:key, :request_hash, '{}', CURRENT_TIMESTAMP, 'PRODUCTION') "
                        "ON CONFLICT (key) DO NOTHING RETURNING key"
                    ),
                    {"key": stored_key, "request_hash": request_hash},
                ).scalar_one_or_none()
                if inserted is None:
                    previous = conn.execute(
                        text("SELECT request_hash, response_body FROM restart_idempotency_keys WHERE key = :key FOR UPDATE"),
                        {"key": stored_key},
                    ).mappings().first()
                    if not previous or previous["request_hash"] != request_hash:
                        raise ApiProblem(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was used for a different evidence upload.")
                    return json.loads(previous["response_body"])
                result = submit_gtin_evidence(
                    conn,
                    account=account,
                    ean=ean,
                    source_product_id=source_product_id,
                    evidence_type=evidence_type,
                    source_name=source_name,
                    artifact_filename=artifact_name,
                    artifact_media_type=artifact_type,
                    artifact_bytes=content,
                    claim_basis=claim_basis,
                    validity_kind=validity_kind,
                    expires_at=expires,
                    actor=principal.subject,
                )
                add_event(
                    conn,
                    account,
                    "GTIN_EVIDENCE_SUBMITTED",
                    "GTIN_EVIDENCE",
                    result["id"],
                    "GTIN evidence uploaded for operator review",
                    {
                        "actor": principal.subject,
                        "evidenceId": result["id"],
                        "sourceProductId": result["sourceProductId"],
                        "ean": ean,
                        "evidenceType": result["evidenceType"],
                        "artifactSha256": result["artifactSha256"],
                    },
                    data_scope="PRODUCTION",
                )
                response_body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                conn.execute(
                    text("UPDATE restart_idempotency_keys SET response_body = :body WHERE key = :key"),
                    {"body": response_body, "key": stored_key},
                )
                return result
    except GtinEvidenceError as error:
        raise ApiProblem(error.status_code, error.code, str(error), error.details) from None
    except IntegrityError:
        raise ApiProblem(409, "GTIN_EVIDENCE_DUPLICATE", "An evidence artifact with this product binding already exists.") from None


@app.get("/api/restart/gtin-evidence")
def get_gtin_evidence(
    account: ACCOUNT,
    ean: str | None = None,
    source_product_id: str | None = Query(None, alias="sourceProductId"),
    review_status: str | None = Query(None, alias="reviewStatus"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    try:
        with connect() as conn:
            items, total = list_gtin_evidence(
                conn,
                account=account,
                ean=ean,
                source_product_id=source_product_id,
                review_status=review_status,
                limit=limit,
                offset=offset,
            )
    except GtinEvidenceError as error:
        raise ApiProblem(error.status_code, error.code, str(error), error.details) from None
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/restart/gtin-evidence/{evidence_id}/artifact")
def download_gtin_evidence_artifact(evidence_id: str, account: ACCOUNT):
    try:
        with connect() as conn:
            artifact = get_gtin_evidence_artifact(conn, account=account, evidence_id=evidence_id)
    except GtinEvidenceError as error:
        raise ApiProblem(error.status_code, error.code, str(error), error.details) from None
    return Response(
        content=artifact["content"],
        media_type=artifact["mediaType"],
        headers={
            "Content-Disposition": f'attachment; filename="{artifact["filename"]}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "ETag": f'"{artifact["sha256"]}"',
        },
    )


@app.post("/api/restart/gtin-evidence/{evidence_id}/review")
def decide_gtin_evidence(
    evidence_id: str,
    payload: GtinEvidenceReviewInput,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    principal = _require_gtin_evidence_write(request, payload.account, x_csrf_token)
    try:
        with connect() as conn:
            with conn.begin():
                result = review_gtin_evidence(
                    conn,
                    account=payload.account,
                    evidence_id=evidence_id,
                    decision=payload.decision,
                    review_basis=payload.reviewBasis,
                    actor=principal.subject,
                    approved_evidence_types=RUNTIME_CONFIG.gtin_approved_evidence_types,
                )
                if result["reviewStatus"] == "PENDING":
                    raise ApiProblem(409, result["code"], result.get("message", "GTIN evidence review was not applied."))
                if not result.get("reused"):
                    add_event(
                        conn,
                        payload.account,
                        "GTIN_EVIDENCE_REVIEWED",
                        "GTIN_EVIDENCE",
                        result["id"],
                        f"GTIN evidence decision recorded: {payload.decision}",
                        {
                            "actor": principal.subject,
                            "evidenceId": result["id"],
                            "ean": result["ean"],
                            "sourceProductId": result["sourceProductId"],
                            "decision": payload.decision,
                            "reviewBasis": payload.reviewBasis,
                            "reviewStatus": result["reviewStatus"],
                            "eligibilityStatus": result.get("eligibilityStatus"),
                        },
                        data_scope="PRODUCTION",
                    )
                return result
    except GtinEvidenceError as error:
        raise ApiProblem(error.status_code, error.code, str(error), error.details) from None


@app.get("/api/restart/events")
def list_events(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
):
    conditions = ["account = :account", "data_scope = 'PRODUCTION'"]
    params: dict[str, Any] = {"account": account}
    if q and q.strip():
        conditions.append("(summary ILIKE :query OR event_type ILIKE :query OR entity_id ILIKE :query OR metadata_json::text ILIKE :query)")
        params["query"] = f"%{q.strip()}%"
    where_clause = " AND ".join(conditions)
    with connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM restart_events WHERE {where_clause}"),
            params,
        ).scalar() or 0

        rows = conn.execute(
            text(
                f"""
                SELECT * FROM restart_events
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": pageSize, "offset": (page - 1) * pageSize},
        ).mappings().all()

    return {
        "items": [
            {
                "id": str(row["id"]),
                "account": row["account"],
                "eventType": row["event_type"],
                "entityType": row["entity_type"],
                "entityId": row["entity_id"],
                "summary": row["summary"],
                "metadata": row["metadata_json"] if isinstance(row["metadata_json"], dict) else json.loads(row["metadata_json"]),
                "createdAt": format_iso(row["created_at"]),
            }
            for row in rows
        ],
        "page": page_info(page, pageSize, total),
    }


@app.get("/api/restart/legacy/summary")
def get_legacy_summary(account: ACCOUNT):
    with connect() as conn:
        return legacy_summary(conn, account)


@app.get("/api/restart/legacy/conflicts")
def get_legacy_conflicts(account: ACCOUNT):
    with connect() as conn:
        return legacy_conflict_report(conn, account)


@app.get("/api/restart/legacy/{table}")
def get_legacy_rows(
    table: str,
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(100, ge=1, le=500),
):
    try:
        with connect() as conn:
            rows, total = legacy_rows(
                conn,
                table,
                account,
                limit=pageSize,
                offset=(page - 1) * pageSize,
            )
    except ValueError as exc:
        raise ApiProblem(404, "NOT_FOUND", "Legacy table is not available.") from exc
    return {"account": account, "dataScope": "LEGACY_UNVERIFIED", "items": rows,
            "page": page_info(page, pageSize, total)}


@app.get("/api/restart/queue")
def get_queue(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    with connect() as conn:
        projection = list_queue_jobs(conn, account=account, page=page, page_size=pageSize)
    return {"account": account, **projection}


# ==============================================================================
# OTTO Library (Stage 2.1: Persistent Aftercool Lineage & Atomic EAN Allocation)
# ==============================================================================


class LibraryProductInput(BaseModel):
    source_type: Literal["AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"] = "AFTERCOOL"
    aftercool_product_id: str | None = None
    aftercool_sku: str | None = None
    aftercool_ean: str
    factory_id: str | None = None
    factory_name: str | None = None
    title: str
    price_cents: int | None = None
    category: str | None = None
    image_url: str | None = None
    period: PERIOD = "3M"
    attributes: dict[str, Any] = Field(default_factory=dict)


class LibraryAssignRequest(BaseModel):
    account: ACCOUNT
    items: list[LibraryProductInput]


class DeactivatePreviewRequest(BaseModel):
    account: ACCOUNT
    source_eans: list[str] = Field(default_factory=list)
    aftercool_product_ids: list[str] = Field(default_factory=list)
    new_eans: list[str] = Field(default_factory=list)
    factory_id: str | None = None


class DeactivateConfirmRequest(BaseModel):
    account: ACCOUNT
    link_ids: list[str]
    reason: str = "Discontinued by factory in Aftercool"


@app.post("/api/restart/library/assign-ean", status_code=200)
def assign_library_eans(payload: LibraryAssignRequest):
    """
    Atomically allocates new GS1 EAN-13 barcodes from ean_pool and establishes
    permanent lineage in source_products, ean_links, restart_operations, and restart_events.
    Idempotent: if an active link already exists for a source product, reuses it without consuming another EAN.
    """
    require_legacy_mutations_enabled()
    if not payload.items:
        raise HTTPException(status_code=400, detail="Список товаров пуст")

    results = []

    with engine.connect() as conn:
        with conn.begin():
            for item in payload.items:
                # 1. Upsert / find source_product
                source_id = None
                if item.aftercool_product_id:
                    row = conn.execute(
                        text(
                            """
                            SELECT id FROM source_products
                            WHERE account = :account AND data_scope = 'LEGACY_UNVERIFIED'
                              AND source_type = :source_type AND aftercool_product_id = :prod_id
                            """
                        ),
                        {
                            "account": payload.account,
                            "source_type": item.source_type,
                            "prod_id": item.aftercool_product_id,
                        },
                    ).mappings().first()
                    if row:
                        source_id = str(row["id"])

                if not source_id and item.aftercool_ean:
                    row = conn.execute(
                        text(
                            """
                            SELECT id FROM source_products
                            WHERE account = :account AND data_scope = 'LEGACY_UNVERIFIED' AND aftercool_ean = :ean
                            LIMIT 1
                            """
                        ),
                        {"account": payload.account, "ean": item.aftercool_ean},
                    ).mappings().first()
                    if row:
                        source_id = str(row["id"])

                if source_id:
                    # Update metadata
                    conn.execute(
                        text(
                            """
                            UPDATE source_products
                            SET original_title = :title,
                                original_price_cents = COALESCE(:price_cents, original_price_cents),
                                category = COALESCE(:category, category),
                                image_url = COALESCE(:image_url, image_url),
                                attributes_json = :attrs,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id AND data_scope = 'LEGACY_UNVERIFIED'
                            """
                        ),
                        {
                            "id": source_id,
                            "title": item.title,
                            "price_cents": item.price_cents,
                            "category": item.category,
                            "image_url": item.image_url,
                            "attrs": json.dumps(item.attributes, ensure_ascii=False),
                        },
                    )
                else:
                    new_uuid = str(uuid4())
                    conn.execute(
                        text(
                            """
                            INSERT INTO source_products (
                                id, account, data_scope, source_type, aftercool_product_id, aftercool_sku,
                                aftercool_ean, factory_id, factory_name, original_title,
                                original_price_cents, category, image_url, attributes_json
                            ) VALUES (
                                :id, :account, 'LEGACY_UNVERIFIED', :source_type, :prod_id, :sku,
                                :ean, :factory_id, :factory_name, :title,
                                :price_cents, :category, :image_url, :attrs
                            )
                            """
                        ),
                        {
                            "id": new_uuid,
                            "account": payload.account,
                            "source_type": item.source_type,
                            "prod_id": item.aftercool_product_id,
                            "sku": item.aftercool_sku,
                            "ean": item.aftercool_ean,
                            "factory_id": item.factory_id,
                            "factory_name": item.factory_name,
                            "title": item.title,
                            "price_cents": item.price_cents,
                            "category": item.category,
                            "image_url": item.image_url,
                            "attrs": json.dumps(item.attributes, ensure_ascii=False),
                        },
                    )
                    source_id = new_uuid

                # 2. Check for existing ACTIVE link (idempotency check)
                existing_link = conn.execute(
                    text(
                        """
                        SELECT id, new_ean, created_at, status FROM ean_links
                        WHERE account = :account AND data_scope = 'LEGACY_UNVERIFIED'
                          AND source_product_id = :source_id AND status = 'ACTIVE'
                        LIMIT 1
                        """
                    ),
                    {"account": payload.account, "source_id": source_id},
                ).mappings().first()

                if existing_link:
                    results.append(
                        {
                            "linkId": str(existing_link["id"]),
                            "sourceProductId": source_id,
                            "sourceEan": item.aftercool_ean,
                            "newEan": existing_link["new_ean"],
                            "status": existing_link["status"],
                            "assignedAt": format_iso(existing_link["created_at"]),
                            "isNewAllocation": False,
                        }
                    )
                    continue

                # 3. Atomically allocate next AVAILABLE EAN from pool
                pool_row = conn.execute(
                    text(
                        """
                        SELECT ean FROM ean_pool
                        WHERE account = :account AND data_scope = 'LEGACY_UNVERIFIED' AND status = 'AVAILABLE'
                        ORDER BY created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """
                    ),
                    {"account": payload.account},
                ).mappings().first()

                if not pool_row:
                    raise HTTPException(
                        status_code=409,
                        detail=f"В пуле аккаунта {payload.account} нет свободных EAN. Пополните пул в OTTO Library.",
                    )

                allocated_ean = pool_row["ean"]

                # Mark EAN as USED in pool
                conn.execute(
                    text(
                        """
                        UPDATE ean_pool
                        SET status = 'USED', used_at = CURRENT_TIMESTAMP
                        WHERE ean = :ean AND account = :account AND data_scope = 'LEGACY_UNVERIFIED'
                        """
                    ),
                    {"ean": allocated_ean, "account": payload.account},
                )

                # Insert ean_link
                link_id = str(uuid4())
                op_id = str(uuid4())

                conn.execute(
                    text(
                        """
                        INSERT INTO ean_links (
                            id, account, data_scope, source_product_id, source_ean, new_ean,
                            status, operation_id, created_at, updated_at
                        ) VALUES (
                            :id, :account, 'LEGACY_UNVERIFIED', :source_id, :source_ean, :new_ean,
                            'ACTIVE', :op_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """
                    ),
                    {
                        "id": link_id,
                        "account": payload.account,
                        "source_id": source_id,
                        "source_ean": item.aftercool_ean,
                        "new_ean": allocated_ean,
                        "op_id": op_id,
                    },
                )

                # Record restart operation
                conn.execute(
                    text(
                        """
                        INSERT INTO restart_operations (
                            id, account, data_scope, source_product_id, period, status,
                            new_ean, target_title, target_price_cents, target_category, attributes_json
                        ) VALUES (
                            :id, :account, 'LEGACY_UNVERIFIED', :source_id, :period, 'READY',
                            :new_ean, :title, :price_cents, :category, :attrs
                        )
                        """
                    ),
                    {
                        "id": op_id,
                        "account": payload.account,
                        "source_id": source_id,
                        "period": item.period,
                        "new_ean": allocated_ean,
                        "title": item.title,
                        "price_cents": item.price_cents,
                        "category": item.category,
                        "attrs": json.dumps(item.attributes, ensure_ascii=False),
                    },
                )

                # Record audit event
                conn.execute(
                    text(
                        """
                        INSERT INTO restart_events (
                            id, account, event_type, entity_type, entity_id, summary, metadata_json
                        ) VALUES (
                            :id, :account, 'EAN_ASSIGNED', 'EAN_LINK', :link_id, :summary, :meta
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "account": payload.account,
                        "link_id": link_id,
                        "summary": f"Назначен новый EAN {allocated_ean} для товара Aftercool EAN {item.aftercool_ean}",
                        "meta": json.dumps(
                            {
                                "sourceEan": item.aftercool_ean,
                                "newEan": allocated_ean,
                                "sourceProductId": source_id,
                                "aftercoolProductId": item.aftercool_product_id,
                                "factoryId": item.factory_id,
                                "title": item.title,
                            },
                            ensure_ascii=False,
                        ),
                    },
                )

                results.append(
                    {
                        "linkId": link_id,
                        "sourceProductId": source_id,
                        "sourceEan": item.aftercool_ean,
                        "newEan": allocated_ean,
                        "status": "ACTIVE",
                        "assignedAt": utc_now(),
                        "isNewAllocation": True,
                    }
                )

    return {
        "account": payload.account,
        "count": len(results),
        "assigned": results,
    }


@app.get("/api/restart/library/links", response_model=LibraryLinkListResponse)
def list_library_links(
    account: ACCOUNT,
    q: str | None = None,
    status: str | None = None,
    factory_id: str | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    """Read the account's production lineage and evidence; link state is not ONLINE."""
    try:
        with connect() as conn:
            return list_production_library_links(
                conn, account=account, query=q, status=status, factory_id=factory_id,
                page=page, page_size=pageSize,
            )
    except LibraryQueryError as error:
        raise ApiProblem(422, error.code, str(error)) from None


@app.get("/api/restart/library/lookup", response_model=AftercoolLibraryLookupResponse)
def lookup_for_aftercool(
    account: ACCOUNT,
    aftercool_ean: str | None = None,
    source_ean: str | None = None,
    aftercool_product_id: str | None = None,
    factory_id: str | None = None,
    new_ean: str | None = None,
    sku: str | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=100),
):
    """Account-scoped M2M lookup; linked identifiers and confirmed ONLINE are separate."""
    try:
        with connect() as conn:
            return lookup_production_library_for_aftercool(
                conn,
                account=account,
                aftercool_ean=aftercool_ean,
                source_ean=source_ean,
                aftercool_product_id=aftercool_product_id,
                factory_id=factory_id,
                new_ean=new_ean,
                sku=sku,
                page=page,
                page_size=pageSize,
            )
    except LibraryQueryError as error:
        raise ApiProblem(422, error.code, str(error)) from None


@app.post("/api/restart/library/deactivate/preview")
def deactivate_preview(payload: DeactivatePreviewRequest):
    """Legacy route is unavailable: a local preview cannot represent OTTO deactivation."""
    raise ApiProblem(
        501,
        "NOT_IMPLEMENTED",
        "Library deactivation is not implemented. This endpoint does not change OTTO or local lineage.",
    )


@app.post("/api/restart/library/deactivate/confirm")
def deactivate_confirm(payload: DeactivateConfirmRequest):
    """Legacy local mutation is permanently disabled; no OTTO write adapter exists."""
    raise ApiProblem(
        501,
        "NOT_IMPLEMENTED",
        "Library deactivation is not implemented. This endpoint does not change OTTO or local lineage.",
    )


@app.get("/api/restart/library/ean-pool")
def get_library_ean_pool(
    account: ACCOUNT,
    status: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    conditions = ["account = :account", "data_scope = 'PRODUCTION'"]
    params: dict[str, Any] = {"account": account}

    if status and status.upper() in ("AVAILABLE", "RESERVED", "USED", "QUARANTINED"):
        conditions.append("status = :status")
        params["status"] = status.upper()

    if q and q.strip():
        conditions.append("ean ILIKE :q")
        params["q"] = f"%{q.strip()}%"

    where_clause = " AND ".join(conditions)

    with connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM ean_pool WHERE {where_clause}"),
            params,
        ).scalar() or 0

        params["limit"] = pageSize
        params["offset"] = (page - 1) * pageSize

        rows = conn.execute(
            text(
                f"""
                SELECT ean, account, status, batch_id, reserved_at, used_at, quarantined_at, quarantine_reason,
                       created_at, provenance_source, owner_reference, eligibility_status,
                       imported_at, imported_by, verified_by, verified_at,
                       reservation_operation_id, version, updated_at
                FROM ean_pool
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()

        counts = {
            "AVAILABLE": 0,
            "RESERVED": 0,
            "USED": 0,
            "QUARANTINED": 0,
        }
        for count_row in conn.execute(
            text("SELECT status, COUNT(*) FROM ean_pool "
                 "WHERE account = :account AND data_scope = 'PRODUCTION' GROUP BY status"),
            {"account": account},
        ).fetchall():
            if count_row[0] in counts:
                counts[count_row[0]] = count_row[1]

    return {
        "items": [
            {
                "ean": r["ean"],
                "account": r["account"],
                "status": r["status"],
                "batchId": r["batch_id"],
                "reservedAt": format_iso(r["reserved_at"]) if r["reserved_at"] else None,
                "usedAt": format_iso(r["used_at"]) if r["used_at"] else None,
                "quarantinedAt": format_iso(r["quarantined_at"]) if r["quarantined_at"] else None,
                "quarantineReason": r["quarantine_reason"],
                "createdAt": format_iso(r["created_at"]),
                "provenanceSource": r["provenance_source"],
                "ownerReference": r["owner_reference"],
                "eligibilityStatus": r["eligibility_status"],
                "importedAt": format_iso(r["imported_at"]) if r["imported_at"] else None,
                "importedBy": r["imported_by"],
                "verifiedBy": r["verified_by"],
                "verifiedAt": format_iso(r["verified_at"]) if r["verified_at"] else None,
                "reservationOperationId": str(r["reservation_operation_id"]) if r["reservation_operation_id"] else None,
                "version": r["version"],
                "updatedAt": format_iso(r["updated_at"]),
            }
            for r in rows
        ],
        "page": page_info(page, pageSize, total),
        "statusCounts": counts,
    }


# ============================================================================
# Factory Completion & Import Pipeline Endpoints (Phase A)
# ============================================================================

@app.get("/api/restart/factories")
def list_restart_factories(
    account: ACCOUNT,
    status: str | None = None,
    sort_by_new: bool = False,
):
    with connect() as conn:
        all_factories = FactoryService.list_factories(conn, account, sort_by_new=sort_by_new)
    if status and status.upper() != "ALL":
        normalized_status = status.upper()
        factories = [factory for factory in all_factories if factory["status"] == normalized_status]
    else:
        factories = all_factories
    return {
        "items": factories,
        "count": len(factories),
        "totalCount": len(all_factories),
        "completeCount": sum(1 for f in all_factories if f["status"] == "COMPLETE"),
        "notCompleteCount": sum(1 for f in all_factories if f["status"] == "NOT_COMPLETE"),
        "unknownCount": sum(1 for f in all_factories if f["status"] == "UNKNOWN"),
        "totalNewCandidates": sum(f["newCandidatesCount"] for f in all_factories),
        "totalCandidates": sum(f["totalCandidates"] for f in all_factories),
        "count3m": sum(f["period3mCount"] for f in all_factories),
        "count12m": sum(f["period12mCount"] for f in all_factories),
    }


@app.get("/api/restart/factories/{factory_id}/history")
def get_factory_history(factory_id: str, account: ACCOUNT):
    with connect() as conn:
        history = FactoryService.get_completion_history(conn, account, factory_id)
    return {"items": history, "count": len(history)}


@app.post("/api/restart/factories/{factory_id}/complete")
def complete_factory(
    factory_id: str,
    request: Request,
    account: ACCOUNT,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    """Request an evidence-backed recalculation; callers cannot force COMPLETE."""
    def recalculate(conn, _actor):
        try:
            return recalculate_factory(conn, account=account, factory_id=factory_id)
        except FactoryError as error:
            raise OperationError(error.code, str(error)) from None

    return _run_operation_action(
        request=request,
        account=account,
        csrf_token=x_csrf_token,
        idempotency_key=idempotency_key,
        action="factory-recalculate",
        request_body={"account": account, "factoryId": factory_id},
        execute=recalculate,
    )


@app.post("/api/restart/imports/upload")
async def upload_import_file(
    request: Request,
    account: ACCOUNT = Query(...),
    dry_run: bool = Query(False),
    run_mode: str | None = Query(None, alias="runMode"),
    file: UploadFile | None = File(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    if not file:
        raise ApiProblem(400, "VALIDATION_ERROR", "An import file is required.")
    raw_bytes = await file.read(MAX_IMPORT_BYTES + 1)
    filename = PurePath(file.filename or "upload.txt").name[:255]
    return _handle_import_request(
        request,
        account=account,
        filename=filename,
        content=raw_bytes,
        dry_run=dry_run,
        run_mode=run_mode,
        idempotency_key=idempotency_key,
        csrf_token=x_csrf_token,
    )


@app.post("/api/restart/imports/paste")
def paste_import_content(
    payload: ImportPreviewRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    raw = payload.raw_text.encode("utf-8")
    filename = PurePath(payload.filename or "paste_input.txt").name[:255]
    return _handle_import_request(
        request,
        account=payload.account,
        filename=filename,
        content=raw,
        dry_run=payload.dry_run,
        run_mode=payload.run_mode,
        idempotency_key=idempotency_key,
        csrf_token=x_csrf_token,
    )


def _handle_import_request(
    request: Request,
    *,
    account: str,
    filename: str,
    content: bytes,
    dry_run: bool,
    run_mode: str,
    idempotency_key: str | None,
    csrf_token: str | None,
):
    try:
        rows = parse_import_file(filename, content)
        pipeline = ImportPipeline(runtime_mode=RUNTIME_CONFIG.mode)
        if dry_run:
            return pipeline.preview(account, rows, filename)
        principal: Principal | None = getattr(request.state, "principal", None)
        if RUNTIME_CONFIG.mode.value != "PRODUCTION":
            raise ApiProblem(409, "READ_ONLY_MODE", "Import commit requires explicit PRODUCTION mode.")
        if not RUNTIME_CONFIG.otto_read_enabled:
            raise ApiProblem(409, "OTTO_READ_DISABLED", "Import commit requires OTTO read integration to be enabled.")
        if not RUNTIME_CONFIG.aftercool_read_enabled:
            raise ApiProblem(409, "AFTERCOOL_READ_DISABLED", "Import commit requires the account-matched Aftercool read integration to be enabled.")
        try:
            OttoCredentials.from_environment(account)
        except OttoApiError:
            raise ApiProblem(503, "OTTO_CREDENTIALS_MISSING", f"OTTO read credentials are not configured for account {account}.") from None
        try:
            AftercoolCredentials.from_environment()
        except AftercoolApiError:
            raise ApiProblem(503, "AFTERCOOL_CREDENTIALS_MISSING", "Aftercool read credentials are not configured.") from None
        if not principal or principal.kind != "operator" or "restart:write" not in principal.permissions:
            raise ApiProblem(403, "FORBIDDEN", "The operator is not authorized to commit imports.")
        if not idempotency_key:
            raise ApiProblem(422, "VALIDATION_ERROR", "Idempotency-Key is required for an import commit.")
        if run_mode not in {"BASELINE", "BASELINE_EXTEND", "INCREMENTAL"}:
            raise ApiProblem(422, "IMPORT_MODE_REQUIRED", "Commit requires an explicit BASELINE, BASELINE_EXTEND, or INCREMENTAL runMode.")
        content_hash = hashlib.sha256(content).hexdigest()
        with connect() as conn:
            with conn.begin():
                result, _created = pipeline.enqueue_import(
                    conn,
                    account=account,
                    rows=rows,
                    filename=filename,
                    run_mode=run_mode,
                    idempotency_key=idempotency_key,
                    principal=principal.subject,
                    content_hash=content_hash,
                )
        return JSONResponse(status_code=202, content=result)
    except ImportValidationError as error:
        raise ApiProblem(error.status_code, error.code, error.message) from None
    except ImportConflict:
        raise ApiProblem(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was already used for a different import.") from None


@app.get("/api/restart/imports/runs", response_model=ImportRunListResponse)
def list_import_runs(account: ACCOUNT, limit: int = Query(20, ge=1, le=100)):
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, account, source_filename, run_type, run_mode, run_state,
                       started_at, finished_at, total_rows, processed_count,
                       afterbuy_count, direct_otto_count, skipped_count, error_count,
                       status, summary_json, created_at
                FROM import_runs
                WHERE account = :account AND data_scope = 'PRODUCTION'
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"account": account, "limit": limit},
        ).mappings().all()
    return {
        "items": [
            {
                "id": str(r["id"]),
                "account": r["account"],
                "filename": r["source_filename"],
                "runType": r["run_type"],
                "runMode": r["run_mode"],
                "runState": r["run_state"],
                "totalRows": r["total_rows"],
                "processedCount": r["processed_count"],
                "afterbuyCount": r["afterbuy_count"],
                "directOttoCount": r["direct_otto_count"],
                "skippedCount": r["skipped_count"],
                "errorCount": r["error_count"],
                "status": r["status"],
                "legacyStatus": r["status"],
                "summary": r["summary_json"],
                "createdAt": format_iso(r["created_at"]),
                "startedAt": format_iso(r["started_at"]) if r["started_at"] else None,
                "finishedAt": format_iso(r["finished_at"]) if r["finished_at"] else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


@app.get("/api/restart/imports/runs/{run_id}", response_model=ImportRunDetailDTO)
def get_import_run(run_id: str, account: ACCOUNT):
    with connect() as conn:
        row = conn.execute(
            text("SELECT id, account, source_filename, run_type, run_mode, run_state, started_at, finished_at, "
                 "total_rows, processed_count, afterbuy_count, direct_otto_count, skipped_count, error_count, "
                 "status, summary_json, parameters_json, coverage_json, error_json, window_from, cutoff_at, created_at "
                 "FROM import_runs WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"),
            {"id": run_id, "account": account},
        ).mappings().first()
    if row is None:
        raise ApiProblem(404, "NOT_FOUND", "Import run was not found.")
    return {
        "id": str(row["id"]), "account": row["account"], "filename": row["source_filename"],
        "runType": row["run_type"], "runMode": row["run_mode"], "runState": row["run_state"],
        "totalRows": row["total_rows"], "processedCount": row["processed_count"],
        "afterbuyCount": row["afterbuy_count"], "directOttoCount": row["direct_otto_count"],
        "skippedCount": row["skipped_count"], "errorCount": row["error_count"],
        "status": row["status"], "legacyStatus": row["status"], "summary": row["summary_json"],
        "createdAt": format_iso(row["created_at"]),
        "startedAt": format_iso(row["started_at"]) if row["started_at"] else None,
        "finishedAt": format_iso(row["finished_at"]) if row["finished_at"] else None,
        "parameters": row["parameters_json"] or {}, "coverage": row["coverage_json"] or {},
        "errors": row["error_json"] or [], "windowFrom": row["window_from"], "cutoffAt": row["cutoff_at"],
    }


@app.get("/api/restart/imports/runs/{run_id}/rows", response_model=ImportRunRowsResponse)
def list_import_run_rows(
    run_id: str,
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500, alias="pageSize"),
):
    offset = (page - 1) * page_size
    with connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM import_runs WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"),
            {"id": run_id, "account": account},
        ).scalar_one_or_none()
        if exists is None:
            raise ApiProblem(404, "NOT_FOUND", "Import run was not found.")
        total = conn.execute(
            text("SELECT COUNT(*) FROM import_rows WHERE run_id = :id AND account = :account AND data_scope = 'PRODUCTION'"),
            {"id": run_id, "account": account},
        ).scalar_one()
        rows = conn.execute(
            text("SELECT id, run_id, row_number, raw_identifier, normalized_identifier, period_hint, row_state, row_json, errors_json "
                 "FROM import_rows WHERE run_id = :id AND account = :account AND data_scope = 'PRODUCTION' "
                 "ORDER BY row_number LIMIT :limit OFFSET :offset"),
            {"id": run_id, "account": account, "limit": page_size, "offset": offset},
        ).mappings().all()
    return {
        "items": [{
            "id": str(row["id"]), "runId": str(row["run_id"]), "rowNumber": row["row_number"],
            "rawIdentifier": row["raw_identifier"], "normalizedIdentifier": row["normalized_identifier"],
            "periodHint": row["period_hint"], "rowState": row["row_state"],
            "row": row["row_json"] or {}, "errors": row["errors_json"] or [],
        } for row in rows],
        "count": len(rows), "page": page, "pageSize": page_size,
        "totalPages": (total + page_size - 1) // page_size if total else 0,
    }


def _restart_openapi_schema() -> dict[str, Any]:
    """Expose the agreed product read DTO before S16 adds its endpoint."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("schemas", {})["ProductReadDTO"] = (
        ProductReadDTO.model_json_schema()
    )
    app.openapi_schema = schema
    return schema


app.openapi = _restart_openapi_schema

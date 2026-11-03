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
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from services.factory_service import FactoryService
from services.import_pipeline import ImportPipeline

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get(
    "OTTORESTART_DATABASE_URL",
    "postgresql://restart_user:restart_secure_pass@127.0.0.1:5435/otto_restart_db",
)
MIGRATION_PATH = ROOT / "migrations" / "0001_postgres_initial.sql"
ACCOUNT = Literal["JV", "XL"]
PERIOD = Literal["3M", "12M"]
EAN_PATTERN = re.compile(r"^\d{8,14}$")
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


def migrate() -> None:
    migration_dir = ROOT / "migrations"
    migration_files = sorted(migration_dir.glob("*.sql"), key=lambda p: p.name)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
        )
        conn.commit()

        applied = {
            row[0]
            for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()
        }

        for file in migration_files:
            match = re.match(r"^(\d+)", file.name)
            if not match:
                continue
            ver = int(match.group(1))
            if ver in applied:
                continue
            sql = file.read_text(encoding="utf-8")
            with conn.begin():
                conn.execute(text(sql))
                conn.execute(
                    text(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (:ver, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING"
                    ),
                    {"ver": ver},
                )


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
    yield


app = FastAPI(title="OttoRestart Local API", version="0.1.0", lifespan=lifespan)
allowed_origins = [
    origin.strip()
    for origin in os.environ.get(
        "OTTORESTART_ALLOWED_ORIGINS",
        "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3002,http://127.0.0.1:3002",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
)


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


def normalize_ean(raw: str) -> str:
    return raw.strip().replace("\ufeff", "")


def valid_ean(raw: str) -> bool:
    if not EAN_PATTERN.fullmatch(raw):
        return False
    digits = [int(char) for char in raw]
    check_digit = digits.pop()
    weighted_sum = sum(digit * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits)))
    return (10 - weighted_sum % 10) % 10 == check_digit


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
):
    conn.execute(
        text(
            """
            INSERT INTO restart_events(id, account, event_type, entity_type, entity_id, summary, metadata_json, created_at)
            VALUES (:id, :account, :event_type, :entity_type, :entity_id, :summary, :metadata_json, CURRENT_TIMESTAMP)
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
        },
    )


def csrf_cookie(request: Request) -> str | None:
    return request.cookies.get("ottorestart_csrf")


def require_local_write(request: Request, csrf_header: str | None) -> None:
    origin = request.headers.get("origin")
    if origin not in allowed_origins:
        raise ApiProblem(403, "FORBIDDEN", "Write requests are limited to configured local origins.")
    cookie = csrf_cookie(request)
    if not cookie or not csrf_header or not secrets.compare_digest(cookie, csrf_header):
        raise ApiProblem(403, "FORBIDDEN", "Missing or invalid OttoRestart CSRF token.")


class ApiProblem(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@app.exception_handler(ApiProblem)
async def handle_api_problem(_: Request, error: ApiProblem):
    return JSONResponse(
        status_code=error.status,
        content={
            "code": error.code,
            "message": error.message,
            "requestId": str(uuid4()),
            **({"details": error.details} if error.details else {}),
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "The request did not match the OttoRestart API contract.",
            "requestId": str(uuid4()),
            "details": {"errors": jsonable_encoder(error.errors())},
        },
    )


@app.get("/api/restart/csrf")
def get_csrf(response: Response):
    token = secrets.token_urlsafe(32)
    response.set_cookie("ottorestart_csrf", token, httponly=False, samesite="strict", secure=False, path="/")
    return {"token": token}


@app.get("/api/restart/overview")
def get_overview(account: ACCOUNT):
    with connect() as conn:
        batches = conn.execute(
            text(
                "SELECT COALESCE(SUM(item_count), 0) FROM restart_batches WHERE account = :account AND status != 'CANCELLED'"
            ),
            {"account": account},
        ).scalar() or 0
        eans = conn.execute(
            text("SELECT COUNT(*) FROM restart_ean_registry WHERE account = :account AND status = 'AVAILABLE'"),
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


@app.get("/api/restart/batches")
def list_batches(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    status: str | None = None,
    query: str | None = Query(None, max_length=200),
):
    where = ["account = :account"]
    params: dict[str, object] = {"account": account}
    if status:
        where.append("status = :status")
        params["status"] = status
    if query:
        where.append("name ILIKE :query")
        params["query"] = f"%{query}%"
    where_sql = " AND ".join(where)

    with connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM restart_batches WHERE {where_sql}"),
            params,
        ).scalar() or 0

        params["limit"] = pageSize
        params["offset"] = (page - 1) * pageSize
        rows = conn.execute(
            text(f"SELECT * FROM restart_batches WHERE {where_sql} ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            params,
        ).mappings().all()

    return {"items": [batch_dict(dict(row)) for row in rows], "page": page_info(page, pageSize, total)}


@app.post("/api/restart/batches/preview")
def preview_batch(
    payload: BatchInput,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    if not payload.name.strip():
        raise ApiProblem(422, "VALIDATION_ERROR", "Batch name cannot be blank.")
    inspection = inspect_eans(payload.sourceEans)
    return {
        "account": payload.account,
        "period": payload.period,
        "inputCount": inspection["inputCount"],
        "uniqueCount": inspection["uniqueCount"],
        "duplicateCount": inspection["duplicateCount"],
        "duplicates": inspection["duplicates"],
        "invalidEans": inspection["invalidEans"],
        "sourceEans": inspection["uniqueEans"],
        "warnings": ["Проверен только формат EAN и контрольная цифра; внешние источники не вызывались."],
        "dataSourcesChecked": ["LOCAL_FORMAT_ONLY"],
    }


@app.post("/api/restart/imports/preview")
async def preview_import_file(
    request: Request,
    account: ACCOUNT = Query(...),
    period: PERIOD | None = Query(None),
    file: UploadFile = File(...),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    values = await read_ean_upload(file)
    inspection = inspect_eans(values)
    return {
        "account": account,
        "period": period,
        "inputCount": inspection["inputCount"],
        "uniqueCount": inspection["uniqueCount"],
        "duplicateCount": inspection["duplicateCount"],
        "duplicates": inspection["duplicates"],
        "invalidEans": inspection["invalidEans"],
        "sourceEans": inspection["uniqueEans"],
        "warnings": ["Проверен только файл и контрольная цифра EAN; внешние источники не вызывались."],
        "dataSourcesChecked": ["LOCAL_FORMAT_ONLY"],
    }


@app.post("/api/restart/batches", status_code=201)
def create_batch(
    payload: BatchInput,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
):
    require_local_write(request, x_csrf_token)
    if not payload.name.strip():
        raise ApiProblem(422, "VALIDATION_ERROR", "Batch name cannot be blank.")
    inspection = inspect_eans(payload.sourceEans)
    if inspection["invalidEans"] or inspection["duplicates"]:
        raise ApiProblem(
            422,
            "VALIDATION_ERROR",
            "Batch contains invalid or duplicate EANs; run preview and correct the input.",
            {"invalidEans": inspection["invalidEans"], "duplicates": inspection["duplicates"]},
        )
    canonical = payload.model_dump(mode="json")
    body_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()
    created_at = utc_now()
    batch_id = str(uuid4())

    with connect() as conn:
        with conn.begin():
            previous = conn.execute(
                text("SELECT request_hash, response_body FROM restart_idempotency_keys WHERE key = :key FOR UPDATE"),
                {"key": idempotency_key},
            ).mappings().first()
            if previous:
                if previous["request_hash"] != body_hash:
                    raise ApiProblem(409, "CONFLICT", "Idempotency key was already used for a different request.")
                return json.loads(previous["response_body"])

            conn.execute(
                text(
                    """
                    INSERT INTO restart_batches(id, account, period, name, status, item_count, created_at)
                    VALUES (:id, :account, :period, :name, :status, :item_count, CURRENT_TIMESTAMP)
                    """
                ),
                {
                    "id": batch_id,
                    "account": payload.account,
                    "period": payload.period,
                    "name": payload.name.strip(),
                    "status": "IMPORTED",
                    "item_count": len(inspection["uniqueEans"]),
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO restart_items(batch_id, account, source_ean, row_number)
                    VALUES (:batch_id, :account, :source_ean, :row_number)
                    """
                ),
                [
                    {
                        "batch_id": batch_id,
                        "account": payload.account,
                        "source_ean": ean,
                        "row_number": index,
                    }
                    for index, ean in enumerate(inspection["uniqueEans"], start=1)
                ],
            )

            result = {
                "id": batch_id,
                "account": payload.account,
                "period": payload.period,
                "name": payload.name.strip(),
                "status": "IMPORTED",
                "itemCount": len(inspection["uniqueEans"]),
                "createdAt": created_at,
            }

            conn.execute(
                text(
                    """
                    INSERT INTO restart_idempotency_keys(key, request_hash, response_body, created_at)
                    VALUES (:key, :request_hash, :response_body, CURRENT_TIMESTAMP)
                    """
                ),
                {
                    "key": idempotency_key,
                    "request_hash": body_hash,
                    "response_body": json.dumps(result),
                },
            )

            add_event(
                conn,
                payload.account,
                "BATCH_IMPORTED",
                "BATCH",
                batch_id,
                f"Импортирована партия «{payload.name.strip()}»",
                {"period": payload.period, "itemCount": result["itemCount"]},
            )

    return result


@app.get("/api/restart/batches/{batch_id}")
def get_batch(batch_id: str, account: ACCOUNT):
    with connect() as conn:
        row = conn.execute(
            text("SELECT * FROM restart_batches WHERE id = :id AND account = :account"),
            {"id": batch_id, "account": account},
        ).mappings().first()
    if not row:
        raise ApiProblem(404, "NOT_FOUND", "Batch was not found.")
    return batch_dict(dict(row))


@app.get("/api/restart/batches/{batch_id}/items")
def list_batch_items(
    batch_id: str,
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    query: str | None = Query(None, max_length=100),
):
    with connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM restart_batches WHERE id = :id AND account = :account"),
            {"id": batch_id, "account": account},
        ).first()
        if not exists:
            raise ApiProblem(404, "NOT_FOUND", "Batch was not found for this account.")

        where = "batch_id = :batch_id AND account = :account"
        params: dict[str, object] = {"batch_id": batch_id, "account": account}
        if query:
            where += " AND source_ean ILIKE :query"
            params["query"] = f"%{query}%"

        total = conn.execute(
            text(f"SELECT COUNT(*) FROM restart_items WHERE {where}"),
            params,
        ).scalar() or 0

        params["limit"] = pageSize
        params["offset"] = (page - 1) * pageSize
        rows = conn.execute(
            text(
                f"""
                SELECT id, batch_id, account, source_ean, row_number
                FROM restart_items
                WHERE {where}
                ORDER BY row_number
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()

    return {
        "items": [
            {
                "id": str(row["id"]),
                "batchId": row["batch_id"],
                "account": row["account"],
                "sourceEan": row["source_ean"],
                "rowNumber": row["row_number"],
            }
            for row in rows
        ],
        "page": page_info(page, pageSize, total),
    }


@app.get("/api/restart/ean-pool")
def list_ean_pool(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    status: str | None = None,
):
    where = ["account = :account"]
    params: dict[str, object] = {"account": account}
    if status:
        where.append("status = :status")
        params["status"] = status
    where_sql = " AND ".join(where)

    with connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM restart_ean_registry WHERE {where_sql}"),
            params,
        ).scalar() or 0

        params["limit"] = pageSize
        params["offset"] = (page - 1) * pageSize
        rows = conn.execute(
            text(
                f"""
                SELECT * FROM restart_ean_registry
                WHERE {where_sql}
                ORDER BY imported_at DESC, ean
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
                "importedAt": format_iso(row["imported_at"]),
            }
            for row in rows
        ],
        "page": page_info(page, pageSize, total),
    }


@app.post("/api/restart/ean-pool/import", status_code=201)
def import_eans(
    payload: EanImportInput,
    request: Request,
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
    imported_at = utc_now()
    eans = inspection["uniqueEans"]

    with connect() as conn:
        with conn.begin():
            existing = []
            for start in range(0, len(eans), 500):
                chunk = eans[start : start + 500]
                rows = conn.execute(
                    text("SELECT ean FROM restart_ean_registry WHERE ean = ANY(:eans)"),
                    {"eans": chunk},
                ).mappings().all()
                existing.extend(r["ean"] for r in rows)

            if existing:
                raise ApiProblem(
                    409,
                    "CONFLICT",
                    "Some EANs already exist in the OttoRestart pool.",
                    {"existingEans": existing},
                )

            conn.execute(
                text(
                    """
                    INSERT INTO restart_ean_registry(ean, account, status, batch_id, imported_at)
                    VALUES (:ean, :account, 'AVAILABLE', NULL, CURRENT_TIMESTAMP)
                    """
                ),
                [{"ean": ean, "account": payload.account} for ean in eans],
            )

            add_event(
                conn,
                payload.account,
                "EAN_POOL_IMPORTED",
                "EAN_POOL",
                imported_at,
                f"Добавлено EAN в пул: {len(eans)}",
                {"count": len(eans)},
            )

    return {
        "importedCount": len(eans),
        "account": payload.account,
        "status": "AVAILABLE",
        "importedAt": imported_at,
    }


@app.get("/api/restart/events")
def list_events(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    with connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM restart_events WHERE account = :account"),
            {"account": account},
        ).scalar() or 0

        rows = conn.execute(
            text(
                """
                SELECT * FROM restart_events
                WHERE account = :account
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"account": account, "limit": pageSize, "offset": (page - 1) * pageSize},
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


@app.get("/api/restart/queue")
def get_queue(
    account: ACCOUNT,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    return {
        "account": account,
        "status": "IDLE",
        "items": [],
        "page": page_info(page, pageSize, 0),
    }


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
                            WHERE account = :account AND source_type = :source_type AND aftercool_product_id = :prod_id
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
                            WHERE account = :account AND aftercool_ean = :ean
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
                            WHERE id = :id
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
                                id, account, source_type, aftercool_product_id, aftercool_sku,
                                aftercool_ean, factory_id, factory_name, original_title,
                                original_price_cents, category, image_url, attributes_json
                            ) VALUES (
                                :id, :account, :source_type, :prod_id, :sku,
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
                        WHERE account = :account AND source_product_id = :source_id AND status = 'ACTIVE'
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
                        WHERE account = :account AND status = 'AVAILABLE'
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
                        WHERE ean = :ean
                        """
                    ),
                    {"ean": allocated_ean},
                )

                # Insert ean_link
                link_id = str(uuid4())
                op_id = str(uuid4())

                conn.execute(
                    text(
                        """
                        INSERT INTO ean_links (
                            id, account, source_product_id, source_ean, new_ean,
                            status, operation_id, created_at, updated_at
                        ) VALUES (
                            :id, :account, :source_id, :source_ean, :new_ean,
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
                            id, account, source_product_id, period, status,
                            new_ean, target_title, target_price_cents, target_category, attributes_json
                        ) VALUES (
                            :id, :account, :source_id, :period, 'READY',
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


@app.get("/api/restart/library/links")
def list_library_links(
    account: ACCOUNT,
    q: str | None = None,
    status: str | None = None,
    factory_id: str | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    """
    Returns paginated list of EAN links with joined source product metadata,
    along with pool and audit summary counts.
    """
    conditions = ["l.account = :account"]
    params: dict[str, Any] = {"account": account}

    if status and status.upper() in ("ACTIVE", "DEACTIVATED", "PENDING_PUBLISH", "PUBLISHED"):
        conditions.append("l.status = :status")
        params["status"] = status.upper()

    if factory_id:
        conditions.append("p.factory_id = :factory_id")
        params["factory_id"] = factory_id

    if q and q.strip():
        term = f"%{q.strip()}%"
        conditions.append(
            "(p.original_title ILIKE :term OR l.source_ean ILIKE :term OR l.new_ean ILIKE :term OR p.factory_name ILIKE :term OR p.aftercool_product_id ILIKE :term)"
        )
        params["term"] = term

    where_clause = " AND ".join(conditions)

    with connect() as conn:
        total = conn.execute(
            text(
                f"""
                SELECT COUNT(*) FROM ean_links l
                JOIN source_products p ON l.source_product_id = p.id
                WHERE {where_clause}
                """
            ),
            params,
        ).scalar() or 0

        params["limit"] = pageSize
        params["offset"] = (page - 1) * pageSize

        rows = conn.execute(
            text(
                f"""
                SELECT
                    l.id as link_id,
                    l.account,
                    l.source_ean,
                    l.new_ean,
                    l.status,
                    l.created_at,
                    l.updated_at,
                    l.deactivated_at,
                    l.deactivation_reason,
                    p.id as product_id,
                    p.source_type,
                    p.aftercool_product_id,
                    p.aftercool_sku,
                    p.factory_id,
                    p.factory_name,
                    p.original_title,
                    p.original_price_cents,
                    p.category,
                    p.image_url
                FROM ean_links l
                JOIN source_products p ON l.source_product_id = p.id
                WHERE {where_clause}
                ORDER BY l.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()

        active_links = conn.execute(
            text("SELECT COUNT(*) FROM ean_links WHERE account = :account AND status = 'ACTIVE'"),
            {"account": account},
        ).scalar() or 0

        pool_available = conn.execute(
            text("SELECT COUNT(*) FROM ean_pool WHERE account = :account AND status = 'AVAILABLE'"),
            {"account": account},
        ).scalar() or 0

        pool_total = conn.execute(
            text("SELECT COUNT(*) FROM ean_pool WHERE account = :account"),
            {"account": account},
        ).scalar() or 0

        events_count = conn.execute(
            text("SELECT COUNT(*) FROM restart_events WHERE account = :account"),
            {"account": account},
        ).scalar() or 0

    return {
        "items": [
            {
                "id": str(r["link_id"]),
                "account": r["account"],
                "sourceProductId": str(r["product_id"]),
                "sourceType": r["source_type"],
                "aftercoolProductId": r["aftercool_product_id"],
                "aftercoolSku": r["aftercool_sku"],
                "sourceEan": r["source_ean"],
                "newEan": r["new_ean"],
                "factoryId": r["factory_id"],
                "factoryName": r["factory_name"],
                "title": r["original_title"],
                "priceCents": r["original_price_cents"],
                "category": r["category"],
                "imageUrl": r["image_url"],
                "status": r["status"],
                "createdAt": format_iso(r["created_at"]),
                "deactivatedAt": format_iso(r["deactivated_at"]) if r["deactivated_at"] else None,
                "deactivationReason": r["deactivation_reason"],
            }
            for r in rows
        ],
        "page": page_info(page, pageSize, total),
        "summary": {
            "activeLinks": active_links,
            "poolAvailable": pool_available,
            "poolTotal": pool_total,
            "eventsCount": events_count,
        },
    }


@app.get("/api/restart/library/lookup")
def lookup_for_aftercool(
    account: ACCOUNT,
    aftercool_ean: str | None = None,
    source_ean: str | None = None,
    aftercool_product_id: str | None = None,
    factory_id: str | None = None,
    new_ean: str | None = None,
    sku: str | None = None,
):
    """
    Dedicated Read-Only API for Ravil's Aftercool system:
    Resolves any Aftercool EAN, Product ID, SKU, or Factory ID to active OTTO listings
    and new OTTO EANs.
    """
    effective_source_ean = aftercool_ean or source_ean

    if not any([effective_source_ean, aftercool_product_id, factory_id, new_ean, sku]):
        raise HTTPException(
            status_code=400,
            detail="Укажите хотя бы один параметр поиска: aftercool_ean, aftercool_product_id, factory_id, new_ean или sku",
        )

    conditions = ["l.account = :account"]
    params: dict[str, Any] = {"account": account}

    if effective_source_ean:
        conditions.append("l.source_ean = :source_ean")
        params["source_ean"] = effective_source_ean.strip()

    if new_ean:
        conditions.append("l.new_ean = :new_ean")
        params["new_ean"] = new_ean.strip()

    if aftercool_product_id:
        conditions.append("p.aftercool_product_id = :prod_id")
        params["prod_id"] = aftercool_product_id.strip()

    if factory_id:
        conditions.append("p.factory_id = :factory_id")
        params["factory_id"] = factory_id.strip()

    if sku:
        conditions.append("p.aftercool_sku = :sku")
        params["sku"] = sku.strip()

    where_clause = " AND ".join(conditions)

    with connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    l.id as link_id,
                    l.source_ean,
                    l.new_ean,
                    l.status as link_status,
                    l.created_at as assigned_at,
                    l.deactivated_at,
                    l.deactivation_reason,
                    p.id as product_id,
                    p.aftercool_product_id,
                    p.aftercool_sku,
                    p.factory_id,
                    p.factory_name,
                    p.original_title,
                    p.original_price_cents,
                    p.category
                FROM ean_links l
                JOIN source_products p ON l.source_product_id = p.id
                WHERE {where_clause}
                ORDER BY l.created_at DESC
                """
            ),
            params,
        ).mappings().all()

    matches = []
    for r in rows:
        matches.append(
            {
                "linkId": str(r["link_id"]),
                "aftercoolEan": r["source_ean"],
                "newOttoEan": r["new_ean"],
                "status": r["link_status"],
                "assignedAt": format_iso(r["assigned_at"]),
                "deactivatedAt": format_iso(r["deactivated_at"]) if r["deactivated_at"] else None,
                "product": {
                    "id": str(r["product_id"]),
                    "aftercoolProductId": r["aftercool_product_id"],
                    "sku": r["aftercool_sku"],
                    "factoryId": r["factory_id"],
                    "factoryName": r["factory_name"],
                    "title": r["original_title"],
                    "priceCents": r["original_price_cents"],
                    "category": r["category"],
                },
            }
        )

    return {
        "account": account,
        "matchCount": len(matches),
        "activeOttoEans": [m["newOttoEan"] for m in matches if m["status"] == "ACTIVE"],
        "matches": matches,
    }


@app.post("/api/restart/library/deactivate/preview")
def deactivate_preview(payload: DeactivatePreviewRequest):
    """
    Safe two-step deactivation: Preview which active EAN links will be affected
    before actually deactivating them on OTTO.
    """
    conditions = ["l.account = :account", "l.status = 'ACTIVE'"]
    params: dict[str, Any] = {"account": payload.account}

    sub_conds = []
    if payload.source_eans:
        sub_conds.append("l.source_ean = ANY(:source_eans)")
        params["source_eans"] = payload.source_eans

    if payload.new_eans:
        sub_conds.append("l.new_ean = ANY(:new_eans)")
        params["new_eans"] = payload.new_eans

    if payload.aftercool_product_ids:
        sub_conds.append("p.aftercool_product_id = ANY(:prod_ids)")
        params["prod_ids"] = payload.aftercool_product_ids

    if payload.factory_id:
        sub_conds.append("p.factory_id = :factory_id")
        params["factory_id"] = payload.factory_id

    if not sub_conds:
        return {"account": payload.account, "affectedCount": 0, "items": []}

    conditions.append(f"({' OR '.join(sub_conds)})")
    where_clause = " AND ".join(conditions)

    with connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    l.id as link_id,
                    l.source_ean,
                    l.new_ean,
                    p.original_title,
                    p.factory_id,
                    p.factory_name
                FROM ean_links l
                JOIN source_products p ON l.source_product_id = p.id
                WHERE {where_clause}
                """
            ),
            params,
        ).mappings().all()

    return {
        "account": payload.account,
        "affectedCount": len(rows),
        "items": [
            {
                "linkId": str(r["link_id"]),
                "sourceEan": r["source_ean"],
                "newEan": r["new_ean"],
                "title": r["original_title"],
                "factoryName": r["factory_name"],
            }
            for r in rows
        ],
    }


@app.post("/api/restart/library/deactivate/confirm")
def deactivate_confirm(payload: DeactivateConfirmRequest):
    """
    Executes safe deactivation:
    1. Sets ean_links.status = 'DEACTIVATED'
    2. Moves ean_pool.status = 'QUARANTINED' (prevents reuse)
    3. Writes audit log to restart_events
    """
    if not payload.link_ids:
        return {"account": payload.account, "deactivatedCount": 0}

    deactivated = 0
    with engine.connect() as conn:
        with conn.begin():
            rows = conn.execute(
                text(
                    """
                    SELECT id, source_ean, new_ean FROM ean_links
                    WHERE account = :account AND id = ANY(:ids) AND status = 'ACTIVE'
                    FOR UPDATE
                    """
                ),
                {"account": payload.account, "ids": [UUID(i) for i in payload.link_ids]},
            ).mappings().all()

            for r in rows:
                conn.execute(
                    text(
                        """
                        UPDATE ean_links
                        SET status = 'DEACTIVATED',
                            deactivated_at = CURRENT_TIMESTAMP,
                            deactivation_reason = :reason,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                        """
                    ),
                    {"id": r["id"], "reason": payload.reason},
                )

                conn.execute(
                    text(
                        """
                        UPDATE ean_pool
                        SET status = 'QUARANTINED',
                            quarantined_at = CURRENT_TIMESTAMP,
                            quarantine_reason = :reason
                        WHERE ean = :ean
                        """
                    ),
                    {"ean": r["new_ean"], "reason": payload.reason},
                )

                conn.execute(
                    text(
                        """
                        INSERT INTO restart_events (
                            id, account, event_type, entity_type, entity_id, summary, metadata_json
                        ) VALUES (
                            :id, :account, 'EAN_DEACTIVATED', 'EAN_LINK', :link_id, :summary, :meta
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "account": payload.account,
                        "link_id": str(r["id"]),
                        "summary": f"Деактивирован EAN {r['new_ean']} (исходный Aftercool: {r['source_ean']}). Причина: {payload.reason}",
                        "meta": json.dumps(
                            {"newEan": r["new_ean"], "sourceEan": r["source_ean"], "reason": payload.reason},
                            ensure_ascii=False,
                        ),
                    },
                )
                deactivated += 1

    return {
        "account": payload.account,
        "deactivatedCount": deactivated,
        "status": "COMPLETED",
    }


@app.get("/api/restart/library/ean-pool")
def get_library_ean_pool(
    account: ACCOUNT,
    status: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
):
    conditions = ["account = :account"]
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
                SELECT ean, account, status, batch_id, reserved_at, used_at, quarantined_at, quarantine_reason, created_at
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
            text("SELECT status, COUNT(*) FROM ean_pool WHERE account = :account GROUP BY status"),
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
            }
            for r in rows
        ],
        "page": page_info(page, pageSize, total),
        "statusCounts": counts,
    }


# ============================================================================
# Factory Completion & Import Pipeline Endpoints (Phase A)
# ============================================================================

class ImportPasteRequest(BaseModel):
    account: ACCOUNT
    raw_text: str = Field(..., description="Raw text or CSV content")
    filename: str = Field("paste_input.txt", description="Source filename")
    dry_run: bool = Field(False, description="Preview mode without DB mutation")


@app.get("/api/restart/factories")
def list_restart_factories(
    account: ACCOUNT,
    status: str | None = None,
    sort_by_new: bool = False,
):
    with connect() as conn:
        factories = FactoryService.list_factories(
            conn, account, status_filter=status, sort_by_new=sort_by_new
        )
    return {
        "items": factories,
        "count": len(factories),
        "completeCount": sum(1 for f in factories if f["status"] == "COMPLETE"),
        "notCompleteCount": sum(1 for f in factories if f["status"] == "NOT_COMPLETE"),
        "unknownCount": sum(1 for f in factories if f["status"] == "UNKNOWN"),
        "totalNewCandidates": sum(f["newCandidatesCount"] for f in factories),
    }


@app.get("/api/restart/factories/{factory_id}/history")
def get_factory_history(factory_id: str, account: ACCOUNT):
    with connect() as conn:
        history = FactoryService.get_completion_history(conn, account, factory_id)
    return {"items": history, "count": len(history)}


@app.post("/api/restart/factories/{factory_id}/complete")
def complete_factory(factory_id: str, account: ACCOUNT):
    with connect() as conn:
        with conn.begin():
            success = FactoryService.record_completion(conn, account, factory_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Фабрика не может быть отмечена как COMPLETE: есть неопубликованные товары или ошибки.",
        )
    return {"success": True, "factoryId": factory_id, "status": "COMPLETE"}


@app.post("/api/restart/imports/upload")
async def upload_import_file(
    account: ACCOUNT = Query(...),
    dry_run: bool = Query(False),
    file: UploadFile | None = File(None),
):
    if not file:
        raise HTTPException(status_code=400, detail="Файл не передан")
    raw_bytes = await file.read()
    content_str = raw_bytes.decode("utf-8", errors="replace")
    pipeline = ImportPipeline()
    with connect() as conn:
        with conn.begin():
            result = pipeline.execute_import(
                conn,
                account,
                content_str,
                filename=file.filename or "upload.txt",
                dry_run=dry_run,
            )
    return result


@app.post("/api/restart/imports/paste")
def paste_import_content(payload: ImportPasteRequest):
    pipeline = ImportPipeline()
    with connect() as conn:
        with conn.begin():
            result = pipeline.execute_import(
                conn,
                payload.account,
                payload.raw_text,
                filename=payload.filename,
                dry_run=payload.dry_run,
            )
    return result


@app.get("/api/restart/imports/runs")
def list_import_runs(account: ACCOUNT, limit: int = Query(20, ge=1, le=100)):
    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, account, source_filename, total_rows, processed_count,
                       afterbuy_count, direct_otto_count, skipped_count, error_count,
                       status, summary_json, created_at
                FROM import_runs
                WHERE account = :account
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
                "totalRows": r["total_rows"],
                "processedCount": r["processed_count"],
                "afterbuyCount": r["afterbuy_count"],
                "directOttoCount": r["direct_otto_count"],
                "skippedCount": r["skipped_count"],
                "errorCount": r["error_count"],
                "status": r["status"],
                "summary": r["summary_json"],
                "createdAt": format_iso(r["created_at"]),
            }
            for r in rows
        ]
    }



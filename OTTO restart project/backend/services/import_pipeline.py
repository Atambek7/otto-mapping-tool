"""Bounded, account-scoped OttoRestart file import and run processing.

Preview and parsing are local-only. Committing a file persists its rows and a
single background job; the worker performs reads only through the existing,
explicitly gated OTTO/Aftercool adapters. No EAN-pool operation is performed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from jobs.service import IdempotencyConflict, JobExecutionError, enqueue_job
from runtime_mode import RuntimeConfig, RuntimeMode
from services.aftercool_lookup_service import AftercoolLookupService
from services.discovery_service import (
    StatusResponseInput,
    apply_staged_run,
    create_import_run,
    stage_status_responses,
)
from services.otto_demo_fixture_service import OttoDemoFixtureService
from services.otto_price_evidence import (
    is_missing_otto_price_error,
    normalize_otto_product_variation,
    resolve_otto_standard_price,
)
from services.otto_status_service import OttoStatusService

MAX_IMPORT_BYTES = 20_000_000
MAX_IMPORT_ROWS = 50_000
PROCESS_CHUNK_SIZE = 500
PROGRESS_REFRESH_INTERVAL = 100
EAN_PATTERN = re.compile(r"^[0-9]{13}$")
SKU_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")

_HEADER_ALIASES: dict[str, set[str]] = {
    "ean": {"ean", "ean13", "gtin", "barcode", "barcode number", "strichcode", "штрихкод"},
    "sku": {"sku", "otto sku", "artikelnummer", "artikelnr", "item number", "product sku"},
    "identifier": {"identifier", "product id", "id", "artikel", "produkt"},
    "period": {"period", "zeitraum", "periode", "gruppe", "restriction", "cohort"},
    "factory": {"factory", "factory id", "factory_id", "fabrik", "fabriknr", "fabriknummer"},
    "title": {"title", "titel", "name", "product title"},
    "price": {"price", "preis", "startpreis"},
}


class ImportValidationError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ImportConflict(ValueError):
    pass


def normalize_identifier(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).replace("\ufeff", "").strip().strip("\"'").strip()


def validate_ean13(ean: str) -> bool:
    """Restart accepts the user-approved 13-digit barcode format without checksum validation."""
    return isinstance(ean, str) and bool(re.fullmatch(r"[0-9]{13}", ean))


def _normal_header(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold().replace("_", " "))


def _header_map(values: Sequence[Any]) -> dict[str, int]:
    normalized = [_normal_header(value) for value in values]
    result: dict[str, int] = {}
    for field, aliases in _HEADER_ALIASES.items():
        match = next((index for index, value in enumerate(normalized) if value in aliases), None)
        if match is not None:
            result[field] = match
    return result


def _xlsx_cell_text(cell: Any) -> str:
    value = cell.value
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    value_text = str(value)
    number_format = getattr(cell, "number_format", "") or ""
    if isinstance(value, int) and re.fullmatch(r"0+", number_format):
        value_text = value_text.zfill(len(number_format))
    return value_text


def _row_from_values(values: Sequence[Any], columns: Mapping[str, int], row_number: int) -> dict[str, Any]:
    cells = [normalize_identifier(value) for value in values]

    def get(name: str) -> str:
        index = columns.get(name)
        return cells[index] if index is not None and index < len(cells) else ""

    ean = get("ean")
    sku = get("sku")
    identifier = get("identifier")
    if not sku and not ean:
        identifier = identifier or (cells[0] if cells else "")
    raw = sku or ean or identifier
    kind = "SKU" if sku else "EAN" if ean else "UNKNOWN"
    period = get("period").upper()
    return {
        "raw_identifier": raw,
        "identifier_type": kind,
        "sku": sku or None,
        "ean": ean or None,
        "period_hint": period if period in {"3M", "12M"} else None,
        "factory_hint": get("factory") or None,
        "title_hint": get("title") or None,
        "price_hint": get("price") or None,
        "source_row": row_number,
    }


def _rows_from_table(rows: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    nonempty = [list(row) for row in rows if any(str(value or "").strip() for value in row)]
    if not nonempty:
        return []
    columns = _header_map(nonempty[0])
    has_header = bool(columns)
    data_rows = nonempty[1:] if has_header else nonempty
    if not has_header:
        columns = {"identifier": 0}
        if len(nonempty[0]) > 1:
            columns["period"] = 1
        if len(nonempty[0]) > 2:
            columns["factory"] = 2
    return [_row_from_values(row, columns, index) for index, row in enumerate(data_rows, start=2 if has_header else 1)]


def _bounded_rows_from_iter(rows: Any) -> list[dict[str, Any]]:
    buffered: list[list[Any]] = []
    for row in rows:
        if any(str(value or "").strip() for value in row):
            buffered.append(list(row))
            if len(buffered) > MAX_IMPORT_ROWS + 1:
                raise ImportValidationError("TOO_MANY_ROWS", f"A file may contain at most {MAX_IMPORT_ROWS} rows.")
    return _rows_from_table(buffered)


def _plain_text_rows(content: str) -> list[dict[str, Any]]:
    nonempty = [line for line in content.splitlines() if line.strip()]
    if not nonempty:
        return []
    sample = "\n".join(nonempty[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        table = list(csv.reader(io.StringIO(content), dialect))
        return _bounded_rows_from_iter(table)
    except csv.Error:
        values = [re.split(r"\s+", line.strip()) for line in nonempty]
        columns = _header_map(values[0])
        if columns:
            return _bounded_rows_from_iter(values)
        parsed = []
        for index, parts in enumerate(values, start=1):
            if len(parts) > 1 and parts[1].upper() in {"3M", "12M"}:
                parsed.append({
                    "raw_identifier": normalize_identifier(parts[0]), "identifier_type": "UNKNOWN",
                    "sku": None, "ean": None, "period_hint": parts[1].upper(), "factory_hint": None,
                    "title_hint": None, "price_hint": None, "source_row": index,
                })
            else:
                parsed.append({
                    "raw_identifier": normalize_identifier(parts[0]), "identifier_type": "UNKNOWN",
                    "sku": None, "ean": None, "period_hint": None, "factory_hint": None,
                    "title_hint": None, "price_hint": None, "source_row": index,
                })
        return parsed


def parse_import_file(filename: str, content: bytes) -> list[dict[str, Any]]:
    """Parse CSV/TXT/XLSX while preserving textual identifiers and row order."""
    if not isinstance(content, bytes):
        raise ImportValidationError("INVALID_FILE", "Import content must be bytes.")
    if len(content) > MAX_IMPORT_BYTES:
        raise ImportValidationError("FILE_TOO_LARGE", "The import file exceeds the 20 MB limit.", 413)
    suffix = PurePath(filename or "").suffix.casefold()
    if suffix not in {".csv", ".txt", ".xlsx"}:
        raise ImportValidationError("UNSUPPORTED_FILE", "Use a .xlsx, .csv, or .txt file.")

    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as error:
            raise ImportValidationError("XLSX_UNAVAILABLE", "XLSX support is not installed.", 503) from error
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                sheet = workbook.active
                values = ([ _xlsx_cell_text(cell) for cell in row ] for row in sheet.iter_rows())
                parsed = _bounded_rows_from_iter(values)
            finally:
                workbook.close()
        except ImportValidationError:
            raise
        except Exception as error:
            raise ImportValidationError("INVALID_XLSX", "Could not read this XLSX file.") from error
    else:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ImportValidationError("INVALID_ENCODING", "CSV/TXT files must use UTF-8 encoding.") from error
        nonempty = [line for line in decoded.splitlines() if line.strip()]
        if not nonempty:
            parsed = []
        else:
            parsed = _plain_text_rows(decoded)

    if len(parsed) > MAX_IMPORT_ROWS:
        raise ImportValidationError("TOO_MANY_ROWS", f"A file may contain at most {MAX_IMPORT_ROWS} rows.")
    return parsed


def _validate_row(row: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    raw = normalize_identifier(row.get("raw_identifier"))
    kind = row.get("identifier_type")
    ean = normalize_identifier(row.get("ean")) or None
    sku = normalize_identifier(row.get("sku")) or None
    if kind == "EAN" or (kind == "UNKNOWN" and EAN_PATTERN.fullmatch(raw)):
        candidate = ean or raw
        if not EAN_PATTERN.fullmatch(candidate):
            return None, None, "INVALID_EAN_FORMAT"
        return sku or candidate, candidate, None
    candidate_sku = sku or raw
    if not SKU_PATTERN.fullmatch(candidate_sku):
        return None, ean, "INVALID_IDENTIFIER"
    if ean is not None and not EAN_PATTERN.fullmatch(ean):
        return candidate_sku, None, "INVALID_EAN"
    return candidate_sku, ean, None


def _insert_import_row_batch(connection: Connection, values: list[dict[str, Any]]) -> None:
    if not values:
        return
    connection.execute(
        text(
            "INSERT INTO import_rows (run_id, account, data_scope, row_number, raw_identifier, "
            "normalized_identifier, period_hint, row_state, row_json, errors_json) "
            "VALUES (:run_id, :account, 'PRODUCTION', :row_number, :raw_identifier, "
            ":normalized_identifier, :period_hint, :row_state, CAST(:row_json AS jsonb), "
            "CAST(:errors_json AS jsonb))"
        ),
        values,
    )


def _canonical_request_hash(account: str, filename: str, run_mode: str, content_hash: str) -> str:
    canonical = json.dumps(
        {"account": account, "filename": filename, "runMode": run_mode, "contentHash": content_hash},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ImportPipeline:
    """Parser, local preview, transactional commit, and read-only run worker."""

    def __init__(
        self,
        otto_service: Optional[Any] = None,
        aftercool_service: Optional[AftercoolLookupService] = None,
        runtime_mode: RuntimeMode | str = RuntimeMode.LEGACY_READ_ONLY,
    ) -> None:
        self.runtime_mode = RuntimeMode(runtime_mode)
        self.otto_service = otto_service or (
            OttoDemoFixtureService(runtime_mode=self.runtime_mode)
            if self.runtime_mode in {RuntimeMode.DEMO, RuntimeMode.TEST}
            else OttoStatusService()
        )
        self.aftercool_service = aftercool_service or AftercoolLookupService(runtime_mode=self.runtime_mode)

    def parse_raw_content(self, content_str: str) -> list[dict[str, Any]]:
        return parse_import_file("manual_import.txt", content_str.encode("utf-8"))

    def preview(self, account: str, rows: Sequence[Mapping[str, Any]], filename: str) -> dict[str, Any]:
        if account not in {"JV", "XL"}:
            raise ImportValidationError("INVALID_ACCOUNT", "Account must be JV or XL.")
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        unverified_sample: list[dict[str, Any]] = []
        counts = {"valid": 0, "duplicate": 0, "invalid": 0}
        for index, row in enumerate(rows, start=1):
            sku, ean, error = _validate_row(row)
            identity = sku or normalize_identifier(row.get("raw_identifier"))
            if error:
                state = "INVALID"
                counts["invalid"] += 1
            elif identity in seen:
                state = "DUPLICATE"
                counts["duplicate"] += 1
                error = "DUPLICATE_IN_FILE"
            else:
                state = "VALID"
                counts["valid"] += 1
                seen.add(identity)
            preview_item = {
                "rowNumber": int(row.get("source_row") or index),
                "rawIdentifier": normalize_identifier(row.get("raw_identifier")),
                "sku": sku,
                "ean": ean,
                "periodHint": row.get("period_hint"),
                "rowState": state,
                "errorCode": error,
            }
            if len(output) < 100:
                output.append(preview_item)
            if state == "VALID" and len(unverified_sample) < 20:
                unverified_sample.append(preview_item)
        summary = {
            "status": "UNVERIFIED",
            "totalParsed": len(rows),
            "uniqueValidCount": 0,
            "unverifiedCount": counts["valid"],
            "skippedExistingCount": 0,
            "invalidCount": counts["invalid"],
            "afterbuyCount": 0,
            "directOttoCount": 0,
            "period3mCount": 0,
            "period12mCount": 0,
            "unverifiedSample": [
                {"identifier": item["rawIdentifier"], "status": "UNVERIFIED", "period": None,
                 "reason": "Preview validates input format only; no current OTTO response was read."}
                for item in unverified_sample
            ],
            "samplePreview": [],
        }
        return {
            "success": False,
            "previewCompleted": True,
            "dryRun": True,
            "status": "UNVERIFIED",
            "account": account,
            "filename": filename,
            "totalRows": len(rows),
            "counts": counts,
            "items": output[:100],
            "summary": summary,
            "warnings": ["Preview проверяет только локальный формат; OTTO и Aftercool не вызывались."],
            "dataSourcesChecked": ["LOCAL_FORMAT_ONLY"],
        }

    def enqueue_import(
        self,
        connection: Connection,
        *,
        account: str,
        rows: Sequence[Mapping[str, Any]],
        filename: str,
        run_mode: str,
        idempotency_key: str,
        principal: str,
        content_hash: str,
    ) -> tuple[dict[str, Any], bool]:
        if account not in {"JV", "XL"}:
            raise ImportValidationError("INVALID_ACCOUNT", "Account must be JV or XL.")
        if run_mode not in {"BASELINE", "BASELINE_EXTEND", "INCREMENTAL"}:
            raise ImportValidationError("INVALID_RUN_MODE", "Unsupported import run mode.")
        if not 8 <= len(idempotency_key) <= 128:
            raise ImportValidationError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key length must be 8–128.")
        request_hash = _canonical_request_hash(account, filename, run_mode, content_hash)
        stored_key = "file-import:" + hashlib.sha256(
            f"{account}\0{principal}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        reserved = connection.execute(
            text(
                "INSERT INTO restart_idempotency_keys (key, request_hash, response_body, created_at) "
                "VALUES (:key, :request_hash, '{}', CURRENT_TIMESTAMP) "
                "ON CONFLICT (key) DO NOTHING RETURNING key"
            ),
            {"key": stored_key, "request_hash": request_hash},
        ).scalar_one_or_none()
        if reserved is None:
            previous = connection.execute(
                text("SELECT request_hash, response_body FROM restart_idempotency_keys WHERE key = :key FOR UPDATE"),
                {"key": stored_key},
            ).mappings().first()
            if previous is None:
                raise ImportConflict("idempotency reservation is not visible")
            if previous["request_hash"] != request_hash:
                raise ImportConflict("Idempotency-Key was already used for a different import request.")
            response = json.loads(previous["response_body"])
            return response, False

        run_id = create_import_run(
            connection, account, "FILE_IMPORT", run_mode,
            source_filename=filename,
            cutoff_at=datetime.now(timezone.utc),
            parameters={"requestHash": request_hash, "principal": principal, "rowLimit": MAX_IMPORT_ROWS},
        )
        seen: set[str] = set()
        values: list[dict[str, Any]] = []
        initially_processed = 0
        for index, row in enumerate(rows, start=1):
            raw = normalize_identifier(row.get("raw_identifier"))
            sku, ean, error = _validate_row(row)
            identity = sku or raw
            state = "PENDING"
            errors: list[dict[str, str]] = []
            if error:
                state = "INVALID"
                errors.append({"code": error})
            elif identity in seen:
                state = "DUPLICATE"
                errors.append({"code": "DUPLICATE_IN_FILE"})
            else:
                seen.add(identity)
            persisted_row = {**dict(row), "sku": sku, "ean": ean}
            values.append({
                "run_id": run_id, "account": account, "row_number": int(row.get("source_row") or index),
                "raw_identifier": raw, "normalized_identifier": sku or ean,
                "period_hint": row.get("period_hint"), "row_state": state,
                "row_json": json.dumps(persisted_row, ensure_ascii=False),
                "errors_json": json.dumps(errors),
            })
            initially_processed += int(state != "PENDING")
            if len(values) >= PROCESS_CHUNK_SIZE:
                _insert_import_row_batch(connection, values)
                values.clear()
        _insert_import_row_batch(connection, values)
        connection.execute(
            text("UPDATE import_runs SET total_rows = :total, processed_count = :processed "
                 "WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"),
            {"total": len(rows), "processed": initially_processed, "id": run_id, "account": account},
        )
        job = enqueue_job(
            connection,
            account=account,
            job_type="import.process",
            idempotency_key=f"file-import-{run_id}-chunk-1",
            payload={"runId": run_id, "chunk": 1},
        ).job
        response = {"success": True, "status": "PENDING", "runId": run_id, "jobId": job.id, "httpStatus": 202}
        connection.execute(
            text("UPDATE restart_idempotency_keys SET response_body = :body WHERE key = :key"),
            {"body": json.dumps(response, separators=(",", ":")), "key": stored_key},
        )
        return response, True

    def execute_import(
        self,
        conn: Connection,
        account: str,
        content_str: str,
        filename: str = "manual_import.txt",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Compatibility preview. Commit is exclusively the run/outbox contract."""
        del conn
        rows = self.parse_raw_content(content_str)
        if dry_run:
            base = self.preview(account, rows, filename)
            if self.runtime_mode not in {RuntimeMode.DEMO, RuntimeMode.TEST}:
                return base
            preview_items: list[dict[str, Any]] = []
            unverified: list[dict[str, Any]] = []
            for row in rows:
                identifier = normalize_identifier(row.get("raw_identifier"))
                try:
                    status = self.otto_service.get_status(account, identifier, hint_period=row.get("period_hint"))
                except Exception:
                    status = {"status": "LOOKUP_ERROR", "is_candidate": False, "data_scope": "UNVERIFIED"}
                if not (
                    status.get("status") == "DEMO_ONLY" and status.get("data_scope") == "DEMO_ONLY"
                    and status.get("is_candidate") is True and status.get("period") in {"3M", "12M"}
                ):
                    unverified.append({"identifier": identifier, "status": status.get("status", "UNVERIFIED"), "period": None})
                    continue
                try:
                    lookup = self.aftercool_service.lookup_product(account, identifier, row.get("factory_hint"))
                except Exception:
                    lookup = {"status": "LOOKUP_ERROR"}
                if not (
                    lookup.get("data_scope") == "DEMO_ONLY"
                    and lookup.get("status") in {"DEMO_FOUND", "DEMO_NOT_FOUND_CONFIRMED"}
                    and lookup.get("source_type") in {"AFTERBUY", "OTTO_DIRECT"}
                ):
                    unverified.append({"identifier": identifier, "status": lookup.get("status", "UNVERIFIED"), "period": status["period"]})
                    continue
                preview_items.append({
                    "identifier": identifier, "period": status["period"],
                    "sourceType": lookup["source_type"], "dataScope": "DEMO_ONLY",
                    "data_scope": "DEMO_ONLY", "status": "DEMO_ONLY",
                })
            base["status"] = "DEMO_ONLY"
            base["dataScope"] = "DEMO_ONLY"
            base["demoCandidates"] = preview_items[:100]
            summary = base["summary"]
            summary.update(
                status="DEMO_ONLY",
                uniqueValidCount=len(preview_items),
                unverifiedCount=len(unverified),
                afterbuyCount=sum(item["sourceType"] == "AFTERBUY" for item in preview_items),
                directOttoCount=sum(item["sourceType"] == "OTTO_DIRECT" for item in preview_items),
                period3mCount=sum(item["period"] == "3M" for item in preview_items),
                period12mCount=sum(item["period"] == "12M" for item in preview_items),
                unverifiedSample=unverified[:20],
                samplePreview=preview_items[:10],
            )
            return base
        return {
            "success": False,
            "status": "NOT_IMPLEMENTED",
            "error": "Use the authenticated file-import run endpoint with Idempotency-Key.",
        }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _image_url_from_asset(asset: Any) -> str | None:
    candidates = [asset] if isinstance(asset, str) else (
        [asset.get(key) for key in ("url", "href", "mediaUrl", "imageUrl", "imageURL", "location")]
        if isinstance(asset, Mapping) else []
    )
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        try:
            parsed = urlsplit(candidate.strip())
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return candidate.strip()
    return None


def _first_otto_media_asset(product: Mapping[str, Any]) -> tuple[Any, str | None]:
    """Select one usable image asset from OTTO, never from Aftercool."""
    assets = product.get("mediaAssets")
    if not isinstance(assets, list):
        return None, None
    for asset in assets:
        url = _image_url_from_asset(asset)
        if url:
            return asset, url
    return None, None


def _otto_product_snapshot(
    product: Mapping[str, Any], prices: Mapping[str, Any] | None,
    *, expected_sku: str | None = None, expected_ean: str | None = None,
) -> dict[str, Any]:
    """Normalize catalog fields while retaining the complete OTTO GET payload."""
    description = product.get("productDescription")
    description = description if isinstance(description, Mapping) else {}
    sku = expected_sku or str(product.get("sku") or "")
    ean = expected_ean or (str(product.get("ean")) if product.get("ean") is not None else None)
    resolved_price = resolve_otto_standard_price(
        product, prices, expected_sku=sku, expected_ean=ean,
    ) if sku else {"evidence": {"status": "MISSING", "amountCents": None}, "response": None}
    evidence = resolved_price["evidence"]
    price_cents = evidence.get("amountCents") if evidence.get("status") in {"CONFIRMED", "ZERO"} else None
    normalized_prices = resolved_price.get("response")
    if not isinstance(normalized_prices, Mapping) and isinstance(prices, Mapping):
        # Keep the exact response for diagnosis, even when its price cannot be
        # used for calculation. A later resolver still checks SKU/EAN/currency.
        normalized_prices = dict(prices)

    attributes = description.get("attributes", product.get("attributes"))
    if isinstance(attributes, Mapping):
        attributes = [{"name": str(key), "value": value} for key, value in attributes.items()]
    elif not isinstance(attributes, list):
        attributes = []
    selected_asset, image_url = _first_otto_media_asset(product)
    stored_product = dict(product)
    if "mediaAssets" in stored_product:
        stored_product["mediaAssets"] = [selected_asset] if selected_asset is not None else []
    return {
        "title": _first_text(
            description.get("productName"), description.get("title"), description.get("productLine"),
            product.get("productName"), product.get("title"), product.get("name"),
        ),
        "category": _first_text(product.get("category"), description.get("category"), product.get("categoryName")),
        "image_url": image_url,
        "price_cents": price_cents,
        "otto_price_evidence": evidence,
        "attributes": attributes,
        "otto_product_data": stored_product,
        "otto_prices_data": dict(normalized_prices) if isinstance(normalized_prices, Mapping) else None,
    }


def _persist_source_product(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    run_mode: str,
    period: str,
    sku: str,
    ean: str,
    source: Mapping[str, Any],
    source_type: str,
) -> str | None:
    """Persist the verified identity plus the OTTO source snapshot and routing link."""
    if source_type not in {"AFTERBUY", "AFTERCOOL", "OTTO_DIRECT"}:
        return None
    title = source.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    product_id = source.get("aftercool_product_id") if source_type in {"AFTERBUY", "AFTERCOOL"} else None
    attributes_json = source.get("attributes_json")
    if not isinstance(attributes_json, (dict, list)):
        attributes_json = {
            "attributes": source.get("attributes") if isinstance(source.get("attributes"), (dict, list)) else [],
            "ottoProductData": source.get("otto_product_data") if isinstance(source.get("otto_product_data"), Mapping) else {},
            "ottoPricesData": source.get("otto_prices_data") if isinstance(source.get("otto_prices_data"), Mapping) else {},
            "ottoPriceEvidence": source.get("otto_price_evidence") if isinstance(source.get("otto_price_evidence"), Mapping) else {},
            "aftercoolLookup": source.get("aftercool_lookup") if isinstance(source.get("aftercool_lookup"), Mapping) else {},
            "aftercoolReportedAccount": source.get("aftercool_reported_account"),
        }
    matched = connection.execute(
        text(
            "SELECT id, source_type, aftercool_product_id, aftercool_sku, aftercool_ean "
            "FROM source_products WHERE account = :account AND data_scope = 'PRODUCTION' "
            "AND ((CAST(:product_id AS TEXT) IS NOT NULL AND aftercool_product_id = :product_id) "
            "OR aftercool_ean = :ean OR (source_type = 'OTTO_DIRECT' AND aftercool_sku = :sku)) "
            "ORDER BY id LIMIT 2 FOR UPDATE"
        ),
        {"account": account, "product_id": product_id, "ean": ean, "sku": sku},
    ).mappings().all()
    if len(matched) > 1:
        return None
    if matched:
        row = matched[0]
        if row["aftercool_ean"] not in {None, ean}:
            return None
        expected_source_sku = source.get("sku") if source_type in {"AFTERBUY", "AFTERCOOL"} else sku
        if row["aftercool_sku"] not in {None, expected_source_sku}:
            return None
        if row["source_type"] != source_type:
            return None
        connection.execute(
            text("UPDATE source_products SET period = :period, is_new_candidate = :is_new, "
                 "current_status = 'CANDIDATE', original_title = :title, original_price_cents = :price, "
                 "category = :category, image_url = :image_url, attributes_json = CAST(:attributes AS jsonb), "
                 "last_checked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                 "WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"),
            {
                "period": period, "is_new": run_mode == "INCREMENTAL", "title": title.strip(),
                "price": source.get("price_cents"), "category": source.get("category"),
                "image_url": source.get("image_url"),
                "attributes": json.dumps(attributes_json, ensure_ascii=False),
                "id": row["id"], "account": account,
            },
        )
        return str(row["id"])

    source_sku = source.get("sku") if source_type in {"AFTERBUY", "AFTERCOOL"} else sku
    source_ean = source.get("ean") if source_type in {"AFTERBUY", "AFTERCOOL"} else ean
    source_product_id = str(uuid4())
    connection.execute(
        text(
            "INSERT INTO source_products (id, account, data_scope, source_type, aftercool_product_id, "
            "aftercool_sku, aftercool_ean, factory_id, factory_name, original_title, original_price_cents, "
            "category, image_url, attributes_json, period, is_new_candidate, current_status, import_run_id) "
            "VALUES (:id, :account, 'PRODUCTION', :source_type, :product_id, :sku, :ean, :factory_id, "
            ":factory_name, :title, :price, :category, :image_url, CAST(:attributes AS jsonb), :period, "
            ":is_new, 'CANDIDATE', :run_id)"
        ),
        {
            "id": source_product_id, "account": account, "source_type": source_type,
            "product_id": product_id, "sku": source_sku, "ean": source_ean,
            "factory_id": source.get("factory_id"), "factory_name": source.get("factory_name"),
            "title": title.strip(), "price": source.get("price_cents"),
            "category": source.get("category"), "image_url": source.get("image_url"),
            "attributes": json.dumps(attributes_json, ensure_ascii=False),
            "period": period, "is_new": run_mode == "INCREMENTAL",
            "run_id": run_id,
        },
    )
    return source_product_id


def _resolve_import_row(
    account: str,
    row: Mapping[str, Any],
    *,
    otto_client: Any,
    aftercool_service: AftercoolLookupService,
    status_service: OttoStatusService,
    market_status_response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    requested_sku = row.get("sku") or row.get("ean") or row.get("raw_identifier")
    if not isinstance(requested_sku, str) or not requested_sku.strip():
        return {"row_state": "INVALID", "error_code": "INVALID_IDENTIFIER", "retryable": False}
    requested_sku = requested_sku.strip()
    market_status = market_status_response or otto_client.get_marketplace_status(requested_sku)
    if not isinstance(market_status, Mapping) or market_status.get("sku") != requested_sku:
        return {
            "row_state": "IDENTITY_UNRESOLVED",
            "requested_sku": requested_sku,
            "error_code": "OTTO_STATUS_IDENTITY_UNRESOLVED",
            "route_status": "NOT_APPLICABLE",
            "retryable": False,
        }
    classification = status_service.analyze_response(market_status, requested_sku, 200)
    result: dict[str, Any] = {
        "row_state": "VALID" if classification.classification == "NONE" else "IDENTITY_UNRESOLVED",
        "requested_sku": requested_sku,
        "status_response": market_status,
        "classification": classification.classification,
        "period": classification.period,
        "reason": classification.reason,
        "route_status": "NOT_APPLICABLE",
        "source_product_id": None,
        "confirmed_ean": None,
        "moin": market_status.get("moin") if isinstance(market_status.get("moin"), str) else None,
        "source_record": None,
        "source_type": None,
        "retryable": False,
    }
    if classification.classification not in {"EXACT_CODE", "TITLE_ONLY"} or classification.period is None:
        if classification.classification in {"AMBIGUOUS", "UNCHECKABLE"}:
            result["error_code"] = classification.classification
        return result

    result["row_state"] = "IDENTITY_UNRESOLVED"
    try:
        product = otto_client.get_product(requested_sku)
    except Exception as error:
        result.update(error_code=getattr(error, "code", "OTTO_PRODUCT_LOOKUP_ERROR"), retryable=bool(getattr(error, "retryable", False)))
        return result
    product_ean = row.get("ean")
    normalized_product = normalize_otto_product_variation(
        product, expected_sku=requested_sku,
    )
    if normalized_product is None:
        result.update(error_code="OTTO_PRODUCT_IDENTITY_UNRESOLVED")
        return result
    product = normalized_product
    product_ean = product.get("ean")
    supplied_ean = row.get("ean")
    if not isinstance(product_ean, str) or not EAN_PATTERN.fullmatch(product_ean):
        result.update(error_code="OTTO_EAN_UNCONFIRMED")
        return result
    if supplied_ean and product_ean != supplied_ean:
        result.update(error_code="SKU_EAN_MISMATCH")
        return result
    result["confirmed_ean"] = product_ean

    nested_price = resolve_otto_standard_price(
        product, None, expected_sku=requested_sku, expected_ean=product_ean,
    )
    price_response = nested_price.get("response")
    if nested_price["evidence"].get("status") not in {"CONFIRMED", "ZERO"}:
        try:
            price_response = otto_client.get_product_prices(requested_sku)
            if not isinstance(price_response, Mapping) or price_response.get("sku") != requested_sku:
                result.update(error_code="OTTO_PRICE_IDENTITY_UNRESOLVED")
                return result
        except Exception as error:
            if is_missing_otto_price_error(error):
                price_response = {"sku": requested_sku}
            else:
                result.update(
                    error_code=getattr(error, "code", "OTTO_PRICE_LOOKUP_ERROR"),
                    retryable=bool(getattr(error, "retryable", False)),
                )
                return result
    otto_snapshot = _otto_product_snapshot(
        product, price_response, expected_sku=requested_sku, expected_ean=product_ean,
    )

    lookup = aftercool_service.lookup_product(account, product_ean, factory_hint=row.get("factory_hint"))
    if isinstance(lookup, Mapping):
        result["aftercool_lookup"] = {
            key: lookup.get(key) for key in (
                "status", "reason_code", "factory_id", "factory_name", "source_file",
                "excluded_factory_ids", "excluded_factory_match_count", "factory_exclusion_policy_sha256",
            ) if key in lookup
        }
    lookup_status = lookup.get("status") if isinstance(lookup, Mapping) else None
    result["route_status"] = lookup_status or "LOOKUP_ERROR"
    if lookup_status == "LOOKUP_ERROR":
        result.update(row_state="ERROR", error_code=lookup.get("reason_code", "AFTERCOOL_LOOKUP_ERROR"), retryable=True)
        return result
    if lookup_status not in {"FOUND", "NOT_FOUND_CONFIRMED"} or lookup.get("verified") is not True or lookup.get("data_scope") != "PRODUCTION":
        result.update(error_code=lookup.get("reason_code", lookup_status or "AFTERCOOL_UNVERIFIED"))
        return result

    source_type = lookup.get("source_type")
    if source_type not in {"AFTERBUY", "AFTERCOOL", "OTTO_DIRECT"}:
        result.update(error_code="AFTERCOOL_ROUTE_UNVERIFIED")
        return result
    if source_type == "OTTO_DIRECT":
        source_record = {
            **otto_snapshot,
            "sku": requested_sku,
            "ean": product_ean,
        }
        if not isinstance(source_record.get("title"), str) or not source_record["title"].strip():
            result.update(error_code="OTTO_TITLE_UNAVAILABLE")
            return result
    else:
        aftercool_name = lookup.get("name") or lookup.get("title")
        source_record = {
            **otto_snapshot,
            "title": aftercool_name or otto_snapshot.get("title"),
            "image_url": otto_snapshot.get("image_url") or lookup.get("image_url"),
            "aftercool_product_id": lookup.get("aftercool_product_id"),
            "sku": lookup.get("sku"),
            "ean": lookup.get("ean"),
            "factory_id": lookup.get("factory_id"),
            "factory_name": lookup.get("factory_name"),
            "aftercool_reported_account": lookup.get("reported_account"),
            "aftercool_lookup": {
                key: lookup.get(key)
                for key in (
                    "account", "status", "aftercool_product_id", "ean", "sku",
                    "factory_id", "factory_name", "reported_account", "name", "title",
                    "source_file", "source_factory_id", "excluded_factory_match_count",
                    "factory_exclusion_policy_sha256",
                    "category", "price_cents", "image_url", "verified", "data_scope",
                )
            },
        }
    if not isinstance(source_record.get("title"), str) or not source_record["title"].strip():
        result.update(error_code="SOURCE_TITLE_UNAVAILABLE")
        return result
    result.update(
        row_state="VALID",
        source_type=source_type,
        source_record=source_record,
        route_status=lookup_status,
        error_code=None,
    )
    return result


def _refresh_import_run_progress(engine: Engine, run_id: str, account: str) -> None:
    """Reconcile progress periodically instead of rescanning the run for every row."""
    with engine.begin() as connection:
        processed = connection.execute(
            text(
                "SELECT COUNT(*) FROM import_rows WHERE run_id = :run_id AND account = :account "
                "AND data_scope = 'PRODUCTION' AND row_state <> 'PENDING'"
            ),
            {"run_id": run_id, "account": account},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE import_runs SET processed_count = :processed "
                "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
            ),
            {"processed": int(processed), "run_id": run_id, "account": account},
        )


def process_import_run(
    engine: Engine,
    run_id: str,
    *,
    otto_client_factory: Callable[[str], Any],
    aftercool_service_factory: Callable[[], AftercoolLookupService],
    runtime_config: RuntimeConfig,
    chunk_number: int = 1,
    job_attempt: int = 1,
    max_attempts: int = 5,
) -> dict[str, Any]:
    """Process one bounded run chunk; retries re-read only that same chunk."""
    if isinstance(chunk_number, bool) or not isinstance(chunk_number, int) or chunk_number < 1:
        raise JobExecutionError("INVALID_IMPORT_CHUNK", retryable=False)
    chunk_offset = (chunk_number - 1) * PROCESS_CHUNK_SIZE
    if runtime_config.mode != RuntimeMode.PRODUCTION or not runtime_config.otto_read_enabled:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE import_runs SET run_state = 'FAILED', status = 'FAILED', finished_at = CURRENT_TIMESTAMP, "
                     "error_json = '[{\"code\":\"OTTO_READ_DISABLED\"}]'::jsonb "
                     "WHERE id = :id AND data_scope = 'PRODUCTION' AND run_state NOT IN ('SUCCEEDED','PARTIAL','FAILED')"),
                {"id": run_id},
            )
        raise JobExecutionError("OTTO_READ_DISABLED", retryable=False)

    with engine.begin() as connection:
        run = connection.execute(
            text("SELECT id, account, run_mode, run_state, total_rows FROM import_runs "
                 "WHERE id = :id AND data_scope = 'PRODUCTION' FOR UPDATE"),
            {"id": run_id},
        ).mappings().first()
        if run is None:
            raise JobExecutionError("IMPORT_RUN_NOT_FOUND", retryable=False)
        if run["run_state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}:
            return {"runId": run_id, "runState": run["run_state"], "alreadyFinished": True}
        account = run["account"]
        connection.execute(
            text("UPDATE import_runs SET run_state = 'RUNNING', status = 'PROCESSING', "
                 "started_at = COALESCE(started_at, CURRENT_TIMESTAMP) WHERE id = :id"),
            {"id": run_id},
        )

    try:
        otto_client = otto_client_factory(account)
        aftercool_service = aftercool_service_factory()
    except Exception:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE import_runs SET run_state = 'FAILED', status = 'FAILED', finished_at = CURRENT_TIMESTAMP, "
                     "error_json = '[{\"code\":\"IMPORT_ADAPTER_UNAVAILABLE\"}]'::jsonb "
                     "WHERE id = :id AND data_scope = 'PRODUCTION' AND run_state NOT IN ('SUCCEEDED','PARTIAL','FAILED')"),
                {"id": run_id},
            )
        raise JobExecutionError("IMPORT_ADAPTER_UNAVAILABLE", retryable=False) from None
    status_service = OttoStatusService()
    retryable_errors = 0
    attempted_row_ids: set[str] = set()
    rows_since_progress_refresh = 0

    while True:
        with engine.begin() as connection:
            batch = connection.execute(
                text(
                    "SELECT id, row_number, raw_identifier, normalized_identifier, row_json, row_state "
                    "FROM import_rows WHERE id IN (SELECT id FROM import_rows WHERE run_id = :run_id "
                    "AND account = :account AND data_scope = 'PRODUCTION' ORDER BY row_number OFFSET :offset LIMIT :limit) "
                    "AND (row_state = 'PENDING' OR (row_state = 'ERROR' AND row_json->>'retryable' = 'true')) "
                    "AND NOT (id::text = ANY(:attempted_ids)) ORDER BY row_number"
                ),
                {"run_id": run_id, "account": account, "limit": PROCESS_CHUNK_SIZE,
                 "attempted_ids": list(attempted_row_ids), "offset": chunk_offset},
            ).mappings().all()
        if not batch:
            break

        for batch_index, stored in enumerate(batch, start=1):
            attempted_row_ids.add(str(stored["id"]))
            raw_row = _json_object(stored["row_json"])
            try:
                outcome = _resolve_import_row(
                    account, raw_row, otto_client=otto_client,
                    aftercool_service=aftercool_service, status_service=status_service,
                )
            except Exception as error:
                outcome = {
                    "row_state": "ERROR", "error_code": getattr(error, "code", "IMPORT_ROW_LOOKUP_ERROR"),
                    "retryable": bool(getattr(error, "retryable", False)),
                    "route_status": "LOOKUP_ERROR",
                }
            if outcome.get("retryable"):
                retryable_errors += 1

            with engine.begin() as connection:
                source_id = None
                response = outcome.get("status_response")
                period = outcome.get("period")
                confirmed_ean = outcome.get("confirmed_ean")
                if outcome.get("source_record") and outcome.get("source_type") and period and confirmed_ean:
                    try:
                        with connection.begin_nested():
                            source_id = _persist_source_product(
                                connection,
                                account=account,
                                run_id=run_id,
                                run_mode=(run["run_mode"] if isinstance(run, Mapping) else "BASELINE"),
                                period=period,
                                sku=str(outcome.get("requested_sku")),
                                ean=confirmed_ean,
                                source=outcome["source_record"],
                                source_type=outcome["source_type"],
                            )
                    except Exception as error:
                        source_id = None
                        safe_code = (
                            "SOURCE_IDENTITY_CONFLICT" if type(error).__name__ == "IntegrityError"
                            else f"SOURCE_PERSISTENCE_{type(error).__name__.upper()}"
                        )
                        outcome.update(row_state="IDENTITY_UNRESOLVED", error_code=safe_code)
                    if source_id is None:
                        outcome.update(
                            row_state="IDENTITY_UNRESOLVED",
                            error_code=outcome.get("error_code") or "SOURCE_IDENTITY_UNRESOLVED",
                        )
                row_data = {
                    **raw_row,
                    "rowState": outcome.get("row_state", "ERROR"),
                    "classification": outcome.get("classification"),
                    "confirmedSku": outcome.get("requested_sku"),
                    "confirmedEan": confirmed_ean,
                    "period": period,
                    "routeStatus": outcome.get("route_status"),
                    "sourceType": outcome.get("source_type"),
                    "sourceProductId": source_id,
                    "ottoStatus": outcome.get("status_response"),
                    "retryable": bool(outcome.get("retryable")),
                }
                source_record = outcome.get("source_record")
                lookup = source_record.get("aftercool_lookup") if isinstance(source_record, Mapping) else outcome.get("aftercool_lookup")
                if isinstance(lookup, Mapping):
                    row_data["aftercoolSelection"] = {
                        key: lookup.get(key)
                        for key in (
                            "status", "aftercool_product_id", "factory_id", "factory_name",
                            "source_file", "source_factory_id", "excluded_factory_match_count",
                            "factory_exclusion_policy_sha256",
                            "reason_code", "excluded_factory_ids",
                        )
                    }
                errors = []
                if outcome.get("error_code"):
                    errors.append({"code": str(outcome["error_code"])[:64]})
                connection.execute(
                    text("UPDATE import_rows SET normalized_identifier = COALESCE(:identifier, normalized_identifier), "
                         "row_state = :state, row_json = CAST(:row_json AS jsonb), errors_json = CAST(:errors AS jsonb), "
                         "updated_at = CURRENT_TIMESTAMP WHERE id = :id AND run_id = :run_id AND account = :account "
                         "AND data_scope = 'PRODUCTION'"),
                    {
                        "identifier": outcome.get("requested_sku"), "state": row_data["rowState"],
                        "row_json": json.dumps(row_data, ensure_ascii=False), "errors": json.dumps(errors),
                        "id": stored["id"], "run_id": run_id, "account": account,
                    },
                )
                if isinstance(response, Mapping):
                    stage_status_responses(
                        connection, account, run_id,
                        [StatusResponseInput(
                            requested_sku=str(outcome["requested_sku"]), http_status=200, body=response,
                            source_product_id=source_id, confirmed_ean=confirmed_ean if source_id else None,
                        )],
                    )
            rows_since_progress_refresh += 1
            if (rows_since_progress_refresh >= PROGRESS_REFRESH_INTERVAL
                    or batch_index == len(batch)):
                _refresh_import_run_progress(engine, run_id, account)
                rows_since_progress_refresh = 0

    if retryable_errors and job_attempt < max_attempts:
        raise JobExecutionError("IMPORT_READ_RETRYABLE", retryable=True)

    with engine.begin() as connection:
        if retryable_errors:
            connection.execute(
                text("UPDATE import_rows SET row_json = jsonb_set(row_json, '{retryable}', 'false'::jsonb), "
                     "updated_at = CURRENT_TIMESTAMP WHERE id IN (SELECT id FROM import_rows "
                     "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION' "
                     "ORDER BY row_number OFFSET :offset LIMIT :limit) AND row_state = 'ERROR' "
                     "AND row_json->>'retryable' = 'true'"),
                {"run_id": run_id, "account": account, "offset": chunk_offset, "limit": PROCESS_CHUNK_SIZE},
            )
        total_rows = int(run["total_rows"] or 0)
        if chunk_offset + PROCESS_CHUNK_SIZE < total_rows:
            current = connection.execute(
                text("SELECT run_state FROM import_runs WHERE id = :run_id AND account = :account "
                     "AND data_scope = 'PRODUCTION' FOR UPDATE"),
                {"run_id": run_id, "account": account},
            ).mappings().first()
            if current and current["run_state"] != "STAGED":
                connection.execute(
                    text("UPDATE import_runs SET run_state = 'STAGED', status = 'PROCESSING' "
                         "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"),
                    {"run_id": run_id, "account": account},
                )
            next_chunk = chunk_number + 1
            next_job = enqueue_job(
                connection,
                account=account,
                job_type="import.process",
                idempotency_key=f"file-import-{run_id}-chunk-{next_chunk}",
                payload={"runId": run_id, "chunk": next_chunk},
            ).job
            return {"runId": run_id, "runState": "RUNNING", "nextJobId": next_job.id, "chunk": chunk_number}

        current = connection.execute(
            text("SELECT run_state FROM import_runs WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION' FOR UPDATE"),
            {"run_id": run_id, "account": account},
        ).mappings().first()
        if current and current["run_state"] != "STAGED":
            connection.execute(
                text("UPDATE import_runs SET run_state = 'STAGED', status = 'PROCESSING' "
                     "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"),
                {"run_id": run_id, "account": account},
            )
        applied = apply_staged_run(connection, account, run_id)
        state_counts = connection.execute(
            text("SELECT row_state, COUNT(*) AS count FROM import_rows WHERE run_id = :run_id "
                 "AND account = :account AND data_scope = 'PRODUCTION' GROUP BY row_state"),
            {"run_id": run_id, "account": account},
        ).mappings().all()
        counts = {row["row_state"]: int(row["count"]) for row in state_counts}
        routes = connection.execute(
            text("SELECT row_json->>'routeStatus' AS route, COUNT(*) AS count FROM import_rows "
                 "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION' "
                 "GROUP BY row_json->>'routeStatus'"),
            {"run_id": run_id, "account": account},
        ).mappings().all()
        route_counts = {row["route"]: int(row["count"]) for row in routes if row["route"]}
        error_count = sum(count for state, count in counts.items() if state in {"INVALID", "DUPLICATE", "IDENTITY_UNRESOLVED", "ERROR"})
        processed_count = sum(counts.values())
        final_state = "PARTIAL" if error_count else "SUCCEEDED"
        summary = {
            "rows": sum(counts.values()), "rowStates": counts, "routes": route_counts,
            "resolvedRows": counts.get("VALID", 0),
            "observations": applied.get("observations", 0), "episodesOpened": applied.get("opened", 0),
            "episodesUpdated": applied.get("updated", 0), "episodesClosed": applied.get("closed", 0),
            "applied": applied.get("applied", True),
        }
        connection.execute(
            text("UPDATE import_runs SET run_state = :state, status = 'COMPLETED', finished_at = CURRENT_TIMESTAMP, "
                 "processed_count = :processed, afterbuy_count = :afterbuy, direct_otto_count = :direct, "
                 "skipped_count = :skipped, error_count = :errors, summary_json = CAST(:summary AS jsonb), "
                 "coverage_json = CAST(:coverage AS jsonb), error_json = CAST(:errors_json AS jsonb) "
                 "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"),
            {
                "state": final_state, "processed": processed_count,
                "afterbuy": route_counts.get("FOUND", 0), "direct": route_counts.get("NOT_FOUND_CONFIRMED", 0),
                "skipped": counts.get("DUPLICATE", 0), "errors": error_count,
                "summary": json.dumps(summary), "coverage": json.dumps(summary),
                "errors_json": json.dumps([{"rowStates": counts}] if error_count else []),
                "run_id": run_id, "account": account,
            },
        )
    return {"runId": run_id, "runState": final_state, "summary": summary}

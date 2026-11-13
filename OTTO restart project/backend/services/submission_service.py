"""Persistent S20 submission state; deliberately contains no OTTO HTTP writer.

The submission adapter is not implemented while the Restart workspace rules
forbid OTTO write calls. These transaction-scoped functions prepare an audited
single-SKU attempt and safely record a normalized response supplied by a later
approved adapter. They never perform network I/O or mark an operation PUBLISHED.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.operation_service import (
    OperationError,
    _record_event,
    begin_submission,
    mark_submission_unknown,
)
from services.preparation_service import PreparationError, price_snapshot_from_sources
from services.qc_service import QC_POLICY_VERSION
from services.title_policy import PreparedTitleError, normalize_title_whitespace, validate_prepared_title

_TASK_PATH = re.compile(
    r"^/v5/products/update-tasks/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"(?:/(?:succeeded|failed|unchanged))?$"
)
_SAFE_CODE = re.compile(r"^[A-Z0-9_.-]{1,64}$")
_SECRET_TEXT = re.compile(
    r"(?i)\b(bearer\s+|access[_-]?token\s*[=:]\s*|client[_-]?secret\s*[=:]\s*)[^\s,;]+"
)
_ALLOWED_RESULT = frozenset({"SUCCEEDED", "FAILED", "UNCHANGED"})
SUBMISSION_JOB_TYPE = "otto.submit_product"


class SubmissionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def enqueue_submission_job(
    conn: Connection,
    *,
    account: str,
    item_id: int,
    operation_id: str,
    revision_id: str,
    payload_hash: str,
    expected_operation_version: int,
) -> dict[str, Any]:
    """Atomically enqueue one product POST for an explicitly released Drip item."""
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise SubmissionError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    try:
        operation_uuid, revision_uuid = str(UUID(str(operation_id))), str(UUID(str(revision_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Operation and revision IDs must be UUIDs.", status_code=422) from None
    if type(item_id) is not int or item_id < 1 or type(expected_operation_version) is not int or expected_operation_version < 1:
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Item ID and expected version are invalid.", status_code=422)
    if not isinstance(payload_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_hash.strip()):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Approved payload hash is invalid.", status_code=422)
    item = conn.execute(
        text("SELECT i.status,i.operation_id,i.revision_id,i.payload_hash,b.status AS batch_status,o.version AS operation_version "
             "FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id AND b.account=i.account "
             "JOIN restart_operations o ON o.id=i.operation_id AND o.account=i.account AND o.data_scope='PRODUCTION' "
             "WHERE i.id=:item_id AND i.account=:account AND i.data_scope='PRODUCTION' FOR UPDATE OF i,b,o"),
        {"item_id": item_id, "account": normalized_account},
    ).mappings().first()
    if (
        item is None or item["status"] != "READY_TO_SUBMIT" or item["batch_status"] not in {"ACTIVE", "SCHEDULED"}
        or str(item["operation_id"]) != operation_uuid or str(item["revision_id"]) != revision_uuid
        or str(item["payload_hash"]).strip() != payload_hash.strip()
        or int(item["operation_version"]) != expected_operation_version
    ):
        raise SubmissionError("DRIP_ITEM_NOT_READY", "Only the exact released item revision can be queued for product submission.")
    job = enqueue_job(
        conn,
        account=normalized_account,
        job_type=SUBMISSION_JOB_TYPE,
        idempotency_key=f"otto-submit:{item_id}:{revision_uuid}",
        payload={
            "itemId": item_id,
            "operationId": operation_uuid,
            "revisionId": revision_uuid,
            "payloadHash": payload_hash.strip(),
            "expectedOperationVersion": expected_operation_version,
        },
        max_attempts=5,
    )
    if job.created:
        _record_event(
            conn, account=normalized_account, actor="system:drip-release",
            event_type="OTTO_SUBMISSION_JOB_ENQUEUED", entity_type="RESTART_OPERATION",
            entity_id=operation_uuid, summary="Single-SKU product submission job queued after scheduled release",
            metadata={"jobId": job.job.id, "itemId": item_id, "revisionId": revision_uuid,
                      "payloadHash": payload_hash.strip(), "published": False},
        )
    return {"jobId": job.job.id, "created": job.created}


def _approved_product_payload(
    payload: Mapping[str, Any],
    *,
    account: str,
    source_snapshot: Mapping[str, Any],
    source_sku: str,
    target_sku: str,
    target_ean: str,
) -> dict[str, Any]:
    gaps = payload.get("readinessGaps")
    identity = payload.get("targetIdentity")
    product_payload = payload.get("ottoProductPayload")
    references = source_snapshot.get("references")
    reference_ids: set[str] = set()
    if isinstance(references, list):
        for ref in references:
            if not isinstance(ref, Mapping) or not isinstance(ref.get("resource"), str):
                continue
            resource = ref["resource"]
            reference_ids.add(resource)
            system = ref.get("system")
            if isinstance(system, str) and system.strip():
                reference_ids.add(f"{system.strip()}:{resource}")
    if (
        payload.get("account") != account
        or payload.get("sourceSku") != source_sku
        or payload.get("targetSku") != target_sku
        or payload.get("newEan") != target_ean
        or not isinstance(source_snapshot.get("account"), str)
        or source_snapshot.get("account") != account
        or source_snapshot.get("sourceSku") != source_sku
        or not isinstance(gaps, list)
        or gaps
        or not isinstance(identity, Mapping)
        or identity.get("verified") is not True
        or identity.get("account") != account
        or identity.get("sourceSku") != source_sku
        or identity.get("targetSku") != target_sku
        or target_sku != target_ean
        or identity.get("newEan") != target_ean
        or identity.get("productReference") != target_ean
        or not isinstance(identity.get("sourceRef"), str)
        or identity.get("sourceRef") not in reference_ids
        or not isinstance(product_payload, Mapping)
    ):
        raise SubmissionError("TARGET_IDENTITY_UNVERIFIED", "Approved revision lacks a verified account-scoped target identity and OTTO payload.")
    product_reference = product_payload.get("productReference")
    if (
        product_payload.get("sku") != target_sku
        or product_payload.get("ean") != target_ean
        or not re.fullmatch(r"[0-9]{13}", target_ean)
        or product_reference != target_ean
        or not isinstance(product_reference, str)
        or not 1 <= len(product_reference.strip()) <= 50
        or product_reference != product_reference.strip()
        or identity.get("productReference") != product_reference
        or "account" in product_payload
    ):
        raise SubmissionError("OTTO_PRODUCT_PAYLOAD_UNVERIFIED", "Immutable OTTO payload SKU, EAN, productReference, or account is missing or inconsistent.")
    product_description = product_payload.get("productDescription")
    product_line = product_description.get("productLine") if isinstance(product_description, Mapping) else None
    try:
        validated_title = validate_prepared_title(product_line)
    except PreparedTitleError as error:
        raise SubmissionError(error.code, str(error)) from None
    if validated_title != normalize_title_whitespace(str(payload.get("title") or "")):
        raise SubmissionError("TITLE_PAYLOAD_MISMATCH", "The approved title and OTTO productLine do not match.")
    return dict(product_payload)


def get_submission_attempt_for_job(
    conn: Connection, *, account: str, job_id: str
) -> dict[str, Any] | None:
    normalized_account = str(account or "").strip().upper()
    try:
        job_uuid = str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Job ID must be a UUID.", status_code=422) from None
    row = conn.execute(
        text("SELECT id,item_id,operation_id,revision_id,status,error_code,payload_hash FROM submission_attempts "
             "WHERE background_job_id=:job_id AND account=:account AND data_scope='PRODUCTION'"),
        {"job_id": job_uuid, "account": normalized_account},
    ).mappings().first()
    if row is None:
        return None
    return {"attemptId": str(row["id"]), "itemId": int(row["item_id"]),
            "operationId": str(row["operation_id"]), "revisionId": str(row["revision_id"]),
            "status": row["status"], "errorCode": row["error_code"],
            "payloadHash": row["payload_hash"].strip()}


def load_submission_preflight_context(
    conn: Connection, *, account: str, job: Mapping[str, Any]
) -> dict[str, Any]:
    normalized_account = str(account or "").strip().upper()
    try:
        operation_id = str(UUID(str(job.get("operationId"))))
        revision_id = str(UUID(str(job.get("revisionId"))))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Operation and revision IDs must be UUIDs.", status_code=422) from None
    item_id, expected_version, expected_hash = job.get("itemId"), job.get("expectedOperationVersion"), job.get("payloadHash")
    if type(item_id) is not int or item_id < 1 or type(expected_version) is not int or expected_version < 1:
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Item ID or operation version is invalid.", status_code=422)
    row = conn.execute(
        text("SELECT i.status AS item_status,i.operation_id,i.revision_id,i.qc_result_id,i.payload_hash AS item_hash,"
             "b.status AS batch_status,o.status AS operation_status,o.submission_state,o.version,o.target_sku,o.new_ean,"
             "o.current_revision_number,r.revision_number,r.payload_json,r.source_snapshot_json,r.price_snapshot_json,"
             "r.payload_hash AS revision_hash,q.id AS latest_qc_id,q.outcome AS qc_outcome,"
             "q.policy_version AS qc_policy_version,q.payload_hash AS qc_payload_hash "
             "FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id AND b.account=i.account "
             "JOIN restart_operations o ON o.id=i.operation_id AND o.account=i.account AND o.data_scope='PRODUCTION' "
             "JOIN operation_revisions r ON r.id=i.revision_id AND r.operation_id=o.id AND r.account=o.account "
             "AND r.data_scope='PRODUCTION' LEFT JOIN LATERAL (SELECT id,outcome,policy_version,payload_hash "
             "FROM qc_results WHERE operation_id=o.id AND revision_id=r.id AND account=o.account "
             "AND data_scope='PRODUCTION' ORDER BY created_at DESC,id DESC LIMIT 1) q ON TRUE "
             "WHERE i.id=:item_id AND i.account=:account AND i.data_scope='PRODUCTION'"),
        {"item_id": item_id, "account": normalized_account},
    ).mappings().first()
    if row is None:
        raise SubmissionError("DRIP_ITEM_NOT_FOUND", "Production Drip Feed item was not found.")
    if (
        str(row["operation_id"]) != operation_id or str(row["revision_id"]) != revision_id
        or str(row["item_hash"]).strip() != str(expected_hash or "").strip()
        or str(row["revision_hash"]).strip() != str(expected_hash or "").strip()
    ):
        raise SubmissionError("SUBMISSION_JOB_SCOPE_MISMATCH", "Job does not match the immutable released item revision.")
    if int(row["version"]) != expected_version:
        raise SubmissionError("VERSION_CONFLICT", "Operation changed after the Drip item was released.")
    if row["batch_status"] not in {"ACTIVE", "SCHEDULED"}:
        raise SubmissionError("DRIP_BATCH_NOT_ACTIVE", "The Drip Feed batch is not active.")
    if row["item_status"] != "READY_TO_SUBMIT" or row["operation_status"] != "READY" or row["submission_state"] != "NEVER_SUBMITTED":
        raise SubmissionError("DRIP_ITEM_NOT_READY", "The queue item or operation is no longer ready for its first submission.")
    if (
        int(row["revision_number"]) != int(row["current_revision_number"])
        or str(row["qc_result_id"] or "") != str(row["latest_qc_id"] or "")
        or row["qc_outcome"] != "PASSED_AUTO"
        or row["qc_policy_version"] != QC_POLICY_VERSION
        or str(row["qc_payload_hash"] or "").strip() != str(row["revision_hash"]).strip()
    ):
        raise SubmissionError("STALE_QC_OR_REVISION", "Submission requires a current auto-passed QC revision.")
    payload, source_snapshot = _object(row["payload_json"]), _object(row["source_snapshot_json"])
    if payload is None or source_snapshot is None:
        raise SubmissionError("SUBMISSION_SNAPSHOT_INCOMPLETE", "Immutable product or source snapshot is missing.")
    source_sku = str(source_snapshot.get("sourceSku") or "").strip()
    target_sku = str(row["target_sku"] or "").strip()
    target_ean = str(row["new_ean"] or "").strip()
    product_payload = _approved_product_payload(
        payload, account=normalized_account, source_snapshot=source_snapshot,
        source_sku=source_sku, target_sku=target_sku, target_ean=target_ean,
    )
    source_type = str(source_snapshot.get("sourceType") or "")
    aftercool_identity = source_snapshot.get("aftercoolIdentity")
    aftercool_identity = aftercool_identity if isinstance(aftercool_identity, Mapping) else {}
    return {"account": normalized_account, "itemId": item_id, "operationId": operation_id,
            "revisionId": revision_id, "expectedOperationVersion": expected_version,
            "sourceSku": source_sku, "targetSku": target_sku, "targetEan": target_ean,
            "sourceType": source_type, "sourceEan": str(source_snapshot.get("sourceEan") or ""),
            "factoryId": aftercool_identity.get("factory_id"),
            "aftercoolProductId": aftercool_identity.get("product_id") or aftercool_identity.get("aftercool_product_id"),
            "priceSnapshot": _object(row["price_snapshot_json"]),
            "payloadHash": str(expected_hash).strip(), "ottoProductPayload": product_payload}


def load_prepared_attempt_context(
    conn: Connection, *, account: str, attempt_id: str, job_id: str
) -> dict[str, Any]:
    """Reload an un-dispatched attempt after worker redelivery without changing it."""
    normalized_account = str(account or "").strip().upper()
    try:
        attempt_uuid, job_uuid = str(UUID(str(attempt_id))), str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Attempt and job IDs must be UUIDs.", status_code=422) from None
    row = conn.execute(
        text("SELECT a.id,a.status,a.background_job_id,a.operation_id,a.revision_id,a.item_id,a.payload_hash,"
             "a.target_sku,a.target_ean,a.source_price_cents,a.source_price_currency,"
             "i.status AS item_status,i.qc_result_id,b.status AS batch_status,o.status AS operation_status,o.submission_state,"
             "r.payload_json,r.source_snapshot_json,r.price_snapshot_json,r.payload_hash AS revision_hash,"
             "o.current_revision_number,r.revision_number,q.id AS latest_qc_id,q.outcome AS qc_outcome,"
             "q.policy_version AS qc_policy_version,q.payload_hash AS qc_payload_hash,"
             "o.target_sku AS current_target_sku,"
             "o.new_ean AS current_target_ean FROM submission_attempts a "
             "JOIN restart_items i ON i.id=a.item_id AND i.account=a.account AND i.data_scope='PRODUCTION' "
             "JOIN restart_batches b ON b.id=i.batch_id AND b.account=i.account AND b.data_scope='PRODUCTION' "
             "JOIN restart_operations o ON o.id=a.operation_id AND o.account=a.account AND o.data_scope='PRODUCTION' "
             "JOIN operation_revisions r ON r.id=a.revision_id AND r.operation_id=o.id AND r.account=o.account "
             "AND r.data_scope='PRODUCTION' LEFT JOIN LATERAL (SELECT id,outcome,policy_version,payload_hash "
             "FROM qc_results WHERE operation_id=o.id AND revision_id=r.id AND account=o.account "
             "AND data_scope='PRODUCTION' ORDER BY created_at DESC,id DESC LIMIT 1) q ON TRUE "
             "WHERE a.id=:attempt_id AND a.account=:account "
             "AND a.data_scope='PRODUCTION' FOR UPDATE OF a,i,b,o"),
        {"attempt_id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if row is None:
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_FOUND", "Submission attempt was not found.")
    if str(row["background_job_id"] or "") != job_uuid or row["status"] != "PREPARED":
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_RESUMABLE", "Only this job's untouched prepared attempt may resume.")
    if (
        row["item_status"] != "SUBMITTING" or row["operation_status"] != "SUBMITTING"
        or row["submission_state"] != "IN_FLIGHT" or row["batch_status"] not in {"ACTIVE", "SCHEDULED"}
        or str(row["current_target_sku"] or "") != str(row["target_sku"] or "")
        or str(row["current_target_ean"] or "") != str(row["target_ean"] or "")
        or int(row["revision_number"]) != int(row["current_revision_number"])
        or str(row["revision_hash"]).strip() != str(row["payload_hash"]).strip()
        or str(row["qc_result_id"] or "") != str(row["latest_qc_id"] or "")
        or row["qc_outcome"] != "PASSED_AUTO"
        or row["qc_policy_version"] != QC_POLICY_VERSION
        or str(row["qc_payload_hash"] or "").strip() != str(row["revision_hash"]).strip()
    ):
        raise SubmissionError("SUBMISSION_ATTEMPT_SCOPE_MISMATCH", "Prepared attempt no longer matches its operation snapshot.")
    payload = _object(row["payload_json"])
    source_snapshot = _object(row["source_snapshot_json"])
    price_snapshot = _object(row["price_snapshot_json"])
    if (
        payload is None or source_snapshot is None or price_snapshot is None
        or _canonical_hash(payload) != str(row["payload_hash"]).strip()
    ):
        raise SubmissionError("SUBMISSION_SNAPSHOT_INCOMPLETE", "Prepared product snapshot is missing or changed.")
    source_sku = str(source_snapshot.get("sourceSku") or "").strip()
    product_payload = _approved_product_payload(
        payload, account=normalized_account, source_snapshot=source_snapshot,
        source_sku=source_sku, target_sku=str(row["target_sku"]), target_ean=str(row["target_ean"]),
    )
    aftercool_identity = source_snapshot.get("aftercoolIdentity")
    aftercool_identity = aftercool_identity if isinstance(aftercool_identity, Mapping) else {}
    return {"attemptId": attempt_uuid, "jobId": job_uuid, "account": normalized_account,
            "sourceSku": source_sku, "targetSku": str(row["target_sku"]), "targetEan": str(row["target_ean"]),
            "sourceType": str(source_snapshot.get("sourceType") or ""),
            "sourceEan": str(source_snapshot.get("sourceEan") or ""),
            "factoryId": aftercool_identity.get("factory_id"),
            "aftercoolProductId": aftercool_identity.get("product_id") or aftercool_identity.get("aftercool_product_id"),
            "payloadHash": str(row["payload_hash"]).strip(), "sourcePriceCents": int(row["source_price_cents"]),
            "sourcePriceCurrency": str(row["source_price_currency"]), "priceSnapshot": price_snapshot,
            "ottoProductPayload": product_payload}


def _object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return dict(decoded) if isinstance(decoded, Mapping) else None
    return None


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise SubmissionError("PAYLOAD_INVALID", "Immutable product payload is not valid JSON.", status_code=422) from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_task_links(response: Mapping[str, Any]) -> tuple[list[dict[str, str]], bool]:
    raw_links = response.get("taskLinks", response.get("links", []))
    if isinstance(raw_links, Mapping):
        raw_links = [raw_links]
    if not isinstance(raw_links, list):
        return [], bool(raw_links)
    accepted: list[dict[str, str]] = []
    invalid = False
    for link in raw_links:
        if not isinstance(link, Mapping):
            invalid = True
            continue
        rel, href = link.get("rel"), link.get("href")
        if not isinstance(rel, str) or rel not in {"self", "succeeded", "failed", "unchanged"}:
            invalid = True
            continue
        if not isinstance(href, str) or not href.strip() or href.startswith("//"):
            invalid = True
            continue
        parts = urlsplit(href)
        try:
            port = parts.port
        except ValueError:
            invalid = True
            continue
        if parts.scheme or parts.netloc:
            if parts.scheme != "https" or parts.hostname != "api.otto.market" or port not in (None, 443):
                invalid = True
                continue
        elif not href.startswith("/"):
            invalid = True
            continue
        if parts.username or parts.password or not _TASK_PATH.fullmatch(parts.path):
            invalid = True
            continue
        # Persist only the validated path. Query strings, fragments, and any
        # unexpected URL credentials never enter the audit record.
        accepted.append({"rel": rel, "href": parts.path})
    return accepted, invalid


def _safe_sku_results(response: Mapping[str, Any], *, target_sku: str) -> tuple[list[dict[str, Any]], str | None]:
    raw_results = response.get("perSkuResults", [])
    if not isinstance(raw_results, list):
        return [], "PER_SKU_RESULTS_INVALID"
    results: list[dict[str, Any]] = []
    target_rows = 0
    mismatched_rows = False
    for row in raw_results:
        if not isinstance(row, Mapping):
            return [], "PER_SKU_RESULTS_INVALID"
        sku, result = row.get("sku"), row.get("result")
        if not isinstance(sku, str) or not isinstance(result, str):
            return [], "PER_SKU_RESULTS_INVALID"
        if sku != target_sku:
            mismatched_rows = True
            continue
        target_rows += 1
        normalized = result.strip().upper()
        if normalized not in _ALLOWED_RESULT:
            return [], "PER_SKU_RESULTS_INVALID"
        safe_errors = []
        errors = row.get("errors", [])
        if isinstance(errors, list):
            for error in errors:
                if not isinstance(error, Mapping):
                    continue
                code = error.get("code")
                title = error.get("title")
                safe_error: dict[str, str] = {}
                if isinstance(code, str) and _SAFE_CODE.fullmatch(code.strip()):
                    safe_error["code"] = code.strip()
                if isinstance(title, str) and title.strip():
                    normalized_title = " ".join(title.split())
                    safe_error["title"] = _SECRET_TEXT.sub(r"\1[REDACTED]", normalized_title)[:500]
                if safe_error:
                    safe_errors.append(safe_error)
        results.append({"sku": sku, "result": normalized, "errors": safe_errors})
    if target_rows > 1 or (mismatched_rows and target_rows == 0):
        return [], "TARGET_SKU_RESULT_MISMATCH"
    return results, None


def classify_submission_response(
    *,
    http_status: int | None,
    target_sku: str,
    response: Mapping[str, Any] | None = None,
    transport_error: str | None = None,
) -> dict[str, Any]:
    """Classify an already obtained normalized response; this function has no I/O."""
    body = response if isinstance(response, Mapping) else {}
    links, invalid_links = _safe_task_links(body)
    results, result_error = _safe_sku_results(body, target_sku=target_sku)
    if transport_error:
        return {"status": "UNKNOWN", "errorCode": "OTTO_TRANSPORT_UNCERTAIN", "taskLinks": [], "perSkuResults": []}
    if type(http_status) is not int or not 100 <= http_status <= 599:
        return {"status": "UNKNOWN", "errorCode": "OTTO_RESPONSE_UNCERTAIN", "taskLinks": links, "perSkuResults": results}
    if http_status == 202:
        code = "TASK_LINK_UNTRUSTED" if invalid_links else ("TASK_LINK_MISSING" if not links else None)
        return {"status": "ACCEPTED", "errorCode": code, "taskLinks": links, "perSkuResults": results}
    if http_status == 429:
        return {"status": "REJECTED", "errorCode": "OTTO_HTTP_429", "taskLinks": [], "perSkuResults": []}
    if http_status == 408 or http_status >= 500 or 300 <= http_status < 400:
        return {"status": "UNKNOWN", "errorCode": f"OTTO_HTTP_{http_status}", "taskLinks": links, "perSkuResults": results}
    if 200 <= http_status < 300:
        if result_error:
            return {"status": "UNKNOWN", "errorCode": result_error, "taskLinks": links, "perSkuResults": results}
        target_result = results[0]["result"] if results else None
        if target_result == "SUCCEEDED":
            return {"status": "ACCEPTED", "errorCode": "PROCESSING_EVIDENCE_PENDING", "taskLinks": links, "perSkuResults": results}
        if target_result == "FAILED":
            return {"status": "REJECTED", "errorCode": "OTTO_SKU_REJECTED", "taskLinks": links, "perSkuResults": results}
        return {"status": "UNKNOWN", "errorCode": "PER_SKU_RESULT_REQUIRED", "taskLinks": links, "perSkuResults": results}
    if 400 <= http_status < 500:
        return {"status": "REJECTED", "errorCode": f"OTTO_HTTP_{http_status}", "taskLinks": [], "perSkuResults": []}
    return {"status": "UNKNOWN", "errorCode": "OTTO_RESPONSE_UNCERTAIN", "taskLinks": links, "perSkuResults": results}


def begin_submission_attempt(
    conn: Connection,
    *,
    account: str,
    item_id: int,
    background_job_id: str,
    expected_operation_version: int,
    source_price_account: str,
    source_price_response: Mapping[str, Any],
    aftercool_price_cents: int | None = None,
    source_price_checked_at: datetime,
    actor: str,
    approved_evidence_types: Iterable[str],
) -> dict[str, Any]:
    """Persist one immutable, one-SKU attempt before a future adapter may send it.

    Caller must supply fresh, account-scoped source prices. No network
    request is performed here. Existing retries remain technical attempts on
    the same operation, target EAN, immutable revision, and payload hash.
    """
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise SubmissionError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    if type(item_id) is not int or item_id < 1:
        raise SubmissionError("INVALID_ITEM_ID", "Drip Feed item ID must be a positive integer.", status_code=422)
    try:
        job_uuid = str(UUID(str(background_job_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Background job ID must be a UUID.", status_code=422) from None
    if not isinstance(actor, str) or not actor.strip():
        raise SubmissionError("ACTOR_REQUIRED", "A trusted actor is required.", status_code=403)
    if source_price_account != normalized_account:
        raise SubmissionError("ACCOUNT_MISMATCH", "The current source price belongs to a different OTTO account.", status_code=409)
    if not isinstance(source_price_checked_at, datetime) or source_price_checked_at.tzinfo is None:
        raise SubmissionError("SOURCE_PRICE_UNVERIFIED", "A timezone-aware source price check time is required.", status_code=422)

    row = conn.execute(
        text(
            "SELECT i.id AS item_id,i.account,i.status AS item_status,i.operation_id,i.revision_id,i.qc_result_id,"
            "i.payload_hash AS item_hash,b.status AS batch_status,o.status AS operation_status,o.submission_state,"
            "o.version,o.current_revision_number,o.target_sku,o.new_ean,o.source_product_id,"
            "r.id AS current_revision_id,r.payload_hash AS revision_hash,r.payload_json,r.source_snapshot_json,"
            "r.price_snapshot_json,q.id AS latest_qc_id,q.outcome AS qc_outcome,"
            "q.policy_version AS qc_policy_version,q.payload_hash AS qc_hash "
            "FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id AND b.account=i.account "
            "JOIN restart_operations o ON o.id=i.operation_id AND o.account=i.account AND o.data_scope='PRODUCTION' "
            "JOIN operation_revisions r ON r.operation_id=o.id AND r.account=o.account AND r.data_scope='PRODUCTION' "
            "AND r.revision_number=o.current_revision_number "
            "LEFT JOIN LATERAL (SELECT id,outcome,policy_version,payload_hash FROM qc_results WHERE operation_id=o.id "
            "AND revision_id=r.id AND account=o.account AND data_scope='PRODUCTION' "
            "ORDER BY created_at DESC,id DESC LIMIT 1) q ON TRUE "
            "WHERE i.id=:item_id AND i.account=:account AND i.data_scope='PRODUCTION' FOR UPDATE OF i,b,o"
        ),
        {"item_id": item_id, "account": normalized_account},
    ).mappings().first()
    if row is None:
        raise SubmissionError("DRIP_ITEM_NOT_FOUND", "Production Drip Feed item was not found.")
    if row["batch_status"] not in {"ACTIVE", "SCHEDULED"}:
        raise SubmissionError("DRIP_BATCH_NOT_ACTIVE", "The Drip Feed batch is not active.")
    if row["item_status"] != "READY_TO_SUBMIT":
        raise SubmissionError("DRIP_ITEM_NOT_READY", "The queue item is not ready for submission.")
    if row["operation_status"] != "READY" or row["submission_state"] != "NEVER_SUBMITTED":
        raise SubmissionError("OPERATION_NOT_READY", "The linked operation is not an untouched READY operation.")
    if int(row["version"]) != expected_operation_version:
        raise SubmissionError("VERSION_CONFLICT", "Operation changed since the submission request was prepared.")
    if (
        str(row["revision_id"]) != str(row["current_revision_id"])
        or str(row["qc_result_id"]) != str(row["latest_qc_id"] or "")
        or row["qc_outcome"] != "PASSED_AUTO"
        or row["qc_policy_version"] != QC_POLICY_VERSION
        or str(row["qc_hash"] or "").strip() != str(row["revision_hash"]).strip()
        or str(row["item_hash"]).strip() != str(row["revision_hash"]).strip()
    ):
        raise SubmissionError("STALE_QC_OR_REVISION", "Queue item no longer matches the current auto-passed revision.")

    payload = _object(row["payload_json"])
    source_snapshot = _object(row["source_snapshot_json"])
    price_snapshot = _object(row["price_snapshot_json"])
    if not payload or not source_snapshot or not price_snapshot:
        raise SubmissionError("SUBMISSION_SNAPSHOT_INCOMPLETE", "The immutable payload or its source/price evidence is incomplete.")
    expected_hash = str(row["revision_hash"]).strip()
    if _canonical_hash(payload) != expected_hash:
        raise SubmissionError("PAYLOAD_HASH_MISMATCH", "The immutable payload does not match its stored hash.")
    source_sku = str(source_snapshot.get("sourceSku") or "").strip()
    target_sku = str(row["target_sku"] or "").strip()
    target_ean = str(row["new_ean"] or "").strip()
    if (
        not source_sku or not target_sku or not target_ean
        or payload.get("account") != normalized_account
        or payload.get("sourceSku") != source_sku
        or payload.get("targetSku") != target_sku
        or payload.get("newEan") != target_ean
    ):
        raise SubmissionError("TARGET_IDENTITY_MISMATCH", "The single target SKU/EAN/account does not match the approved revision.")
    product_payload = _approved_product_payload(
        payload, account=normalized_account, source_snapshot=source_snapshot,
        source_sku=source_sku, target_sku=target_sku, target_ean=target_ean,
    )
    try:
        op_id = str(UUID(str(row["operation_id"])))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("OPERATION_ID_INVALID", "Linked operation identity is invalid.") from None
    job = conn.execute(
        text("SELECT id,payload_json,idempotency_key FROM background_jobs "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
             "AND job_type=:job_type FOR SHARE"),
        {"id": job_uuid, "account": normalized_account, "job_type": SUBMISSION_JOB_TYPE},
    ).mappings().first()
    job_payload = _object(job["payload_json"]) if job else None
    if (
        job is None or job_payload is None
        or job["idempotency_key"] != f"otto-submit:{item_id}:{row['current_revision_id']}"
        or job_payload.get("itemId") != item_id
        or job_payload.get("operationId") != op_id
        or job_payload.get("revisionId") != str(row["current_revision_id"])
        or job_payload.get("payloadHash") != expected_hash
        or job_payload.get("expectedOperationVersion") != expected_operation_version
    ):
        raise SubmissionError("SUBMISSION_JOB_NOT_FOUND", "A matching durable submission job is required.")
    if not isinstance(source_price_response, Mapping) or source_price_response.get("sku") != source_sku:
        raise SubmissionError("SOURCE_SKU_MISMATCH", "Current standard price does not confirm the original source SKU.")
    source_type = str(source_snapshot.get("sourceType") or "")
    try:
        current_price = price_snapshot_from_sources(
            normalized_account, source_price_response, source_type=source_type,
            aftercool_price_cents=aftercool_price_cents,
        )
    except PreparationError as error:
        raise SubmissionError(error.code, str(error)) from None
    for field in (
        "currency", "standardPriceAmount", "ottoStandardPriceCents", "aftercoolStartPriceCents", "originalPriceCents",
        "selectedSource", "discountCents", "discountedPriceCents", "targetPriceCents", "targetFractionCents",
        "uvpPriceCents", "uvpFormula", "formula",
    ):
        if price_snapshot.get(field) != current_price.get(field):
            raise SubmissionError("SOURCE_PRICE_CHANGED", "A current source price no longer matches the immutable revision.")

    try:
        state = begin_submission(
            conn,
            account=normalized_account,
            operation_id=op_id,
            expected_version=expected_operation_version,
            approved_evidence_types=approved_evidence_types,
        )
    except OperationError as error:
        raise SubmissionError(error.code, str(error), status_code=error.status_code if hasattr(error, "status_code") else 409) from None

    locked_item = conn.execute(
        text("SELECT status,operation_id,revision_id,qc_result_id,payload_hash FROM restart_items "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": item_id, "account": normalized_account},
    ).mappings().first()
    if (
        locked_item is None or locked_item["status"] != "READY_TO_SUBMIT"
        or str(locked_item["operation_id"]) != op_id
        or str(locked_item["revision_id"]) != str(row["current_revision_id"])
        or str(locked_item["qc_result_id"]) != str(row["latest_qc_id"])
        or str(locked_item["payload_hash"]).strip() != expected_hash
    ):
        raise SubmissionError("DRIP_ITEM_CHANGED", "Queue item changed while submission was being prepared.")
    attempt_number = int(conn.execute(
        text("SELECT COALESCE(MAX(attempt_number),0)+1 FROM submission_attempts "
             "WHERE operation_id=:operation_id AND account=:account AND data_scope='PRODUCTION'"),
        {"operation_id": op_id, "account": normalized_account},
    ).scalar_one())
    attempt_id = str(uuid4())
    checked_at = source_price_checked_at.astimezone(timezone.utc)
    conn.execute(
        text(
            "INSERT INTO submission_attempts(id,account,data_scope,operation_id,revision_id,item_id,attempt_number,"
            "source_sku,target_sku,target_ean,payload_hash,source_price_cents,source_price_currency,"
            "source_price_checked_at,background_job_id,status,created_by,started_at) VALUES (:id,:account,'PRODUCTION',:operation_id,"
            ":revision_id,:item_id,:attempt_number,:source_sku,:target_sku,:target_ean,:payload_hash,:price_cents,"
            "'EUR',:checked_at,:background_job_id,'PREPARED',:actor,CURRENT_TIMESTAMP)"
        ),
        {"id": attempt_id, "account": normalized_account, "operation_id": op_id,
         "revision_id": row["current_revision_id"], "item_id": item_id, "attempt_number": attempt_number,
         "source_sku": source_sku, "target_sku": target_sku, "target_ean": target_ean,
         "payload_hash": expected_hash, "price_cents": current_price["originalPriceCents"],
         "background_job_id": job_uuid,
         "checked_at": checked_at, "actor": actor.strip()[:200]},
    )
    conn.execute(
        text("UPDATE restart_items SET status='SUBMITTING',version=version+1,last_error_code=NULL "
             "WHERE id=:id AND account=:account AND status='READY_TO_SUBMIT'"),
        {"id": item_id, "account": normalized_account},
    )
    _record_event(conn, account=normalized_account, actor=actor, event_type="OTTO_SUBMISSION_ATTEMPT_PREPARED",
                  entity_type="RESTART_OPERATION", entity_id=op_id,
                  summary="Single-SKU OTTO submission attempt prepared; no external call was made",
                  metadata={"attemptId": attempt_id, "attemptNumber": attempt_number, "revisionId": str(row["current_revision_id"]), "payloadHash": expected_hash})
    return {"attemptId": attempt_id, "operationId": op_id, "itemId": item_id,
            "attemptNumber": attempt_number, "account": normalized_account,
            "targetSku": target_sku, "targetEan": target_ean, "payloadHash": expected_hash,
            "status": "PREPARED", "operationVersion": state["version"],
            "jobId": job_uuid, "ottoProductPayload": product_payload}


def claim_submission_dispatch(conn: Connection, *, account: str, attempt_id: str, job_id: str) -> dict[str, Any]:
    """Durably claim the sole POST before network I/O; claims are never repeatable.

    The caller must commit this transaction before calling OTTO. If the process
    stops after that commit, recovery must mark the attempt UNKNOWN and
    reconcile with GETs; it must not dispatch the same attempt again.
    """
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise SubmissionError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    try:
        attempt_uuid, job_uuid = str(UUID(str(attempt_id))), str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_ATTEMPT_ID", "Submission attempt ID must be a UUID.", status_code=422) from None

    row = conn.execute(
        text(
            "SELECT a.id,a.operation_id,a.item_id,a.status,a.target_sku,a.target_ean,a.payload_hash,a.background_job_id,"
            "o.status AS operation_status,o.submission_state,o.current_revision_number,i.status AS item_status,"
            "i.qc_result_id,r.revision_number,q.id AS latest_qc_id,q.outcome AS qc_outcome,"
            "q.policy_version AS qc_policy_version,q.payload_hash AS qc_payload_hash "
            "FROM submission_attempts a "
            "JOIN restart_operations o ON o.id=a.operation_id AND o.account=a.account "
            "JOIN restart_items i ON i.id=a.item_id AND i.account=a.account "
            "JOIN operation_revisions r ON r.id=a.revision_id AND r.operation_id=o.id AND r.account=o.account "
            "AND r.data_scope='PRODUCTION' LEFT JOIN LATERAL (SELECT id,outcome,policy_version,payload_hash "
            "FROM qc_results WHERE operation_id=o.id AND revision_id=r.id AND account=o.account "
            "AND data_scope='PRODUCTION' ORDER BY created_at DESC,id DESC LIMIT 1) q ON TRUE "
            "WHERE a.id=:id AND a.account=:account AND a.data_scope='PRODUCTION' FOR UPDATE OF a,o,i"
        ),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if row is None:
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_FOUND", "Submission attempt was not found.")
    if str(row["background_job_id"] or "") != job_uuid:
        raise SubmissionError("SUBMISSION_JOB_SCOPE_MISMATCH", "Job does not own this submission attempt.")
    if row["status"] != "PREPARED":
        raise SubmissionError(
            "SUBMISSION_DISPATCH_ALREADY_CLAIMED",
            "This attempt cannot be dispatched again; reconcile its recorded state.",
        )
    if (
        row["operation_status"] != "SUBMITTING"
        or row["submission_state"] != "IN_FLIGHT"
        or row["item_status"] != "SUBMITTING"
        or int(row["revision_number"]) != int(row["current_revision_number"])
        or str(row["qc_result_id"] or "") != str(row["latest_qc_id"] or "")
        or row["qc_outcome"] != "PASSED_AUTO"
        or row["qc_policy_version"] != QC_POLICY_VERSION
        or str(row["qc_payload_hash"] or "").strip() != str(row["payload_hash"]).strip()
    ):
        raise SubmissionError("SUBMISSION_DISPATCH_STATE_INVALID", "Attempt, operation, and queue item are not in the expected pre-dispatch state.")

    conn.execute(
        text("UPDATE submission_attempts SET status='DISPATCHING',dispatch_started_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account AND status='PREPARED'"),
        {"id": attempt_uuid, "account": normalized_account},
    )
    _record_event(
        conn,
        account=normalized_account,
        actor="system:otto-submission",
        event_type="OTTO_SUBMISSION_DISPATCH_CLAIMED",
        entity_type="RESTART_OPERATION",
        entity_id=str(row["operation_id"]),
        summary="Single-SKU OTTO dispatch fence committed before network I/O",
        metadata={"attemptId": attempt_uuid, "targetSku": row["target_sku"], "payloadHash": row["payload_hash"]},
    )
    return {
        "attemptId": attempt_uuid,
        "operationId": str(row["operation_id"]),
        "itemId": int(row["item_id"]),
        "account": normalized_account,
        "targetSku": row["target_sku"],
        "targetEan": row["target_ean"],
        "payloadHash": row["payload_hash"].strip(),
        "status": "DISPATCHING",
    }


def mark_interrupted_dispatch_unknown(conn: Connection, *, account: str, attempt_id: str) -> dict[str, Any]:
    """Move an abandoned dispatch to reconciliation without retrying or freeing GTIN."""
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise SubmissionError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    try:
        attempt_uuid = str(UUID(str(attempt_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_ATTEMPT_ID", "Submission attempt ID must be a UUID.", status_code=422) from None
    attempt = conn.execute(
        text("SELECT * FROM submission_attempts WHERE id=:id AND account=:account "
             "AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None:
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_FOUND", "Submission attempt was not found.")
    if attempt["status"] in {"ACCEPTED", "REJECTED", "UNKNOWN"}:
        return {"attemptId": attempt_uuid, "status": attempt["status"],
                "operationStatus": "RECONCILIATION_REQUIRED" if attempt["status"] == "UNKNOWN" else None,
                "idempotentReplay": True}
    if attempt["status"] != "DISPATCHING":
        raise SubmissionError("SUBMISSION_DISPATCH_NOT_INTERRUPTED", "Only an unresolved dispatch can be moved to reconciliation.")
    if attempt["http_status"] is not None or attempt["task_links_json"] or attempt["per_sku_results_json"]:
        raise SubmissionError("SUBMISSION_DISPATCH_HAS_RESULT", "An attempt with a recorded response cannot be treated as interrupted.")

    conn.execute(
        text("UPDATE submission_attempts SET status='UNKNOWN',error_code='DISPATCH_RESULT_MISSING',"
             "completed_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"),
        {"id": attempt_uuid, "account": normalized_account},
    )
    try:
        mark_submission_unknown(conn, account=normalized_account, operation_id=str(attempt["operation_id"]))
    except OperationError as error:
        raise SubmissionError(error.code, str(error)) from None
    conn.execute(
        text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code='DISPATCH_RESULT_MISSING',"
             "version=version+1 WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
        {"id": attempt["item_id"], "account": normalized_account},
    )
    _record_event(
        conn,
        account=normalized_account,
        actor="system:otto-submission-recovery",
        event_type="OTTO_SUBMISSION_DISPATCH_INTERRUPTED",
        entity_type="RESTART_OPERATION",
        entity_id=str(attempt["operation_id"]),
        summary="Interrupted OTTO dispatch requires reconciliation; automatic resubmission is prohibited",
        metadata={"attemptId": attempt_uuid, "errorCode": "DISPATCH_RESULT_MISSING", "published": False},
    )
    return {"attemptId": attempt_uuid, "status": "UNKNOWN", "operationStatus": "RECONCILIATION_REQUIRED", "idempotentReplay": False}


def reject_prepared_attempt_for_review(
    conn: Connection, *, account: str, attempt_id: str, job_id: str, error_code: str
) -> dict[str, Any]:
    """Close a definitely unsent prepared attempt when its source snapshot changed."""
    normalized_account = str(account or "").strip().upper()
    try:
        attempt_uuid, job_uuid = str(UUID(str(attempt_id))), str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_SUBMISSION_JOB", "Attempt and job IDs must be UUIDs.", status_code=422) from None
    if not isinstance(error_code, str) or not _SAFE_CODE.fullmatch(error_code):
        raise SubmissionError("INVALID_SUBMISSION_ERROR", "A stable safe error code is required.", status_code=422)
    attempt = conn.execute(
        text("SELECT a.operation_id,a.item_id,a.status,a.background_job_id FROM submission_attempts a "
             "WHERE a.id=:id AND a.account=:account AND a.data_scope='PRODUCTION' FOR UPDATE"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None:
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_FOUND", "Submission attempt was not found.")
    if (
        str(attempt["background_job_id"] or "") != job_uuid
        or attempt["status"] != "PREPARED"
    ):
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_PREPARED", "Only this job's unsent prepared attempt may be rejected.")
    conn.execute(
        text("UPDATE submission_attempts SET status='REJECTED',error_code=:code,completed_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account AND status='PREPARED'"),
        {"code": error_code, "id": attempt_uuid, "account": normalized_account},
    )
    conn.execute(
        text("UPDATE restart_operations SET status='NEEDS_REVIEW',submission_state='NEVER_SUBMITTED', "
             "version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account "
             "AND data_scope='PRODUCTION' AND status='SUBMITTING' AND submission_state='IN_FLIGHT'"),
        {"id": attempt["operation_id"], "account": normalized_account},
    )
    conn.execute(
        text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code=:code,version=version+1 "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' AND status='SUBMITTING'"),
        {"code": error_code, "id": attempt["item_id"], "account": normalized_account},
    )
    _record_event(
        conn, account=normalized_account, actor="system:otto-submission-preflight",
        event_type="OTTO_SUBMISSION_PREPARED_REJECTED", entity_type="RESTART_OPERATION",
        entity_id=str(attempt["operation_id"]),
        summary="Prepared submission was not sent because its source preflight needs review",
        metadata={"attemptId": attempt_uuid, "errorCode": error_code, "published": False},
    )
    return {"attemptId": attempt_uuid, "status": "REJECTED", "errorCode": error_code, "published": False}


def record_submission_result(
    conn: Connection,
    *,
    account: str,
    attempt_id: str,
    http_status: int | None,
    response: Mapping[str, Any] | None = None,
    transport_error: str | None = None,
) -> dict[str, Any]:
    """Persist a normalized HTTP outcome without treating 202/2xx as PUBLISHED."""
    try:
        attempt_uuid = str(UUID(str(attempt_id)))
    except (TypeError, ValueError, AttributeError):
        raise SubmissionError("INVALID_ATTEMPT_ID", "Submission attempt ID must be a UUID.", status_code=422) from None
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise SubmissionError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
        raise SubmissionError("INVALID_HTTP_STATUS", "HTTP status must be between 100 and 599.", status_code=422)
    attempt = conn.execute(
        text("SELECT * FROM submission_attempts WHERE id=:id AND account=:account "
             "AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None:
        raise SubmissionError("SUBMISSION_ATTEMPT_NOT_FOUND", "Submission attempt was not found.")
    result = classify_submission_response(http_status=http_status, target_sku=attempt["target_sku"],
                                          response=response, transport_error=transport_error)
    current_status = attempt["status"]
    if current_status == "UNKNOWN" and attempt["http_status"] == http_status:
        return {"attemptId": attempt_uuid, "status": current_status, "idempotentReplay": True,
                "operationStatus": "RECONCILIATION_REQUIRED"}
    if current_status == "PREPARED":
        raise SubmissionError("SUBMISSION_DISPATCH_NOT_CLAIMED", "The durable dispatch fence must be committed before recording a network outcome.")
    if current_status not in {"DISPATCHING", "UNKNOWN"}:
        if current_status == result["status"] and attempt["http_status"] == http_status:
            return {"attemptId": attempt_uuid, "status": current_status, "idempotentReplay": True,
                    "operationStatus": "OTTO_PROCESSING" if current_status == "ACCEPTED" else "READY" if current_status == "REJECTED" else "RECONCILIATION_REQUIRED"}
        raise SubmissionError("SUBMISSION_RESULT_CONFLICT", "A terminal attempt result cannot be replaced.")

    tasks_json = json.dumps(result["taskLinks"], ensure_ascii=False, separators=(",", ":"))
    sku_json = json.dumps(result["perSkuResults"], ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        text("UPDATE submission_attempts SET status=:status,http_status=:http_status,task_links_json=CAST(:tasks AS jsonb),"
             "per_sku_results_json=CAST(:sku_results AS jsonb),error_code=:error_code,completed_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account"),
        {"status": result["status"], "http_status": http_status, "tasks": tasks_json,
         "sku_results": sku_json, "error_code": result["errorCode"], "id": attempt_uuid, "account": normalized_account},
    )
    op_id = str(attempt["operation_id"])
    item_id = int(attempt["item_id"])
    if result["status"] == "ACCEPTED":
        conn.execute(
            text("UPDATE restart_operations SET submission_state='IN_FLIGHT',status='OTTO_PROCESSING',version=version+1,"
                 "updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
                 "AND submission_state IN ('IN_FLIGHT','UNKNOWN')"),
            {"id": op_id, "account": normalized_account},
        )
        conn.execute(text("UPDATE restart_items SET status='OTTO_PROCESSING',last_error_code=:code,version=version+1 "
                          "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
                     {"code": result["errorCode"], "id": item_id, "account": normalized_account})
        operation_status = "OTTO_PROCESSING"
    elif result["status"] == "REJECTED":
        conn.execute(
            text("UPDATE restart_operations SET submission_state='NEVER_SUBMITTED',status='READY',version=version+1,"
                 "updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
                 "AND submission_state IN ('IN_FLIGHT','UNKNOWN')"),
            {"id": op_id, "account": normalized_account},
        )
        conn.execute(text("UPDATE restart_items SET status='READY_TO_SUBMIT',last_error_code=:code,version=version+1 "
                          "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
                     {"code": result["errorCode"], "id": item_id, "account": normalized_account})
        operation_status = "READY"
    else:
        try:
            mark_submission_unknown(conn, account=normalized_account, operation_id=op_id)
        except OperationError as error:
            raise SubmissionError(error.code, str(error)) from None
        conn.execute(text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code=:code,version=version+1 "
                          "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
                     {"code": result["errorCode"], "id": item_id, "account": normalized_account})
        operation_status = "RECONCILIATION_REQUIRED"
    _record_event(conn, account=normalized_account, actor="system:otto-submission", event_type="OTTO_SUBMISSION_RESULT_RECORDED",
                  entity_type="RESTART_OPERATION", entity_id=op_id,
                  summary="Normalized OTTO submission outcome recorded",
                  metadata={"attemptId": attempt_uuid, "attemptStatus": result["status"], "httpStatus": http_status,
                            "errorCode": result["errorCode"], "published": False})
    processing = None
    if result["status"] == "ACCEPTED" and http_status == 202:
        # A 202 only starts a durable GET poll. The task's counters are not
        # publication proof; S22 reads all three per-SKU result collections.
        from services.otto_processing_service import start_processing_poll

        processing = start_processing_poll(
            conn, account=normalized_account, attempt_id=attempt_uuid,
            task_links=result["taskLinks"],
        )
    elif (
        result["status"] == "ACCEPTED"
        and http_status is not None
        and http_status != 202
        and result["perSkuResults"]
        and result["perSkuResults"][0].get("result") == "SUCCEEDED"
    ):
        # Synchronous success is accepted only when the exact one-SKU result
        # was present and validated by classify_submission_response.
        from services.otto_processing_service import record_synchronous_processing_success

        processing = record_synchronous_processing_success(
            conn, account=normalized_account, attempt_id=attempt_uuid,
        )
        operation_status = "AWAITING_ONLINE"
    return {"attemptId": attempt_uuid, "status": result["status"], "operationStatus": operation_status,
            "httpStatus": http_status, "errorCode": result["errorCode"],
            "taskLinks": result["taskLinks"], "perSkuResults": result["perSkuResults"],
            "processing": processing, "published": False}

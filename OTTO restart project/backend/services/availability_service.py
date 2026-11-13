"""Durable, fail-closed availability attempts for new Restart target SKUs."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.operation_service import _record_event

_SAFE_CODE = re.compile(r"^[A-Z0-9_.-]{1,64}$")
_SECRET_TEXT = re.compile(
    r"(?i)\b(bearer\s+|access[_-]?token\s*[=:]\s*|client[_-]?secret\s*[=:]\s*)[^\s,;]+"
)
_SECRET_KEY = re.compile(r"(?i)(authorization|cookie|token|secret|password)")
_PROCESSING_TIME = re.compile(r"^(?:DEFAULT|[1-9][0-9]?)$")
_ACTIONS = {
    "QUANTITY": "otto.availability.quantity",
    "DELIVERY_INFORMATION": "otto.availability.delivery",
}


class AvailabilityError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_summary_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        parts = urlsplit(cleaned)
        if parts.scheme and parts.netloc:
            cleaned = parts.path
        return _SECRET_TEXT.sub(r"\1[REDACTED]", cleaned)[:500]
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, nested in list(value.items())[:100]:
            if not isinstance(key, str) or _SECRET_KEY.search(key):
                continue
            safe[key[:100]] = _safe_summary_value(nested, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_summary_value(item, depth=depth + 1) for item in list(value)[:100]]
    return "[OMITTED]"


def _validated_fulfillment_payloads(row: Mapping[str, Any], *, account: str) -> dict[str, dict[str, Any]]:
    target_sku = row.get("target_sku")
    if not isinstance(target_sku, str) or not re.fullmatch(r"[0-9]{13}", target_sku):
        raise AvailabilityError("TARGET_SKU_UNVERIFIED", "Availability updates require an explicit target SKU.")
    fulfillment = _object(row.get("fulfillment_snapshot_json"))
    source = _object(row.get("source_snapshot_json"))
    target = fulfillment.get("target")
    references = source.get("references")
    if not isinstance(target, Mapping) or target.get("verified") is not True:
        raise AvailabilityError("TARGET_FULFILLMENT_UNVERIFIED", "Target fulfillment data has no verified source.")
    if (
        target.get("account") != account
        or target.get("targetSku") != target_sku
        or isinstance(target.get("stockQuantity"), bool)
        or not isinstance(target.get("stockQuantity"), int)
        or target.get("stockQuantity") < 0
        or not isinstance(target.get("shippingProfile"), str)
        or not target.get("shippingProfile").strip()
        or not isinstance(target.get("processingTime"), str)
        or not _PROCESSING_TIME.fullmatch(target.get("processingTime", "").strip().upper())
    ):
        raise AvailabilityError("TARGET_FULFILLMENT_INVALID", "Target quantity, profile, processing time, account, or SKU is invalid.")
    try:
        shipping_profile = str(UUID(target["shippingProfile"]))
    except (ValueError, TypeError, AttributeError):
        raise AvailabilityError("SHIPPING_PROFILE_UNVERIFIED", "Shipping profile must be a confirmed UUID.") from None
    source_ref = target.get("sourceRef")
    reference_ids: set[str] = set()
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, Mapping) or not isinstance(reference.get("resource"), str):
                continue
            resource = reference["resource"]
            reference_ids.add(resource)
            system = reference.get("system")
            if isinstance(system, str) and system.strip():
                reference_ids.add(f"{system.strip()}:{resource}")
    if (
        not isinstance(references, list)
        or not isinstance(source_ref, str)
        or source_ref not in reference_ids
    ):
        raise AvailabilityError("FULFILLMENT_SOURCE_UNVERIFIED", "Target fulfillment source is not present in the immutable source snapshot.")

    return {
        "QUANTITY": {"sku": target_sku, "quantity": target["stockQuantity"]},
        "DELIVERY_INFORMATION": {
            "sku": target_sku,
            "shippingProfileId": shipping_profile,
            "processingTime": target["processingTime"].strip().upper(),
        },
    }


def _operation_row(conn: Connection, *, account: str, operation_id: str, lock: bool) -> Mapping[str, Any] | None:
    suffix = " FOR UPDATE OF o, r" if lock else ""
    return conn.execute(
        text(
            "SELECT o.id,o.account,o.status,o.submission_state,o.target_sku,o.processing_evidence_event_id,"
            "o.current_revision_number,r.id AS revision_id,r.payload_hash,r.source_snapshot_json,"
            "r.fulfillment_snapshot_json,e.entity_type AS processing_entity_type,e.entity_id AS processing_entity_id,"
            "e.metadata_json AS processing_metadata "
            "FROM restart_operations o JOIN operation_revisions r ON r.operation_id=o.id AND r.account=o.account "
            "AND r.data_scope=o.data_scope AND r.revision_number=o.current_revision_number "
            "LEFT JOIN restart_events e ON e.id=o.processing_evidence_event_id AND e.account=o.account "
            "AND e.data_scope=o.data_scope "
            "WHERE o.id=:operation_id AND o.account=:account AND o.data_scope='PRODUCTION'" + suffix
        ),
        {"operation_id": operation_id, "account": account},
    ).mappings().first()


def _require_confirmed_processing(row: Mapping[str, Any], *, account: str) -> None:
    metadata = _object(row.get("processing_metadata"))
    if (
        row.get("submission_state") != "CONFIRMED"
        or row.get("status") not in {"AWAITING_ONLINE", "PUBLISHED"}
        or not row.get("processing_evidence_event_id")
        or row.get("processing_entity_type") != "OTTO_PROCESSING_RESULT"
        or str(row.get("processing_entity_id")) != str(row.get("id"))
        or metadata.get("targetSku") != row.get("target_sku")
        or metadata.get("result") != "SUCCEEDED"
        or metadata.get("account") != account
    ):
        raise AvailabilityError("PRODUCT_PROCESSING_NOT_CONFIRMED", "Availability jobs require successful per-SKU processing evidence for this account and target SKU.")


def retry_rejected_availability_step(
    conn: Connection, *, account: str, operation_id: str, action: str, actor: str,
) -> dict[str, Any]:
    """Manually enqueue a bounded retry only after a confirmed non-application."""
    normalized_account = str(account or "").strip().upper()
    normalized_action = str(action or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise AvailabilityError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    if normalized_action not in _ACTIONS:
        raise AvailabilityError("INVALID_AVAILABILITY_ACTION", "Action must be QUANTITY or DELIVERY_INFORMATION.", status_code=422)
    if not isinstance(actor, str) or not actor.strip():
        raise AvailabilityError("ACTOR_REQUIRED", "A trusted operator is required.", status_code=403)
    try:
        operation_uuid = str(UUID(str(operation_id)))
    except (TypeError, ValueError, AttributeError):
        raise AvailabilityError("INVALID_OPERATION_ID", "Operation ID must be a UUID.", status_code=422) from None

    row = _operation_row(conn, account=normalized_account, operation_id=operation_uuid, lock=True)
    if row is None:
        raise AvailabilityError("OPERATION_NOT_FOUND", "Production operation was not found.")
    _require_confirmed_processing(row, account=normalized_account)
    payloads = _validated_fulfillment_payloads(row, account=normalized_account)
    expected_payload = payloads[normalized_action]
    attempt = conn.execute(text(
        "SELECT id,job_id,status,http_status,error_code,payload_hash,response_summary_json "
        "FROM availability_attempts WHERE operation_id=:operation AND account=:account "
        "AND data_scope='PRODUCTION' AND revision_id=:revision AND action=:action FOR UPDATE"
    ), {"operation": operation_uuid, "account": normalized_account,
        "revision": row["revision_id"], "action": normalized_action}).mappings().first()
    if attempt is None:
        queue_failure = conn.execute(text(
            "SELECT id,metadata_json FROM restart_events WHERE account=:account AND data_scope='PRODUCTION' "
            "AND entity_type='RESTART_OPERATION' AND entity_id=:operation "
            "AND event_type='OTTO_AVAILABILITY_NEEDS_REVIEW' AND metadata_json->>'action'=:action "
            "ORDER BY created_at DESC,id DESC LIMIT 1"
        ), {"account": normalized_account, "operation": operation_uuid,
            "action": normalized_action}).mappings().first()
        if queue_failure is None:
            raise AvailabilityError("AVAILABILITY_ATTEMPT_NOT_FOUND", "No failed queue attempt exists for this action.")
        retry_count = int(conn.execute(text(
            "SELECT COUNT(*) FROM restart_events WHERE account=:account AND data_scope='PRODUCTION' "
            "AND entity_type='RESTART_OPERATION' AND entity_id=:operation "
            "AND event_type='OTTO_AVAILABILITY_RETRY_REQUESTED' AND metadata_json->>'action'=:action"
        ), {"account": normalized_account, "operation": operation_uuid,
            "action": normalized_action}).scalar_one())
        if retry_count >= 5:
            raise AvailabilityError("AVAILABILITY_RETRY_LIMIT_REACHED", "Five manual retries have already been queued for this action.")
        retry_number = retry_count + 1
        attempt_id = str(uuid4())
        attempt_key = f"availability:{operation_uuid}:{row['revision_id']}:{normalized_action.lower()}"
        payload_hash = _canonical_hash(expected_payload)
        with conn.begin_nested():
            conn.execute(text(
                "INSERT INTO availability_attempts(id,account,data_scope,operation_id,revision_id,action,attempt_key,"
                "target_sku,payload_hash,status,created_by) VALUES (:id,:account,'PRODUCTION',:operation,:revision,:action,"
                ":attempt_key,:sku,:payload_hash,'PREPARED',:actor)"
            ), {"id": attempt_id, "account": normalized_account, "operation": operation_uuid,
                "revision": row["revision_id"], "action": normalized_action, "attempt_key": attempt_key,
                "sku": row["target_sku"], "payload_hash": payload_hash, "actor": actor.strip()[:200]})
            job = enqueue_job(
                conn, account=normalized_account, job_type=_ACTIONS[normalized_action],
                idempotency_key=f"availability:{attempt_id}",
                payload={"availabilityAttemptId": attempt_id, "operationId": operation_uuid,
                         "revisionId": str(row["revision_id"]), "action": normalized_action},
                max_attempts=5,
            )
            conn.execute(text(
                "UPDATE availability_attempts SET job_id=:job WHERE id=:attempt AND account=:account "
                "AND data_scope='PRODUCTION' AND status='PREPARED' AND job_id IS NULL"
            ), {"job": job.job.id, "attempt": attempt_id, "account": normalized_account})
            _record_event(
                conn, account=normalized_account, actor=actor.strip()[:200],
                event_type="OTTO_AVAILABILITY_JOB_ENQUEUED", entity_type="RESTART_OPERATION",
                entity_id=operation_uuid, summary="Availability action queued after a confirmed pre-dispatch queue failure",
                metadata={"attemptId": attempt_id, "jobId": job.job.id, "action": normalized_action,
                          "targetSku": row["target_sku"], "revisionId": str(row["revision_id"])},
            )
        action_job = {"attemptId": attempt_id, "jobId": job.job.id, "action": normalized_action, "status": "PREPARED"}
        _record_event(
            conn, account=normalized_account, actor=actor.strip()[:200],
            event_type="OTTO_AVAILABILITY_RETRY_REQUESTED", entity_type="RESTART_OPERATION",
            entity_id=operation_uuid, summary="Operator requeued an availability action that had failed before an attempt was created",
            metadata={"action": normalized_action, "retryNumber": retry_number,
                      "previousStatus": "NOT_QUEUED", "previousErrorCode": _object(queue_failure["metadata_json"]).get("errorCode"),
                      "jobId": action_job["jobId"], "published": False},
        )
        return {"operationId": operation_uuid, "attemptId": action_job["attemptId"],
                "jobId": action_job["jobId"], "action": normalized_action,
                "retryNumber": retry_number, "status": action_job["status"]}

    manually_confirmed_not_applied = conn.execute(text(
        "SELECT id FROM restart_events WHERE account=:account AND data_scope='PRODUCTION' "
        "AND entity_type='RESTART_OPERATION' AND entity_id=:operation "
        "AND event_type='OTTO_AVAILABILITY_MANUALLY_RESOLVED' "
        "AND metadata_json->>'action'=:action AND metadata_json->>'resolution'='NOT_APPLIED' "
        "ORDER BY created_at DESC,id DESC LIMIT 1"
    ), {"account": normalized_account, "operation": operation_uuid,
        "action": normalized_action}).first()
    confirmed_rejection = (
        attempt["status"] == "REJECTED"
        and isinstance(attempt["http_status"], int)
        and 400 <= attempt["http_status"] < 500
    )
    confirmed_manual_rejection = (
        attempt["status"] == "REJECTED"
        and attempt["error_code"] == "MANUAL_CONFIRMED_NOT_APPLIED"
        and manually_confirmed_not_applied is not None
    )
    if not confirmed_rejection and not confirmed_manual_rejection:
        raise AvailabilityError("AVAILABILITY_RETRY_REQUIRES_CONFIRMED_REJECTION", "Only an explicitly rejected or manually confirmed not-applied action can be retried.")
    if attempt["payload_hash"].strip() != _canonical_hash(expected_payload):
        raise AvailabilityError("AVAILABILITY_PAYLOAD_CONFLICT", "Retry payload differs from the immutable fulfillment revision.")
    retry_count = int(conn.execute(text(
        "SELECT COUNT(*) FROM restart_events WHERE account=:account AND data_scope='PRODUCTION' "
        "AND entity_type='RESTART_OPERATION' AND entity_id=:operation "
        "AND event_type='OTTO_AVAILABILITY_RETRY_REQUESTED' AND metadata_json->>'action'=:action"
    ), {"account": normalized_account, "operation": operation_uuid,
        "action": normalized_action}).scalar_one())
    if retry_count >= 5:
        raise AvailabilityError("AVAILABILITY_RETRY_LIMIT_REACHED", "Five manual retries have already been queued for this action.")
    retry_number = retry_count + 1
    attempt_id = str(attempt["id"])
    conn.execute(text(
        "UPDATE availability_attempts SET status='PREPARED',job_id=NULL,http_status=NULL,error_code=NULL,"
        "response_summary_json='{}'::jsonb,dispatch_started_at=NULL,completed_at=NULL "
        "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"
    ), {"id": attempt_id, "account": normalized_account})
    job = enqueue_job(
        conn, account=normalized_account, job_type=_ACTIONS[normalized_action],
        idempotency_key=f"availability:{attempt_id}:manual-retry:{retry_number}",
        payload={"availabilityAttemptId": attempt_id, "operationId": operation_uuid,
                 "revisionId": str(row["revision_id"]), "action": normalized_action},
        max_attempts=5,
    )
    conn.execute(text(
        "UPDATE availability_attempts SET job_id=:job WHERE id=:attempt AND account=:account "
        "AND data_scope='PRODUCTION' AND status='PREPARED'"
    ), {"job": job.job.id, "attempt": attempt_id, "account": normalized_account})
    _record_event(
        conn, account=normalized_account, actor=actor.strip()[:200],
        event_type="OTTO_AVAILABILITY_RETRY_REQUESTED", entity_type="RESTART_OPERATION",
        entity_id=operation_uuid, summary="Operator queued a bounded retry after confirming the previous action was not applied",
        metadata={"attemptId": attempt_id, "action": normalized_action, "retryNumber": retry_number,
                  "previousStatus": attempt["status"], "previousHttpStatus": attempt["http_status"],
                  "previousErrorCode": attempt["error_code"],
                  "previousResponse": _safe_summary_value(attempt["response_summary_json"]),
                  "jobId": job.job.id, "published": False},
    )
    return {"operationId": operation_uuid, "attemptId": attempt_id, "jobId": job.job.id,
            "action": normalized_action, "retryNumber": retry_number, "status": "PREPARED"}


def resolve_unknown_availability_step(
    conn: Connection, *, account: str, operation_id: str, action: str, resolution: str,
    evidence_reference: str, reason: str, actor: str,
) -> dict[str, Any]:
    """Record operator evidence for UNKNOWN; never infer success from HTTP 202."""
    normalized_account = str(account or "").strip().upper()
    normalized_action = str(action or "").strip().upper()
    normalized_resolution = str(resolution or "").strip().upper()
    if normalized_account not in {"JV", "XL"} or normalized_action not in _ACTIONS:
        raise AvailabilityError("INVALID_AVAILABILITY_RESOLUTION", "Account or availability action is invalid.", status_code=422)
    if normalized_resolution not in {"APPLIED", "NOT_APPLIED"}:
        raise AvailabilityError("INVALID_AVAILABILITY_RESOLUTION", "Resolution must be APPLIED or NOT_APPLIED.", status_code=422)
    if not isinstance(actor, str) or not actor.strip():
        raise AvailabilityError("ACTOR_REQUIRED", "A trusted operator is required.", status_code=403)
    reason_text = " ".join(str(reason or "").split())
    evidence_text = " ".join(str(evidence_reference or "").split())
    if len(reason_text) < 20 or len(reason_text) > 2000 or not evidence_text or len(evidence_text) > 300:
        raise AvailabilityError("MANUAL_EVIDENCE_REQUIRED", "A bounded OTTO evidence reference and a 20–2000 character reason are required.", status_code=422)
    if _SECRET_TEXT.search(reason_text) or _SECRET_TEXT.search(evidence_text):
        raise AvailabilityError("MANUAL_EVIDENCE_INVALID", "Credentials and tokens must not be entered as evidence.", status_code=422)
    try:
        operation_uuid = str(UUID(str(operation_id)))
    except (TypeError, ValueError, AttributeError):
        raise AvailabilityError("INVALID_OPERATION_ID", "Operation ID must be a UUID.", status_code=422) from None
    row = _operation_row(conn, account=normalized_account, operation_id=operation_uuid, lock=True)
    if row is None:
        raise AvailabilityError("OPERATION_NOT_FOUND", "Production operation was not found.")
    _require_confirmed_processing(row, account=normalized_account)
    attempt = conn.execute(text(
        "SELECT id,status,http_status,error_code,response_summary_json FROM availability_attempts "
        "WHERE operation_id=:operation AND account=:account AND data_scope='PRODUCTION' "
        "AND revision_id=:revision AND action=:action FOR UPDATE"
    ), {"operation": operation_uuid, "account": normalized_account,
        "revision": row["revision_id"], "action": normalized_action}).mappings().first()
    if attempt is None or attempt["status"] != "UNKNOWN":
        raise AvailabilityError("AVAILABILITY_NOT_UNKNOWN", "Manual resolution is available only for an unresolved UNKNOWN result.")
    resolved_status = "ACCEPTED" if normalized_resolution == "APPLIED" else "REJECTED"
    error_code = None if normalized_resolution == "APPLIED" else "MANUAL_CONFIRMED_NOT_APPLIED"
    conn.execute(text(
        "UPDATE availability_attempts SET status=:status,error_code=:error WHERE id=:id AND account=:account "
        "AND data_scope='PRODUCTION' AND status='UNKNOWN'"
    ), {"status": resolved_status, "error": error_code,
        "id": str(attempt["id"]), "account": normalized_account})
    _record_event(
        conn, account=normalized_account, actor=actor.strip()[:200],
        event_type="OTTO_AVAILABILITY_MANUALLY_RESOLVED", entity_type="RESTART_OPERATION",
        entity_id=operation_uuid, summary="Operator reconciled an uncertain OTTO availability outcome",
        metadata={"attemptId": str(attempt["id"]), "action": normalized_action,
                  "resolution": normalized_resolution, "evidenceReference": evidence_text,
                  "reason": reason_text, "previousHttpStatus": attempt["http_status"],
                  "previousErrorCode": attempt["error_code"],
                  "previousResponse": _safe_summary_value(attempt["response_summary_json"]),
                  "published": False},
    )
    return {"operationId": operation_uuid, "attemptId": str(attempt["id"]),
            "action": normalized_action, "resolution": normalized_resolution,
            "status": resolved_status, "published": False}


def enqueue_availability_jobs(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    actor: str,
) -> dict[str, Any]:
    """Create one idempotent job per verified fulfillment action after import success."""
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in {"JV", "XL"}:
        raise AvailabilityError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    try:
        operation_uuid = str(UUID(str(operation_id)))
    except (TypeError, ValueError, AttributeError):
        raise AvailabilityError("INVALID_OPERATION_ID", "Operation ID must be a UUID.", status_code=422) from None
    if not isinstance(actor, str) or not actor.strip():
        raise AvailabilityError("ACTOR_REQUIRED", "A trusted actor is required.", status_code=403)

    row = _operation_row(conn, account=normalized_account, operation_id=operation_uuid, lock=True)
    if row is None:
        raise AvailabilityError("OPERATION_NOT_FOUND", "Production operation was not found.")
    _require_confirmed_processing(row, account=normalized_account)
    payloads = _validated_fulfillment_payloads(row, account=normalized_account)
    outputs: list[dict[str, Any]] = []

    for action, payload in payloads.items():
        attempt_key = f"availability:{operation_uuid}:{row['revision_id']}:{action.lower()}"
        attempt_id = str(uuid4())
        payload_hash = _canonical_hash(payload)
        conn.execute(
            text(
                "INSERT INTO availability_attempts(id,account,data_scope,operation_id,revision_id,action,attempt_key,"
                "target_sku,payload_hash,status,created_by) VALUES (:id,:account,'PRODUCTION',:operation_id,:revision_id,"
                ":action,:attempt_key,:target_sku,:payload_hash,'PREPARED',:actor) "
                "ON CONFLICT (account,operation_id,revision_id,action) DO NOTHING"
            ),
            {"id": attempt_id, "account": normalized_account, "operation_id": operation_uuid,
             "revision_id": row["revision_id"], "action": action, "attempt_key": attempt_key,
             "target_sku": row["target_sku"], "payload_hash": payload_hash, "actor": actor.strip()[:200]},
        )
        attempt = conn.execute(
            text("SELECT id,job_id,status,payload_hash FROM availability_attempts WHERE account=:account "
                 "AND operation_id=:operation_id AND revision_id=:revision_id AND action=:action FOR UPDATE"),
            {"account": normalized_account, "operation_id": operation_uuid,
             "revision_id": row["revision_id"], "action": action},
        ).mappings().one()
        if attempt["payload_hash"].strip() != payload_hash:
            raise AvailabilityError("AVAILABILITY_PAYLOAD_CONFLICT", "An immutable fulfillment revision produced a different action payload.")
        attempt_uuid = str(attempt["id"])
        job_type = _ACTIONS[action]
        job_result = enqueue_job(
            conn,
            account=normalized_account,
            job_type=job_type,
            idempotency_key=f"availability:{attempt_uuid}",
            payload={"availabilityAttemptId": attempt_uuid, "operationId": operation_uuid,
                     "revisionId": str(row["revision_id"]), "action": action},
            max_attempts=5,
        )
        if attempt["job_id"] is not None and str(attempt["job_id"]) != job_result.job.id:
            raise AvailabilityError("AVAILABILITY_JOB_CONFLICT", "Availability attempt is already linked to a different job.")
        conn.execute(
            text("UPDATE availability_attempts SET job_id=:job_id WHERE id=:id AND account=:account AND job_id IS NULL"),
            {"job_id": job_result.job.id, "id": attempt_uuid, "account": normalized_account},
        )
        if attempt["status"] == "PREPARED" and job_result.created:
            _record_event(
                conn, account=normalized_account, actor=actor.strip()[:200],
                event_type="OTTO_AVAILABILITY_JOB_ENQUEUED", entity_type="RESTART_OPERATION",
                entity_id=operation_uuid, summary="One-SKU availability update was queued after confirmed product processing",
                metadata={"attemptId": attempt_uuid, "jobId": job_result.job.id, "action": action,
                          "targetSku": row["target_sku"], "revisionId": str(row["revision_id"])},
            )
        outputs.append({"attemptId": attempt_uuid, "jobId": job_result.job.id,
                        "action": action, "status": attempt["status"], "created": job_result.created})

    return {"operationId": operation_uuid, "jobs": outputs}


def claim_availability_dispatch(
    conn: Connection, *, account: str, attempt_id: str, job_id: str
) -> dict[str, Any]:
    """Commit a non-repeatable dispatch claim before a single availability POST."""
    normalized_account = str(account or "").strip().upper()
    try:
        attempt_uuid, job_uuid = str(UUID(str(attempt_id))), str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        raise AvailabilityError("INVALID_AVAILABILITY_JOB", "Availability attempt and job IDs must be UUIDs.", status_code=422) from None
    attempt = conn.execute(
        text("SELECT a.*,o.status AS operation_status,o.submission_state,o.target_sku AS operation_target_sku,"
             "j.job_type,j.account AS job_account FROM availability_attempts a "
             "JOIN restart_operations o ON o.id=a.operation_id AND o.account=a.account "
             "JOIN background_jobs j ON j.id=a.job_id AND j.account=a.account AND j.data_scope=a.data_scope "
             "WHERE a.id=:id AND a.account=:account AND a.data_scope='PRODUCTION' FOR UPDATE OF a,o,j"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None:
        raise AvailabilityError("AVAILABILITY_ATTEMPT_NOT_FOUND", "Availability attempt was not found.")
    if str(attempt["job_id"]) != job_uuid or attempt["job_account"] != normalized_account:
        raise AvailabilityError("AVAILABILITY_JOB_SCOPE_MISMATCH", "Job is not bound to this availability attempt and account.")
    if attempt["status"] != "PREPARED":
        raise AvailabilityError("AVAILABILITY_DISPATCH_ALREADY_CLAIMED", "This availability attempt cannot be posted twice.")
    if (
        attempt["operation_status"] not in {"AWAITING_ONLINE", "PUBLISHED"}
        or attempt["submission_state"] != "CONFIRMED"
        or attempt["target_sku"] != attempt["operation_target_sku"]
        or attempt["job_type"] != _ACTIONS.get(attempt["action"])
    ):
        raise AvailabilityError("AVAILABILITY_DISPATCH_STATE_INVALID", "Attempt no longer matches its confirmed operation state.")
    operation = _operation_row(conn, account=normalized_account, operation_id=str(attempt["operation_id"]), lock=False)
    if operation is None:
        raise AvailabilityError("OPERATION_NOT_FOUND", "Production operation was not found.")
    _require_confirmed_processing(operation, account=normalized_account)
    payloads = _validated_fulfillment_payloads(operation, account=normalized_account)
    payload = payloads[attempt["action"]]
    if _canonical_hash(payload) != attempt["payload_hash"].strip():
        raise AvailabilityError("AVAILABILITY_PAYLOAD_CONFLICT", "Fulfillment payload no longer matches its immutable attempt.")

    conn.execute(
        text("UPDATE availability_attempts SET status='DISPATCHING',dispatch_started_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account AND status='PREPARED'"),
        {"id": attempt_uuid, "account": normalized_account},
    )
    _record_event(
        conn, account=normalized_account, actor="system:otto-availability", event_type="OTTO_AVAILABILITY_DISPATCH_CLAIMED",
        entity_type="RESTART_OPERATION", entity_id=str(attempt["operation_id"]),
        summary="Availability dispatch fence committed before network I/O",
        metadata={"attemptId": attempt_uuid, "jobId": job_uuid, "action": attempt["action"],
                  "targetSku": attempt["target_sku"], "payloadHash": attempt["payload_hash"].strip()},
    )
    return {"attemptId": attempt_uuid, "jobId": job_uuid, "operationId": str(attempt["operation_id"]),
            "account": normalized_account, "action": attempt["action"], "targetSku": attempt["target_sku"],
            "payload": payload, "status": "DISPATCHING"}


def mark_interrupted_availability_unknown(
    conn: Connection, *, account: str, attempt_id: str
) -> dict[str, Any]:
    normalized_account = str(account or "").strip().upper()
    try:
        attempt_uuid = str(UUID(str(attempt_id)))
    except (TypeError, ValueError, AttributeError):
        raise AvailabilityError("INVALID_AVAILABILITY_ATTEMPT", "Availability attempt ID must be a UUID.", status_code=422) from None
    attempt = conn.execute(
        text("SELECT id,operation_id,status,error_code FROM availability_attempts WHERE id=:id AND account=:account "
             "AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None:
        raise AvailabilityError("AVAILABILITY_ATTEMPT_NOT_FOUND", "Availability attempt was not found.")
    if attempt["status"] in {"ACCEPTED", "REJECTED", "UNKNOWN"}:
        # A broker redelivery can happen after the OTTO outcome was committed
        # but before the job lease was finalized. Preserve that outcome and
        # let the executor finish without issuing the POST again.
        return {"attemptId": attempt_uuid, "status": attempt["status"], "idempotentReplay": True}
    if attempt["status"] != "DISPATCHING":
        raise AvailabilityError("AVAILABILITY_NOT_INTERRUPTED", "Only an unresolved availability dispatch can be reconciled as interrupted.")
    conn.execute(
        text("UPDATE availability_attempts SET status='UNKNOWN',error_code='DISPATCH_RESULT_MISSING',"
             "completed_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"),
        {"id": attempt_uuid, "account": normalized_account},
    )
    _record_event(
        conn, account=normalized_account, actor="system:otto-availability-recovery",
        event_type="OTTO_AVAILABILITY_DISPATCH_INTERRUPTED", entity_type="RESTART_OPERATION",
        entity_id=str(attempt["operation_id"]), summary="Interrupted availability update requires reconciliation; automatic resubmission is prohibited",
        metadata={"attemptId": attempt_uuid, "errorCode": "DISPATCH_RESULT_MISSING", "published": False},
    )
    return {"attemptId": attempt_uuid, "status": "UNKNOWN", "idempotentReplay": False}


def record_availability_result(
    conn: Connection,
    *,
    account: str,
    attempt_id: str,
    http_status: int | None,
    response: Mapping[str, Any] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    normalized_account = str(account or "").strip().upper()
    try:
        attempt_uuid = str(UUID(str(attempt_id)))
    except (TypeError, ValueError, AttributeError):
        raise AvailabilityError("INVALID_AVAILABILITY_ATTEMPT", "Availability attempt ID must be a UUID.", status_code=422) from None
    if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
        raise AvailabilityError("INVALID_HTTP_STATUS", "HTTP status must be between 100 and 599.", status_code=422)
    attempt = conn.execute(
        text("SELECT operation_id,status,http_status,error_code FROM availability_attempts WHERE id=:id "
             "AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None:
        raise AvailabilityError("AVAILABILITY_ATTEMPT_NOT_FOUND", "Availability attempt was not found.")
    if attempt["status"] == "UNKNOWN" and attempt["http_status"] == http_status and attempt["error_code"] == error_code:
        return {"attemptId": attempt_uuid, "status": "UNKNOWN", "idempotentReplay": True, "published": False}
    if attempt["status"] not in {"DISPATCHING", "UNKNOWN"}:
        raise AvailabilityError("AVAILABILITY_DISPATCH_NOT_CLAIMED", "A durable dispatch claim must exist before recording a result.")

    if error_code == "AVAILABILITY_WRITE_OUTCOME_UNKNOWN" or http_status is None:
        state, stable_code = "UNKNOWN", "AVAILABILITY_WRITE_OUTCOME_UNKNOWN"
    elif http_status in {408} or http_status >= 500 or 300 <= http_status < 400:
        state, stable_code = "UNKNOWN", f"OTTO_HTTP_{http_status}"
    elif 400 <= http_status < 500:
        state, stable_code = "REJECTED", f"OTTO_HTTP_{http_status}"
    elif 200 <= http_status < 300 and error_code:
        state, stable_code = "UNKNOWN", error_code if _SAFE_CODE.fullmatch(error_code) else "OTTO_RESPONSE_INVALID"
    elif 200 <= http_status < 300:
        state, stable_code = "ACCEPTED", None
    else:
        state, stable_code = "UNKNOWN", "OTTO_RESPONSE_UNCERTAIN"

    summary = _safe_summary_value(response or {})
    summary_json = json.dumps(summary if isinstance(summary, dict) else {}, ensure_ascii=False, separators=(",", ":"))
    conn.execute(
        text("UPDATE availability_attempts SET status=:status,http_status=:http_status,"
             "response_summary_json=CAST(:summary AS jsonb),error_code=:error_code,completed_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account"),
        {"status": state, "http_status": http_status, "summary": summary_json, "error_code": stable_code,
         "id": attempt_uuid, "account": normalized_account},
    )
    _record_event(
        conn, account=normalized_account, actor="system:otto-availability", event_type="OTTO_AVAILABILITY_RESULT_RECORDED",
        entity_type="RESTART_OPERATION", entity_id=str(attempt["operation_id"]),
        summary="One-SKU OTTO availability result recorded; this does not confirm marketplace ONLINE",
        metadata={"attemptId": attempt_uuid, "status": state, "httpStatus": http_status,
                  "errorCode": stable_code, "published": False},
    )
    return {"attemptId": attempt_uuid, "status": state, "httpStatus": http_status,
            "errorCode": stable_code, "published": False}

"""Durable OTTO task polling and ONLINE confirmation state transitions.

This module performs database work only. HTTP GETs live in the job handlers,
outside transactions; all evidence and the next durable job are then committed
atomically by these functions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.operation_service import (
    OperationError,
    _record_event,
    mark_submission_unknown,
    record_online_observation,
    record_processing_evidence,
)

TASK_JOB_TYPE = "otto.poll_product_task"
ONLINE_JOB_TYPE = "otto.confirm_online"
TASK_DEADLINE = timedelta(hours=24)
ONLINE_DEADLINE = timedelta(hours=2)
BASE_POLL_SECONDS = 60
MAX_POLL_SECONDS = 900
_TASK_PATH = re.compile(
    r"^/v5/products/update-tasks/"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_SAFE_CODE = re.compile(r"^[A-Z0-9_.-]{1,64}$")


class ProcessingError(RuntimeError):
    def __init__(self, code: str, message: str = "OTTO processing state is invalid."):
        self.code = code
        super().__init__(message)


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_code(value: str | None, fallback: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if _SAFE_CODE.fullmatch(candidate) else fallback


def _enqueue_initial_availability_isolated(
    conn: Connection, *, account: str, operation_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Keep availability outbox failures from rolling back confirmed processing/ONLINE polling."""
    from sqlalchemy.exc import SQLAlchemyError
    from jobs.service import JobError
    from services.availability_service import AvailabilityError, enqueue_availability_jobs

    try:
        with conn.begin_nested():
            result = enqueue_availability_jobs(
                conn, account=account, operation_id=operation_id, actor="system:otto-processing",
            )
        return result, None
    except AvailabilityError as error:
        return None, _safe_code(error.code, "AVAILABILITY_NOT_QUEUED")
    except (SQLAlchemyError, JobError, ValueError):
        return None, "AVAILABILITY_QUEUE_ERROR"


def _task_path(task_links: Any) -> tuple[str, str] | None:
    if not isinstance(task_links, list):
        return None
    self_links = [row for row in task_links if isinstance(row, Mapping) and str(row.get("rel", "")).casefold() == "self"]
    if len(self_links) != 1:
        return None
    href = self_links[0].get("href")
    if not isinstance(href, str):
        return None
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or parts.query or parts.fragment or parts.username or parts.password:
        return None
    match = _TASK_PATH.fullmatch(parts.path)
    if not match:
        return None
    try:
        task_id = str(UUID(match.group(1)))
    except ValueError:
        return None
    return parts.path, task_id


def _enqueue_task_poll(
    conn: Connection, *, account: str, attempt_id: str, task_href: str, sequence: int,
    available_at: datetime,
) -> str:
    result = enqueue_job(
        conn,
        account=account,
        job_type=TASK_JOB_TYPE,
        idempotency_key=f"otto-task-poll:{attempt_id}:{sequence}",
        payload={"attemptId": attempt_id},
        max_attempts=3,
        available_at=available_at,
    )
    return result.job.id


def start_processing_poll(
    conn: Connection, *, account: str, attempt_id: str, task_links: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Enqueue the first poll for a sanitized OTTO task self link, or review it."""
    normalized_account = str(account or "").strip().upper()
    try:
        attempt_uuid = str(UUID(str(attempt_id)))
    except (TypeError, ValueError, AttributeError):
        raise ProcessingError("INVALID_ATTEMPT_ID") from None
    if normalized_account not in {"JV", "XL"}:
        raise ProcessingError("INVALID_ACCOUNT")
    current = _now(now)
    attempt = conn.execute(
        text("SELECT id,operation_id,item_id,status FROM submission_attempts "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if attempt is None or attempt["status"] != "ACCEPTED":
        raise ProcessingError("SUBMISSION_NOT_ACCEPTED")

    existing = conn.execute(
        text("SELECT state,current_job_id,task_href FROM otto_processing_polls "
             "WHERE attempt_id=:attempt AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"attempt": attempt_uuid, "account": normalized_account},
    ).mappings().first()
    if existing:
        return {"started": existing["state"] == "POLLING", "state": existing["state"],
                "jobId": str(existing["current_job_id"]) if existing["current_job_id"] else None}

    task = _task_path(task_links)
    if task is None:
        code = "TASK_LINK_MISSING_OR_INVALID"
        conn.execute(
            text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code=:code,version=version+1 "
                 "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
            {"code": code, "id": attempt["item_id"], "account": normalized_account},
        )
        _record_event(
            conn, account=normalized_account, actor="system:otto-processing",
            event_type="OTTO_PROCESSING_POLL_UNAVAILABLE", entity_type="RESTART_OPERATION",
            entity_id=str(attempt["operation_id"]),
            summary="Accepted OTTO product task has no single trusted self link; manual reconciliation is required",
            metadata={"attemptId": attempt_uuid, "errorCode": code, "published": False},
        )
        return {"started": False, "state": "NEEDS_REVIEW", "errorCode": code}

    task_href, task_id = task
    job_id = _enqueue_task_poll(
        conn, account=normalized_account, attempt_id=attempt_uuid, task_href=task_href,
        sequence=0, available_at=current,
    )
    conn.execute(
        text("INSERT INTO otto_processing_polls "
             "(attempt_id,account,data_scope,task_id,task_href,state,poll_count,deadline_at,current_job_id) "
             "VALUES (:attempt,:account,'PRODUCTION',:task_id,:href,'POLLING',0,:deadline,:job_id)"),
        {"attempt": attempt_uuid, "account": normalized_account, "task_id": task_id,
         "href": task_href, "deadline": current + TASK_DEADLINE, "job_id": job_id},
    )
    return {"started": True, "state": "POLLING", "jobId": job_id, "taskId": task_id}


def load_processing_poll_context(
    conn: Connection, *, account: str, attempt_id: str, job_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        text("SELECT p.attempt_id,p.task_id,p.task_href,p.state,p.poll_count,p.deadline_at,p.current_job_id,"
             "a.operation_id,a.item_id,a.target_sku,a.status AS attempt_status,"
             "o.status AS operation_status,o.submission_state,o.target_sku AS operation_target_sku "
             "FROM otto_processing_polls p JOIN submission_attempts a ON a.id=p.attempt_id "
             "AND a.account=p.account AND a.data_scope=p.data_scope "
             "JOIN restart_operations o ON o.id=a.operation_id AND o.account=a.account AND o.data_scope=a.data_scope "
             "WHERE p.attempt_id=:attempt AND p.account=:account AND p.data_scope='PRODUCTION'"),
        {"attempt": attempt_id, "account": account},
    ).mappings().first()
    if row is None or row["state"] != "POLLING" or str(row["current_job_id"]) != str(job_id):
        return None
    if (
        row["attempt_status"] != "ACCEPTED"
        or row["operation_status"] != "OTTO_PROCESSING"
        or row["submission_state"] != "IN_FLIGHT"
        or row["target_sku"] != row["operation_target_sku"]
    ):
        raise ProcessingError("PROCESSING_POLL_SCOPE_OR_STATE_MISMATCH")
    context = dict(row)
    context["task_id"] = str(row["task_id"])
    return context


def _schedule_task_poll(
    conn: Connection, *, account: str, attempt_id: str, job_id: str, now: datetime,
    delay_seconds: int, task_state: str | None, error_code: str | None,
) -> dict[str, Any]:
    poll = conn.execute(
        text("SELECT p.*,a.operation_id,a.item_id FROM otto_processing_polls p "
             "JOIN submission_attempts a ON a.id=p.attempt_id AND a.account=p.account "
             "AND a.data_scope=p.data_scope WHERE p.attempt_id=:attempt AND p.account=:account "
             "AND p.data_scope='PRODUCTION' FOR UPDATE OF p,a"),
        {"attempt": attempt_id, "account": account},
    ).mappings().first()
    if poll is None or poll["state"] != "POLLING" or str(poll["current_job_id"]) != job_id:
        return {"scheduled": False, "stale": True}
    current = _now(now)
    if current >= poll["deadline_at"]:
        _mark_processing_review(conn, account=account, attempt_id=attempt_id,
                                operation_id=str(poll["operation_id"]), item_id=int(poll["item_id"]),
                                code="OTTO_PROCESSING_DEADLINE")
        conn.execute(
            text("UPDATE otto_processing_polls SET state='DEADLINE',current_job_id=NULL,"
                 "last_task_state=:task_state,last_error_code=:error,completed_at=:now,updated_at=:now "
                 "WHERE attempt_id=:attempt AND account=:account"),
            {"task_state": task_state[:100] if task_state else None,
             "error": _safe_code(error_code, "OTTO_PROCESSING_DEADLINE"), "now": current,
             "attempt": attempt_id, "account": account},
        )
        return {"scheduled": False, "state": "DEADLINE"}
    count = int(poll["poll_count"]) + 1
    safe_delay = max(0, min(int(delay_seconds), 86_400))
    next_at = min(current + timedelta(seconds=safe_delay), poll["deadline_at"])
    job = enqueue_job(
        conn, account=account, job_type=TASK_JOB_TYPE,
        idempotency_key=f"otto-task-poll:{attempt_id}:{count}",
        payload={"attemptId": attempt_id}, max_attempts=3, available_at=next_at, now=current,
    )
    conn.execute(
        text("UPDATE otto_processing_polls SET poll_count=:count,current_job_id=:job_id,"
             "last_task_state=:task_state,last_error_code=:error,updated_at=:now "
             "WHERE attempt_id=:attempt AND account=:account AND state='POLLING'"),
        {"count": count, "job_id": job.job.id,
         "task_state": task_state[:100] if task_state else None,
         "error": _safe_code(error_code, "OTTO_POLL_RETRY") if error_code else None,
         "now": current, "attempt": attempt_id, "account": account},
    )
    return {"scheduled": True, "jobId": job.job.id, "pollCount": count, "availableAt": next_at.isoformat()}


def defer_processing_poll(
    conn: Connection, *, account: str, attempt_id: str, job_id: str, now: datetime | None = None,
    delay_seconds: int | None = None, task_state: str | None = None, error_code: str | None = None,
) -> dict[str, Any]:
    current = _now(now)
    row = conn.execute(
        text("SELECT poll_count FROM otto_processing_polls WHERE attempt_id=:attempt AND account=:account "
             "AND data_scope='PRODUCTION' AND current_job_id=:job AND state='POLLING'"),
        {"attempt": attempt_id, "account": account, "job": job_id},
    ).mappings().first()
    if row is None:
        return {"scheduled": False, "stale": True}
    backoff = min(BASE_POLL_SECONDS * (2 ** min(int(row["poll_count"]), 4)), MAX_POLL_SECONDS)
    delay = max(backoff, int(delay_seconds or 0))
    return _schedule_task_poll(conn, account=account, attempt_id=attempt_id, job_id=job_id,
                               now=current, delay_seconds=delay, task_state=task_state, error_code=error_code)


def _mark_processing_review(
    conn: Connection, *, account: str, attempt_id: str, operation_id: str, item_id: int, code: str,
) -> None:
    safe = _safe_code(code, "OTTO_PROCESSING_REVIEW_REQUIRED")
    try:
        mark_submission_unknown(conn, account=account, operation_id=operation_id)
    except OperationError as error:
        if error.code != "SUBMISSION_ALREADY_CONFIRMED":
            raise
    conn.execute(
        text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code=:code,version=version+1 "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
             "AND status NOT IN ('PUBLISHED','CANCELLED')"),
        {"code": safe, "id": item_id, "account": account},
    )
    _record_event(
        conn, account=account, actor="system:otto-processing", event_type="OTTO_PROCESSING_NEEDS_REVIEW",
        entity_type="RESTART_OPERATION", entity_id=operation_id,
        summary="OTTO processing could not be confirmed for the exact target SKU",
        metadata={"attemptId": attempt_id, "errorCode": safe, "published": False},
    )


def record_processing_task_result(
    conn: Connection, *, account: str, attempt_id: str, job_id: str,
    result: str, error_code: str | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    normalized_account = str(account or "").strip().upper()
    current = _now(now)
    if result not in {"SUCCEEDED", "FAILED", "NEEDS_REVIEW"}:
        raise ProcessingError("INVALID_PROCESSING_RESULT")
    poll = conn.execute(
        text("SELECT p.*,a.operation_id,a.item_id,a.target_sku,a.status AS attempt_status "
             "FROM otto_processing_polls p JOIN submission_attempts a ON a.id=p.attempt_id "
             "AND a.account=p.account AND a.data_scope=p.data_scope "
             "WHERE p.attempt_id=:attempt AND p.account=:account AND p.data_scope='PRODUCTION' "
             "FOR UPDATE OF p,a"),
        {"attempt": attempt_id, "account": normalized_account},
    ).mappings().first()
    if poll is None:
        raise ProcessingError("PROCESSING_POLL_NOT_FOUND")
    if poll["state"] != "POLLING" or str(poll["current_job_id"]) != str(job_id):
        return {"state": poll["state"], "idempotentReplay": True}
    if poll["attempt_status"] != "ACCEPTED":
        raise ProcessingError("PROCESSING_ATTEMPT_STATE_INVALID")

    operation_id = str(poll["operation_id"])
    item_id = int(poll["item_id"])
    sku = str(poll["target_sku"])
    if result == "NEEDS_REVIEW":
        code = _safe_code(error_code, "OTTO_PROCESSING_RESULT_UNVERIFIED")
        _mark_processing_review(conn, account=normalized_account, attempt_id=attempt_id,
                                operation_id=operation_id, item_id=item_id, code=code)
        state = "NEEDS_REVIEW"
    else:
        event_id = _record_event(
            conn, account=normalized_account, actor="system:otto-processing",
            event_type="OTTO_PROCESSING_RESULT", entity_type="OTTO_PROCESSING_RESULT",
            entity_id=operation_id,
            summary="OTTO returned a per-SKU processing result for the exact Restart target",
            metadata={"attemptId": attempt_id, "targetSku": sku, "result": result,
                      "account": normalized_account, "errorCode": _safe_code(error_code, "OTTO_PROCESSING_FAILED") if result == "FAILED" else None},
        )
        if result == "SUCCEEDED":
            record_processing_evidence(
                conn, account=normalized_account, operation_id=operation_id,
                event_id=event_id, target_sku=sku,
            )
            conn.execute(
                text("UPDATE restart_items SET status='AWAITING_ONLINE',last_error_code=NULL,version=version+1 "
                     "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
                {"id": item_id, "account": normalized_account},
            )
            state = "SUCCEEDED"
            # Product processing and marketplace visibility are independent of
            # the first stock/delivery writes. Start ONLINE polling as soon as
            # OTTO confirms this exact SKU, in the same transaction as evidence.
            online_confirmation = maybe_enqueue_online_confirmation(
                conn, account=normalized_account, operation_id=operation_id, now=current,
            )
            _, availability_error = _enqueue_initial_availability_isolated(
                conn, account=normalized_account, operation_id=operation_id,
            )
            if availability_error:
                code = availability_error
                conn.execute(
                    text("UPDATE restart_items SET status='AWAITING_ONLINE',last_error_code=:code,version=version+1 "
                         "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
                    {"code": code, "id": item_id, "account": normalized_account},
                )
                for action in ("QUANTITY", "DELIVERY_INFORMATION"):
                    _record_event(
                        conn, account=normalized_account, actor="system:otto-processing",
                        event_type="OTTO_AVAILABILITY_NEEDS_REVIEW", entity_type="RESTART_OPERATION",
                        entity_id=operation_id,
                        summary="Availability action could not be queued; marketplace polling continues",
                        metadata={"attemptId": attempt_id, "action": action, "errorCode": code,
                                  "onlinePolling": online_confirmation, "published": False},
                    )
        else:
            stable = _safe_code(error_code, "OTTO_TASK_SKU_FAILED")
            conn.execute(
                text("UPDATE submission_attempts SET status='REJECTED',error_code=:code "
                     "WHERE id=:attempt AND account=:account AND data_scope='PRODUCTION' AND status='ACCEPTED'"),
                {"code": stable, "attempt": attempt_id, "account": normalized_account},
            )
            conn.execute(
                text("UPDATE restart_operations SET status='READY',submission_state='NEVER_SUBMITTED',"
                     "version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account "
                     "AND data_scope='PRODUCTION' AND status='OTTO_PROCESSING' AND submission_state='IN_FLIGHT'"),
                {"id": operation_id, "account": normalized_account},
            )
            conn.execute(
                text("UPDATE restart_items SET status='READY_TO_SUBMIT',last_error_code=:code,version=version+1 "
                     "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
                {"code": stable, "id": item_id, "account": normalized_account},
            )
            state = "FAILED"

    terminal = {"SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "NEEDS_REVIEW": "NEEDS_REVIEW"}[result]
    conn.execute(
        text("UPDATE otto_processing_polls SET state=:state,current_job_id=NULL,last_error_code=:error,"
             "completed_at=:now,updated_at=:now WHERE attempt_id=:attempt AND account=:account"),
        {"state": terminal, "error": _safe_code(error_code, "OTTO_PROCESSING_RESULT_UNVERIFIED") if result != "SUCCEEDED" else None,
         "now": current, "attempt": attempt_id, "account": normalized_account},
    )
    result_payload = {"state": state, "operationId": operation_id, "published": False}
    if result == "SUCCEEDED":
        result_payload["onlineConfirmation"] = online_confirmation
    return result_payload


def record_synchronous_processing_success(
    conn: Connection, *, account: str, attempt_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Persist per-SKU success and independently enqueue ONLINE and availability work."""
    normalized_account = str(account or "").strip().upper()
    attempt = conn.execute(
        text("SELECT a.id,a.operation_id,a.item_id,a.target_sku,a.status,o.submission_state,"
             "o.processing_evidence_event_id FROM submission_attempts a JOIN restart_operations o "
             "ON o.id=a.operation_id AND o.account=a.account AND o.data_scope=a.data_scope "
             "WHERE a.id=:attempt AND a.account=:account AND a.data_scope='PRODUCTION' "
             "FOR UPDATE OF a,o"),
        {"attempt": attempt_id, "account": normalized_account},
    ).mappings().first()
    if attempt is None or attempt["status"] != "ACCEPTED":
        raise ProcessingError("SUBMISSION_NOT_ACCEPTED")
    if attempt["processing_evidence_event_id"]:
        online_confirmation = maybe_enqueue_online_confirmation(
            conn, account=normalized_account, operation_id=str(attempt["operation_id"]), now=now,
        )
        return {"state": "SUCCEEDED", "idempotentReplay": True, "published": False,
                "onlineConfirmation": online_confirmation}
    sku = str(attempt["target_sku"] or "").strip()
    if not sku:
        raise ProcessingError("TARGET_SKU_UNVERIFIED")
    event_id = _record_event(
        conn, account=normalized_account, actor="system:otto-processing",
        event_type="OTTO_PROCESSING_RESULT", entity_type="OTTO_PROCESSING_RESULT",
        entity_id=str(attempt["operation_id"]),
        summary="OTTO returned explicit synchronous per-SKU success",
        metadata={"attemptId": attempt_id, "targetSku": sku, "result": "SUCCEEDED",
                  "account": normalized_account},
    )
    record_processing_evidence(
        conn, account=normalized_account, operation_id=str(attempt["operation_id"]),
        event_id=event_id, target_sku=sku,
    )
    conn.execute(
        text("UPDATE restart_items SET status='AWAITING_ONLINE',last_error_code=NULL,version=version+1 "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
        {"id": attempt["item_id"], "account": normalized_account},
    )
    online_confirmation = maybe_enqueue_online_confirmation(
        conn, account=normalized_account, operation_id=str(attempt["operation_id"]), now=now,
    )
    _, availability_error = _enqueue_initial_availability_isolated(
        conn, account=normalized_account, operation_id=str(attempt["operation_id"]),
    )
    if availability_error:
        code = availability_error
        conn.execute(
            text("UPDATE restart_items SET status='AWAITING_ONLINE',last_error_code=:code,version=version+1 "
                 "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
            {"code": code, "id": attempt["item_id"], "account": normalized_account},
        )
        for action in ("QUANTITY", "DELIVERY_INFORMATION"):
            _record_event(
                conn, account=normalized_account, actor="system:otto-processing",
                event_type="OTTO_AVAILABILITY_NEEDS_REVIEW", entity_type="RESTART_OPERATION",
                entity_id=str(attempt["operation_id"]),
                summary="Availability action could not be queued; marketplace polling continues",
                metadata={"attemptId": attempt_id, "action": action, "errorCode": code,
                          "onlinePolling": online_confirmation, "published": False},
            )
    return {"state": "SUCCEEDED", "operationId": str(attempt["operation_id"]),
            "onlineConfirmation": online_confirmation, "published": False}


def maybe_enqueue_online_confirmation(
    conn: Connection, *, account: str, operation_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Queue a status GET after per-SKU processing; availability is independent."""
    normalized_account = str(account or "").strip().upper()
    current = _now(now)
    try:
        operation_uuid = str(UUID(str(operation_id)))
    except (TypeError, ValueError, AttributeError):
        raise ProcessingError("INVALID_OPERATION_ID") from None
    row = conn.execute(
        text("SELECT o.id,o.target_sku,o.submission_state,o.status,o.online_observation_id "
             "FROM restart_operations o WHERE o.id=:id AND o.account=:account "
             "AND o.data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_uuid, "account": normalized_account},
    ).mappings().first()
    if row is None or row["submission_state"] != "CONFIRMED" or row["status"] != "AWAITING_ONLINE":
        return {"queued": False, "reason": "PROCESSING_NOT_CONFIRMED"}
    if row["online_observation_id"]:
        return {"queued": False, "reason": "ONLINE_ALREADY_CONFIRMED"}
    existing = conn.execute(
        text("SELECT state,current_job_id FROM otto_online_polls WHERE operation_id=:id "
             "AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_uuid, "account": normalized_account},
    ).mappings().first()
    if existing:
        return {"queued": existing["state"] == "POLLING", "jobId": str(existing["current_job_id"]) if existing["current_job_id"] else None,
                "state": existing["state"]}
    sku = str(row["target_sku"] or "").strip()
    if not sku:
        return {"queued": False, "reason": "TARGET_SKU_UNVERIFIED"}
    job = enqueue_job(
        conn, account=normalized_account, job_type=ONLINE_JOB_TYPE,
        idempotency_key=f"otto-online-confirm:{operation_uuid}:0",
        payload={"operationId": operation_uuid}, max_attempts=3, available_at=current, now=current,
    )
    conn.execute(
        text("INSERT INTO otto_online_polls(operation_id,account,data_scope,target_sku,state,poll_count,"
             "deadline_at,current_job_id) VALUES (:id,:account,'PRODUCTION',:sku,'POLLING',0,:deadline,:job)"),
        {"id": operation_uuid, "account": normalized_account, "sku": sku,
         "deadline": current + ONLINE_DEADLINE, "job": job.job.id},
    )
    return {"queued": True, "jobId": job.job.id, "operationId": operation_uuid}


def load_online_poll_context(
    conn: Connection, *, account: str, operation_id: str, job_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        text("SELECT p.operation_id,p.account,p.target_sku,p.state,p.poll_count,p.deadline_at,p.current_job_id,"
             "o.submission_state,o.status,o.processing_evidence_event_id,o.online_observation_id "
             "FROM otto_online_polls p JOIN restart_operations o ON o.id=p.operation_id "
             "AND o.account=p.account AND o.data_scope=p.data_scope "
             "WHERE p.operation_id=:id AND p.account=:account AND p.data_scope='PRODUCTION'"),
        {"id": operation_id, "account": account},
    ).mappings().first()
    if row is None or row["state"] != "POLLING" or str(row["current_job_id"]) != str(job_id):
        return None
    if (
        row["submission_state"] != "CONFIRMED" or row["status"] != "AWAITING_ONLINE"
        or not row["processing_evidence_event_id"] or row["online_observation_id"]
    ):
        raise ProcessingError("ONLINE_CONFIRMATION_SCOPE_OR_STATE_MISMATCH")
    return dict(row)


def _insert_operation_observation(
    conn: Connection, *, account: str, operation_id: str, requested_sku: str,
    response: Mapping[str, Any], http_status: int,
) -> str:
    from services.otto_status_service import classify_marketplace_status_response

    classified = classify_marketplace_status_response(response, requested_sku, http_status)
    response_text = _json(dict(response))
    response_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
    errors = list(classified.errors)
    codes = [item.get("code") for item in errors if isinstance(item, Mapping) and "code" in item]
    titles = [item.get("title") for item in errors if isinstance(item, Mapping) and "title" in item]
    last_modified = None
    if isinstance(classified.last_modified, str):
        try:
            last_modified = datetime.fromisoformat(classified.last_modified.replace("Z", "+00:00"))
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
        except ValueError:
            last_modified = None
    source_id = conn.execute(
        text("SELECT source_product_id FROM restart_operations WHERE id=:id AND account=:account "
             "AND data_scope='PRODUCTION'"),
        {"id": operation_id, "account": account},
    ).scalar_one()
    observation_id = conn.execute(
        text("INSERT INTO otto_status_observations "
             "(run_id,operation_id,account,data_scope,sequence_no,requested_sku,response_sku,source_product_id,"
             "http_status,classification,period,reason,status,market_place_status,active,last_modified,errors_json,"
             "error_codes_json,error_titles_json,unknown_codes_json,response_json,response_hash,reduction_status) "
             "VALUES (NULL,:operation,:account,'PRODUCTION',1,:requested,:response_sku,:source,:http,:classification,"
             ":period,:reason,:status,:marketplace,:active,:modified,CAST(:errors AS jsonb),CAST(:codes AS jsonb),"
             "CAST(:titles AS jsonb),CAST(:unknown AS jsonb),CAST(:response AS jsonb),:hash,'NONE') RETURNING id"),
        {"operation": operation_id, "account": account, "requested": requested_sku,
         "response_sku": classified.sku, "source": source_id, "http": http_status,
         "classification": classified.classification, "period": classified.period,
         "reason": classified.reason[:500], "status": classified.status if isinstance(classified.status, str) else None,
         "marketplace": classified.marketplace_status if isinstance(classified.marketplace_status, str) else None,
         "active": classified.active, "modified": last_modified, "errors": _json(errors),
         "codes": _json(codes), "titles": _json(titles), "unknown": _json(list(classified.unknown_codes)),
         "response": response_text, "hash": response_hash},
    ).scalar_one()
    return str(observation_id)


def _schedule_online_poll(
    conn: Connection, *, account: str, operation_id: str, job_id: str, now: datetime,
    delay_seconds: int, last_status: str | None, error_code: str | None,
) -> dict[str, Any]:
    poll = conn.execute(
        text("SELECT * FROM otto_online_polls WHERE operation_id=:id AND account=:account "
             "AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_id, "account": account},
    ).mappings().first()
    if poll is None or poll["state"] != "POLLING" or str(poll["current_job_id"]) != job_id:
        return {"scheduled": False, "stale": True}
    current = _now(now)
    if current >= poll["deadline_at"]:
        _mark_online_review(conn, account=account, operation_id=operation_id, code="MARKETPLACE_ONLINE_DEADLINE")
        conn.execute(
            text("UPDATE otto_online_polls SET state='DEADLINE',current_job_id=NULL,last_status=:status,"
                 "last_error_code=:error,completed_at=:now,updated_at=:now WHERE operation_id=:id AND account=:account"),
            {"status": last_status[:100] if last_status else None,
             "error": _safe_code(error_code, "MARKETPLACE_ONLINE_DEADLINE"), "now": current,
             "id": operation_id, "account": account},
        )
        return {"scheduled": False, "state": "DEADLINE"}
    count = int(poll["poll_count"]) + 1
    delay = max(0, min(int(delay_seconds), 86_400))
    next_at = min(current + timedelta(seconds=delay), poll["deadline_at"])
    job = enqueue_job(
        conn, account=account, job_type=ONLINE_JOB_TYPE,
        idempotency_key=f"otto-online-confirm:{operation_id}:{count}",
        payload={"operationId": operation_id}, max_attempts=3, available_at=next_at, now=current,
    )
    conn.execute(
        text("UPDATE otto_online_polls SET poll_count=:count,current_job_id=:job,last_status=:status,"
             "last_error_code=:error,updated_at=:now WHERE operation_id=:id AND account=:account AND state='POLLING'"),
        {"count": count, "job": job.job.id, "status": last_status[:100] if last_status else None,
         "error": _safe_code(error_code, "MARKETPLACE_STATUS_RETRY") if error_code else None,
         "now": current, "id": operation_id, "account": account},
    )
    return {"scheduled": True, "jobId": job.job.id, "pollCount": count, "availableAt": next_at.isoformat()}


def defer_online_poll(
    conn: Connection, *, account: str, operation_id: str, job_id: str, now: datetime | None = None,
    delay_seconds: int | None = None, last_status: str | None = None, error_code: str | None = None,
) -> dict[str, Any]:
    current = _now(now)
    row = conn.execute(
        text("SELECT poll_count FROM otto_online_polls WHERE operation_id=:id AND account=:account "
             "AND data_scope='PRODUCTION' AND current_job_id=:job AND state='POLLING'"),
        {"id": operation_id, "account": account, "job": job_id},
    ).mappings().first()
    if row is None:
        return {"scheduled": False, "stale": True}
    backoff = min(BASE_POLL_SECONDS * (2 ** min(int(row["poll_count"]), 4)), MAX_POLL_SECONDS)
    delay = max(backoff, int(delay_seconds or 0))
    return _schedule_online_poll(conn, account=account, operation_id=operation_id, job_id=job_id,
                                 now=current, delay_seconds=delay, last_status=last_status, error_code=error_code)


def _mark_online_review(conn: Connection, *, account: str, operation_id: str, code: str) -> None:
    safe = _safe_code(code, "MARKETPLACE_ONLINE_REVIEW_REQUIRED")
    row = conn.execute(
        text("SELECT item_id FROM submission_attempts WHERE operation_id=:id AND account=:account "
             "AND data_scope='PRODUCTION' ORDER BY attempt_number DESC LIMIT 1"),
        {"id": operation_id, "account": account},
    ).mappings().first()
    if row:
        conn.execute(
            text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code=:code,version=version+1 "
                 "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
                 "AND status NOT IN ('PUBLISHED','CANCELLED')"),
            {"code": safe, "id": row["item_id"], "account": account},
        )
    _record_event(
        conn, account=account, actor="system:otto-online-confirmation",
        event_type="OTTO_ONLINE_CONFIRMATION_NEEDS_REVIEW", entity_type="RESTART_OPERATION",
        entity_id=operation_id, summary="ONLINE could not be confirmed before the polling deadline or after a read error",
        metadata={"errorCode": safe, "published": False},
    )


def record_online_status_response(
    conn: Connection, *, account: str, operation_id: str, job_id: str,
    response: Mapping[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    """Persist a one-SKU status response; publish only with both linked proofs."""
    normalized_account = str(account or "").strip().upper()
    current = _now(now)
    context = conn.execute(
        text("SELECT p.target_sku,p.state,p.current_job_id,o.source_product_id,sp.factory_id "
             "FROM otto_online_polls p JOIN restart_operations o ON o.id=p.operation_id "
             "AND o.account=p.account AND o.data_scope=p.data_scope "
             "JOIN source_products sp ON sp.id=o.source_product_id AND sp.account=o.account "
             "AND sp.data_scope=o.data_scope WHERE p.operation_id=:id AND p.account=:account "
             "AND p.data_scope='PRODUCTION'"),
        {"id": operation_id, "account": normalized_account},
    ).mappings().first()
    if context is None or context["state"] != "POLLING" or str(context["current_job_id"]) != str(job_id):
        return {"state": "STALE", "published": False}
    sku = str(context["target_sku"])
    from services.otto_status_service import classify_marketplace_status_response
    classified = classify_marketplace_status_response(response, sku, 200)
    if classified.classification == "UNCHECKABLE" or classified.sku != sku:
        return _mark_online_response_unverified(
            conn, account=normalized_account, operation_id=operation_id, job_id=job_id,
            code="MARKETPLACE_STATUS_IDENTITY_UNVERIFIED", now=current,
        )
    marketplace_status = classified.marketplace_status
    online = isinstance(marketplace_status, str) and marketplace_status.strip().upper() == "ONLINE"

    # Lock in the documented order before a successful status can update the
    # operation/factory projections. A non-ONLINE observation only touches its poll row.
    if online and context["factory_id"]:
        from services.factory_service import _ensure_production_factories
        _ensure_production_factories(conn, normalized_account, [str(context["factory_id"])])
    if online:
        conn.execute(
            text("SELECT id FROM source_products WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
            {"id": context["source_product_id"], "account": normalized_account},
        ).first()

    poll = conn.execute(
        text("SELECT * FROM otto_online_polls WHERE operation_id=:id AND account=:account "
             "AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_id, "account": normalized_account},
    ).mappings().first()
    if poll is None or poll["state"] != "POLLING" or str(poll["current_job_id"]) != str(job_id):
        return {"state": "STALE", "published": False}
    observation_id = _insert_operation_observation(
        conn, account=normalized_account, operation_id=operation_id, requested_sku=sku,
        response=response, http_status=200,
    )
    if not online:
        result = _schedule_online_poll(
            conn, account=normalized_account, operation_id=operation_id, job_id=job_id,
            now=current,
            delay_seconds=min(BASE_POLL_SECONDS * (2 ** min(int(poll["poll_count"]), 4)), MAX_POLL_SECONDS),
            last_status=str(marketplace_status) if isinstance(marketplace_status, str) else None,
            error_code=None,
        )
        return {"state": "POLLING" if result.get("scheduled") else result.get("state", "NEEDS_REVIEW"),
                "observationId": observation_id, "published": False, **result}

    try:
        record_online_observation(
            conn, account=normalized_account, operation_id=operation_id,
            observation_id=observation_id, target_sku=sku,
        )
    except OperationError as error:
        raise ProcessingError(error.code, str(error)) from None
    operation = conn.execute(
        text("UPDATE restart_operations SET status='PUBLISHED',version=version+1,updated_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
             "AND submission_state='CONFIRMED' AND processing_evidence_event_id IS NOT NULL "
             "AND online_observation_id=:observation RETURNING source_product_id"),
        {"id": operation_id, "account": normalized_account, "observation": observation_id},
    ).mappings().first()
    if operation is None:
        raise ProcessingError("PUBLICATION_EVIDENCE_INCOMPLETE")
    item = conn.execute(
        text("UPDATE restart_items SET status='PUBLISHED',last_error_code=NULL,version=version+1 "
             "WHERE operation_id=:id AND account=:account AND data_scope='PRODUCTION' "
             "AND status NOT IN ('PUBLISHED','CANCELLED') RETURNING batch_id"),
        {"id": operation_id, "account": normalized_account},
    ).mappings().first()
    if item:
        conn.execute(
            text("UPDATE restart_batches b SET status='COMPLETED',version=version+1,updated_at=CURRENT_TIMESTAMP "
                 "WHERE b.id=:batch AND b.account=:account AND b.data_scope='PRODUCTION' AND b.status='ACTIVE' "
                 "AND b.item_count=(SELECT COUNT(*) FROM restart_items i WHERE i.batch_id=b.id AND i.account=b.account "
                 "AND i.data_scope='PRODUCTION') AND NOT EXISTS (SELECT 1 FROM restart_items i "
                 "WHERE i.batch_id=b.id AND i.account=b.account AND i.data_scope='PRODUCTION' AND i.status<>'PUBLISHED')"),
            {"batch": item["batch_id"], "account": normalized_account},
        )
    conn.execute(
        text("UPDATE otto_online_polls SET state='CONFIRMED',current_job_id=NULL,poll_count=poll_count+1,"
             "last_status=:status,last_error_code=NULL,completed_at=:now,updated_at=:now "
             "WHERE operation_id=:id AND account=:account"),
        {"status": str(marketplace_status)[:100], "now": current,
         "id": operation_id, "account": normalized_account},
    )
    _record_event(
        conn, account=normalized_account, actor="system:otto-online-confirmation",
        event_type="OTTO_PUBLICATION_CONFIRMED", entity_type="RESTART_OPERATION",
        entity_id=operation_id, summary="Product processing and target SKU ONLINE were both confirmed",
        metadata={"targetSku": sku, "processingEvidence": True,
                  "onlineObservationId": observation_id, "published": True},
    )
    if context["factory_id"]:
        from services.factory_service import recalculate_factory
        recalculate_factory(conn, account=normalized_account, factory_id=str(context["factory_id"]))
    return {"state": "CONFIRMED", "operationId": operation_id,
            "observationId": observation_id, "published": True}


def _mark_online_response_unverified(
    conn: Connection, *, account: str, operation_id: str, job_id: str, code: str, now: datetime,
) -> dict[str, Any]:
    poll = conn.execute(
        text("SELECT poll_count,deadline_at,state,current_job_id FROM otto_online_polls WHERE operation_id=:id "
             "AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_id, "account": account},
    ).mappings().first()
    if poll is None or poll["state"] != "POLLING" or str(poll["current_job_id"]) != str(job_id):
        return {"state": "STALE", "published": False}
    if _now(now) >= poll["deadline_at"]:
        _mark_online_review(conn, account=account, operation_id=operation_id, code=code)
        conn.execute(
            text("UPDATE otto_online_polls SET state='DEADLINE',current_job_id=NULL,last_error_code=:code,"
                 "completed_at=:now,updated_at=:now WHERE operation_id=:id AND account=:account"),
            {"code": _safe_code(code, "ONLINE_CONFIRMATION_UNVERIFIED"), "now": _now(now),
             "id": operation_id, "account": account},
        )
        return {"state": "DEADLINE", "published": False}
    return defer_online_poll(
        conn, account=account, operation_id=operation_id, job_id=job_id, now=now,
        error_code=code,
    )


def record_online_poll_failure(
    conn: Connection, *, account: str, operation_id: str, job_id: str,
    error_code: str, retryable: bool, retry_after_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _now(now)
    if retryable:
        return defer_online_poll(
            conn, account=account, operation_id=operation_id, job_id=job_id, now=current,
            delay_seconds=int(retry_after_seconds or 0), error_code=error_code,
        )
    poll = conn.execute(
        text("SELECT state,current_job_id FROM otto_online_polls WHERE operation_id=:id "
             "AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_id, "account": account},
    ).mappings().first()
    if poll is None or poll["state"] != "POLLING" or str(poll["current_job_id"]) != str(job_id):
        return {"state": "STALE", "published": False}
    code = _safe_code(error_code, "MARKETPLACE_STATUS_READ_FAILED")
    _mark_online_review(conn, account=account, operation_id=operation_id, code=code)
    conn.execute(
        text("UPDATE otto_online_polls SET state='NEEDS_REVIEW',current_job_id=NULL,last_error_code=:code,"
             "completed_at=:now,updated_at=:now WHERE operation_id=:id AND account=:account"),
        {"code": code, "now": current, "id": operation_id, "account": account},
    )
    return {"state": "NEEDS_REVIEW", "errorCode": code, "published": False}


def enqueue_online_after_availability(
    conn: Connection, *, account: str, operation_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper; ONLINE polling no longer depends on availability."""
    return maybe_enqueue_online_confirmation(conn, account=account, operation_id=operation_id, now=now)

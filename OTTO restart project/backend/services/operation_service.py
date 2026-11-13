"""Account-scoped Restart operation history and authorization primitives.

This module deliberately does not prepare product data, run QC, or call OTTO.
Those actions belong to later stages and must supply their own verified inputs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.gtin_service import validate_gtin_eligibility

PRODUCTION_SCOPE = "PRODUCTION"
_HISTORY_DECISIONS = {"CONFIRMED_TEST", "CONFIRMED_PRODUCTION", "INSUFFICIENT"}


class OperationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise OperationError("INVALID_ID", f"{field} must be a UUID.") from None


def _require_account(account: str) -> None:
    if account not in {"JV", "XL"}:
        raise OperationError("INVALID_ACCOUNT", "Account must be JV or XL.")


def _require_expiry(expires_at: datetime) -> datetime:
    if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
        raise OperationError("INVALID_EXPIRY", "Authorization expiry must include a timezone.")
    expiry = expires_at.astimezone(timezone.utc)
    if expiry <= datetime.now(timezone.utc):
        raise OperationError("AUTHORIZATION_EXPIRED", "Authorization expiry must be in the future.")
    return expiry


def _canonical_json(value: Any, field: str) -> tuple[str, Any]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        normalized = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise OperationError("INVALID_JSON", f"{field} must contain valid JSON data.") from None
    return encoded, normalized


def _record_event(
    conn: Connection,
    *,
    account: str,
    actor: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    summary: str,
    metadata: dict[str, Any],
) -> str:
    event_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO restart_events "
            "(id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json, created_at) "
            "VALUES (:id, :account, 'PRODUCTION', :event_type, :entity_type, :entity_id, :summary, "
            "CAST(:metadata AS jsonb), CURRENT_TIMESTAMP)"
        ),
        {
            "id": event_id,
            "account": account,
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "summary": summary[:500],
            "metadata": json.dumps({"actor": actor, **metadata}, ensure_ascii=False, separators=(",", ":"), default=str),
        },
    )
    return event_id


def _void_expired_authorizations(
    conn: Connection, *, account: str, source_product_id: str, candidate_episode_id: str, actor: str
) -> int:
    """Expire stale open grants so they cannot block replacement or be reused later."""
    rows = conn.execute(
        text(
            "SELECT id, authorization_type FROM restart_authorizations WHERE account=:account "
            "AND data_scope='PRODUCTION' AND source_product_id=:source_id AND candidate_episode_id=:episode_id "
            "AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at <= CURRENT_TIMESTAMP FOR UPDATE"
        ),
        {"account": account, "source_id": source_product_id, "episode_id": candidate_episode_id},
    ).mappings().all()
    for row in rows:
        conn.execute(
            text(
                "UPDATE restart_authorizations SET revoked_at=CURRENT_TIMESTAMP, revoked_by=:actor, "
                "revocation_reason='Expired authorization replaced after its validity window.', version=version+1 "
                "WHERE id=:id AND consumed_at IS NULL AND revoked_at IS NULL"
            ),
            {"actor": actor[:200], "id": row["id"]},
        )
        _record_event(
            conn,
            account=account,
            actor=actor,
            event_type="RESTART_AUTHORIZATION_EXPIRED",
            entity_type="RESTART_AUTHORIZATION",
            entity_id=str(row["id"]),
            summary="Expired Restart authorization was voided",
            metadata={"authorizationType": row["authorization_type"], "sourceProductId": source_product_id},
        )
    return len(rows)


def _lock_source_episode(
    conn: Connection, *, account: str, source_product_id: str, candidate_episode_id: str
) -> dict[str, Any]:
    _require_account(account)
    source_id = _uuid(source_product_id, "sourceProductId")
    episode_id = _uuid(candidate_episode_id, "candidateEpisodeId")
    row = conn.execute(
        text(
            "SELECT sp.id AS source_product_id, sp.account, sp.data_scope, sp.aftercool_ean, "
            "ce.id AS candidate_episode_id, ce.observed_sku, ce.confirmed_ean, ce.period, "
            "ce.classification, ce.origin, ce.episode_state "
            "FROM source_products sp JOIN candidate_episodes ce "
            "ON ce.source_product_id = sp.id AND ce.account = sp.account AND ce.data_scope = sp.data_scope "
            "WHERE sp.id = :source_id AND ce.id = :episode_id AND sp.account = :account "
            "AND sp.data_scope = 'PRODUCTION' AND ce.data_scope = 'PRODUCTION' "
            "FOR UPDATE OF sp, ce"
        ),
        {"source_id": source_id, "episode_id": episode_id, "account": account},
    ).mappings().first()
    if row is None:
        raise OperationError("CANDIDATE_NOT_FOUND", "A production source product and candidate episode were not found together.")
    if row["episode_state"] != "OPEN":
        raise OperationError("CANDIDATE_CLOSED", "A closed restriction episode cannot start a Restart operation.")
    if row["classification"] != "EXACT_CODE":
        raise OperationError("CANDIDATE_UNVERIFIED", "Only an exact OTTO error-code classification can start a production operation.")
    return dict(row)


def _history_rows(conn: Connection, *, account: str, source_product_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            text(
                "SELECT id, attempt_number, status, submission_state, created_at "
                "FROM restart_operations WHERE account = :account AND data_scope = 'PRODUCTION' "
                "AND source_product_id = :source_id ORDER BY attempt_number, created_at, id FOR UPDATE"
            ),
            {"account": account, "source_id": source_product_id},
        ).mappings()
    ]


def _legacy_history_exists(conn: Connection, *, account: str, source: dict[str, Any]) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT EXISTS ("
                " SELECT 1 FROM restart_operations WHERE account=:account AND data_scope='LEGACY_UNVERIFIED' "
                "   AND source_product_id=:source_id"
                " UNION ALL"
                " SELECT 1 FROM ean_links WHERE account=:account AND data_scope='LEGACY_UNVERIFIED' "
                "   AND source_product_id=:source_id"
                " UNION ALL"
                " SELECT 1 FROM source_products WHERE account=:account AND data_scope='LEGACY_UNVERIFIED' "
                "   AND CAST(:source_ean AS text) IS NOT NULL AND aftercool_ean=CAST(:source_ean AS text)"
                " UNION ALL"
                " SELECT 1 FROM otto_library WHERE account=:account AND data_scope='LEGACY_UNVERIFIED' "
                "   AND old_ean IS NOT DISTINCT FROM CAST(:source_ean AS text)"
                ")"
            ),
            {"account": account, "source_id": source["source_product_id"], "source_ean": source["aftercool_ean"]},
        ).scalar_one()
    )


def _validate_reason(value: str, field: str = "reason") -> str:
    reason = value.strip() if isinstance(value, str) else ""
    if not 20 <= len(reason) <= 2000:
        raise OperationError("INVALID_REASON", f"{field} must contain between 20 and 2000 characters.")
    return reason


def create_history_review(
    conn: Connection,
    *,
    account: str,
    source_product_id: str,
    candidate_episode_id: str,
    decision: str,
    confirmed_prior_attempt_count: int,
    reviewed_through_operation_id: str | None,
    reason: str,
    actor: str,
    expires_at: datetime,
) -> dict[str, Any]:
    """Record an explicit review of prior Restart history; never infer its outcome."""
    source = _lock_source_episode(
        conn, account=account, source_product_id=source_product_id, candidate_episode_id=candidate_episode_id
    )
    _void_expired_authorizations(
        conn,
        account=account,
        source_product_id=source["source_product_id"],
        candidate_episode_id=source["candidate_episode_id"],
        actor=actor,
    )
    if decision not in _HISTORY_DECISIONS:
        raise OperationError("INVALID_HISTORY_DECISION", "Unsupported Restart history decision.")
    if type(confirmed_prior_attempt_count) is not int or confirmed_prior_attempt_count < 0:
        raise OperationError("INVALID_ATTEMPT_COUNT", "Confirmed prior attempt count must be a non-negative integer.")
    if decision == "CONFIRMED_TEST" and confirmed_prior_attempt_count != 0:
        raise OperationError("INVALID_HISTORY_DECISION", "A test-only history must have zero confirmed production attempts.")
    if decision == "CONFIRMED_PRODUCTION" and confirmed_prior_attempt_count < 1:
        raise OperationError("INVALID_HISTORY_DECISION", "Confirmed production history must include at least one attempt.")
    history = _history_rows(conn, account=account, source_product_id=source["source_product_id"])
    latest_id = str(history[-1]["id"]) if history else None
    reviewed_id = _uuid(reviewed_through_operation_id, "reviewedThroughOperationId") if reviewed_through_operation_id else None
    if reviewed_id != latest_id:
        raise OperationError(
            "HISTORY_REVIEW_STALE",
            "History review must identify the latest known production operation, or omit it when none exists.",
            {"latestOperationId": latest_id},
        )
    if confirmed_prior_attempt_count < len(history):
        raise OperationError("HISTORY_COUNT_INCOMPLETE", "The reviewed prior-attempt count is below the known production operation count.")
    if decision == "CONFIRMED_TEST" and any(row["submission_state"] != "NEVER_SUBMITTED" for row in history):
        raise OperationError("HISTORY_CONFLICT", "A previously submitted operation cannot be classified as test-only.")

    reason_text = _validate_reason(reason)
    expiry = _require_expiry(expires_at)
    auth_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO restart_authorizations "
            "(id, account, data_scope, source_product_id, candidate_episode_id, authorization_type, decision, "
            "confirmed_prior_attempt_count, reviewed_through_operation_id, reason, created_by, expires_at) "
            "VALUES (:id, :account, 'PRODUCTION', :source_id, :episode_id, 'HISTORY_REVIEW', :decision, "
            ":attempt_count, :latest_id, :reason, :actor, :expires_at)"
        ),
        {
            "id": auth_id,
            "account": account,
            "source_id": source["source_product_id"],
            "episode_id": source["candidate_episode_id"],
            "decision": decision,
            "attempt_count": confirmed_prior_attempt_count,
            "latest_id": latest_id,
            "reason": reason_text,
            "actor": actor[:200],
            "expires_at": expiry,
        },
    )
    _record_event(
        conn,
        account=account,
        actor=actor,
        event_type="RESTART_HISTORY_REVIEWED",
        entity_type="RESTART_AUTHORIZATION",
        entity_id=auth_id,
        summary="Restart history review recorded",
        metadata={
            "sourceProductId": source["source_product_id"],
            "candidateEpisodeId": source["candidate_episode_id"],
            "decision": decision,
            "confirmedPriorAttemptCount": confirmed_prior_attempt_count,
            "reviewedThroughOperationId": latest_id,
            "reason": reason_text,
        },
    )
    return {
        "id": auth_id,
        "account": account,
        "sourceProductId": str(source["source_product_id"]),
        "candidateEpisodeId": str(source["candidate_episode_id"]),
        "authorizationType": "HISTORY_REVIEW",
        "decision": decision,
        "confirmedPriorAttemptCount": confirmed_prior_attempt_count,
        "reviewedThroughOperationId": latest_id,
        "expiresAt": expiry.isoformat(),
        "consumed": False,
    }


def create_retry_authorization(
    conn: Connection,
    *,
    account: str,
    source_product_id: str,
    candidate_episode_id: str,
    history_review_id: str,
    reason: str,
    actor: str,
    expires_at: datetime,
) -> dict[str, Any]:
    """Issue a separate, single-use authorization for one later business attempt."""
    source = _lock_source_episode(
        conn, account=account, source_product_id=source_product_id, candidate_episode_id=candidate_episode_id
    )
    _void_expired_authorizations(
        conn,
        account=account,
        source_product_id=source["source_product_id"],
        candidate_episode_id=source["candidate_episode_id"],
        actor=actor,
    )
    history_id = _uuid(history_review_id, "historyReviewId")
    review = conn.execute(
        text(
            "SELECT * FROM restart_authorizations WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
            "AND source_product_id=:source_id AND candidate_episode_id=:episode_id "
            "AND authorization_type='HISTORY_REVIEW' AND consumed_at IS NULL FOR UPDATE"
        ),
        {"id": history_id, "account": account, "source_id": source["source_product_id"], "episode_id": source["candidate_episode_id"]},
    ).mappings().first()
    if review is None or review["expires_at"] <= datetime.now(timezone.utc):
        raise OperationError("HISTORY_REVIEW_REQUIRED", "A current unconsumed history review is required.")
    if review["decision"] != "CONFIRMED_PRODUCTION" or review["confirmed_prior_attempt_count"] < 1:
        raise OperationError("RETRY_NOT_AUTHORIZED", "A repeat Restart requires confirmed production history.")

    history = _history_rows(conn, account=account, source_product_id=source["source_product_id"])
    max_known_attempt = max((int(row["attempt_number"] or 0) for row in history), default=0)
    expected_attempt = int(review["confirmed_prior_attempt_count"]) + 1
    if expected_attempt <= max_known_attempt:
        raise OperationError("HISTORY_COUNT_INCOMPLETE", "The reviewed history does not cover all known operation attempts.")
    if history and str(history[-1]["id"]) != str(review["reviewed_through_operation_id"]):
        raise OperationError("HISTORY_REVIEW_STALE", "A new operation appeared after the history review.")

    expiry = _require_expiry(expires_at)
    reason_text = _validate_reason(reason)
    auth_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO restart_authorizations "
            "(id, account, data_scope, source_product_id, candidate_episode_id, authorization_type, decision, "
            "confirmed_prior_attempt_count, expected_attempt_number, reviewed_through_operation_id, reason, created_by, expires_at) "
            "VALUES (:id, :account, 'PRODUCTION', :source_id, :episode_id, 'RESTART_RETRY', 'AUTHORIZED', "
            ":attempt_count, :expected_attempt, :latest_id, :reason, :actor, :expires_at)"
        ),
        {
            "id": auth_id,
            "account": account,
            "source_id": source["source_product_id"],
            "episode_id": source["candidate_episode_id"],
            "attempt_count": review["confirmed_prior_attempt_count"],
            "expected_attempt": expected_attempt,
            "latest_id": review["reviewed_through_operation_id"],
            "reason": reason_text,
            "actor": actor[:200],
            "expires_at": expiry,
        },
    )
    _record_event(
        conn,
        account=account,
        actor=actor,
        event_type="RESTART_RETRY_AUTHORIZED",
        entity_type="RESTART_AUTHORIZATION",
        entity_id=auth_id,
        summary="A single Restart retry was authorized",
        metadata={
            "sourceProductId": source["source_product_id"],
            "candidateEpisodeId": source["candidate_episode_id"],
            "historyReviewId": history_id,
            "expectedAttemptNumber": expected_attempt,
            "reason": reason_text,
        },
    )
    return {
        "id": auth_id,
        "account": account,
        "sourceProductId": str(source["source_product_id"]),
        "candidateEpisodeId": str(source["candidate_episode_id"]),
        "authorizationType": "RESTART_RETRY",
        "decision": "AUTHORIZED",
        "expectedAttemptNumber": expected_attempt,
        "historyReviewId": history_id,
        "expiresAt": expiry.isoformat(),
        "consumed": False,
    }


def _consume_authorization(conn: Connection, authorization: dict[str, Any], operation_id: str) -> None:
    conn.execute(
        text(
            "UPDATE restart_authorizations SET consumed_at=CURRENT_TIMESTAMP, consumed_operation_id=:operation_id, "
            "version=version+1 WHERE id=:id AND account=:account AND data_scope='PRODUCTION' AND consumed_at IS NULL"
        ),
        {"operation_id": operation_id, "id": authorization["id"], "account": authorization["account"]},
    )


def create_operation(
    conn: Connection,
    *,
    account: str,
    candidate_episode_id: str,
    actor: str,
    history_review_id: str | None = None,
    retry_authorization_id: str | None = None,
) -> dict[str, Any]:
    """Create the first business attempt or consume explicit single-use approvals."""
    episode_uuid = _uuid(candidate_episode_id, "candidateEpisodeId")
    episode = conn.execute(
        text(
            "SELECT sp.id AS source_product_id, sp.account, sp.data_scope, sp.aftercool_ean, ce.id AS episode_id, "
            "ce.observed_sku, ce.confirmed_ean, ce.period, ce.classification, ce.episode_state "
            "FROM candidate_episodes ce JOIN source_products sp ON sp.id=ce.source_product_id "
            "AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
            "WHERE ce.id=:episode_id AND ce.account=:account AND ce.data_scope='PRODUCTION' FOR UPDATE OF sp, ce"
        ),
        {"episode_id": episode_uuid, "account": account},
    ).mappings().first()
    if episode is None:
        raise OperationError("CANDIDATE_NOT_FOUND", "A production candidate episode was not found.")
    source = _lock_source_episode(
        conn,
        account=account,
        source_product_id=str(episode["source_product_id"]),
        candidate_episode_id=episode_uuid,
    )
    if conn.execute(
        text("SELECT 1 FROM restart_operations WHERE account=:account AND candidate_episode_id=:episode_id AND data_scope='PRODUCTION'"),
        {"account": account, "episode_id": episode_uuid},
    ).first():
        raise OperationError("OPERATION_ALREADY_EXISTS", "This candidate episode already has a Restart operation.")
    if conn.execute(
        text(
            "SELECT 1 FROM restart_operations WHERE account=:account AND source_product_id=:source_id "
            "AND data_scope='PRODUCTION' AND status NOT IN ('PUBLISHED','FAILED','CANCELLED') LIMIT 1"
        ),
        {"account": account, "source_id": source["source_product_id"]},
    ).first():
        raise OperationError("OPERATION_ALREADY_OPEN", "This source product already has an open Restart operation.")
    history = _history_rows(conn, account=account, source_product_id=source["source_product_id"])
    legacy_overlap = _legacy_history_exists(conn, account=account, source=source)
    review = None
    retry_auth = None
    attempt_number = 1

    if history or legacy_overlap or history_review_id is not None or retry_authorization_id is not None:
        if not history_review_id:
            raise OperationError("HISTORY_REVIEW_REQUIRED", "Existing Restart or legacy lineage requires an explicit history review.")
        review_id = _uuid(history_review_id, "historyReviewId")
        review = conn.execute(
            text(
                "SELECT * FROM restart_authorizations WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
                "AND source_product_id=:source_id AND candidate_episode_id=:episode_id "
                "AND authorization_type='HISTORY_REVIEW' AND consumed_at IS NULL FOR UPDATE"
            ),
            {
                "id": review_id,
                "account": account,
                "source_id": source["source_product_id"],
                "episode_id": episode_uuid,
            },
        ).mappings().first()
        if review is None or review["expires_at"] <= datetime.now(timezone.utc):
            raise OperationError("HISTORY_REVIEW_REQUIRED", "A current unconsumed history review is required.")
        if review["decision"] == "INSUFFICIENT":
            raise OperationError("HISTORY_UNVERIFIED", "Insufficient history evidence blocks a new business Restart.")
        max_known_attempt = max((int(row["attempt_number"] or 0) for row in history), default=0)
        if review["confirmed_prior_attempt_count"] < max_known_attempt:
            raise OperationError("HISTORY_COUNT_INCOMPLETE", "The reviewed count is below a known attempt number.")
        attempt_number = int(review["confirmed_prior_attempt_count"]) + 1
        if attempt_number > 1:
            if review["decision"] != "CONFIRMED_PRODUCTION" or not retry_authorization_id:
                raise OperationError("RESTART_RETRY_REQUIRED", "A repeat business Restart needs a separate retry authorization.")
            retry_id = _uuid(retry_authorization_id, "retryAuthorizationId")
            retry_auth = conn.execute(
                text(
                    "SELECT * FROM restart_authorizations WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
                    "AND source_product_id=:source_id AND candidate_episode_id=:episode_id "
                    "AND authorization_type='RESTART_RETRY' AND decision='AUTHORIZED' AND consumed_at IS NULL FOR UPDATE"
                ),
                {
                    "id": retry_id,
                    "account": account,
                    "source_id": source["source_product_id"],
                    "episode_id": episode_uuid,
                },
            ).mappings().first()
            if (
                retry_auth is None
                or retry_auth["expires_at"] <= datetime.now(timezone.utc)
                or int(retry_auth["expected_attempt_number"] or 0) != attempt_number
                or retry_auth["reviewed_through_operation_id"] != review["reviewed_through_operation_id"]
            ):
                raise OperationError("RESTART_RETRY_INVALID", "Retry authorization is expired or does not match the reviewed attempt.")
        elif retry_authorization_id:
            raise OperationError("RESTART_RETRY_UNEXPECTED", "A retry authorization is not valid for the first business attempt.")
    elif retry_authorization_id:
        raise OperationError("RESTART_RETRY_UNEXPECTED", "A retry authorization requires a history review.")

    if source["classification"] != "EXACT_CODE":
        raise OperationError("CANDIDATE_UNVERIFIED", "Only exact-code OTTO status can create a production operation.")
    if source["episode_state"] != "OPEN":
        raise OperationError("CANDIDATE_CLOSED", "A closed restriction episode cannot start a Restart operation.")
    operation_id = str(uuid4())
    conn.execute(
        text(
            "INSERT INTO restart_operations "
            "(id, account, data_scope, source_product_id, candidate_episode_id, period, status, attempt_number, "
            "target_sku, created_by, history_review_id, repeat_authorization_id, created_at, updated_at) "
            "VALUES (:id, :account, 'PRODUCTION', :source_id, :episode_id, :period, 'DRAFT', :attempt_number, "
            "NULL, :actor, :review_id, :retry_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": operation_id,
            "account": account,
            "source_id": source["source_product_id"],
            "episode_id": episode_uuid,
            "period": source["period"],
            "attempt_number": attempt_number,
            "actor": actor[:200],
            "review_id": review["id"] if review else None,
            "retry_id": retry_auth["id"] if retry_auth else None,
        },
    )
    if retry_auth:
        _consume_authorization(conn, dict(retry_auth), operation_id)
    if review:
        _consume_authorization(conn, dict(review), operation_id)
    _record_event(
        conn,
        account=account,
        actor=actor,
        event_type="RESTART_OPERATION_CREATED",
        entity_type="RESTART_OPERATION",
        entity_id=operation_id,
        summary="Restart operation created from a confirmed restriction episode",
        metadata={
            "sourceProductId": source["source_product_id"],
            "candidateEpisodeId": episode_uuid,
            "period": source["period"],
            "attemptNumber": attempt_number,
            "historyReviewId": review["id"] if review else None,
            "retryAuthorizationId": retry_auth["id"] if retry_auth else None,
        },
    )
    return {
        "id": operation_id,
        "account": account,
        "sourceProductId": str(source["source_product_id"]),
        "candidateEpisodeId": episode_uuid,
        "period": source["period"],
        "attemptNumber": attempt_number,
        "status": "DRAFT",
        "targetSku": None,
        "newEan": None,
        "version": 1,
        "revisionNumber": 0,
        "submissionState": "NEVER_SUBMITTED",
        "historyReviewId": review["id"] if review else None,
        "retryAuthorizationId": retry_auth["id"] if retry_auth else None,
    }


def append_operation_revision(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    source_snapshot: dict[str, Any],
    content: dict[str, Any],
    price_snapshot: dict[str, Any],
    fulfillment_snapshot: dict[str, Any],
    gtin_evidence_ids: Iterable[str],
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Append an immutable version; S13+ owns trust/semantic validation of the snapshots."""
    _require_account(account)
    op_id = _uuid(operation_id, "operationId")
    if type(expected_version) is not int or expected_version < 1:
        raise OperationError("INVALID_VERSION", "Expected operation version must be a positive integer.")
    snapshot_values = {
        "source": source_snapshot,
        "content": content,
        "price": price_snapshot,
        "fulfillment": fulfillment_snapshot,
        "payload": payload,
    }
    encoded: dict[str, str] = {}
    for key, value in snapshot_values.items():
        if not isinstance(value, dict):
            raise OperationError("INVALID_SNAPSHOT", f"{key} snapshot must be a JSON object.")
        encoded[key], _ = _canonical_json(value, f"{key} snapshot")
    evidence_ids = sorted({_uuid(str(item), "gtinEvidenceId") for item in gtin_evidence_ids})

    operation = conn.execute(
        text(
            "SELECT id, account, source_product_id, new_ean, status, submission_state, version, current_revision_number "
            "FROM restart_operations WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"
        ),
        {"id": op_id, "account": account},
    ).mappings().first()
    if operation is None:
        raise OperationError("OPERATION_NOT_FOUND", "Production operation was not found.")
    if int(operation["version"]) != expected_version:
        raise OperationError(
            "VERSION_CONFLICT",
            "Operation changed since it was read.",
            {"expectedVersion": expected_version, "currentVersion": operation["version"]},
        )
    if operation["status"] not in {"DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"}:
        raise OperationError("OPERATION_NOT_EDITABLE", "Only a draft or reviewed operation can receive a new revision.")
    if operation["submission_state"] != "NEVER_SUBMITTED":
        raise OperationError("OPERATION_NOT_EDITABLE", "A submitted operation cannot be revised.")
    if evidence_ids:
        bound_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM gtin_evidence WHERE account=:account AND data_scope='PRODUCTION' "
                "AND source_product_id=:source_id AND ean IS NOT DISTINCT FROM :ean "
                "AND id::text = ANY(CAST(:ids AS text[]))"
            ),
            {
                "account": account,
                "source_id": str(operation["source_product_id"]),
                "ean": operation["new_ean"],
                "ids": evidence_ids,
            },
        ).scalar_one()
        if int(bound_count) != len(evidence_ids):
            raise OperationError("GTIN_EVIDENCE_SCOPE_MISMATCH", "Revision evidence must be bound to this account, source product, and operation GTIN.")

    revision_number = int(operation["current_revision_number"]) + 1
    revision_id = str(uuid4())
    serialized_evidence_ids, evidence_json = _canonical_json(evidence_ids, "gtinEvidenceIds")
    payload_hash = hashlib.sha256(encoded["payload"].encode("utf-8")).hexdigest()
    conn.execute(
        text(
            "INSERT INTO operation_revisions (id, operation_id, account, data_scope, revision_number, "
            "source_snapshot_json, content_json, price_snapshot_json, fulfillment_snapshot_json, "
            "gtin_evidence_ids_json, payload_json, payload_hash, created_by) VALUES "
            "(:id, :operation_id, :account, 'PRODUCTION', :revision, CAST(:source AS jsonb), CAST(:content AS jsonb), "
            "CAST(:price AS jsonb), CAST(:fulfillment AS jsonb), CAST(:evidence AS jsonb), CAST(:payload AS jsonb), :hash, :actor)"
        ),
        {
            "id": revision_id,
            "operation_id": op_id,
            "account": account,
            "revision": revision_number,
            "source": encoded["source"],
            "content": encoded["content"],
            "price": encoded["price"],
            "fulfillment": encoded["fulfillment"],
            "evidence": serialized_evidence_ids,
            "payload": encoded["payload"],
            "hash": payload_hash,
            "actor": actor[:200],
        },
    )
    conn.execute(
        text(
            "UPDATE restart_operations SET current_revision_number=:revision, "
            "status=CASE WHEN status IN ('READY','QC_PENDING') THEN 'NEEDS_REVIEW' ELSE status END, version=version+1, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account AND version=:expected"
        ),
        {"revision": revision_number, "id": op_id, "account": account, "expected": expected_version},
    )
    _record_event(
        conn,
        account=account,
        actor=actor,
        event_type="RESTART_REVISION_CREATED",
        entity_type="RESTART_OPERATION",
        entity_id=op_id,
        summary="Immutable Restart payload revision created",
        metadata={"revisionId": revision_id, "revisionNumber": revision_number, "payloadHash": payload_hash},
    )
    return {
        "id": revision_id,
        "operationId": op_id,
        "account": account,
        "revisionNumber": revision_number,
        "payloadHash": payload_hash,
        "gtinEvidenceIds": evidence_json,
        "createdBy": actor[:200],
    }


def begin_submission(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    approved_evidence_types: Iterable[str] = (),
) -> dict[str, Any]:
    """Durably mark the operation in flight; this helper performs no network request."""
    return _set_submission_state(
        conn,
        account=account,
        operation_id=operation_id,
        expected_version=expected_version,
        state="IN_FLIGHT",
        status="SUBMITTING",
        approved_evidence_types=approved_evidence_types,
    )


def mark_submission_unknown(conn: Connection, *, account: str, operation_id: str) -> dict[str, Any]:
    """Retain the EAN reservation when a future submission result is uncertain."""
    operation = _lock_operation(conn, account=account, operation_id=operation_id)
    if operation["submission_state"] == "CONFIRMED":
        raise OperationError("SUBMISSION_ALREADY_CONFIRMED", "A confirmed submission cannot be downgraded to unknown.")
    if operation["submission_state"] not in {"IN_FLIGHT", "UNKNOWN"}:
        raise OperationError("SUBMISSION_NOT_IN_FLIGHT", "An operation can be marked unknown only after submission has begun.")
    if operation["submission_state"] != "UNKNOWN":
        conn.execute(
            text(
                "UPDATE restart_operations SET submission_state='UNKNOWN', status='RECONCILIATION_REQUIRED', "
                "version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"
            ),
            {"id": operation["id"], "account": account},
        )
    return {"id": str(operation["id"]), "status": "RECONCILIATION_REQUIRED", "submissionState": "UNKNOWN"}


def _lock_operation(conn: Connection, *, account: str, operation_id: str) -> dict[str, Any]:
    _require_account(account)
    op_id = _uuid(operation_id, "operationId")
    row = conn.execute(
        text("SELECT * FROM restart_operations WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": op_id, "account": account},
    ).mappings().first()
    if row is None:
        raise OperationError("OPERATION_NOT_FOUND", "Production operation was not found.")
    return dict(row)


def _set_submission_state(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    state: str,
    status: str,
    approved_evidence_types: Iterable[str] = (),
) -> dict[str, Any]:
    operation = _lock_operation(conn, account=account, operation_id=operation_id)
    if operation["version"] != expected_version:
        raise OperationError("VERSION_CONFLICT", "Operation changed since it was read.", {"currentVersion": operation["version"]})
    if operation["submission_state"] != "NEVER_SUBMITTED":
        raise OperationError("SUBMISSION_ALREADY_STARTED", "Submission cannot start again as a new business attempt.")
    expected_status = {"IN_FLIGHT": "SUBMITTING", "UNKNOWN": "RECONCILIATION_REQUIRED"}.get(state)
    if expected_status is None or status != expected_status:
        raise OperationError(
            "SUBMISSION_TRANSITION_INVALID",
            "Submission state may only transition to its fixed non-READY operation status.",
        )
    if state == "IN_FLIGHT" and (
        operation["status"] != "READY"
        or not operation["target_sku"]
        or not operation["new_ean"]
        or int(operation["current_revision_number"]) < 1
    ):
        raise OperationError("OPERATION_NOT_READY", "Submission requires a READY operation, target SKU, GTIN, and immutable revision.")
    if state == "IN_FLIGHT":
        revision = conn.execute(
            text(
                "SELECT id, payload_hash FROM operation_revisions WHERE operation_id=:id AND account=:account "
                "AND data_scope='PRODUCTION' AND revision_number=:revision"
            ),
            {"id": operation["id"], "account": account, "revision": operation["current_revision_number"]},
        ).mappings().first()
        qc = conn.execute(
            text(
                "SELECT outcome, payload_hash FROM qc_results WHERE operation_id=:id AND account=:account "
                "AND data_scope='PRODUCTION' AND revision_id=:revision_id "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"id": operation["id"], "account": account, "revision_id": revision["id"] if revision else None},
        ).mappings().first() if revision else None
        if (
            revision is None
            or qc is None
            or qc["outcome"] != "PASSED_AUTO"
            or qc["payload_hash"].strip() != revision["payload_hash"].strip()
        ):
            raise OperationError("OPERATION_QC_REQUIRED", "Submission requires an auto-passed QC result for the current immutable revision.")
        eligibility = validate_gtin_eligibility(
            conn,
            account=account,
            ean=operation["new_ean"],
            source_product_id=str(operation["source_product_id"]),
            approved_evidence_types=approved_evidence_types,
            operation_id=str(operation["id"]),
        )
        if not eligibility.eligible:
            raise OperationError(eligibility.code, eligibility.message)
    conn.execute(
        text(
            "UPDATE restart_operations SET submission_state=:state, status=:status, version=version+1, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"
        ),
        {"state": state, "status": status, "id": operation["id"], "account": account},
    )
    return {"id": str(operation["id"]), "status": status, "submissionState": state, "version": int(operation["version"]) + 1}


def record_processing_evidence(
    conn: Connection, *, account: str, operation_id: str, event_id: str, target_sku: str
) -> dict[str, Any]:
    """Link a verified per-SKU processing event, only after target identity is explicit."""
    operation = _lock_operation(conn, account=account, operation_id=operation_id)
    if operation["submission_state"] not in {"IN_FLIGHT", "UNKNOWN"}:
        raise OperationError("PROCESSING_EVIDENCE_STATE_INVALID", "Processing evidence requires an in-flight or reconciled submission.")
    sku = target_sku.strip() if isinstance(target_sku, str) else ""
    if not sku or not operation["target_sku"] or sku != operation["target_sku"]:
        raise OperationError("TARGET_SKU_UNVERIFIED", "Processing evidence requires an explicitly verified matching target SKU.")
    event_uuid = _uuid(event_id, "eventId")
    event = conn.execute(
        text(
            "SELECT id, entity_type, entity_id, metadata_json FROM restart_events "
            "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR SHARE"
        ),
        {"id": event_uuid, "account": account},
    ).mappings().first()
    if event is None or event["entity_type"] != "OTTO_PROCESSING_RESULT" or str(event["entity_id"]) != str(operation["id"]):
        raise OperationError("PROCESSING_EVIDENCE_INVALID", "Processing event is not bound to this operation and account.")
    metadata = event["metadata_json"] or {}
    if metadata.get("targetSku") != sku or metadata.get("result") != "SUCCEEDED":
        raise OperationError("PROCESSING_EVIDENCE_INVALID", "Processing event does not confirm this target SKU succeeded.")
    conn.execute(
        text(
            "UPDATE restart_operations SET processing_evidence_event_id=:event_id, submission_state='CONFIRMED', "
            "status='AWAITING_ONLINE', version=version+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=:id AND account=:account"
        ),
        {"event_id": event_uuid, "id": operation["id"], "account": account},
    )
    return {"operationId": str(operation["id"]), "processingEvidenceEventId": event_uuid, "submissionState": "CONFIRMED"}


def record_online_observation(
    conn: Connection, *, account: str, operation_id: str, observation_id: str, target_sku: str
) -> dict[str, Any]:
    """Link a fresh ONLINE observation only for the same explicit target SKU/account."""
    operation = _lock_operation(conn, account=account, operation_id=operation_id)
    if operation["submission_state"] != "CONFIRMED" or not operation["processing_evidence_event_id"]:
        raise OperationError("PROCESSING_EVIDENCE_REQUIRED", "ONLINE evidence cannot be attached before successful OTTO processing is confirmed.")
    sku = target_sku.strip() if isinstance(target_sku, str) else ""
    if not sku or not operation["target_sku"] or sku != operation["target_sku"]:
        raise OperationError("TARGET_SKU_UNVERIFIED", "ONLINE evidence requires an explicitly verified matching target SKU.")
    observation_uuid = _uuid(observation_id, "observationId")
    observation = conn.execute(
        text(
            "SELECT requested_sku, response_sku, market_place_status, observed_at FROM otto_status_observations "
            "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR SHARE"
        ),
        {"id": observation_uuid, "account": account},
    ).mappings().first()
    if (
        observation is None
        or observation["requested_sku"] != sku
        or observation["response_sku"] != sku
        or observation["market_place_status"] != "ONLINE"
    ):
        raise OperationError("ONLINE_EVIDENCE_INVALID", "Marketplace observation does not show this target SKU as ONLINE.")
    conn.execute(
        text(
            "UPDATE restart_operations SET online_observation_id=:observation_id, version=version+1, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"
        ),
        {"observation_id": observation_uuid, "id": operation["id"], "account": account},
    )
    return {"operationId": str(operation["id"]), "onlineObservationId": observation_uuid, "marketPlaceStatus": "ONLINE"}


def cancel_operation(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    actor: str,
    approved_evidence_types: Iterable[str] = (),
) -> dict[str, Any]:
    """Cancel only a never-submitted operation; uncertain submissions stay reserved."""
    operation = _lock_operation(conn, account=account, operation_id=operation_id)
    if int(operation["version"]) != expected_version:
        raise OperationError("VERSION_CONFLICT", "Operation changed since it was read.", {"currentVersion": operation["version"]})
    if operation["status"] in {"PUBLISHED", "CANCELLED"}:
        raise OperationError("OPERATION_TERMINAL", "A published or cancelled operation cannot be cancelled again.")
    if operation["submission_state"] == "CONFIRMED" or operation["status"] == "AWAITING_ONLINE":
        return {
            "id": str(operation["id"]),
            "cancelled": False,
            "status": operation["status"],
            "submissionState": "CONFIRMED",
            "eanReleased": False,
            "code": "SUBMISSION_ALREADY_CONFIRMED",
        }
    if operation["submission_state"] == "IN_FLIGHT" or operation["status"] in {"SUBMITTING", "OTTO_PROCESSING"}:
        conn.execute(
            text(
                "UPDATE restart_operations SET submission_state='UNKNOWN', status='RECONCILIATION_REQUIRED', "
                "version=version+1, updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"
            ),
            {"id": operation["id"], "account": account},
        )
        return {
            "id": str(operation["id"]),
            "cancelled": False,
            "status": "RECONCILIATION_REQUIRED",
            "submissionState": "UNKNOWN",
            "eanReleased": False,
            "code": "SUBMISSION_RECONCILIATION_REQUIRED",
        }
    if operation["submission_state"] == "UNKNOWN" or operation["status"] == "RECONCILIATION_REQUIRED":
        return {
            "id": str(operation["id"]),
            "cancelled": False,
            "status": "RECONCILIATION_REQUIRED",
            "submissionState": "UNKNOWN",
            "eanReleased": False,
            "code": "SUBMISSION_RECONCILIATION_REQUIRED",
        }
    if operation["submission_state"] != "NEVER_SUBMITTED":
        raise OperationError("SUBMISSION_STATE_INVALID", "Operation submission state is not safe to cancel.")

    released = False
    ean = operation["new_ean"]
    links = conn.execute(
        text(
            "SELECT id, new_ean, status FROM ean_links "
            "WHERE account=:account AND data_scope='PRODUCTION' AND operation_id=:id "
            "ORDER BY id FOR UPDATE"
        ),
        {"account": account, "id": operation["id"]},
    ).mappings().all()
    if ean:
        if any(str(link["new_ean"]) != str(ean) for link in links):
            raise OperationError(
                "EAN_LINK_IDENTITY_CONFLICT",
                "The operation has an EAN lineage link that does not match its assigned EAN.",
            )
        if any(link["status"] in {"PENDING_PUBLISH", "PUBLISHED"} for link in links):
            raise OperationError(
                "EAN_LINK_RECONCILIATION_REQUIRED",
                "A publish-related EAN link cannot be released by cancelling the operation.",
            )
        pool = conn.execute(
            text(
                "SELECT status, reservation_operation_id FROM ean_pool WHERE ean=:ean AND account=:account "
                "AND data_scope='PRODUCTION' FOR UPDATE"
            ),
            {"ean": ean, "account": account},
        ).mappings().first()
        if pool is None:
            raise OperationError("EAN_POOL_ENTRY_MISSING", "The operation's assigned EAN is missing from the pool.")
        reservation_owner = str(pool["reservation_operation_id"]) if pool["reservation_operation_id"] else None
        operation_uuid = str(operation["id"])
        if pool["status"] == "RESERVED":
            if reservation_owner != operation_uuid:
                raise OperationError(
                    "EAN_RESERVATION_CONFLICT",
                    "The reserved EAN is owned by a different operation.",
                )
            eligibility = validate_gtin_eligibility(
                conn,
                account=account,
                ean=ean,
                source_product_id=str(operation["source_product_id"]),
                approved_evidence_types=approved_evidence_types,
                operation_id=operation_uuid,
            )
            if eligibility.eligible:
                conn.execute(
                    text(
                        "UPDATE ean_pool SET status='AVAILABLE', reserved_at=NULL, reservation_operation_id=NULL, "
                        "version=version+1, updated_at=CURRENT_TIMESTAMP WHERE ean=:ean AND account=:account "
                        "AND data_scope='PRODUCTION' AND reservation_operation_id=:operation_id"
                    ),
                    {"ean": ean, "account": account, "operation_id": operation["id"]},
                )
                released = True
            else:
                conn.execute(
                    text(
                        "UPDATE ean_pool SET status='QUARANTINED', quarantined_at=CURRENT_TIMESTAMP, "
                        "quarantine_reason=:reason, version=version+1, updated_at=CURRENT_TIMESTAMP "
                        "WHERE ean=:ean AND account=:account AND data_scope='PRODUCTION' "
                        "AND reservation_operation_id=:operation_id"
                    ),
                    {"ean": ean, "account": account, "reason": eligibility.code, "operation_id": operation["id"]},
                )
        elif pool["status"] == "AVAILABLE" and reservation_owner is None:
            # A previous safe release may have committed before the operation
            # cancellation was retried. Preserve that state and finish cleanup.
            released = True
        elif pool["status"] == "QUARANTINED":
            if reservation_owner not in {None, operation_uuid}:
                raise OperationError(
                    "EAN_RESERVATION_CONFLICT",
                    "The quarantined EAN is owned by a different operation.",
                )
            released = False
        else:
            raise OperationError(
                "EAN_POOL_STATE_CONFLICT",
                "The assigned EAN is already consumed or cannot be released safely.",
            )

        if links:
            conn.execute(
                text(
                    "UPDATE ean_links SET status='DEACTIVATED', deactivated_at=CURRENT_TIMESTAMP, "
                    "deactivation_reason='OPERATION_CANCELLED_BEFORE_SUBMISSION', updated_at=CURRENT_TIMESTAMP "
                    "WHERE account=:account AND data_scope='PRODUCTION' AND operation_id=:id "
                    "AND new_ean=:ean AND status='ACTIVE'"
                ),
                {"account": account, "id": operation["id"], "ean": ean},
            )

        if released:
            conn.execute(
                text("UPDATE restart_operations SET new_ean=NULL WHERE id=:id AND account=:account"),
                {"id": operation["id"], "account": account},
            )
    else:
        if any(link["status"] in {"PENDING_PUBLISH", "PUBLISHED"} for link in links):
            raise OperationError(
                "EAN_LINK_RECONCILIATION_REQUIRED",
                "The operation has a publish-related EAN link but no assigned EAN; reconcile before cancellation.",
            )
        if any(link["status"] != "DEACTIVATED" for link in links):
            raise OperationError(
                "EAN_LINK_IDENTITY_CONFLICT",
                "The operation has active EAN lineage but no assigned EAN; reconcile before cancellation.",
            )
        owned_pool_rows = conn.execute(
            text(
                "SELECT ean, status FROM ean_pool WHERE account=:account AND data_scope='PRODUCTION' "
                "AND reservation_operation_id=:operation_id ORDER BY ean FOR UPDATE"
            ),
            {"account": account, "operation_id": operation["id"]},
        ).mappings().all()
        if owned_pool_rows:
            raise OperationError(
                "EAN_RESERVATION_IDENTITY_CONFLICT",
                "The operation owns a pool reservation but has no assigned EAN; reconcile before cancellation.",
            )

    conn.execute(
        text(
            "UPDATE restart_operations SET status='CANCELLED', version=version+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=:id AND account=:account"
        ),
        {"id": operation["id"], "account": account},
    )
    _record_event(
        conn,
        account=account,
        actor=actor,
        event_type="RESTART_OPERATION_CANCELLED",
        entity_type="RESTART_OPERATION",
        entity_id=str(operation["id"]),
        summary="Never-submitted Restart operation cancelled",
        metadata={"sourceProductId": str(operation["source_product_id"]), "newEan": ean, "eanReleased": released},
    )
    return {
        "id": str(operation["id"]),
        "cancelled": True,
        "status": "CANCELLED",
        "submissionState": "NEVER_SUBMITTED",
        "eanReleased": released,
    }


def idempotent_operation_action(
    conn: Connection,
    *,
    account: str,
    actor: str,
    action: str,
    idempotency_key: str,
    request_body: dict[str, Any],
    execute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Serialize concurrent first requests and replay an identical stored result."""
    _require_account(account)
    key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
    if not 8 <= len(key) <= 128:
        raise OperationError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain 8 to 128 characters.")
    encoded, _ = _canonical_json(request_body, "request")
    request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    stored_key = "restart-op:" + hashlib.sha256(f"{account}\0{actor}\0{action}\0{key}".encode()).hexdigest()
    inserted = conn.execute(
        text(
            "INSERT INTO restart_idempotency_keys (key, request_hash, response_body, created_at, data_scope) "
            "VALUES (:key, :hash, '{}', CURRENT_TIMESTAMP, 'PRODUCTION') ON CONFLICT (key) DO NOTHING RETURNING key"
        ),
        {"key": stored_key, "hash": request_hash},
    ).scalar_one_or_none()
    if inserted is None:
        previous = conn.execute(
            text("SELECT request_hash, response_body FROM restart_idempotency_keys WHERE key=:key AND data_scope='PRODUCTION' FOR UPDATE"),
            {"key": stored_key},
        ).mappings().first()
        if previous is None or previous["request_hash"] != request_hash:
            raise OperationError("IDEMPOTENCY_CONFLICT", "Idempotency-Key was used for a different request.")
        body = json.loads(previous["response_body"])
        if not body:
            raise OperationError("IDEMPOTENCY_IN_PROGRESS", "The idempotent action has no committed result.")
        return body
    result = execute()
    conn.execute(
        text("UPDATE restart_idempotency_keys SET response_body=:body WHERE key=:key AND data_scope='PRODUCTION'"),
        {"body": json.dumps(result, ensure_ascii=False, separators=(",", ":")), "key": stored_key},
    )
    return result

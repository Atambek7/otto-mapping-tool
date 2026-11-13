"""PostgreSQL-backed, account-scoped job queue primitives.

All repository functions use the caller's transaction and never commit. This
lets callers enqueue a job atomically with the business row that requires it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

ALLOWED_ACCOUNTS = frozenset({"JV", "XL"})
_JOB_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ERROR_CODE = re.compile(r"^[A-Z0-9_.-]{1,64}$")


class JobError(RuntimeError):
    """Base class for safe, stable job queue errors."""


class IdempotencyConflict(JobError):
    pass


class JobNotFound(JobError):
    pass


class InvalidJobState(JobError):
    pass


class LeaseLost(JobError):
    pass


class JobExecutionError(JobError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = True,
        retry_delay_seconds: int | None = None,
    ) -> None:
        self.code = _validate_error_code(code)
        self.retryable = retryable
        if retry_delay_seconds is not None and (
            isinstance(retry_delay_seconds, bool)
            or not isinstance(retry_delay_seconds, int)
            or not 0 <= retry_delay_seconds <= 86_400
        ):
            raise ValueError("retry_delay_seconds must be an integer between 0 and 86400")
        self.retry_delay_seconds = retry_delay_seconds
        super().__init__(self.code)


@dataclass(frozen=True)
class BackgroundJob:
    id: str
    account: str
    job_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    payload_hash: str
    state: str
    priority: int
    available_at: datetime
    lease_owner: str | None
    lease_token: str | None
    lease_until: datetime | None
    attempts_in_cycle: int
    total_attempts: int
    max_attempts: int
    replay_count: int
    last_error_code: str | None
    dispatch_generation: int
    dispatch_attempts: int
    dispatched_at: datetime | None
    last_dispatch_error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class EnqueueResult:
    job: BackgroundJob
    created: bool


@dataclass(frozen=True)
class DispatchClaim:
    job_id: str
    lease_token: str
    generation: int


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value


def _validate_account(account: str) -> str:
    value = str(account or "").strip().upper()
    if value not in ALLOWED_ACCOUNTS:
        raise ValueError("account must be JV or XL")
    return value


def _validate_error_code(code: str) -> str:
    value = str(code or "").strip().upper()
    if not _ERROR_CODE.fullmatch(value):
        raise ValueError("error code must contain 1–64 safe ASCII characters")
    return value


def _payload_and_hash(payload: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object")
    serialized = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _job_from_row(row: Mapping[str, Any]) -> BackgroundJob:
    raw_payload = row["payload_json"]
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    return BackgroundJob(
        id=str(row["id"]),
        account=str(row["account"]),
        job_type=str(row["job_type"]),
        idempotency_key=str(row["idempotency_key"]),
        payload=dict(payload),
        payload_hash=str(row["payload_hash"]),
        state=str(row["state"]),
        priority=int(row["priority"]),
        available_at=row["available_at"],
        lease_owner=row["lease_owner"],
        lease_token=str(row["lease_token"]) if row["lease_token"] else None,
        lease_until=row["lease_until"],
        attempts_in_cycle=int(row["attempts_in_cycle"]),
        total_attempts=int(row["total_attempts"]),
        max_attempts=int(row["max_attempts"]),
        replay_count=int(row["replay_count"]),
        last_error_code=row["last_error_code"],
        dispatch_generation=int(row["dispatch_generation"]),
        dispatch_attempts=int(row["dispatch_attempts"]),
        dispatched_at=row["dispatched_at"],
        last_dispatch_error_code=row["last_dispatch_error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def enqueue_job(
    connection: Connection,
    *,
    account: str,
    job_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    priority: int = 0,
    max_attempts: int = 5,
    available_at: datetime | None = None,
    now: datetime | None = None,
) -> EnqueueResult:
    """Insert a production job in the caller's current transaction.

    An identical idempotent request returns the existing job. Reusing its key
    with a different payload is a conflict and never mutates that job.
    """
    normalized_account = _validate_account(account)
    if not isinstance(job_type, str) or not _JOB_TYPE.fullmatch(job_type):
        raise ValueError("job_type is invalid")
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
        raise ValueError("idempotency_key length must be between 8 and 128")
    if isinstance(priority, bool) or not isinstance(priority, int) or not -100 <= priority <= 100:
        raise ValueError("priority must be between -100 and 100")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 20:
        raise ValueError("max_attempts must be between 1 and 20")

    current = _now(now)
    eligible_at = _now(available_at) if available_at else current
    payload_json, payload_hash = _payload_and_hash(payload)
    result = connection.execute(
        text(
            """
            INSERT INTO background_jobs (
                account, data_scope, job_type, idempotency_key, payload_json,
                payload_hash, priority, max_attempts, available_at, created_at, updated_at
            ) VALUES (
                :account, 'PRODUCTION', :job_type, :idempotency_key,
                CAST(:payload_json AS jsonb), :payload_hash, :priority,
                :max_attempts, :available_at, :now, :now
            )
            ON CONFLICT (account, data_scope, job_type, idempotency_key) DO NOTHING
            RETURNING *
            """
        ),
        {
            "account": normalized_account,
            "job_type": job_type,
            "idempotency_key": idempotency_key,
            "payload_json": payload_json,
            "payload_hash": payload_hash,
            "priority": priority,
            "max_attempts": max_attempts,
            "available_at": eligible_at,
            "now": current,
        },
    ).mappings().first()
    if result is not None:
        return EnqueueResult(_job_from_row(result), created=True)

    existing = connection.execute(
        text(
            """
            SELECT * FROM background_jobs
            WHERE account = :account AND data_scope = 'PRODUCTION'
              AND job_type = :job_type AND idempotency_key = :idempotency_key
            FOR UPDATE
            """
        ),
        {
            "account": normalized_account,
            "job_type": job_type,
            "idempotency_key": idempotency_key,
        },
    ).mappings().first()
    if existing is None:
        raise JobError("idempotent job was not visible after conflict")
    if existing["payload_hash"] != payload_hash:
        raise IdempotencyConflict("idempotency key is already bound to a different job payload")
    return EnqueueResult(_job_from_row(existing), created=False)


def claim_next_job(
    connection: Connection,
    *,
    worker_id: str,
    lease_seconds: int = 60,
    accounts: Sequence[str] | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Atomically claim one due job using ``FOR UPDATE SKIP LOCKED``."""
    worker = str(worker_id or "").strip()
    if not worker or len(worker) > 128:
        raise ValueError("worker_id must contain 1–128 characters")
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    normalized_accounts = None if accounts is None else tuple(sorted({_validate_account(a) for a in accounts}))
    if normalized_accounts == ():
        return None
    current = _now(now)
    lease_token = str(uuid4())
    account_clause = "AND job.account = ANY(:accounts)" if normalized_accounts is not None else ""
    row = connection.execute(
        text(
            f"""
            WITH candidate AS (
                SELECT job.id
                FROM background_jobs AS job
                WHERE job.data_scope = 'PRODUCTION'
                  AND job.state IN ('PENDING', 'RETRY_WAIT')
                  AND job.available_at <= :now
                  {account_clause}
                ORDER BY job.priority DESC, job.available_at, job.created_at, job.id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE background_jobs AS job
            SET state = 'RUNNING',
                lease_owner = :worker_id,
                lease_token = :lease_token,
                lease_until = :now + make_interval(secs => :lease_seconds),
                dispatch_lease_token = NULL,
                dispatch_lease_until = NULL,
                attempts_in_cycle = job.attempts_in_cycle + 1,
                total_attempts = job.total_attempts + 1,
                started_at = COALESCE(job.started_at, :now),
                updated_at = :now
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.*
            """
        ),
        {
            "now": current,
            "worker_id": worker,
            "lease_token": lease_token,
            "lease_seconds": lease_seconds,
            "accounts": list(normalized_accounts) if normalized_accounts is not None else None,
        },
    ).mappings().first()
    if row is None:
        return None

    connection.execute(
        text(
            """
            INSERT INTO background_job_attempts (
                job_id, replay_number, attempt_number, worker_id, lease_token,
                state, started_at, heartbeat_at
            ) VALUES (
                :job_id, :replay_number, :attempt_number, :worker_id, :lease_token,
                'RUNNING', :now, :now
            )
            """
        ),
        {
            "job_id": row["id"],
            "replay_number": row["replay_count"],
            "attempt_number": row["attempts_in_cycle"],
            "worker_id": worker,
            "lease_token": lease_token,
            "now": current,
        },
    )
    return _job_from_row(row)


def claim_job_by_id(
    connection: Connection,
    *,
    job_id: str,
    worker_id: str,
    lease_seconds: int = 60,
    job_types: Sequence[str] | None = None,
    created_after: datetime | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Claim the job named by a Celery message; duplicate delivery is a no-op."""
    try:
        normalized_id = str(UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError):
        return None
    worker = str(worker_id or "").strip()
    if not worker or len(worker) > 128:
        raise ValueError("worker_id must contain 1–128 characters")
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    current = _now(now)
    cutoff = _now(created_after) if created_after is not None else None
    normalized_types = None if job_types is None else tuple(sorted({str(value) for value in job_types}))
    if normalized_types is not None:
        if any(not _JOB_TYPE.fullmatch(value) for value in normalized_types):
            raise ValueError("job_types contains an invalid job type")
        if not normalized_types:
            return None
    lease_token = str(uuid4())
    job_type_clause = "AND job_type = ANY(:job_types)" if normalized_types is not None else ""
    created_after_clause = "AND created_at >= :created_after" if cutoff is not None else ""
    row = connection.execute(
        text(
            f"""
            WITH candidate AS (
                SELECT id FROM background_jobs
                WHERE id = :job_id AND data_scope = 'PRODUCTION'
                  AND state IN ('PENDING', 'RETRY_WAIT') AND available_at <= :now
                  {job_type_clause}
                  {created_after_clause}
                FOR UPDATE SKIP LOCKED
            )
            UPDATE background_jobs AS job
            SET state = 'RUNNING', lease_owner = :worker_id, lease_token = :lease_token,
                lease_until = :now + make_interval(secs => :lease_seconds),
                dispatch_lease_token = NULL, dispatch_lease_until = NULL,
                attempts_in_cycle = job.attempts_in_cycle + 1,
                total_attempts = job.total_attempts + 1,
                started_at = COALESCE(job.started_at, :now), updated_at = :now
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.*
            """
        ),
        {
            "job_id": normalized_id,
            "worker_id": worker,
            "lease_token": lease_token,
            "lease_seconds": lease_seconds,
            "now": current,
            "job_types": list(normalized_types) if normalized_types is not None else None,
            "created_after": cutoff,
        },
    ).mappings().first()
    if row is None:
        return None
    connection.execute(
        text(
            """
            INSERT INTO background_job_attempts (
                job_id, replay_number, attempt_number, worker_id, lease_token,
                state, started_at, heartbeat_at
            ) VALUES (
                :job_id, :replay_number, :attempt_number, :worker_id, :lease_token,
                'RUNNING', :now, :now
            )
            """
        ),
        {
            "job_id": row["id"],
            "replay_number": row["replay_count"],
            "attempt_number": row["attempts_in_cycle"],
            "worker_id": worker,
            "lease_token": lease_token,
            "now": current,
        },
    )
    return _job_from_row(row)


def claim_job_dispatches(
    connection: Connection,
    *,
    limit: int = 100,
    lease_seconds: int = 30,
    stale_after_seconds: int = 3600,
    accounts: Sequence[str] | None = None,
    job_types: Sequence[str] | None = None,
    created_after: datetime | None = None,
    now: datetime | None = None,
) -> list[DispatchClaim]:
    """Lease due job IDs for publication without holding a DB lock over I/O.

    A previously published but still unclaimed job becomes eligible after the
    stale window. This lets PostgreSQL recover from a broker message loss.
    """
    if isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    if isinstance(stale_after_seconds, bool) or not 1 <= stale_after_seconds <= 86_400:
        raise ValueError("stale_after_seconds must be between 1 and 86400")
    normalized_accounts = None if accounts is None else tuple(sorted({_validate_account(a) for a in accounts}))
    if normalized_accounts == ():
        return []
    normalized_types = None if job_types is None else tuple(sorted({str(value) for value in job_types}))
    if normalized_types is not None:
        if any(not _JOB_TYPE.fullmatch(value) for value in normalized_types):
            raise ValueError("job_types contains an invalid job type")
        if not normalized_types:
            return []
    current = _now(now)
    cutoff = _now(created_after) if created_after is not None else None
    stale_before = current - timedelta(seconds=stale_after_seconds)
    account_clause = "AND job.account = ANY(:accounts)" if normalized_accounts is not None else ""
    job_type_clause = "AND job.job_type = ANY(:job_types)" if normalized_types is not None else ""
    created_after_clause = "AND job.created_at >= :created_after" if cutoff is not None else ""
    rows = connection.execute(
        text(
            f"""
            WITH candidate AS (
                SELECT job.id
                FROM background_jobs AS job
                WHERE job.data_scope = 'PRODUCTION'
                  AND job.state IN ('PENDING', 'RETRY_WAIT')
                  AND job.available_at <= :now
                  AND (job.dispatched_at IS NULL OR job.dispatched_at <= :stale_before)
                  AND (job.dispatch_lease_until IS NULL OR job.dispatch_lease_until <= :now)
                  {account_clause}
                  {job_type_clause}
                  {created_after_clause}
                ORDER BY job.priority DESC, job.available_at, job.created_at, job.id
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
            )
            UPDATE background_jobs AS job
            SET dispatch_lease_token = gen_random_uuid(),
                dispatch_lease_until = :now + make_interval(secs => :lease_seconds),
                dispatch_attempts = job.dispatch_attempts + 1,
                dispatch_generation = job.dispatch_generation +
                    CASE WHEN job.dispatched_at IS NULL THEN 0 ELSE 1 END,
                updated_at = :now
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.id, job.dispatch_lease_token, job.dispatch_generation
            """
        ),
        {
            "now": current,
            "stale_before": stale_before,
            "lease_seconds": lease_seconds,
            "limit": limit,
            "accounts": list(normalized_accounts) if normalized_accounts is not None else None,
            "job_types": list(normalized_types) if normalized_types is not None else None,
            "created_after": cutoff,
        },
    ).mappings().all()
    return [
        DispatchClaim(
            job_id=str(row["id"]),
            lease_token=str(row["dispatch_lease_token"]),
            generation=int(row["dispatch_generation"]),
        )
        for row in rows
    ]


def mark_job_dispatched(
    connection: Connection,
    *,
    claim: DispatchClaim,
    now: datetime | None = None,
) -> bool:
    current = _now(now)
    changed = connection.execute(
        text(
            """
            UPDATE background_jobs
            SET dispatched_at = :now, dispatch_lease_token = NULL,
                dispatch_lease_until = NULL, last_dispatch_error_code = NULL, updated_at = :now
            WHERE id = :job_id AND data_scope = 'PRODUCTION'
              AND state IN ('PENDING', 'RETRY_WAIT')
              AND dispatch_lease_token = :lease_token
            """
        ),
        {"job_id": claim.job_id, "lease_token": claim.lease_token, "now": current},
    ).rowcount
    return changed == 1


def release_job_dispatch(
    connection: Connection,
    *,
    claim: DispatchClaim,
    error_code: str,
    now: datetime | None = None,
) -> bool:
    current = _now(now)
    safe_code = _validate_error_code(error_code)
    changed = connection.execute(
        text(
            """
            UPDATE background_jobs
            SET dispatch_lease_token = NULL, dispatch_lease_until = NULL,
                dispatched_at = NULL, last_dispatch_error_code = :error_code, updated_at = :now
            WHERE id = :job_id AND data_scope = 'PRODUCTION'
              AND state IN ('PENDING', 'RETRY_WAIT')
              AND dispatch_lease_token = :lease_token
            """
        ),
        {
            "job_id": claim.job_id,
            "lease_token": claim.lease_token,
            "error_code": safe_code,
            "now": current,
        },
    ).rowcount
    return changed == 1


def _locked_leased_job(
    connection: Connection,
    *,
    job_id: str,
    account: str,
    lease_token: str,
    now: datetime,
) -> Mapping[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT * FROM background_jobs
            WHERE id = :job_id AND account = :account AND data_scope = 'PRODUCTION'
            FOR UPDATE
            """
        ),
        {"job_id": job_id, "account": _validate_account(account)},
    ).mappings().first()
    if row is None:
        raise JobNotFound("job not found")
    if (
        row["state"] != "RUNNING"
        or str(row["lease_token"]) != str(lease_token)
        or row["lease_until"] is None
        or row["lease_until"] <= now
    ):
        raise LeaseLost("job lease is no longer current")
    return row


def heartbeat_job(
    connection: Connection,
    *,
    job_id: str,
    account: str,
    lease_token: str,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> datetime:
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    current = _now(now)
    _locked_leased_job(connection, job_id=job_id, account=account, lease_token=lease_token, now=current)
    lease_until = connection.execute(
        text(
            """
            UPDATE background_jobs
            SET lease_until = :now + make_interval(secs => :lease_seconds), updated_at = :now
            WHERE id = :job_id AND account = :account AND lease_token = :lease_token
            RETURNING lease_until
            """
        ),
        {
            "now": current,
            "lease_seconds": lease_seconds,
            "job_id": job_id,
            "account": _validate_account(account),
            "lease_token": lease_token,
        },
    ).scalar_one()
    connection.execute(
        text("UPDATE background_job_attempts SET heartbeat_at = :now WHERE lease_token = :lease_token AND state = 'RUNNING'"),
        {"now": current, "lease_token": lease_token},
    )
    return lease_until


def complete_job(
    connection: Connection,
    *,
    job_id: str,
    account: str,
    lease_token: str,
    now: datetime | None = None,
) -> BackgroundJob:
    current = _now(now)
    _locked_leased_job(connection, job_id=job_id, account=account, lease_token=lease_token, now=current)
    row = connection.execute(
        text(
            """
            UPDATE background_jobs
            SET state = 'SUCCEEDED', lease_owner = NULL, lease_token = NULL, lease_until = NULL,
                completed_at = :now, updated_at = :now
            WHERE id = :job_id AND account = :account AND lease_token = :lease_token
            RETURNING *
            """
        ),
        {"now": current, "job_id": job_id, "account": _validate_account(account), "lease_token": lease_token},
    ).mappings().one()
    changed = connection.execute(
        text(
            """
            UPDATE background_job_attempts SET state = 'SUCCEEDED', finished_at = :now, heartbeat_at = :now
            WHERE job_id = :job_id AND lease_token = :lease_token AND state = 'RUNNING'
            """
        ),
        {"job_id": job_id, "lease_token": lease_token, "now": current},
    ).rowcount
    if changed != 1:
        raise LeaseLost("job attempt lease is no longer current")
    return _job_from_row(row)


def fail_job(
    connection: Connection,
    *,
    job_id: str,
    account: str,
    lease_token: str,
    error_code: str,
    retryable: bool = True,
    retry_delay_seconds: int | None = None,
    now: datetime | None = None,
) -> BackgroundJob:
    current = _now(now)
    row = _locked_leased_job(connection, job_id=job_id, account=account, lease_token=lease_token, now=current)
    safe_code = _validate_error_code(error_code)
    if retry_delay_seconds is None:
        retry_delay_seconds = min(2 ** max(0, int(row["attempts_in_cycle"]) - 1), 300)
    if isinstance(retry_delay_seconds, bool) or not 0 <= retry_delay_seconds <= 86_400:
        raise ValueError("retry_delay_seconds must be between 0 and 86400")

    will_retry = retryable and int(row["attempts_in_cycle"]) < int(row["max_attempts"])
    state = "RETRY_WAIT" if will_retry else "DEAD"
    finished_at = None if will_retry else current
    available_at = current + timedelta(seconds=retry_delay_seconds) if will_retry else current
    updated = connection.execute(
        text(
            """
            UPDATE background_jobs
            SET state = :state, available_at = :available_at,
                lease_owner = NULL, lease_token = NULL, lease_until = NULL,
                dispatch_lease_token = NULL, dispatch_lease_until = NULL,
                dispatched_at = CASE WHEN :retryable THEN NULL ELSE dispatched_at END,
                dispatch_generation = dispatch_generation + CASE WHEN :retryable THEN 1 ELSE 0 END,
                last_error_code = :error_code, completed_at = :completed_at, updated_at = :now
            WHERE id = :job_id AND account = :account AND lease_token = :lease_token
            RETURNING *
            """
        ),
        {
            "state": state,
            "retryable": will_retry,
            "available_at": available_at,
            "error_code": safe_code,
            "completed_at": finished_at,
            "now": current,
            "job_id": job_id,
            "account": _validate_account(account),
            "lease_token": lease_token,
        },
    ).mappings().one()
    attempt_state = "RETRYABLE_FAILURE" if will_retry else "FINAL_FAILURE"
    changed = connection.execute(
        text(
            """
            UPDATE background_job_attempts
            SET state = :state, error_code = :error_code, finished_at = :now, heartbeat_at = :now
            WHERE job_id = :job_id AND lease_token = :lease_token AND state = 'RUNNING'
            """
        ),
        {
            "state": attempt_state,
            "error_code": safe_code,
            "now": current,
            "job_id": job_id,
            "lease_token": lease_token,
        },
    ).rowcount
    if changed != 1:
        raise LeaseLost("job attempt lease is no longer current")
    return _job_from_row(updated)


def reap_expired_leases(
    connection: Connection,
    *,
    limit: int = 100,
    accounts: Sequence[str] | None = None,
    job_types: Sequence[str] | None = None,
    created_after: datetime | None = None,
    now: datetime | None = None,
) -> int:
    if isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    normalized_accounts = None if accounts is None else tuple(sorted({_validate_account(a) for a in accounts}))
    if normalized_accounts == ():
        return 0
    normalized_types = None if job_types is None else tuple(sorted({str(value) for value in job_types}))
    if normalized_types is not None:
        if any(not _JOB_TYPE.fullmatch(value) for value in normalized_types):
            raise ValueError("job_types contains an invalid job type")
        if not normalized_types:
            return 0
    current = _now(now)
    cutoff = _now(created_after) if created_after is not None else None
    account_clause = "AND account = ANY(:accounts)" if normalized_accounts is not None else ""
    job_type_clause = "AND job_type = ANY(:job_types)" if normalized_types is not None else ""
    created_after_clause = "AND created_at >= :created_after" if cutoff is not None else ""
    expired = connection.execute(
        text(
            f"""
            SELECT * FROM background_jobs
            WHERE data_scope = 'PRODUCTION' AND state = 'RUNNING' AND lease_until <= :now
              {account_clause}
              {job_type_clause}
              {created_after_clause}
            ORDER BY lease_until, id
            FOR UPDATE SKIP LOCKED
            LIMIT :limit
            """
        ),
        {
            "now": current,
            "limit": limit,
            "accounts": list(normalized_accounts) if normalized_accounts is not None else None,
            "job_types": list(normalized_types) if normalized_types is not None else None,
            "created_after": cutoff,
        },
    ).mappings().all()
    for row in expired:
        token = row["lease_token"]
        will_retry = int(row["attempts_in_cycle"]) < int(row["max_attempts"])
        state = "RETRY_WAIT" if will_retry else "DEAD"
        delay = min(2 ** max(0, int(row["attempts_in_cycle"]) - 1), 300) if will_retry else 0
        connection.execute(
            text(
                """
                UPDATE background_job_attempts
                SET state = 'LEASE_EXPIRED', error_code = 'LEASE_EXPIRED', finished_at = :now, heartbeat_at = :now
                WHERE job_id = :job_id AND lease_token = :lease_token AND state = 'RUNNING'
                """
            ),
            {"now": current, "job_id": row["id"], "lease_token": token},
        )
        connection.execute(
            text(
                """
                UPDATE background_jobs
                SET state = :state, available_at = :available_at,
                    lease_owner = NULL, lease_token = NULL, lease_until = NULL,
                    dispatch_lease_token = NULL, dispatch_lease_until = NULL,
                    dispatched_at = CASE WHEN :retryable THEN NULL ELSE dispatched_at END,
                    dispatch_generation = dispatch_generation + CASE WHEN :retryable THEN 1 ELSE 0 END,
                    last_error_code = 'LEASE_EXPIRED', completed_at = :completed_at, updated_at = :now
                WHERE id = :job_id AND lease_token = :lease_token
                """
            ),
            {
                "state": state,
                "retryable": will_retry,
                "available_at": current + timedelta(seconds=delay),
                "completed_at": None if will_retry else current,
                "now": current,
                "job_id": row["id"],
                "lease_token": token,
            },
        )
    return len(expired)


def replay_dead_job(
    connection: Connection,
    *,
    job_id: str,
    account: str,
    now: datetime | None = None,
) -> BackgroundJob:
    current = _now(now)
    normalized_account = _validate_account(account)
    row = connection.execute(
        text(
            """
            SELECT * FROM background_jobs
            WHERE id = :job_id AND account = :account AND data_scope = 'PRODUCTION'
            FOR UPDATE
            """
        ),
        {"job_id": job_id, "account": normalized_account},
    ).mappings().first()
    if row is None:
        raise JobNotFound("job not found")
    if row["state"] != "DEAD":
        raise InvalidJobState("only dead jobs can be manually replayed")
    updated = connection.execute(
        text(
            """
            UPDATE background_jobs
            SET state = 'PENDING', replay_count = replay_count + 1,
                attempts_in_cycle = 0, available_at = :now, completed_at = NULL,
                dispatched_at = NULL, dispatch_lease_token = NULL, dispatch_lease_until = NULL,
                dispatch_generation = dispatch_generation + 1, last_dispatch_error_code = NULL,
                updated_at = :now
            WHERE id = :job_id AND account = :account AND state = 'DEAD'
            RETURNING *
            """
        ),
        {"now": current, "job_id": job_id, "account": normalized_account},
    ).mappings().one()
    return _job_from_row(updated)


def get_job(connection: Connection, *, job_id: str, account: str) -> BackgroundJob:
    row = connection.execute(
        text(
            """
            SELECT * FROM background_jobs
            WHERE id = :job_id AND account = :account AND data_scope = 'PRODUCTION'
            """
        ),
        {"job_id": job_id, "account": _validate_account(account)},
    ).mappings().first()
    if row is None:
        raise JobNotFound("job not found")
    return _job_from_row(row)


def list_dead_jobs(
    connection: Connection,
    *,
    account: str,
    limit: int = 50,
    offset: int = 0,
) -> list[BackgroundJob]:
    if isinstance(limit, bool) or not 1 <= limit <= 100 or isinstance(offset, bool) or offset < 0:
        raise ValueError("limit must be 1–100 and offset must be non-negative")
    rows = connection.execute(
        text(
            """
            SELECT * FROM background_jobs
            WHERE account = :account AND data_scope = 'PRODUCTION' AND state = 'DEAD'
            ORDER BY updated_at DESC, id
            LIMIT :limit OFFSET :offset
            """
        ),
        {"account": _validate_account(account), "limit": limit, "offset": offset},
    ).mappings().all()
    return [_job_from_row(row) for row in rows]


def run_next_job(
    engine: Engine,
    *,
    worker_id: str,
    executor: Callable[[BackgroundJob], None],
    lease_seconds: int = 60,
    accounts: Sequence[str] | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Claim, execute outside the transaction, then persist a terminal/retry state."""
    current = _now(now)
    with engine.begin() as connection:
        reap_expired_leases(connection, now=current)
        job = claim_next_job(
            connection,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            accounts=accounts,
            now=current,
        )
    if job is None:
        return None

    try:
        executor(job)
    except JobExecutionError as error:
        with engine.begin() as connection:
            fail_job(
                connection,
                job_id=job.id,
                account=job.account,
                lease_token=job.lease_token or "",
                error_code=error.code,
                retryable=error.retryable,
                retry_delay_seconds=error.retry_delay_seconds,
                now=current if now is not None else None,
            )
    except Exception:
        with engine.begin() as connection:
            fail_job(
                connection,
                job_id=job.id,
                account=job.account,
                lease_token=job.lease_token or "",
                error_code="EXECUTOR_ERROR",
                retryable=True,
                now=current if now is not None else None,
            )
    else:
        with engine.begin() as connection:
            complete_job(
                connection,
                job_id=job.id,
                account=job.account,
                lease_token=job.lease_token or "",
                now=current if now is not None else None,
            )

    with engine.connect() as connection:
        return get_job(connection, job_id=job.id, account=job.account)


def run_job_by_id(
    engine: Engine,
    *,
    job_id: str,
    worker_id: str,
    executors: Mapping[str, Callable[[BackgroundJob], None]],
    lease_seconds: int = 60,
    allowed_job_types: Sequence[str] | None = None,
    created_after: datetime | None = None,
    now: datetime | None = None,
) -> BackgroundJob | None:
    """Execute only the ID carried by a Celery message; duplicates are no-ops."""
    current = _now(now)
    with engine.begin() as connection:
        job = claim_job_by_id(
            connection,
            job_id=job_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            job_types=allowed_job_types,
            created_after=created_after,
            now=current,
        )
    if job is None:
        return None

    executor = executors.get(job.job_type)
    try:
        if executor is None:
            raise JobExecutionError("NO_EXECUTOR", retryable=False)
        executor(job)
    except JobExecutionError as error:
        with engine.begin() as connection:
            fail_job(
                connection,
                job_id=job.id,
                account=job.account,
                lease_token=job.lease_token or "",
                error_code=error.code,
                retryable=error.retryable,
                retry_delay_seconds=error.retry_delay_seconds,
                now=current if now is not None else None,
            )
    except Exception:
        with engine.begin() as connection:
            fail_job(
                connection,
                job_id=job.id,
                account=job.account,
                lease_token=job.lease_token or "",
                error_code="EXECUTOR_ERROR",
                retryable=True,
                now=current if now is not None else None,
            )
    else:
        with engine.begin() as connection:
            complete_job(
                connection,
                job_id=job.id,
                account=job.account,
                lease_token=job.lease_token or "",
                now=current if now is not None else None,
            )

    with engine.connect() as connection:
        return get_job(connection, job_id=job.id, account=job.account)

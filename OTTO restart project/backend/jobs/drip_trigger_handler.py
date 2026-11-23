"""Durably dispatch only the selected batch's release jobs due at click time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import text

from jobs.dispatcher import dispatch_once
from jobs.service import BackgroundJob, JobExecutionError, enqueue_job, heartbeat_job
from jobs.transport import JobTransport
from services.drip_service import _event
from services.drip_trigger_service import RELEASE_JOB_TYPE, due_release_job_ids

PAGE_SIZE = 100


def _uuid(value: object, code: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise JobExecutionError(code, retryable=False) from None


def _trigger_cutoff(value: object) -> datetime:
    if not isinstance(value, str):
        raise JobExecutionError("INVALID_TRIGGER_TIME", retryable=False)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise JobExecutionError("INVALID_TRIGGER_TIME", retryable=False) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JobExecutionError("INVALID_TRIGGER_TIME", retryable=False)
    return parsed.astimezone(timezone.utc)


def create_drip_trigger_executor(engine_provider: Callable, transport: JobTransport):
    def execute(job: BackgroundJob) -> None:
        batch_id = _uuid(job.payload.get("batchId"), "INVALID_TRIGGER_BATCH")
        trigger_id = _uuid(job.payload.get("triggerId"), "INVALID_TRIGGER_ID")
        cutoff = _trigger_cutoff(job.payload.get("cutoffAt"))
        generation = job.payload.get("scheduleGeneration")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise JobExecutionError("INVALID_TRIGGER_GENERATION", retryable=False)
        requested_by = str(job.payload.get("requestedBy") or "system")[:200]
        cursor = job.payload.get("cursor")
        if cursor is not None:
            cursor = _uuid(cursor, "INVALID_TRIGGER_CURSOR")

        with engine_provider().begin() as connection:
            batch = connection.execute(
                text("SELECT status,schedule_generation FROM restart_batches "
                     "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR SHARE"),
                {"id": batch_id, "account": job.account},
            ).mappings().first()
            if batch is None:
                _event(connection, account=job.account, actor=requested_by,
                       event_type="DRIP_TRIGGER_NOW_SKIPPED", entity_type="RESTART_BATCH", entity_id=batch_id,
                       summary="Immediate Drip dispatch skipped because its batch no longer exists",
                       metadata={"triggerId": trigger_id, "reason": "BATCH_NOT_FOUND"})
                return
            if batch["status"] not in {"SCHEDULED", "ACTIVE"} or int(batch["schedule_generation"]) != generation:
                _event(connection, account=job.account, actor=requested_by,
                       event_type="DRIP_TRIGGER_NOW_SKIPPED", entity_type="RESTART_BATCH", entity_id=batch_id,
                       summary="Immediate Drip dispatch skipped because the batch is paused, cancelled, or on another generation",
                       metadata={"triggerId": trigger_id, "reason": "BATCH_STATE_CHANGED",
                                 "status": batch["status"], "scheduleGeneration": int(batch["schedule_generation"])})
                return
            job_ids = due_release_job_ids(
                connection, account=job.account, batch_id=batch_id, schedule_generation=generation,
                cutoff_at=cutoff, after_job_id=cursor, limit=PAGE_SIZE,
            )

        if not job_ids:
            with engine_provider().begin() as connection:
                _event(connection, account=job.account, actor=requested_by,
                       event_type="DRIP_TRIGGER_NOW_COMPLETED", entity_type="RESTART_BATCH", entity_id=batch_id,
                       summary="Immediate Drip dispatch finished; no additional due release jobs were available",
                       metadata={"triggerId": trigger_id, "cutoffAt": cutoff.isoformat(), "published": 0,
                                 "publishFailed": 0, "claimRaced": 0})
            return

        def heartbeat() -> None:
            with engine_provider().begin() as connection:
                heartbeat_job(connection, job_id=job.id, account=job.account,
                              lease_token=job.lease_token or "", lease_seconds=60)

        report = dispatch_once(
            engine_provider(), transport, limit=len(job_ids), accounts=[job.account],
            allowed_job_types=[RELEASE_JOB_TYPE], job_ids=job_ids, on_progress=heartbeat,
        )

        continuation_job_id = None
        with engine_provider().begin() as connection:
            batch = connection.execute(
                text("SELECT status,schedule_generation FROM restart_batches "
                     "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR SHARE"),
                {"id": batch_id, "account": job.account},
            ).mappings().first()
            next_ids = []
            if batch and batch["status"] in {"SCHEDULED", "ACTIVE"} and int(batch["schedule_generation"]) == generation:
                next_ids = due_release_job_ids(
                    connection, account=job.account, batch_id=batch_id, schedule_generation=generation,
                    cutoff_at=cutoff, after_job_id=job_ids[-1], limit=1,
                )
            if next_ids:
                continuation = enqueue_job(
                    connection,
                    account=job.account,
                    job_type="drip.trigger_due",
                    idempotency_key=f"drip-trigger-{trigger_id}-{job_ids[-1]}",
                    payload={"batchId": batch_id, "triggerId": trigger_id, "cutoffAt": cutoff.isoformat(),
                             "scheduleGeneration": generation, "requestedBy": requested_by,
                             "cursor": job_ids[-1]},
                    priority=10,
                    max_attempts=5,
                )
                continuation_job_id = continuation.job.id
            _event(
                connection,
                account=job.account,
                actor=requested_by,
                event_type="DRIP_TRIGGER_NOW_DISPATCHED",
                entity_type="RESTART_BATCH",
                entity_id=batch_id,
                summary="Immediate Drip dispatch processed a bounded page of already-due release jobs",
                metadata={"triggerId": trigger_id, "cutoffAt": cutoff.isoformat(),
                          "pageLastJobId": job_ids[-1], "claimed": report.claimed,
                          "published": report.published, "publishFailed": report.publish_failed,
                          "claimRaced": report.claim_raced, "continuationJobId": continuation_job_id,
                          "batchStillActive": bool(batch and batch["status"] in {"SCHEDULED", "ACTIVE"}
                                                    and int(batch["schedule_generation"]) == generation)},
            )

    return execute

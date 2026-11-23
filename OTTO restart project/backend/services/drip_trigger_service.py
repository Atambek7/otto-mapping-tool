"""Request an immediate, bounded dispatch of already-due Drip releases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.drip_service import DripError, _account, _event, _lock_batch, _uuid

TRIGGER_JOB_TYPE = "drip.trigger_due"
RELEASE_JOB_TYPE = "drip.release_item"


def _cutoff(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise DripError("INVALID_TRIGGER_TIME", "Trigger time must include a timezone.", status=422)
    return result.astimezone(timezone.utc)


def _due_release_where() -> str:
    return """
        job.account = :account
        AND job.data_scope = 'PRODUCTION'
        AND job.job_type = :release_job_type
        AND job.state IN ('PENDING','RETRY_WAIT')
        AND job.available_at <= :cutoff
        AND job.created_at <= :cutoff
        AND (job.dispatched_at IS NULL OR job.dispatched_at <= :stale_before)
        AND (job.dispatch_lease_until IS NULL OR job.dispatch_lease_until <= :cutoff)
        AND job.payload_json->>'batchId' = batch.id::text
        AND job.payload_json->>'scheduleGeneration' = batch.schedule_generation::text
        AND EXISTS (
            SELECT 1 FROM restart_items item
            WHERE item.id::text = job.payload_json->>'itemId'
              AND item.batch_id = batch.id
              AND item.account = batch.account
              AND item.data_scope = 'PRODUCTION'
              AND item.status = 'SCHEDULED'
              AND item.scheduled_at <= :cutoff
        )
    """


def due_release_job_ids(
    connection: Connection,
    *,
    account: str,
    batch_id: str,
    schedule_generation: int,
    cutoff_at: datetime,
    after_job_id: str | None = None,
    limit: int = 100,
) -> list[str]:
    """Return one deterministic page of jobs eligible at the user's cutoff."""
    normalized_account = _account(account)
    normalized_batch = _uuid(batch_id, "batchId")
    cutoff = _cutoff(cutoff_at)
    if isinstance(schedule_generation, bool) or not isinstance(schedule_generation, int) or schedule_generation < 0:
        raise DripError("INVALID_SCHEDULE_GENERATION", "Schedule generation is invalid.", status=422)
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise DripError("INVALID_TRIGGER_PAGE_SIZE", "Trigger page size must be between 1 and 100.", status=422)
    cursor = None
    if after_job_id is not None:
        try:
            cursor = str(UUID(str(after_job_id)))
        except (ValueError, TypeError, AttributeError):
            raise DripError("INVALID_TRIGGER_CURSOR", "Trigger cursor must be a job UUID.", status=422) from None
    cursor_clause = "AND job.id > CAST(:cursor AS uuid)" if cursor else ""
    rows = connection.execute(
        text(
            f"SELECT job.id::text FROM background_jobs job "
            "JOIN restart_batches batch ON batch.id=:batch_id AND batch.account=job.account "
            "WHERE batch.data_scope='PRODUCTION' AND batch.status IN ('SCHEDULED','ACTIVE') "
            "AND batch.schedule_generation=:generation AND " + _due_release_where() + " " + cursor_clause +
            " ORDER BY job.id LIMIT :limit"
        ),
        {
            "account": normalized_account,
            "batch_id": normalized_batch,
            "generation": schedule_generation,
            "release_job_type": RELEASE_JOB_TYPE,
            "cutoff": cutoff,
            "stale_before": cutoff - timedelta(seconds=3600),
            "cursor": cursor,
            "limit": limit,
        },
    ).scalars().all()
    return [str(value) for value in rows]


def request_trigger_now(
    connection: Connection,
    *,
    account: str,
    batch_id: str,
    expected_version: int,
    actor: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Persist a durable request; future release times are never modified."""
    normalized_account = _account(account)
    normalized_batch = _uuid(batch_id, "batchId")
    cutoff = _cutoff(now)
    batch = _lock_batch(
        connection,
        account=normalized_account,
        batch_id=normalized_batch,
        expected_version=expected_version,
    )
    if batch["status"] not in {"SCHEDULED", "ACTIVE"}:
        raise DripError(
            "BATCH_NOT_TRIGGERABLE",
            "Only an active or scheduled batch can dispatch its already-due items.",
            {"status": batch["status"]},
        )

    params = {
        "account": normalized_account,
        "batch_id": normalized_batch,
        "generation": int(batch["schedule_generation"]),
        "release_job_type": RELEASE_JOB_TYPE,
        "cutoff": cutoff,
        "stale_before": cutoff - timedelta(seconds=3600),
    }
    due_count = int(connection.execute(
        text(
            "SELECT count(*) FROM background_jobs job "
            "JOIN restart_batches batch ON batch.id=:batch_id AND batch.account=job.account "
            "WHERE batch.data_scope='PRODUCTION' AND batch.status IN ('SCHEDULED','ACTIVE') "
            "AND batch.schedule_generation=:generation AND " + _due_release_where()
        ),
        params,
    ).scalar_one())
    if due_count == 0:
        _event(
            connection,
            account=normalized_account,
            actor=actor,
            event_type="DRIP_TRIGGER_NOW_NO_DUE_ITEMS",
            entity_type="RESTART_BATCH",
            entity_id=normalized_batch,
            summary="Immediate Drip dispatch requested; no due outbox release jobs were available",
            metadata={"cutoffAt": cutoff.isoformat(), "dueJobCount": 0,
                      "scheduleGeneration": int(batch["schedule_generation"])},
        )
        return {"batchId": normalized_batch, "jobId": None, "status": "NO_DUE_ITEMS",
                "dueItemCount": 0, "cutoffAt": cutoff.isoformat()}

    trigger_id = str(uuid4())
    payload = {
        "batchId": normalized_batch,
        "triggerId": trigger_id,
        "cutoffAt": cutoff.isoformat(),
        "scheduleGeneration": int(batch["schedule_generation"]),
        "requestedBy": str(actor or "")[:200],
        "cursor": None,
    }
    job = enqueue_job(
        connection,
        account=normalized_account,
        job_type=TRIGGER_JOB_TYPE,
        idempotency_key=f"drip-trigger-now-{trigger_id}",
        payload=payload,
        priority=10,
        max_attempts=5,
        now=cutoff,
    )
    _event(
        connection,
        account=normalized_account,
        actor=actor,
        event_type="DRIP_TRIGGER_NOW_REQUESTED",
        entity_type="RESTART_BATCH",
        entity_id=normalized_batch,
        summary="Immediate Drip dispatch queued for release jobs already due at request time",
        metadata={"triggerId": trigger_id, "jobId": job.job.id, "cutoffAt": cutoff.isoformat(),
                  "dueJobCount": due_count, "scheduleGeneration": int(batch["schedule_generation"])},
    )
    return {"batchId": normalized_batch, "jobId": job.job.id, "status": "QUEUED",
            "dueItemCount": due_count, "cutoffAt": cutoff.isoformat()}


def request_immediate_day(connection, *, account, batch_id, expected_version, actor, now=None):
    """Persist an expedited daily portion, including items still preparing."""
    from zoneinfo import ZoneInfo
    from datetime import datetime as dt, time as local_time
    cutoff = _cutoff(now)
    account = _account(account)
    batch_id = _uuid(batch_id, 'batchId')
    batch = _lock_batch(connection, account=account, batch_id=batch_id, expected_version=expected_version)
    if batch['status'] not in {'SCHEDULED', 'ACTIVE'}:
        raise DripError('BATCH_NOT_TRIGGERABLE', 'Only scheduled or active plans may be expedited.')
    zone = ZoneInfo(batch['timezone_name'])
    day = max(batch['local_start_date'], cutoff.astimezone(zone).date())
    # Use original assignments; accelerating a row must not change day membership.
    end = dt.combine(day + timedelta(days=1), local_time.min, zone).astimezone(timezone.utc)
    rows = connection.execute(text("""
        SELECT i.id,i.status,i.scheduled_at,
               COALESCE(m.scheduled_at,i.scheduled_at) original_at
        FROM restart_items i JOIN restart_operations o
          ON o.id=i.operation_id AND o.account=i.account AND o.data_scope=i.data_scope
        LEFT JOIN restart_plan_request_items m ON m.request_id=:request
          AND m.operation_id=i.operation_id AND m.account=i.account AND m.data_scope=i.data_scope
        WHERE i.batch_id=:batch AND i.account=:account AND i.data_scope='PRODUCTION'
          AND i.status IN ('PREPARING','SCHEDULED') AND o.submission_state='NEVER_SUBMITTED'
          AND COALESCE(m.scheduled_at,i.scheduled_at)<:end
        ORDER BY i.id FOR UPDATE OF i,o
    """), {'request':batch['plan_request_id'], 'batch':batch_id,'account':account,'end':end}).mappings().all()
    changed = [row for row in rows if row['scheduled_at'] > cutoff]
    # Jobs already claimed/dispatched retain their delivery identity. The worker
    # reads the updated row; the normal dispatcher will recover waiting jobs.
    for row in changed:
        connection.execute(text("UPDATE restart_items SET scheduled_at=:now,version=version+1 WHERE id=:id AND account=:account"),
                           {'now':cutoff,'id':row['id'],'account':account})
        connection.execute(text("""UPDATE background_jobs SET available_at=:now,updated_at=:now
            WHERE account=:account AND data_scope='PRODUCTION' AND job_type='drip.release_item'
              AND payload_json->>'batchId'=:batch AND payload_json->>'itemId'=:item
              AND payload_json->>'scheduleGeneration'=:generation
              AND state IN ('PENDING','RETRY_WAIT') AND lease_token IS NULL
              AND dispatch_lease_token IS NULL AND dispatched_at IS NULL AND available_at>:now"""),
            {'now':cutoff,'account':account,'batch':batch_id,'item':str(row['id']),
             'generation':str(batch['schedule_generation'])})
    preparing = sum(row['status']=='PREPARING' for row in rows)
    status = 'REQUESTED' if changed else 'ALREADY_REQUESTED' if rows else 'NO_ELIGIBLE_ITEMS'
    _event(connection,account=account,actor=actor,event_type='DRIP_IMMEDIATE_DAY_REQUESTED',
        entity_type='RESTART_BATCH',entity_id=batch_id,summary='Daily portion requested for immediate preparation and release',
        metadata={'cutoffAt':cutoff.isoformat(),'day':day.isoformat(),'status':status,
                  'changedItems':[{'id':row['id'],'originalAt':row['original_at'].isoformat(),
                                   'previousAt':row['scheduled_at'].isoformat()} for row in changed],
                  'selectedCount':len(rows),'preparingCount':preparing})
    return {'batchId':batch_id,'jobId':None,'status':status,'dueItemCount':len(rows),
            'selectedCount':len(rows),'preparingCount':preparing,'readyCount':len(rows)-preparing,
            'cutoffAt':cutoff.isoformat()}

"""Transaction-only scheduling of automatic preparation and corrected-item retries."""
import json
from datetime import datetime, timezone
from sqlalchemy import text
from jobs.service import enqueue_job

PREPARATION_JOB_TYPE = 'drip.prepare_item'
PREPARATION_BATCH_JOB_TYPE = 'drip.prepare_batch'


def enqueue_preparation(conn, *, item, account, now=None):
    return enqueue_job(conn, account=account, job_type=PREPARATION_JOB_TYPE,
        idempotency_key=f"drip-prepare-{item['id']}-{item['preparation_generation']}",
        payload={'itemId':int(item['id']), 'generation':int(item['preparation_generation'])}, max_attempts=10, available_at=now)


def enqueue_preparation_batch(conn, *, batch, account, now=None):
    generation = int(batch['schedule_generation'])
    return enqueue_job(conn, account=account, job_type=PREPARATION_BATCH_JOB_TYPE,
        idempotency_key=f"drip-prepare-batch-{batch['id']}-{generation}-0",
        payload={'batchId':str(batch['id']), 'scheduleGeneration':generation, 'afterItemId':0},
        max_attempts=10, available_at=now)


def start_automatic_batch(conn, *, batch, operations, actor, now, heartbeat=None, scheduled_assignments=None):
    from services.drip_service import _event, _schedule, _batch_dto, DripError
    if scheduled_assignments is not None:
        expected={str(operation['operation_id']) for operation in operations}
        actual={str(operation_id) for operation_id in scheduled_assignments}
        if expected!=actual or any(scheduled_assignments.get(operation_id) is None for operation_id in expected):
            raise DripError('PLAN_SCHEDULE_SNAPSHOT_INCOMPLETE','The saved plan does not contain one assignment for every operation.',status=409)
        scheduled=[(operation,scheduled_assignments[str(operation['operation_id'])]) for operation in operations]
    else:
        from services.drip_distribution import distribute
        days = _schedule(start_date=batch['local_start_date'], daily_time=batch['daily_time'], timezone_name=batch['timezone_name'],
                         total_days=int(batch['total_days']), now=now)
        from services.drip_service import apply_known_anti_collision_schedule
        scheduled = apply_known_anti_collision_schedule(
            conn, account=batch['account'], operations=distribute(operations, days), now=now,
        )
    for offset in range(0, len(scheduled), 500):
        if heartbeat:
            heartbeat()
        chunk = scheduled[offset:offset + 500]
        conn.execute(text("INSERT INTO restart_items (batch_id,account,source_ean,row_number,data_scope,operation_id,scheduled_at,status) "
            "VALUES (:batch,:account,:ean,:row,'PRODUCTION',:op,:date,'PREPARING')"),
            [{'batch':batch['id'],'account':batch['account'],'ean':operation['confirmed_ean'],'row':offset+index+1,
              'op':operation['operation_id'],'date':scheduled_at} for index,(operation,scheduled_at) in enumerate(chunk)])
    updated=conn.execute(text("UPDATE restart_batches SET status='SCHEDULED',started_at=:now,version=version+1,updated_at=:now "
        "WHERE id=:id AND account=:account RETURNING *"),{'id':batch['id'],'account':batch['account'],'now':now}).mappings().one()
    enqueue_preparation_batch(conn, batch=updated, account=batch['account'], now=now)
    _event(conn, account=batch['account'],actor=actor,event_type='DRIP_AUTOMATIC_PREPARATION_STARTED',entity_type='RESTART_BATCH',
           entity_id=str(batch['id']),summary='Automatic per-product preparation queued',metadata={'count':len(operations)})
    return _batch_dto(updated)


def retry_preparation(conn, *, batch, actor, now=None):
    from services.drip_service import _event, get_batch, DripError
    if batch['status'] not in {'SCHEDULED','ACTIVE','PAUSED','COMPLETED'}:
        raise DripError('BATCH_NOT_RETRYABLE','This plan cannot retry erroneous products.')
    now=now or datetime.now(timezone.utc)
    rows=conn.execute(text("SELECT i.* FROM restart_items i JOIN restart_operations o ON o.id=i.operation_id AND o.account=i.account "
        "AND o.data_scope=i.data_scope WHERE i.batch_id=:id AND i.account=:account AND i.data_scope='PRODUCTION' "
        "AND i.status IN ('NEEDS_REVIEW','FAILED') AND o.submission_state='NEVER_SUBMITTED' ORDER BY i.row_number FOR UPDATE OF i,o"),
        {'id':batch['id'],'account':batch['account']}).mappings().all()
    deferred_until_resume=batch['status']=='PAUSED'
    for row in rows:
        # The trigger allows only never-submitted erroneous snapshots to be rebuilt.
        if row['status']=='FAILED':
            conn.execute(text("UPDATE restart_items SET status='NEEDS_REVIEW' WHERE id=:id AND account=:account"),{'id':row['id'],'account':batch['account']})
        item=conn.execute(text("UPDATE restart_items SET status='PREPARING',preparation_generation=preparation_generation+1,"
            "scheduled_at=:now,version=version+1,last_error_code=NULL,preparation_errors_json='[]'::jsonb WHERE id=:id AND account=:account RETURNING *"),
            {'id':row['id'],'account':batch['account'],'now':now}).mappings().one()
        if not deferred_until_resume:
            enqueue_preparation(conn,item=item,account=batch['account'],now=now)
    if rows and not deferred_until_resume:
        conn.execute(text("UPDATE restart_batches SET status='ACTIVE',version=version+1,updated_at=:now WHERE id=:id AND account=:account"),
            {'id':batch['id'],'account':batch['account'],'now':now})
    _event(conn,account=batch['account'],actor=actor,event_type='DRIP_ERRORS_RETRIED',entity_type='RESTART_BATCH',entity_id=str(batch['id']),
           summary='Corrected unsubmitted products queued or deferred until Resume; uncertain submissions excluded',
           metadata={'count':len(rows),'deferredUntilResume':deferred_until_resume})
    return get_batch(conn,account=batch['account'],batch_id=str(batch['id']))


def create_preparation_batch_executor(engine_provider):
    """Expand a large preparation batch into bounded, idempotent item jobs."""
    from jobs.service import heartbeat_job, JobExecutionError

    def execute(job):
        if job.job_type != PREPARATION_BATCH_JOB_TYPE:
            raise JobExecutionError('INVALID_PREPARATION_BATCH_JOB', retryable=False)
        batch_id = str(job.payload.get('batchId') or '')
        generation = job.payload.get('scheduleGeneration')
        after_item_id = job.payload.get('afterItemId', 0)
        if not isinstance(generation, int) or not isinstance(after_item_id, int) or after_item_id < 0:
            raise JobExecutionError('INVALID_PREPARATION_BATCH_CURSOR', retryable=False)
        with engine_provider().begin() as conn:
            batch = conn.execute(text("SELECT id::text,account,status,schedule_generation FROM restart_batches "
                "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
                {'id':batch_id,'account':job.account}).mappings().first()
            if batch is None or batch['status'] in {'CANCELLED','COMPLETED'}:
                return
            if int(batch['schedule_generation']) != generation:
                return
            if batch['status'] == 'PAUSED':
                raise JobExecutionError('BATCH_PAUSED', retryable=True, retry_delay_seconds=300)
            items=conn.execute(text("SELECT id,preparation_generation FROM restart_items WHERE batch_id=:batch "
                "AND account=:account AND data_scope='PRODUCTION' AND status='PREPARING' AND id>:cursor "
                "ORDER BY id LIMIT 500"),{'batch':batch_id,'account':job.account,'cursor':after_item_id}).mappings().all()
            for item in items:
                enqueue_preparation(conn,item=item,account=job.account)
            heartbeat_job(conn,job_id=job.id,account=job.account,lease_token=job.lease_token or '',lease_seconds=60)
            if len(items) == 500:
                next_cursor = int(items[-1]['id'])
                enqueue_job(conn,account=job.account,job_type=PREPARATION_BATCH_JOB_TYPE,
                    idempotency_key=f"drip-prepare-batch-{batch_id}-{generation}-{next_cursor}",
                    payload={'batchId':batch_id,'scheduleGeneration':generation,'afterItemId':next_cursor},
                    max_attempts=10)
    return execute

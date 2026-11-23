"""Bounded selection manifests and resumable DB-only plan construction."""
import json
import hashlib
import re
from uuid import UUID, uuid4, uuid5
from datetime import datetime, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo
from sqlalchemy import text
from jobs.service import JobExecutionError, enqueue_job
from services.drip_service import DripError, _schedule, create_batch_draft, start_batch
from services.operation_service import OperationError
from services.product_catalog_service import _filters, _PRODUCT_FROM, _PRODUCT_REMAINING_JOINS, _CATEGORY_SQL, _card_schemas


def _selection_statement(conn, payload):
    filters=payload.filters or {}
    if set(filters)-{'period','status','source','factoryId','query','category','titleOver70'}:
        raise DripError('INVALID_FILTERS','Unsupported selection filter.',status=422)
    if payload.candidate_ids is not None:
        from uuid import UUID
        try:
            if len(payload.candidate_ids)!=len(set(payload.candidate_ids)):
                raise ValueError()
            for value in payload.candidate_ids:
                UUID(value)
        except (ValueError, TypeError):
            raise DripError('INVALID_SELECTION','Selection must contain unique candidate UUIDs.',status=422) from None
    where,params=_filters(account=payload.account,period=filters.get('period'),status=filters.get('status'),source=filters.get('source'),
        factory_id=filters.get('factoryId'),query=filters.get('query'))
    params['card_schemas'] = _card_schemas(conn, account=payload.account)
    where += ["card.status IN ('READY','ERROR')", "btrim("+_CATEGORY_SQL+")<>''", "ce.classification='EXACT_CODE'",
              "(op.id IS NULL OR (op.status IN ('DRAFT','PREPARING','QC_PENDING','NEEDS_REVIEW','READY') AND EXISTS(SELECT 1 FROM restart_operations allowed WHERE allowed.id=op.id AND allowed.account=ce.account AND allowed.submission_state='NEVER_SUBMITTED')))"]
    if payload.candidate_ids is not None:
        where.append('ce.id=ANY(CAST(:candidate_ids AS uuid[]))')
        params['candidate_ids']=payload.candidate_ids
    if filters.get('category'):
        where.append(_CATEGORY_SQL+'=:category')
        params['category']=filters['category']
    if filters.get('titleOver70'):
        from services.product_catalog_service import _TITLE_SQL
        where.append('char_length('+_TITLE_SQL+')>70')
    statement='SELECT ce.id::text AS candidate_id,op.id::text AS operation_id,ce.confirmed_ean,sp.factory_id,ce.period,'+_CATEGORY_SQL+' AS category,card.status AS card_status '+_PRODUCT_FROM+_PRODUCT_REMAINING_JOINS+' WHERE '+' AND '.join(where)+' ORDER BY sp.factory_id,ce.period,ce.id LIMIT 50001'
    return statement,params


def selection(conn, payload):
    statement,params=_selection_statement(conn,payload)
    rows=conn.execute(text(statement),params).mappings().all()
    if not rows:
        raise DripError('NO_ELIGIBLE_PRODUCTS','No eligible Ready/Error products match this selection.',status=422)
    if len(rows)>50000:
        raise DripError('SELECTION_LIMIT_EXCEEDED','Select at most 50000 products per planning request.',status=422)
    return rows


def _schedule_assignments(conn, payload, rows):
    """Calculate per-candidate assignment once for preview, persistence and legacy recovery."""
    from services.drip_distribution import distribute
    from services.drip_service import apply_known_anti_collision_schedule
    days=_schedule(start_date=payload.start_date,daily_time=payload.daily_time,timezone_name=payload.timezone_name,total_days=payload.total_days)
    cohorts=defaultdict(list)
    for row in rows:
        cohorts[(row['factory_id'],row['period'])].append(row)
    counts={d:0 for d in days}
    categories=defaultdict(lambda: defaultdict(int))
    assignments={}
    for cohort in cohorts.values():
        for operation,when in apply_known_anti_collision_schedule(conn,account=payload.account,operations=distribute(cohort,days)):
            counts[when]=counts.get(when,0)+1
            category=str(operation.get('category') or '').strip() or 'Без категории'
            categories[when][category] += 1
            assignments[str(operation['candidate_id'])]={'scheduled_at':when,'category':category}
    zone=ZoneInfo(payload.timezone_name)
    distribution=[]
    for scheduled_at,count in sorted(counts.items()):
        local_date=scheduled_at.astimezone(zone).date()
        distribution.append({
            'dayNumber':(local_date-payload.start_date).days+1,
            'scheduledAt':scheduled_at.isoformat(),
            'count':count,
            'categories':dict(sorted(categories[scheduled_at].items())),
        })
    return assignments,distribution


def _schedule_snapshot(conn, payload, rows):
    """Return the one authoritative, category-aware schedule used by preview and creation."""
    assignments,distribution=_schedule_assignments(conn,payload,rows)
    zone=ZoneInfo(payload.timezone_name)
    selected=[{
        'candidateId':str(row['candidate_id']),
        'operationId':str(row['operation_id']) if row['operation_id'] else None,
        'ean':str(row['confirmed_ean'] or ''),
        'factoryId':str(row['factory_id']) if row['factory_id'] is not None else None,
        'period':str(row['period']),
        'category':str(row['category'] or '').strip(),
        'cardStatus':str(row['card_status']),
        # Compare local day, not collision-adjustment seconds: the latter are
        # based on the current instant for in-flight cross-account items.
        'scheduledDate':assignments[str(row['candidate_id'])]['scheduled_at'].astimezone(zone).date().isoformat(),
    } for row in rows]
    selection_spec={
        'account':payload.account,'name':payload.name,
        'candidateIds':sorted(payload.candidate_ids or []),
        'filters':payload.filters or {},
        'startDate':payload.start_date.isoformat(),'dailyTime':payload.daily_time.isoformat(),
        'timezone':payload.timezone_name,'totalDays':payload.total_days,
        'items':selected,
    }
    selection_hash=hashlib.sha256(json.dumps(selection_spec,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()
    occupied=[entry for entry in distribution if entry['count']]
    return {
        'account':payload.account,'itemCount':len(rows),
        'readyCount':sum(1 for row in rows if row['card_status']=='READY'),
        'needsCorrectionCount':sum(1 for row in rows if row['card_status']=='ERROR'),
        'groupCount':len({(row['factory_id'],row['period']) for row in rows}),
        'averagePerDay':round(len(rows)/payload.total_days,2),
        'firstRunAt':occupied[0]['scheduledAt'] if occupied else None,
        'selectionHash':selection_hash,'distribution':distribution,
        'externalServicesCalled':[],
    },assignments


def preview(conn, payload):
    rows=selection(conn,payload)
    result,_=_schedule_snapshot(conn,payload,rows)
    return result


def create_request(conn,payload,actor, *, materialize=False):
    if not payload.preview_hash:
        raise DripError('PREVIEW_REQUIRED','Refresh the schedule preview before creating the plan.',status=409)
    rows=selection(conn,payload)
    calculated,assignments=_schedule_snapshot(conn,payload,rows)
    if calculated['selectionHash']!=payload.preview_hash:
        raise DripError('PREVIEW_STALE','Products or schedule parameters changed. Refresh the preview before creating the plan.',status=409,
                        details={'currentPreviewHash':calculated['selectionHash']})
    request_id=str(uuid4())
    inserted_count=len(rows)
    conn.execute(text('INSERT INTO restart_plan_requests(id,account,name,start_date,daily_time,timezone_name,total_days,created_by,item_count) VALUES (:id,:account,:name,:date,:time,:tz,:days,:actor,:count)'),
        {'id':request_id,'account':payload.account,'name':payload.name,'date':payload.start_date,'time':payload.daily_time,'tz':payload.timezone_name,'days':payload.total_days,'actor':actor,'count':inserted_count})
    manifest=[]
    for row_number,row in enumerate(rows,1):
        assignment=assignments[str(row['candidate_id'])]
        manifest.append({'request_id':request_id,'account':payload.account,'candidate_id':row['candidate_id'],
            'row_number':row_number,'operation_id':row['operation_id'],'scheduled_at':assignment['scheduled_at'],
            'category_snapshot':assignment['category']})
    for offset in range(0,len(manifest),500):
        conn.execute(text("INSERT INTO restart_plan_request_items(request_id,account,candidate_episode_id,row_number,operation_id,scheduled_at,category_snapshot) "
            "VALUES (:request_id,:account,CAST(:candidate_id AS uuid),:row_number,CAST(:operation_id AS uuid),:scheduled_at,:category_snapshot)"),
            manifest[offset:offset+500])
    # Small selections become executable schedules in this transaction, as in
    # OttoFlow. Large selections retain bounded, resumable construction.
    if materialize and inserted_count <= 500:
        from services.operation_service import create_operation
        groups = defaultdict(list)
        for row in rows:
            candidate_id = str(row['candidate_id'])
            operation_id = row['operation_id']
            if not operation_id:
                operation_id = create_operation(conn, account=payload.account,
                    candidate_episode_id=candidate_id, actor=actor)['id']
                conn.execute(text('UPDATE restart_plan_request_items SET operation_id=:op '
                    'WHERE request_id=:request AND account=:account AND candidate_episode_id=CAST(:candidate AS uuid)'),
                    {'op':operation_id, 'request':request_id, 'account':payload.account, 'candidate':candidate_id})
            groups[(row['factory_id'], row['period'])].append((str(operation_id), assignments[candidate_id]['scheduled_at']))
        batches = []
        for (factory_id, period), entries in groups.items():
            batch = create_batch_draft(conn, account=payload.account,
                name=f"{payload.name} · {period}"[:160], operation_ids=[entry[0] for entry in entries],
                start_date=payload.start_date, daily_time=payload.daily_time,
                timezone_name=payload.timezone_name, total_days=payload.total_days, actor=actor)
            conn.execute(text('UPDATE restart_batches SET plan_request_id=:request WHERE id=:id AND account=:account'),
                {'request':request_id, 'id':batch['id'], 'account':payload.account})
            start_batch(conn, account=payload.account, batch_id=batch['id'], expected_version=batch['version'],
                actor=actor, auto_prepare=True, scheduled_assignments=dict(entries))
            batches.append(batch['id'])
        conn.execute(text("UPDATE restart_plan_requests SET status='COMPLETED',prepared_count=item_count,"
            "processing_started_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account"),
            {'id':request_id, 'account':payload.account})
        return {'id':request_id, 'jobId':None, 'status':'COMPLETED', 'itemCount':inserted_count, 'batchIds':batches}
    job=enqueue_job(conn,account=payload.account,job_type='drip.plan_create',idempotency_key=f'drip-plan-{request_id}-0',payload={'requestId':request_id})
    return {'id':request_id,'jobId':job.job.id,'status':'PENDING','itemCount':inserted_count}


def _validate_plan_paging(page, page_size):
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
        raise DripError('INVALID_PAGINATION','Page must be positive and pageSize must be between 1 and 200.',status=422)


def list_requests(conn, *, account, page=1, page_size=50, factory_id=None, source_type=None,
                  status=None, query=None, created_from=None, created_to=None):
    """List saved account-scoped production manifests, even before they have batches."""
    from datetime import timedelta
    from services.drip_service import _account

    account=_account(account)
    _validate_plan_paging(page,page_size)
    where=["r.account=:account", "r.data_scope='PRODUCTION'"]
    params={'account':account}
    manifest_exists=("EXISTS (SELECT 1 FROM restart_plan_request_items i "
        "JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account AND ce.data_scope=i.data_scope "
        "JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
        "LEFT JOIN restart_operations op ON op.id=i.operation_id AND op.account=i.account AND op.data_scope=i.data_scope "
        "LEFT JOIN operation_revisions rev ON rev.operation_id=op.id AND rev.account=op.account "
        "AND rev.data_scope=op.data_scope AND rev.revision_number=op.current_revision_number "
        "LEFT JOIN restart_product_edits edit ON edit.candidate_episode_id=ce.id AND edit.account=ce.account "
        "AND edit.data_scope=ce.data_scope "
        "WHERE i.request_id=r.id AND i.account=r.account AND i.data_scope='PRODUCTION' ")
    if status:
        if status not in {'PENDING','BUILDING','COMPLETED','FAILED'}:
            raise DripError('INVALID_PLAN_STATUS','Unsupported planning request status.',status=422)
        where.append('r.status=:status'); params['status']=status
    if factory_id and str(factory_id).strip():
        where.append(manifest_exists+"AND sp.factory_id=:factory_id)")
        params['factory_id']=str(factory_id).strip()[:200]
    if source_type:
        source_values={'AFTERBUY':['AFTERBUY','AFTERCOOL'],'OTTO_DIRECT':['OTTO_DIRECT']}.get(source_type)
        if not source_values:
            raise DripError('INVALID_SOURCE','Source must be AFTERBUY or OTTO_DIRECT.',status=422)
        where.append(manifest_exists+"AND sp.source_type=ANY(:source_types))")
        params['source_types']=source_values
    if query and str(query).strip():
        params['query']=f"%{str(query).strip()[:100]}%"
        where.append("(r.name ILIKE :query OR "+manifest_exists+
            "AND (sp.original_title ILIKE :query OR edit.title ILIKE :query "
            "OR rev.content_json->>'title' ILIKE :query OR sp.aftercool_sku ILIKE :query OR sp.aftercool_ean ILIKE :query "
            "OR ce.observed_sku ILIKE :query OR ce.confirmed_ean ILIKE :query OR op.target_sku ILIKE :query "
            "OR op.new_ean ILIKE :query)))")
    if created_from:
        params['created_from']=created_from
        where.append('r.created_at >= CAST(:created_from AS date)')
    if created_to:
        params['created_to']=created_to+timedelta(days=1)
        where.append('r.created_at < CAST(:created_to AS date)')
    clause=' AND '.join(where)
    total=int(conn.execute(text(f'SELECT COUNT(*) FROM restart_plan_requests r WHERE {clause}'),params).scalar_one())
    rows=conn.execute(text(
        "SELECT r.id::text,r.account,r.name,r.start_date,r.daily_time,r.timezone_name,r.total_days,r.item_count,"
        "r.prepared_count,r.status,r.last_error_code,r.created_at,r.processing_started_at,job.id::text AS job_id,job.state AS job_state,"
        "job.last_error_code AS job_error,COALESCE(job.attempts_in_cycle,0) AS job_attempts,"
        "COALESCE(job.max_attempts,0) AS job_max_attempts,"
        "(SELECT COUNT(*) FROM restart_batches b WHERE b.plan_request_id=r.id AND b.account=r.account AND b.data_scope='PRODUCTION') AS batch_count,"
        "COALESCE((SELECT array_agg(DISTINCT sp.factory_id ORDER BY sp.factory_id) FROM restart_plan_request_items i "
        "JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account AND ce.data_scope=i.data_scope "
        "JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
        "WHERE i.request_id=r.id AND i.account=r.account AND i.data_scope='PRODUCTION' AND sp.factory_id IS NOT NULL),ARRAY[]::text[]) AS factory_ids "
        "FROM restart_plan_requests r LEFT JOIN LATERAL (SELECT j.* FROM background_jobs j WHERE j.account=r.account "
        "AND j.data_scope='PRODUCTION' AND j.job_type='drip.plan_create' AND j.payload_json->>'requestId'=r.id::text "
        "ORDER BY j.created_at DESC,j.id DESC LIMIT 1) job ON TRUE "
        f"WHERE {clause} ORDER BY r.created_at DESC,r.id DESC LIMIT :limit OFFSET :offset"),
        {**params,'limit':page_size,'offset':(page-1)*page_size}).mappings().all()
    return {'items':[{'id':r['id'],'account':r['account'],'name':r['name'],'status':r['status'],
        'itemCount':int(r['item_count']),'preparedCount':int(r['prepared_count']),
        'errorCode':r['last_error_code'] or r['job_error'],'jobId':r['job_id'],'jobState':r['job_state'],
        'jobAttempts':int(r['job_attempts']),'jobMaxAttempts':int(r['job_max_attempts']),
        'batchCount':int(r['batch_count']),'factoryIds':list(r['factory_ids'] or []),
        'startDate':r['start_date'].isoformat(),'dailyTime':r['daily_time'].isoformat(timespec='minutes'),
        'timezone':r['timezone_name'],'totalDays':int(r['total_days']),'createdAt':r['created_at'].isoformat(),
        'processingStartedAt':r['processing_started_at'].isoformat() if r['processing_started_at'] else None}
        for r in rows], 'page':{'page':page,'pageSize':page_size,'totalItems':total,
        'totalPages':(total+page_size-1)//page_size if total else 0}}


def get_request(conn,account,request_id,*,page=1,page_size=50,query=None,status=None,stage=None,batch_id=None):
    from services.drip_service import _account, _batch_dto, _item_group_counts, ITEM_STATUS_GROUPS

    account=_account(account)
    request_id=str(UUID(str(request_id)))
    _validate_plan_paging(page,page_size)
    row=conn.execute(text("SELECT * FROM restart_plan_requests WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),{'id':request_id,'account':account}).mappings().first()
    if row is None:
        raise DripError('PLAN_REQUEST_NOT_FOUND','Plan request was not found.',status=404)
    manifest_where="i.request_id=:id AND i.account=:account AND i.data_scope='PRODUCTION'"
    manifest_params={'id':request_id,'account':account}
    if batch_id:
        try:
            batch_uuid=str(UUID(str(batch_id)))
        except (ValueError,TypeError,AttributeError):
            raise DripError('INVALID_BATCH_ID','Batch ID must be a UUID.',status=422) from None
        batch_scope=conn.execute(text("SELECT factory_id,period FROM restart_batches WHERE id=:batch_id AND account=:account "
            "AND data_scope='PRODUCTION' AND plan_request_id=:request_id"),
            {'batch_id':batch_uuid,'account':account,'request_id':request_id}).mappings().first()
        if batch_scope is None:
            raise DripError('BATCH_NOT_FOUND','Batch does not belong to this planning request and account.',status=404)
        manifest_where += " AND sp.factory_id IS NOT DISTINCT FROM :batch_factory_id AND ce.period=:batch_period"
        manifest_params['batch_factory_id']=batch_scope['factory_id']
        manifest_params['batch_period']=batch_scope['period']
    if query and str(query).strip():
        manifest_where += " AND (sp.original_title ILIKE :query OR edit.title ILIKE :query OR ce.confirmed_ean ILIKE :query "
        manifest_where += "OR ce.observed_sku ILIKE :query OR sp.aftercool_sku ILIKE :query OR sp.aftercool_ean ILIKE :query "
        manifest_where += "OR op.target_sku ILIKE :query OR op.new_ean ILIKE :query OR rev.content_json->>'title' ILIKE :query)"
        manifest_params['query']=f"%{str(query).strip()[:100]}%"
    allowed_stages={'PREPARING','DRAFT','QC_PENDING','READY','QUEUED','SUBMITTING','OTTO_PROCESSING','AWAITING_ONLINE',
        'PUBLISHED','NEEDS_REVIEW','RECONCILIATION_REQUIRED','FAILED','CANCELLED','SCHEDULED','READY_TO_SUBMIT'}
    if stage:
        if stage not in allowed_stages:
            raise DripError('INVALID_ITEM_STAGE','Unsupported Drip Feed item stage.',status=422)
        manifest_where += " AND COALESCE(queue.status,op.status)=:stage"
        manifest_params['stage']=stage
    if status:
        if status not in {*ITEM_STATUS_GROUPS, "all"}:
            raise DripError('INVALID_ITEM_FILTER','Unsupported item filter.',status=422)
        if status != 'all':
            manifest_where += " AND COALESCE(queue.status,op.status,CASE WHEN i.operation_id IS NULL THEN :request_status ELSE 'DRAFT' END)=ANY(:statuses)"
            manifest_params['request_status']=row['status']
            manifest_params['statuses']=list(ITEM_STATUS_GROUPS[status])
    batches=conn.execute(text(
        "SELECT b.*,COALESCE(counts.item_counts,'{}'::jsonb) AS item_counts FROM restart_batches b "
        "LEFT JOIN LATERAL (SELECT jsonb_object_agg(x.status,x.count) AS item_counts FROM "
        "(SELECT status,COUNT(*) AS count FROM restart_items WHERE batch_id=b.id AND account=b.account "
        "AND data_scope='PRODUCTION' GROUP BY status) x) counts ON TRUE "
        "WHERE b.plan_request_id=:id AND b.account=:account AND b.data_scope='PRODUCTION' ORDER BY b.created_at,b.id"),
        {'id':request_id,'account':account}).mappings().all()
    job=conn.execute(text("SELECT id::text,state,last_error_code,attempts_in_cycle,max_attempts FROM background_jobs WHERE account=:account AND data_scope='PRODUCTION' AND job_type='drip.plan_create' AND payload_json->>'requestId'=:id ORDER BY created_at DESC,id DESC LIMIT 1"),{'id':request_id,'account':account}).mappings().first()
    item_count = int(row['item_count'])
    if (query and str(query).strip()) or status or stage or batch_id:
        page_total=int(conn.execute(text(
            "SELECT COUNT(*) FROM restart_plan_request_items i JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id "
            "AND ce.account=i.account AND ce.data_scope=i.data_scope JOIN source_products sp ON sp.id=ce.source_product_id "
            "AND sp.account=ce.account AND sp.data_scope=ce.data_scope LEFT JOIN restart_operations op ON op.id=i.operation_id "
            "AND op.account=i.account AND op.data_scope=i.data_scope LEFT JOIN operation_revisions rev ON rev.operation_id=op.id "
            "AND rev.account=op.account AND rev.data_scope=op.data_scope AND rev.revision_number=op.current_revision_number "
            "LEFT JOIN restart_product_edits edit ON edit.candidate_episode_id=ce.id AND edit.account=ce.account "
            "AND edit.data_scope=ce.data_scope LEFT JOIN LATERAL (SELECT ri.status FROM restart_items ri "
            "JOIN restart_batches rb ON rb.id=ri.batch_id AND rb.plan_request_id=i.request_id "
            "AND rb.account=i.account AND rb.data_scope=i.data_scope WHERE ri.operation_id=op.id AND ri.account=i.account "
            "AND ri.data_scope=i.data_scope ORDER BY ri.id DESC LIMIT 1) queue ON TRUE WHERE "+manifest_where),manifest_params).scalar_one())
    else:
        page_total=item_count
    manifest=conn.execute(text(
        "SELECT i.row_number,ce.id::text AS candidate_episode_id,ce.period,ce.confirmed_ean AS source_ean,ce.observed_sku AS source_sku,"
        "sp.source_type,sp.factory_id,sp.factory_name,COALESCE(NULLIF(rev.content_json->>'title',''),NULLIF(edit.title,''),sp.original_title) AS title,"
        "COALESCE(CASE WHEN jsonb_typeof(rev.content_json->'images')='array' THEN NULLIF(rev.content_json->'images'->0->>'url','') END,sp.image_url) AS image_url,"
        "i.operation_id::text AS operation_id,op.status AS operation_status,op.target_sku,op.new_ean,"
        "COALESCE(queue.scheduled_at,i.scheduled_at) AS scheduled_at,i.category_snapshot,"
        "queue.status AS queue_status,queue.last_error_code AS queue_error FROM restart_plan_request_items i "
        "JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account AND ce.data_scope=i.data_scope "
        "JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
        "LEFT JOIN restart_operations op ON op.id=i.operation_id AND op.account=i.account AND op.data_scope=i.data_scope "
        "LEFT JOIN operation_revisions rev ON rev.operation_id=op.id AND rev.account=op.account AND rev.data_scope='PRODUCTION' AND rev.revision_number=op.current_revision_number "
        "LEFT JOIN restart_product_edits edit ON edit.candidate_episode_id=ce.id AND edit.account=ce.account AND edit.data_scope=ce.data_scope "
        "LEFT JOIN LATERAL (SELECT ri.scheduled_at,ri.status,ri.last_error_code FROM restart_items ri "
        "JOIN restart_batches rb ON rb.id=ri.batch_id AND rb.plan_request_id=i.request_id AND rb.account=i.account "
        "AND rb.data_scope=i.data_scope WHERE ri.operation_id=op.id AND ri.account=i.account AND ri.data_scope=i.data_scope "
        "ORDER BY ri.id DESC LIMIT 1) queue ON TRUE "
        "WHERE "+manifest_where+" ORDER BY i.row_number LIMIT :limit OFFSET :offset"),
        {**manifest_params,'limit':page_size,'offset':(page-1)*page_size}).mappings().all()
    factory_ids=conn.execute(text(
        "SELECT DISTINCT sp.factory_id FROM restart_plan_request_items i "
        "JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account AND ce.data_scope=i.data_scope "
        "JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
        "WHERE i.request_id=:id AND i.account=:account AND i.data_scope='PRODUCTION' AND sp.factory_id IS NOT NULL ORDER BY sp.factory_id"),
        {'id':request_id,'account':account}).scalars().all()
    effective_status_counts=conn.execute(text(
        "SELECT COALESCE(queue.status,op.status,CASE WHEN i.operation_id IS NULL THEN :request_status ELSE 'DRAFT' END) AS status,COUNT(*) AS count "
        "FROM restart_plan_request_items i LEFT JOIN restart_operations op ON op.id=i.operation_id AND op.account=i.account AND op.data_scope=i.data_scope "
        "LEFT JOIN LATERAL (SELECT ri.status FROM restart_items ri JOIN restart_batches rb ON rb.id=ri.batch_id "
        "AND rb.plan_request_id=i.request_id AND rb.account=i.account AND rb.data_scope=i.data_scope "
        "WHERE ri.operation_id=i.operation_id AND ri.account=i.account AND ri.data_scope=i.data_scope ORDER BY ri.id DESC LIMIT 1) queue ON TRUE "
        "WHERE i.request_id=:id AND i.account=:account AND i.data_scope='PRODUCTION' GROUP BY 1"),
        {'id':request_id,'account':account,'request_status':row['status']}).mappings().all()
    item_group_counts=_item_group_counts({str(item['status']):int(item['count']) for item in effective_status_counts})
    return {'id':str(row['id']),'account':account,'name':row['name'],'status':row['status'],'itemCount':item_count,
        'preparedCount':int(row['prepared_count']),'errorCode':row['last_error_code'] or (job['last_error_code'] if job else None),
        'jobId':job['id'] if job else None,'jobState':job['state'] if job else None,
        'jobAttempts':int(job['attempts_in_cycle']) if job else 0,'jobMaxAttempts':int(job['max_attempts']) if job else 0,
        'startDate':row['start_date'].isoformat(),'dailyTime':row['daily_time'].isoformat(timespec='minutes'),
        'timezone':row['timezone_name'],'totalDays':int(row['total_days']),'createdAt':row['created_at'].isoformat(),
        'processingStartedAt':row['processing_started_at'].isoformat() if row['processing_started_at'] else None,
        'itemGroupCounts':item_group_counts,
        'batchCount':len(batches),'factoryIds':[str(value) for value in factory_ids],
        'batches':[{**_batch_dto(b),'itemCounts':dict(b['item_counts'] or {}),
                    'itemGroupCounts':_item_group_counts(dict(b['item_counts'] or {}))} for b in batches],
        'items':[{'rowNumber':int(i['row_number']),'candidateEpisodeId':i['candidate_episode_id'],'period':i['period'],
            'sourceEan':i['source_ean'],'sourceSku':i['source_sku'],'sourceType':i['source_type'],
            'factoryId':i['factory_id'],'factoryName':i['factory_name'],'title':i['title'],'imageUrl':i['image_url'],
            'operationId':i['operation_id'],'operationStatus':i['operation_status'],'targetSku':i['target_sku'],'newEan':i['new_ean'],
            'category':i['category_snapshot'],
            'scheduledAt':i['scheduled_at'].isoformat() if i['scheduled_at'] else None,'queueStatus':i['queue_status'],
            'queueErrorCode':i['queue_error']} for i in manifest],
        'page':{'page':page,'pageSize':page_size,'totalItems':page_total,'totalPages':(page_total+page_size-1)//page_size if page_total else 0}}


def retry_request(conn, *, account, request_id, actor):
    """Resume a failed manifest with a new job; never replay a completed job."""
    from jobs.service import enqueue_job
    from services.drip_service import _event
    try:
        normalized_id = str(UUID(str(request_id)))
    except (ValueError, TypeError, AttributeError):
        raise DripError('INVALID_PLAN_REQUEST','Plan request ID must be a UUID.',status=422) from None
    row=conn.execute(text("SELECT * FROM restart_plan_requests WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {'id':normalized_id,'account':account}).mappings().first()
    if row is None:
        raise DripError('PLAN_REQUEST_NOT_FOUND','Plan request was not found.',status=404)
    if row['status']!='FAILED':
        raise DripError('PLAN_REQUEST_NOT_RETRYABLE','Only a failed plan request can be resumed.',status=409,
                        details={'status':row['status']})
    if row['processing_started_at'] is None:
        try:
            _schedule(start_date=row['start_date'],daily_time=row['daily_time'],timezone_name=row['timezone_name'],
                      total_days=int(row['total_days']))
        except DripError as error:
            if error.code == 'SCHEDULE_START_NOT_FUTURE':
                raise DripError('PLAN_START_EXPIRED','The saved start time has passed. Create a new plan with a future start time.',status=409) from None
            raise
    active=conn.execute(text("SELECT EXISTS(SELECT 1 FROM background_jobs WHERE account=:account AND data_scope='PRODUCTION' AND job_type='drip.plan_create' AND payload_json->>'requestId'=:id AND state IN ('PENDING','RUNNING','RETRY_WAIT'))"),
        {'id':normalized_id,'account':account}).scalar_one()
    if active:
        raise DripError('PLAN_REQUEST_JOB_ACTIVE','A plan creation job is already active.',status=409)
    previous_error=row['last_error_code']
    status='PENDING' if int(row['prepared_count'])==0 else 'BUILDING'
    conn.execute(text("UPDATE restart_plan_requests SET status=:status,last_error_code=NULL WHERE id=:id AND account=:account"),
        {'status':status,'id':normalized_id,'account':account})
    job=enqueue_job(conn,account=account,job_type='drip.plan_create',
        idempotency_key=f"drip-plan-{normalized_id}-retry-{uuid4().hex}",
        payload={'requestId':normalized_id},max_attempts=5)
    _event(conn,account=account,actor=actor,event_type='DRIP_PLAN_REQUEST_RETRIED',entity_type='DRIP_PLAN_REQUEST',
           entity_id=normalized_id,summary='Failed Drip plan creation resumed from its persisted manifest cursor',
           metadata={'preparedCount':int(row['prepared_count']),'itemCount':int(row['item_count']),
                     'previousErrorCode':previous_error,'jobId':job.job.id})
    return {'id':normalized_id,'jobId':job.job.id,'jobState':job.job.state,'status':status,
            'itemCount':int(row['item_count']),'preparedCount':int(row['prepared_count']),'errorCode':None}


def _next_unbuilt_group(conn, *, request_id, account):
    return conn.execute(text(
        "WITH groups AS ("
        " SELECT sp.factory_id,ce.period FROM restart_plan_request_items i"
        " JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account"
        " JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account"
        " WHERE i.request_id=:id AND i.account=:account AND i.data_scope='PRODUCTION'"
        " GROUP BY sp.factory_id,ce.period)"
        " SELECT g.factory_id,g.period FROM groups g WHERE NOT EXISTS ("
        " SELECT 1 FROM restart_batches b WHERE b.plan_request_id=:id AND b.account=:account"
        " AND b.data_scope='PRODUCTION' AND b.factory_id=g.factory_id AND b.period=g.period)"
        " ORDER BY g.factory_id,g.period LIMIT 1"
    ),{'id':request_id,'account':account}).mappings().first()


def create_planning_executor(engine_provider,*,environment=None):
    def execute(job):
        from services.operation_service import create_operation
        from jobs.service import heartbeat_job
        request_id=job.payload.get('requestId')
        try:
            request_id=str(UUID(str(request_id)))
        except (ValueError,TypeError,AttributeError):
            raise JobExecutionError('INVALID_PLAN_REQUEST',retryable=False) from None
        # Persist the first successful preflight separately from the planning
        # work. Continuations can then finish a large manifest after its first
        # scheduled time without reassigning days or failing mid-plan. A request
        # that has not begun by its start time is still rejected safely.
        with engine_provider().begin() as conn:
            req=conn.execute(text('SELECT * FROM restart_plan_requests WHERE id=:id AND account=:account AND data_scope=\'PRODUCTION\' FOR UPDATE'),{'id':request_id,'account':job.account}).mappings().first()
            if req is None:
                raise JobExecutionError('PLAN_REQUEST_NOT_FOUND',retryable=False)
            if req['status'] in {'COMPLETED','FAILED'}:
                return
            if req['processing_started_at'] is None:
                _schedule(start_date=req['start_date'],daily_time=req['daily_time'],timezone_name=req['timezone_name'],
                          total_days=int(req['total_days']))
                conn.execute(text('UPDATE restart_plan_requests SET processing_started_at=CURRENT_TIMESTAMP '
                    'WHERE id=:id AND account=:account AND processing_started_at IS NULL'),
                    {'id':req['id'],'account':job.account})
        with engine_provider().begin() as conn:
            req=conn.execute(text('SELECT * FROM restart_plan_requests WHERE id=:id AND account=:account AND data_scope=\'PRODUCTION\' FOR UPDATE'),{'id':request_id,'account':job.account}).mappings().first()
            if req is None:
                raise JobExecutionError('PLAN_REQUEST_NOT_FOUND',retryable=False)
            if req['status'] in {'COMPLETED','FAILED'}:
                return
            missing_schedule=conn.execute(text('SELECT COUNT(*) FROM restart_plan_request_items '
                'WHERE request_id=:id AND account=:account AND data_scope=\'PRODUCTION\' AND scheduled_at IS NULL'),
                {'id':req['id'],'account':job.account}).scalar_one()
            if missing_schedule:
                legacy=conn.execute(text('SELECT i.candidate_episode_id::text AS candidate_id,i.operation_id::text AS operation_id,'
                    'ce.confirmed_ean,sp.factory_id,ce.period,'+_CATEGORY_SQL+' AS category '
                    'FROM restart_plan_request_items i JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id '
                    'AND ce.account=i.account AND ce.data_scope=i.data_scope JOIN source_products sp ON sp.id=ce.source_product_id '
                    'AND sp.account=ce.account AND sp.data_scope=ce.data_scope LEFT JOIN restart_product_edits edit '
                    'ON edit.candidate_episode_id=ce.id AND edit.account=ce.account AND edit.data_scope=ce.data_scope '
                    'LEFT JOIN restart_operations op ON op.id=i.operation_id AND op.account=i.account AND op.data_scope=i.data_scope '
                    'LEFT JOIN operation_revisions rev ON rev.operation_id=op.id AND rev.account=op.account AND rev.data_scope=op.data_scope '
                    'AND rev.revision_number=op.current_revision_number WHERE i.request_id=:id AND i.account=:account '
                    'AND i.data_scope=\'PRODUCTION\' ORDER BY i.row_number'),
                    {'id':req['id'],'account':job.account}).mappings().all()
                legacy_payload=type('LegacySchedulePayload',(),{
                    'account':job.account,'start_date':req['start_date'],'daily_time':req['daily_time'],
                    'timezone_name':req['timezone_name'],'total_days':int(req['total_days']),
                })()
                legacy_assignments,_=_schedule_assignments(conn,legacy_payload,legacy)
                for candidate_id,assignment in legacy_assignments.items():
                    conn.execute(text('UPDATE restart_plan_request_items SET scheduled_at=:scheduled_at,category_snapshot=:category '
                        'WHERE request_id=:id AND account=:account AND candidate_episode_id=CAST(:candidate_id AS uuid) '
                        'AND scheduled_at IS NULL'),{'scheduled_at':assignment['scheduled_at'],'category':assignment['category'],
                            'id':req['id'],'account':job.account,'candidate_id':candidate_id})
            rows=conn.execute(text('SELECT * FROM restart_plan_request_items WHERE request_id=:id AND account=:account AND row_number>:cursor ORDER BY row_number LIMIT 500'),
                {'id':req['id'],'account':job.account,'cursor':req['prepared_count']}).mappings().all()
            for row in rows:
                if not row['operation_id']:
                    operation=create_operation(conn,account=job.account,candidate_episode_id=str(row['candidate_episode_id']),actor=req['created_by'])
                    conn.execute(text('UPDATE restart_plan_request_items SET operation_id=:op WHERE request_id=:request AND row_number=:row'),{'op':operation['id'],'request':req['id'],'row':row['row_number']})
            cursor=rows[-1]['row_number'] if rows else req['prepared_count']
            heartbeat_job(conn,job_id=job.id,account=job.account,lease_token=job.lease_token or '',lease_seconds=60)
            conn.execute(text("UPDATE restart_plan_requests SET prepared_count=:count,status='BUILDING',last_error_code=NULL WHERE id=:id AND account=:account"),{'count':cursor,'id':req['id'],'account':job.account})
            if cursor < req['item_count']:
                enqueue_job(conn,account=job.account,job_type='drip.plan_create',idempotency_key=f"drip-plan-{req['id']}-{cursor}",payload={'requestId':str(req['id'])})
                return
            # Commit one factory/period cohort per job. If a later cohort fails,
            # earlier batches remain durable and recovery continues from the
            # first group that does not yet have a batch linked to this request.
            group=_next_unbuilt_group(conn,request_id=req['id'],account=job.account)
            if group is None:
                conn.execute(text("UPDATE restart_plan_requests SET status='COMPLETED',last_error_code=NULL WHERE id=:id AND account=:account"),
                    {'id':req['id'],'account':job.account})
                return
            operation_ids=conn.execute(text(
                'SELECT i.operation_id::text FROM restart_plan_request_items i '
                'JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account '
                'JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account '
                'WHERE i.request_id=:id AND i.account=:account AND i.data_scope=\'PRODUCTION\' '
                'AND sp.factory_id=:factory AND ce.period=:period ORDER BY i.row_number'),
                {'id':req['id'],'account':job.account,'factory':group['factory_id'],'period':group['period']}).scalars().all()
            assignment_rows=conn.execute(text(
                'SELECT i.operation_id::text,i.scheduled_at FROM restart_plan_request_items i '
                'JOIN candidate_episodes ce ON ce.id=i.candidate_episode_id AND ce.account=i.account AND ce.data_scope=i.data_scope '
                'JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope '
                'WHERE i.request_id=:id AND i.account=:account AND i.data_scope=\'PRODUCTION\' '
                'AND sp.factory_id=:factory AND ce.period=:period ORDER BY i.row_number'),
                {'id':req['id'],'account':job.account,'factory':group['factory_id'],'period':group['period']}).mappings().all()
            if len(assignment_rows)!=len(operation_ids) or any(row['scheduled_at'] is None for row in assignment_rows):
                raise DripError('PLAN_SCHEDULE_SNAPSHOT_INCOMPLETE','The saved plan is missing per-product schedule assignments.',status=409)
            scheduled_assignments={str(row['operation_id']):row['scheduled_at'] for row in assignment_rows}
            batch=create_batch_draft(conn,account=job.account,name=f"{req['name']} · {group['period']}"[:160],operation_ids=operation_ids,start_date=req['start_date'],daily_time=req['daily_time'],timezone_name=req['timezone_name'],total_days=req['total_days'],actor=req['created_by'])
            conn.execute(text('UPDATE restart_batches SET plan_request_id=:request WHERE id=:id AND account=:account'),{'id':batch['id'],'account':job.account,'request':req['id']})
            start_batch(conn,account=job.account,batch_id=batch['id'],expected_version=batch['version'],actor=req['created_by'],auto_prepare=True,
                scheduled_assignments=scheduled_assignments,
                heartbeat=lambda: heartbeat_job(conn,job_id=job.id,account=job.account,lease_token=job.lease_token or '',lease_seconds=60))
            next_group=_next_unbuilt_group(conn,request_id=req['id'],account=job.account)
            if next_group is None:
                conn.execute(text("UPDATE restart_plan_requests SET status='COMPLETED',last_error_code=NULL WHERE id=:id AND account=:account"),
                    {'id':req['id'],'account':job.account})
                return
            continuation=uuid5(UUID(str(req['id'])),f"{next_group['factory_id']}\0{next_group['period']}").hex
            enqueue_job(conn,account=job.account,job_type='drip.plan_create',
                idempotency_key=f"drip-plan-finalize-{req['id']}-{continuation}",
                payload={'requestId':str(req['id'])},max_attempts=5)
    def guarded(job):
        try:
            execute(job)
        except (DripError, OperationError) as error:
            code=str(getattr(error,'code','PLAN_CREATION_FAILED')).upper()
            code=re.sub(r'[^A-Z0-9_.-]','_',code)[:64] or 'PLAN_CREATION_FAILED'
            raise JobExecutionError(code,retryable=False) from None
        except JobExecutionError:
            raise
        except Exception:
            # Database/lease/unclassified failures are bounded by the queue's
            # retry policy. The manifest cursor and operation IDs were written
            # in the same transaction as each chunk, so replay resumes safely.
            raise JobExecutionError('PLAN_CREATION_TEMPORARY',retryable=True) from None
    return guarded

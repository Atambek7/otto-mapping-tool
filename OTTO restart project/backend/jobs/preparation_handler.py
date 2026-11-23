"""Durable per-item preparation; external reads happen outside DB transactions."""
from datetime import datetime, timezone
import os
import threading
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from jobs.service import JobExecutionError, heartbeat_job
from runtime_mode import RuntimeConfig, RuntimeMode
from services.preparation_orchestrator import read_preparation_sources
from services.preparation_service import load_preparation_context, prepare_operation_revision
from services.product_edit_service import get_product_edit, save_product_edit
from services.qc_service import record_operation_qc


def _preparation_error_code(error):
    # SQLAlchemy's .code (e.g. gkpj) is a documentation code, not a domain error.
    if isinstance(error, SQLAlchemyError):
        return 'PREPARATION_DATABASE_ERROR'
    code = str(getattr(error, 'code', 'PREPARATION_FAILED'))
    return code if re.fullmatch(r'[A-Z0-9_.-]{1,64}', code) else 'PREPARATION_FAILED'


def _item(conn, job, lock=False):
    return conn.execute(text(
        "SELECT i.*,o.version AS operation_version,o.status AS operation_status,o.candidate_episode_id,o.submission_state,"
        "b.status AS batch_status,b.created_by,b.schedule_generation FROM restart_items i "
        "JOIN restart_batches b ON b.id=i.batch_id AND b.account=i.account AND b.data_scope=i.data_scope "
        "JOIN restart_operations o ON o.id=i.operation_id AND o.account=i.account AND o.data_scope=i.data_scope "
        "WHERE i.id=:id AND i.account=:account AND i.data_scope='PRODUCTION'" + (' FOR UPDATE OF i,b,o' if lock else '')
    ), {'id': job.payload.get('itemId'), 'account': job.account}).mappings().first()


def _pending(row, job):
    return row and row['status']=='PREPARING' and row['submission_state']=='NEVER_SUBMITTED' and int(row['preparation_generation'])==job.payload.get('generation')


def _title_proposal(title, config, env):
    if len(title) <= 70:
        return title
    if not config.openai_enabled:
        raise JobExecutionError('OPENAI_DISABLED', retryable=False)
    from integrations.openai_client import OpenAIResponsesClient, OpenAISettings
    from services.ai_service import AISuggestionService, SuggestionContext
    provider = OpenAIResponsesClient(OpenAISettings.from_environment(env), enabled=True)
    return AISuggestionService(provider, enabled=True).suggest(SuggestionContext(
        source_title=title, verified_facts=(), taxonomy=(), attribute_candidates=())).title


def create_preparation_executor(engine_provider, *, environment=None, source_reader=None, title_provider=None):
    env = dict(os.environ if environment is None else environment)
    reader = source_reader or read_preparation_sources
    propose_title = title_provider or _title_proposal

    def execute_inner(job):
        if job.job_type!='drip.prepare_item' or not isinstance(job.payload.get('itemId'), int):
            raise JobExecutionError('INVALID_PREPARATION_JOB', retryable=False)
        config = RuntimeConfig.from_environment(env)
        if config.mode not in {RuntimeMode.PRODUCTION, RuntimeMode.TEST}:
            raise JobExecutionError('PREPARATION_MODE_DISABLED', retryable=False)
        engine = engine_provider()
        with engine.begin() as conn:
            row = _item(conn, job, lock=True)
            if not _pending(row, job) or row['batch_status'] in {'CANCELLED','COMPLETED'}:
                return
            if row['batch_status']=='PAUSED':
                raise JobExecutionError('BATCH_PAUSED', retryable=True)
            if row['operation_status'] in {'READY','QC_PENDING'}:
                conn.execute(text("UPDATE restart_operations SET status='PREPARING',version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND account=:account AND submission_state='NEVER_SUBMITTED'"),{'id':row['operation_id'],'account':job.account})
                row = _item(conn, job, lock=True)
            version = int(row['operation_version'])
            context = load_preparation_context(conn, account=job.account, operation_id=str(row['operation_id']), expected_version=version)
            edit = get_product_edit(conn, account=job.account, candidate_id=str(row['candidate_episode_id']))['edit']
        stop = threading.Event()
        lease_failed = threading.Event()
        def heartbeat():
            while not stop.wait(15):
                try:
                    with engine.begin() as conn:
                        heartbeat_job(conn, job_id=job.id, account=job.account, lease_token=job.lease_token or '', lease_seconds=60)
                except Exception:
                    lease_failed.set()
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            sources = reader(context, account=job.account, config=config)
            excluded = {name.casefold() for name in row['excluded_attribute_names_json']}
            # Work on provider snapshots only; retain original source rows and history.
            from copy import deepcopy
            sources = deepcopy(sources)
            product_snapshot=sources['otto_product']
            for container in (product_snapshot, product_snapshot.get('productDescription') or {}, sources.get('aftercool') or {}):
                attrs=container.get('attributes')
                if isinstance(attrs,list):
                    container['attributes']=[a for a in attrs if not isinstance(a,dict) or str(a.get('name','')).casefold() not in excluded]
                elif isinstance(attrs,dict):
                    container['attributes']={k:v for k,v in attrs.items() if k.casefold() not in excluded}
            description = sources['otto_product'].get('productDescription') or {}
            title = str(edit['title'] if edit else context.get('original_title') or description.get('productLine') or '')
            category = str(edit['category'] if edit else description.get('category') or context.get('category') or '')
            proposed_title = propose_title(title, config, env)
            with engine.begin() as conn:
                current = _item(conn, job, lock=True)
                if not _pending(current, job) or current['batch_status']=='CANCELLED':
                    return
                if lease_failed.is_set() or current['batch_status']=='PAUSED':
                    raise JobExecutionError('PREPARATION_INTERRUPTED', retryable=True)
                heartbeat_job(conn, job_id=job.id, account=job.account, lease_token=job.lease_token or '', lease_seconds=60)
                if int(current['operation_version'])!=version:
                    raise JobExecutionError('VERSION_CONFLICT', retryable=False)
                actor = str(current['created_by'])
                # Keep the operator's complete editor map. Category and explicit
                # rejection rules are applied by preparation_service when it
                # builds the immutable outgoing package, not by rewriting edits.
                attributes = dict(edit['attributes']) if edit else {}
                if edit and proposed_title != title:
                    save_product_edit(conn, account=job.account, candidate_id=str(current['candidate_episode_id']),
                        expected_version=int(edit['version']), title=proposed_title, category=category,
                        attributes=attributes, actor=actor, preparation_item_id=int(current['id']))
                elif not edit and proposed_title != title:
                    from services.product_catalog_service import get_product
                    product = get_product(conn, account=job.account, candidate_episode_id=str(current['candidate_episode_id']))
                    from services.preparation_service import _attribute_values
                    original = _attribute_values({'attributes': product.attributes})
                    save_product_edit(conn, account=job.account, candidate_id=str(current['candidate_episode_id']), expected_version=0,
                        title=proposed_title, category=category, attributes=original, actor=actor,
                        preparation_item_id=int(current['id']))
                current = _item(conn, job, lock=True)
                context = load_preparation_context(conn, account=job.account, operation_id=str(current['operation_id']), expected_version=int(current['operation_version']))
                result = prepare_operation_revision(conn, account=job.account, operation_id=str(current['operation_id']),
                    expected_version=int(current['operation_version']), preflight_context=context, **sources,
                    captured_at=datetime.now(timezone.utc), actor=actor, approved_evidence_types=config.gtin_approved_evidence_types)
                current = _item(conn, job, lock=True)
                qc = record_operation_qc(conn, account=job.account, operation_id=str(current['operation_id']),
                    expected_version=int(current['operation_version']), actor=actor, approved_evidence_types=config.gtin_approved_evidence_types)
                snapshot = conn.execute(text('SELECT id,payload_hash FROM operation_revisions WHERE operation_id=:op AND account=:account AND revision_number=:revision'),
                    {'op':current['operation_id'],'account':job.account,'revision':result['revisionNumber']}).mappings().one()
                conn.execute(text('UPDATE restart_items SET revision_id=:revision,qc_result_id=:qc,payload_hash=:hash WHERE id=:id AND account=:account'),
                    {'revision':snapshot['id'],'qc':qc['id'],'hash':snapshot['payload_hash'],'id':current['id'],'account':job.account})
                if qc['outcome']!='PASSED_AUTO':
                    codes = [i for i in qc.get('issues', []) if isinstance(i,dict)]
                    _failure(conn, job, codes or ['QC_BLOCKED'])
                    return
                snapshot = conn.execute(text('SELECT id,payload_hash FROM operation_revisions WHERE operation_id=:op AND account=:account AND revision_number=:revision'),
                    {'op':current['operation_id'],'account':job.account,'revision':result['revisionNumber']}).mappings().one()
                item = conn.execute(text("UPDATE restart_items SET revision_id=:revision,qc_result_id=:qc,payload_hash=:hash,status='SCHEDULED',"
                    "preparation_errors_json='[]'::jsonb,last_error_code=NULL,version=version+1 WHERE id=:id AND account=:account RETURNING *"),
                    {'revision':snapshot['id'],'qc':qc['id'],'hash':snapshot['payload_hash'],'id':current['id'],'account':job.account}).mappings().one()
                from services.drip_service import _enqueue_release
                _enqueue_release(conn, batch={'id':item['batch_id'],'account':job.account,'schedule_generation':current['schedule_generation']}, item=item, now=datetime.now(timezone.utc))
        except Exception as error:
            if isinstance(error, JobExecutionError) and error.retryable:
                raise
            if getattr(error, 'retryable', False):
                delay = getattr(error, 'retry_after_seconds', None)
                delay = min(86400, max(1, int(delay))) if delay is not None else 60
                raise JobExecutionError(getattr(error, 'code', 'SOURCE_TEMPORARILY_UNAVAILABLE'),
                    retryable=True, retry_delay_seconds=delay) from None
            with engine.begin() as conn:
                current = _item(conn, job, lock=True)
                if _pending(current, job):
                    _failure(conn, job, [_preparation_error_code(error)])
        finally:
            stop.set()
            thread.join(timeout=2)
    def execute(job):
        try:
            execute_inner(job)
        except JobExecutionError as error:
            if error.retryable:
                raise
            with engine_provider().begin() as conn:
                if _pending(_item(conn, job, lock=True), job):
                    _failure(conn, job, [error.code])
        except Exception as error:
            with engine_provider().begin() as conn:
                if _pending(_item(conn, job, lock=True), job):
                    _failure(conn, job, [_preparation_error_code(error)])
    return execute


def _failure(conn, job, codes):
    import json
    from services.drip_service import _event
    conn.execute(text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code=:code,preparation_errors_json=CAST(:errors AS jsonb),version=version+1 "
        "WHERE id=:id AND account=:account AND status='PREPARING'"), {'code':codes[0].get('code','QC_BLOCKED') if isinstance(codes[0],dict) else codes[0], 'errors':json.dumps(codes), 'id':job.payload['itemId'],'account':job.account})
    _event(conn, account=job.account, actor='system:drip-prepare', event_type='DRIP_PREPARATION_FAILED', entity_type='RESTART_ITEM',
        entity_id=str(job.payload['itemId']), summary='Automatic preparation requires correction', metadata={'codes':codes})

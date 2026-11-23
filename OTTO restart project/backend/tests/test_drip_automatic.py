"""Real PostgreSQL queue transitions with all providers replaced by test doubles."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from zoneinfo import ZoneInfo
from sqlalchemy import text
from tests.test_drip_service import DripServicePostgresTests as Fixture
from services.drip_service import create_batch_draft, start_batch, list_batches, list_batch_items, list_batch_manifest, control_batch
from services.drip_distribution import distribute
from jobs.preparation_handler import create_preparation_executor
from services.preparation_service import PreparationError
from services.availability_service import (
    AvailabilityError,
    claim_availability_dispatch,
    enqueue_availability_jobs,
    record_availability_result,
    resolve_unknown_availability_step,
)
from services.product_edit_service import get_product_edit, save_product_edit
from services.qc_service import QC_POLICY_VERSION


class AutomaticPreparationTests(unittest.TestCase):
    setUpClass = classmethod(Fixture.setUpClass.__func__)
    tearDownClass = classmethod(Fixture.tearDownClass.__func__)
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    ean = staticmethod(Fixture.ean)
    make_ready_operation = Fixture.make_ready_operation
    schedule_input = Fixture.schedule_input
    create_and_start = Fixture.create_and_start
    prepare_submission_fixture = Fixture.prepare_submission_fixture
    confirm_processing = Fixture.confirm_processing

    @staticmethod
    def create_request_with_preview(conn, payload, actor="test"):
        from services.drip_planning import create_request, preview

        preview_result = preview(conn, payload)
        request_payload = payload.model_copy(update={"preview_hash": preview_result["selectionHash"]})
        return create_request(conn, request_payload, actor), preview_result

    def test_small_schedule_is_created_before_response_without_planning_worker(self):
        from schemas.drip import DripPlanRequestInput
        from services.drip_planning import create_request, preview, get_request
        operation = self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                {'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}', 'id':operation['sourceId']})
        payload = DripPlanRequestInput(account='XL',name='Immediate schedule',candidateIds=[operation['episodeId']],
            startDate=(datetime.now(ZoneInfo('Europe/Berlin')).date()+timedelta(days=2)).isoformat(),
            dailyTime='08:00:00',totalDays=3)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                calculated=preview(conn,payload)
                result=create_request(conn,payload.model_copy(update={'preview_hash':calculated['selectionHash']}),'test',materialize=True)
        # A second schedule for the same active operation must roll back all
        # request/manifest writes rather than leave another saved draft.
        from sqlalchemy.exc import IntegrityError
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.assertRaises(IntegrityError):
                with self.engine.begin() as conn:
                    calculated=preview(conn,payload)
                    create_request(conn,payload.model_copy(update={'preview_hash':calculated['selectionHash']}),'test',materialize=True)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT count(*) FROM restart_plan_requests')).scalar_one(),1)
        self.assertEqual(result['status'],'COMPLETED')
        self.assertIsNone(result['jobId'])
        self.assertEqual(len(result['batchIds']),1)
        with self.engine.connect() as conn:
            details=get_request(conn,'XL',result['id'])
            self.assertEqual(details['preparedCount'],1)
            self.assertEqual(details['batchCount'],1)
            self.assertEqual(details['items'][0]['queueStatus'],'PREPARING')
            batch=conn.execute(text('SELECT status FROM restart_batches WHERE id=:id'),{'id':result['batchIds'][0]}).scalar_one()
            self.assertEqual(batch,'SCHEDULED')
            self.assertEqual(conn.execute(text("SELECT count(*) FROM background_jobs WHERE job_type='drip.plan_create'")).scalar_one(),0)
            item=conn.execute(text('SELECT scheduled_at FROM restart_items WHERE batch_id=:id'),{'id':result['batchIds'][0]}).scalar_one()
            manifest=conn.execute(text('SELECT scheduled_at FROM restart_plan_request_items WHERE request_id=:id'),{'id':result['id']}).scalar_one()
            self.assertEqual(item,manifest)
            self.assertEqual(item.isoformat(),calculated['firstRunAt'])
            from services.drip_trigger_service import request_immediate_day
        with self.engine.begin() as conn:
            request_immediate_day(conn,account='XL',batch_id=result['batchIds'][0],expected_version=2,actor='test')
            page=list_batch_items(conn,account='XL',batch_id=result['batchIds'][0],day=1)
            self.assertEqual(page['page']['totalItems'],1)
            self.assertEqual(page['items'][0]['scheduledDay'],1)
            self.assertEqual(list_batch_items(conn,account='XL',batch_id=result['batchIds'][0],day=2)['page']['totalItems'],0)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM background_jobs WHERE job_type='drip.prepare_batch'")).scalar_one(),1)

    def test_immediate_day_persists_through_preparation_and_keeps_later_days(self):
        from services.drip_trigger_service import request_immediate_day
        operation,batch,item=self.start_auto()
        now=datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            later=conn.execute(text("UPDATE restart_items SET scheduled_at=scheduled_at+interval '1 day' WHERE id=:id RETURNING scheduled_at"),{'id':item['id']}).scalar_one()
            # This fixture has no manifest: before the first day it must not
            # expedite a later portion accidentally.
            result=request_immediate_day(conn,account='XL',batch_id=batch['id'],expected_version=batch['version'],actor='test',now=now)
            self.assertEqual(result['status'],'NO_ELIGIBLE_ITEMS')
            conn.execute(text('UPDATE restart_items SET scheduled_at=:date WHERE id=:id'),{'date':item['scheduled_at'],'id':item['id']})
            result=request_immediate_day(conn,account='XL',batch_id=batch['id'],expected_version=batch['version'],actor='test',now=now)
            self.assertEqual(result['status'],'REQUESTED')
            self.assertEqual(result['preparingCount'],1)
            repeat=request_immediate_day(conn,account='XL',batch_id=batch['id'],expected_version=batch['version'],actor='test',now=now)
            self.assertEqual(repeat['status'],'ALREADY_REQUESTED')
            self.assertEqual(conn.execute(text('SELECT scheduled_at FROM restart_items WHERE id=:id'),{'id':item['id']}).scalar_one(),now)
            self.assertEqual(conn.execute(text("SELECT count(*) FROM background_jobs WHERE job_type='otto.submit_product'")).scalar_one(),0)

    def test_immediate_day_accelerates_already_scheduled_item_and_release_job(self):
        from sqlalchemy.exc import DBAPIError
        from services.drip_trigger_service import request_immediate_day
        operation = self.make_ready_operation()
        _, batch = self.create_and_start([operation])
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            item = conn.execute(text('SELECT * FROM restart_items WHERE batch_id=:id'), {'id': batch['id']}).mappings().one()
            self.assertEqual(item['status'], 'SCHEDULED')
            self.assertGreater(item['scheduled_at'], now)
            # Snapshot columns and delaying scheduled_at into the future remain forbidden on SCHEDULED items.
            with self.assertRaises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(text("UPDATE restart_items SET scheduled_at=scheduled_at + interval '1 hour' WHERE id=:id"), {'id': item['id']})
            with self.assertRaises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(text("UPDATE restart_items SET payload_hash=:h WHERE id=:id"), {'h': '0' * 64, 'id': item['id']})
            result = request_immediate_day(conn, account='XL', batch_id=batch['id'], expected_version=batch['version'], actor='test', now=now)
            self.assertEqual(result['status'], 'REQUESTED')
            self.assertEqual(result['selectedCount'], 1)
            self.assertEqual(result['preparingCount'], 0)
            self.assertEqual(result['readyCount'], 1)
            self.assertEqual(conn.execute(text('SELECT scheduled_at FROM restart_items WHERE id=:id'), {'id': item['id']}).scalar_one(), now)
            self.assertEqual(
                conn.execute(text("SELECT available_at FROM background_jobs WHERE job_type='drip.release_item' AND payload_json->>'itemId'=:id"), {'id': str(item['id'])}).scalar_one(),
                now,
            )

    def test_retryable_source_error_preserves_preparation_for_queue_retry(self):
        from integrations.otto_client import OttoApiError
        from jobs.service import JobExecutionError
        _,_,item=self.start_auto()
        def reader(*args,**kwargs):
            raise OttoApiError('HTTP_429',retryable=True,retry_after_seconds=75)
        executor=create_preparation_executor(lambda:self.engine,environment={'OTTORESTART_MODE':'TEST'},source_reader=reader)
        with self.assertRaises(JobExecutionError) as caught:
            executor(self.job(item))
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.retry_delay_seconds,75)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT status FROM restart_items WHERE id=:id'),{'id':item['id']}).scalar_one(),'PREPARING')

    def test_trigger_http_response_contract_accepts_immediate_and_legacy_results(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from schemas.drip import DripTriggerNowResponse
        application=FastAPI()
        response={'batchId':'test','jobId':None,'status':'REQUESTED','dueItemCount':1,
                  'cutoffAt':datetime.now(timezone.utc).isoformat(),
                  'selectedCount':1,'preparingCount':1,'readyCount':0}
        @application.post('/trigger',response_model=DripTriggerNowResponse)
        def trigger():
            return response
        with TestClient(application) as client:
            for state in ('REQUESTED','ALREADY_REQUESTED','NO_ELIGIBLE_ITEMS'):
                response['status']=state
                result=client.post('/trigger')
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['status'],state)
            for key in ('selectedCount','preparingCount','readyCount'):
                response.pop(key)
            response['status']='NO_DUE_ITEMS'
            self.assertEqual(client.post('/trigger').status_code,200)

    def test_failed_preparation_can_be_corrected_and_saved_without_unlocking_scheduled_items(self):
        from services.operation_service import OperationError
        operation,batch,item=self.start_auto()
        with self.engine.begin() as conn:
            with self.assertRaises(OperationError) as error:
                # A worker still preparing this row must keep exclusive edits.
                with conn.begin_nested():
                    save_product_edit(conn,account='XL',candidate_id=operation['episodeId'],expected_version=0,
                        title='Corrected title',category='fixture',attributes={'Anzahl Sitzflächen':'3'},actor='test')
            self.assertEqual(error.exception.code,'OPERATION_QUEUED')
            conn.execute(text("UPDATE restart_items SET status='NEEDS_REVIEW' WHERE id=:id"),{'id':item['id']})
            conn.execute(text("UPDATE restart_operations SET status='NEEDS_REVIEW' WHERE id=:id"),{'id':operation['operationId']})
            saved=save_product_edit(conn,account='XL',candidate_id=operation['episodeId'],expected_version=0,
                title='Corrected title',category='fixture',attributes={'Anzahl Sitzflächen':'3'},actor='test')
        with self.engine.connect() as conn:
            reopened=get_product_edit(conn,account='XL',candidate_id=operation['episodeId'])
            self.assertEqual(reopened['edit']['attributes']['Anzahl Sitzflächen'],'3')
            self.assertEqual(reopened['edit']['version'],saved['edit']['version'])
            self.assertEqual(conn.execute(text('SELECT status FROM restart_items WHERE id=:id'),{'id':item['id']}).scalar_one(),'NEEDS_REVIEW')

    def start_auto(self):
        operation=self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET source_type='OTTO_DIRECT' WHERE id=:id"), {'id':operation['sourceId']})
        with self.engine.begin() as conn:
            draft=create_batch_draft(conn,account='XL',name='Automatic test',actor='test',**self.schedule_input([operation]))
            batch=start_batch(conn,account='XL',batch_id=draft['id'],expected_version=draft['version'],actor='test',auto_prepare=True)
            item=conn.execute(text('SELECT * FROM restart_items WHERE batch_id=:id'),{'id':batch['id']}).mappings().one()
        return operation,batch,item

    def job(self,item):
        return SimpleNamespace(account='XL',job_type='drip.prepare_item',id='unused',lease_token='test',payload={'itemId':item['id'],'generation':item['preparation_generation']})

    def test_unchecked_item_is_visible_and_failure_can_be_retried_immediately(self):
        operation,batch,item=self.start_auto()
        with self.engine.connect() as conn:
            page=list_batch_items(conn,account='XL',batch_id=batch['id'])
            from schemas.drip import DripBatchItemsResponse
            serialized=DripBatchItemsResponse.model_validate(page).model_dump(by_alias=True)
        self.assertEqual(serialized['items'][0]['title'],'Offline Drip source')
        self.assertEqual(serialized['items'][0]['stockStatus'],'NOT_STARTED')
        self.assertEqual(page['page']['totalItems'],1)
        self.assertEqual(page['items'][0]['status'],'PREPARING')
        self.assertIsNone(page['items'][0]['payloadHash'])
        self.assertEqual(page['items'][0]['sourceType'],'OTTO_DIRECT')
        self.assertEqual(page['items'][0]['title'],'Offline Drip source')
        self.assertEqual(page['items'][0]['onlineStatus'],None)
        self.assertEqual(page['items'][0]['stockStatus'],'NOT_STARTED')
        reader=lambda *a,**k: (_ for _ in ()).throw(PreparationError('TEST_SOURCE_FAILURE','unavailable'))
        executor=create_preparation_executor(lambda:self.engine,environment={'OTTORESTART_MODE':'TEST'},source_reader=reader)
        executor(self.job(item))
        with self.engine.begin() as conn:
            row=conn.execute(text('SELECT * FROM restart_items WHERE id=:id'),{'id':item['id']}).mappings().one()
            self.assertEqual(row['status'],'NEEDS_REVIEW')
            self.assertEqual(row['last_error_code'],'TEST_SOURCE_FAILURE')
            control_batch(conn,account='XL',batch_id=batch['id'],expected_version=batch['version'],action='retry-errors',actor='test')
            retried=conn.execute(text('SELECT * FROM restart_items WHERE id=:id'),{'id':item['id']}).mappings().one()
            self.assertEqual(retried['status'],'PREPARING')
            self.assertEqual(retried['preparation_generation'],2)
            self.assertLessEqual(retried['scheduled_at'],datetime.now(timezone.utc))
        executor(self.job(item))  # stale generation must be a no-op
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT status FROM restart_items WHERE id=:id'),{'id':item['id']}).scalar_one(),'PREPARING')

    def test_success_binds_checked_snapshot_and_redelivery_does_not_prepare_again(self):
        operation,batch,item=self.start_auto()
        sources={'otto_product':{},'otto_prices':{},'fulfillment':{},'aftercool':None}
        with patch('jobs.preparation_handler.prepare_operation_revision',return_value={'revisionNumber':1}) as prepare, patch('jobs.preparation_handler.record_operation_qc',return_value={'outcome':'PASSED_AUTO','id':operation['qcId']}) as qc, patch('jobs.preparation_handler.heartbeat_job'):
            executor=create_preparation_executor(lambda:self.engine,environment={'OTTORESTART_MODE':'TEST'},source_reader=lambda *a,**k:sources)
            executor(self.job(item))
            executor(self.job(item))
            with self.engine.connect() as conn:
                error = conn.execute(text("SELECT last_error_code FROM restart_items WHERE id=:id"), {"id":item["id"]}).scalar_one()
            self.assertIsNone(error)
            self.assertEqual(prepare.call_count,1)
            self.assertEqual(qc.call_count,1)
        with self.engine.connect() as conn:
            row=conn.execute(text('SELECT * FROM restart_items WHERE id=:id'),{'id':item['id']}).mappings().one()
            self.assertEqual(row['status'],'SCHEDULED')
            self.assertEqual(str(row['qc_result_id']),operation['qcId'])
            self.assertEqual(row['payload_hash'].strip(),operation['payloadHash'])

    def test_pause_resume_requeues_preparation_and_cancel_fences_old_jobs(self):
        _, batch, item = self.start_auto()
        with self.engine.begin() as conn:
            paused = control_batch(conn, account='XL', batch_id=batch['id'], expected_version=batch['version'], action='pause', actor='test')
        with self.engine.connect() as conn:
            preparing = conn.execute(text('SELECT status,preparation_generation FROM restart_items WHERE id=:id'), {'id':item['id']}).mappings().one()
            self.assertEqual((preparing['status'],preparing['preparation_generation']),('PREPARING',2))
            self.assertEqual(conn.execute(text("SELECT state FROM background_jobs WHERE job_type='drip.prepare_batch' AND payload_json->>'batchId'=:id ORDER BY created_at LIMIT 1"),{'id':batch['id']}).scalar_one(),'CANCELLED')
        with self.engine.begin() as conn:
            resumed = control_batch(conn, account='XL', batch_id=batch['id'], expected_version=paused['version'], action='resume', actor='test')
            prep_batch = conn.execute(text("SELECT id::text,payload_json FROM background_jobs WHERE job_type='drip.prepare_batch' AND payload_json->>'batchId'=:id AND state='PENDING' ORDER BY created_at DESC LIMIT 1"),{'id':batch['id']}).mappings().one()
        scheduler_job=SimpleNamespace(id=prep_batch['id'],account='XL',job_type='drip.prepare_batch',lease_token='test',payload=dict(prep_batch['payload_json']))
        with patch('jobs.service.heartbeat_job'):
            from services.drip_preparation import create_preparation_batch_executor
            create_preparation_batch_executor(lambda:self.engine)(scheduler_job)
        with self.engine.connect() as conn:
            item_job=conn.execute(text("SELECT id::text,payload_json,state FROM background_jobs WHERE job_type='drip.prepare_item' AND payload_json->>'itemId'=:id ORDER BY created_at DESC LIMIT 1"),{'id':str(item['id'])}).mappings().one()
            self.assertEqual(item_job['state'],'PENDING')
            self.assertEqual(item_job['payload_json']['generation'],2)
        with self.engine.begin() as conn:
            cancelled=control_batch(conn,account='XL',batch_id=batch['id'],expected_version=resumed['version'],action='cancel',actor='test')
        self.assertEqual(cancelled['status'],'CANCELLED')
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT status FROM restart_items WHERE id=:id'),{'id':item['id']}).scalar_one(),'CANCELLED')
            self.assertEqual(conn.execute(text('SELECT state FROM background_jobs WHERE id=:id'),{'id':item_job['id']}).scalar_one(),'CANCELLED')
        # An already-running pre-pause worker is fenced by the generation bump.
        old_job=self.job(item)
        executor=create_preparation_executor(lambda:self.engine,environment={'OTTORESTART_MODE':'TEST'},source_reader=lambda *a,**k: {})
        executor(old_job)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT status FROM restart_items WHERE id=:id'),{'id':item['id']}).scalar_one(),'CANCELLED')

    def test_retry_errors_and_release_retry_keep_pause_until_resume_restores_jobs(self):
        operations=[self.make_ready_operation() for _ in range(3)]
        with self.engine.begin() as conn:
            draft=create_batch_draft(conn,account='XL',name='Paused mixed retry',actor='test',**self.schedule_input(operations))
            batch=start_batch(conn,account='XL',batch_id=draft['id'],expected_version=draft['version'],actor='test',auto_prepare=True)
            rows=conn.execute(text('SELECT * FROM restart_items WHERE batch_id=:id ORDER BY row_number'),{'id':batch['id']}).mappings().all()
            by_operation={str(row['operation_id']):row for row in rows}
            failed=by_operation[operations[0]['operationId']]
            scheduled=by_operation[operations[2]['operationId']]
            conn.execute(text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code='FIXTURE_ERROR' WHERE id=:id"),{'id':failed['id']})
            conn.execute(text("UPDATE restart_items SET status='SCHEDULED',revision_id=:revision,qc_result_id=:qc,payload_hash=:hash WHERE id=:id"),
                {'revision':operations[2]['revisionId'],'qc':operations[2]['qcId'],'hash':operations[2]['payloadHash'],'id':scheduled['id']})
        with self.engine.begin() as conn:
            paused=control_batch(conn,account='XL',batch_id=batch['id'],expected_version=batch['version'],action='pause',actor='test')
        with self.engine.begin() as conn:
            retried=control_batch(conn,account='XL',batch_id=batch['id'],expected_version=paused['version'],action='retry-errors',actor='test')
            self.assertEqual(retried['status'],'PAUSED')
            self.assertEqual(conn.execute(text('SELECT status FROM restart_batches WHERE id=:id'),{'id':batch['id']}).scalar_one(),'PAUSED')
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM background_jobs WHERE account='XL' AND state IN ('PENDING','RETRY_WAIT') AND ((job_type='drip.prepare_item' AND payload_json->>'itemId'=CAST(:item AS text)) OR (job_type='drip.prepare_batch' AND payload_json->>'batchId'=:batch))"),{'item':failed['id'],'batch':batch['id']}).scalar_one(),0)
            release_retry=control_batch(conn,account='XL',batch_id=batch['id'],expected_version=retried['version'],action='retry',actor='test')
            self.assertEqual(release_retry['status'],'PAUSED')
        with self.engine.begin() as conn:
            resumed=control_batch(conn,account='XL',batch_id=batch['id'],expected_version=release_retry['version'],action='resume',actor='test')
            prepare_job=conn.execute(text("SELECT id::text,payload_json FROM background_jobs WHERE account='XL' AND job_type='drip.prepare_batch' AND payload_json->>'batchId'=:id AND state='PENDING' ORDER BY created_at DESC LIMIT 1"),{'id':batch['id']}).mappings().one()
            release_job=conn.execute(text("SELECT state FROM background_jobs WHERE account='XL' AND job_type='drip.release_item' AND payload_json->>'itemId'=:id AND state='PENDING' ORDER BY created_at DESC LIMIT 1"),{'id':str(scheduled['id'])}).scalar_one()
        self.assertEqual(release_job,'PENDING')
        with patch('jobs.service.heartbeat_job'):
            from services.drip_preparation import create_preparation_batch_executor
            create_preparation_batch_executor(lambda:self.engine)(SimpleNamespace(id=prepare_job['id'],account='XL',job_type='drip.prepare_batch',lease_token='test',payload=dict(prepare_job['payload_json'])))
        with self.engine.connect() as conn:
            pending_ids={row[0] for row in conn.execute(text("SELECT payload_json->>'itemId' FROM background_jobs WHERE account='XL' AND job_type='drip.prepare_item' AND state='PENDING'"))}
            self.assertEqual(pending_ids,{str(failed['id']),str(by_operation[operations[1]['operationId']]['id'])})
            self.assertEqual(conn.execute(text('SELECT status FROM restart_batches WHERE id=:id'),{'id':batch['id']}).scalar_one(),'SCHEDULED')

    def test_paused_retry_without_errors_does_not_activate_plan(self):
        _, batch, _ = self.start_auto()
        with self.engine.begin() as conn:
            paused = control_batch(conn, account='XL', batch_id=batch['id'], expected_version=batch['version'], action='pause', actor='test')
            retried = control_batch(conn, account='XL', batch_id=batch['id'], expected_version=paused['version'], action='retry-errors', actor='test')
            self.assertEqual(retried['status'], 'PAUSED')
            self.assertEqual(conn.execute(text('SELECT status FROM restart_batches WHERE id=:id'), {'id': batch['id']}).scalar_one(), 'PAUSED')

    def test_planning_snapshot_is_account_scoped_and_resumes_idempotently(self):
        from schemas.drip import DripPlanRequestInput
        from services.drip_planning import create_request,get_request,list_requests,create_planning_executor
        from jobs.service import claim_job_by_id
        operation=self.make_ready_operation()
        other_operation=self.make_ready_operation()
        self.make_ready_operation(account='JV')
        with self.engine.begin() as conn:
            for candidate in (operation, other_operation):
                conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                    {'id':candidate['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
            conn.execute(text("UPDATE source_products SET original_title='Distinct offline product' WHERE id=:id"),
                {'id':other_operation['sourceId']})
        payload=DripPlanRequestInput(account='XL',name='Fixed selection',candidateIds=[operation['episodeId'],other_operation['episodeId']],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                job=claim_job_by_id(conn,job_id=request['jobId'],worker_id='test')
        self.assertEqual(request['itemCount'],2)
        with self.engine.connect() as conn:
            pending=get_request(conn,'XL',request['id'])
            self.assertEqual(pending['status'],'PENDING')
            self.assertEqual(pending['batchCount'],0)
            self.assertEqual({item['title'] for item in pending['items']},{'Offline Drip source','Distinct offline product'})
            self.assertEqual(pending['items'][0]['queueStatus'],None)
        executor=create_planning_executor(lambda:self.engine)
        executor(job)
        executor(job)
        with self.engine.connect() as conn:
            result=get_request(conn,'XL',request['id'])
            self.assertEqual(result['status'],'COMPLETED',result)
            self.assertEqual(result['preparedCount'],2)
            self.assertEqual(len(result['batches']),1)
            self.assertEqual(result['batchCount'],1)
            self.assertEqual(result['factoryIds'],['factory-drip'])
            self.assertEqual({item['title'] for item in result['items']},{'Offline Drip source','Distinct offline product'})
            self.assertEqual(result['items'][0]['sourceType'],'AFTERCOOL')
            scoped=get_request(conn,'XL',request['id'],query=other_operation['targetSku'],batch_id=result['batches'][0]['id'])
            self.assertEqual(scoped['itemCount'],2)
            self.assertEqual(scoped['page']['totalItems'],1)
            self.assertEqual(scoped['items'][0]['targetSku'],other_operation['targetSku'])
            no_match=get_request(conn,'XL',request['id'],query='does-not-match')
            self.assertEqual(no_match['itemCount'],2)
            self.assertEqual(no_match['page']['totalItems'],0)
            listed=list_requests(conn,account='XL',factory_id='factory-drip',source_type='AFTERBUY',query=other_operation['targetSku'])
            self.assertEqual(listed['page']['totalItems'],1)
            self.assertEqual(listed['items'][0]['id'],request['id'])
            batches=list_batches(conn,account='XL',factory_id='factory-drip',source_type='AFTERBUY',
                plan_request_id=request['id'],query='Distinct offline product')
            self.assertEqual(batches['page']['totalItems'],1)
            filtered_items=list_batch_items(conn,account='XL',batch_id=result['batches'][0]['id'],
                stage='PREPARING',day=1)
            self.assertEqual(filtered_items['page']['totalItems'],1)
            self.assertEqual(filtered_items['items'][0]['scheduledDay'],1)
            self.assertEqual(list_requests(conn,account='JV')['page']['totalItems'],0)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items WHERE account='XL'")).scalar_one(),2)
            from services.drip_service import DripError
            with self.assertRaises(DripError): get_request(conn,'JV',request['id'])

    def test_plan_preview_categories_are_persisted_and_used_by_queue(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import run_job_by_id
        from services.drip_planning import create_planning_executor, get_request

        operations = [self.make_ready_operation() for _ in range(3)]
        categories = ["fixture-sofa", "fixture-chair", "fixture-table"]
        with self.engine.begin() as conn:
            for index, operation in enumerate(operations):
                attributes = (
                    '{"attributes":[{"name":"Marke","value":"Brand"}]}'
                    if index != 1 else '{"attributes":[{"name":"Farbe","value":"Rot"}]}'
                )
                conn.execute(text("UPDATE source_products SET category=:category,attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                    {"category": categories[index], "attrs": attributes, "id": operation["sourceId"]})

        start_date = (datetime.now(ZoneInfo("Europe/Berlin")).date() + timedelta(days=2)).isoformat()
        payload = DripPlanRequestInput(account="XL", name="Category distribution", candidateIds=[item["episodeId"] for item in operations],
            startDate=start_date, dailyTime="08:00:00", timezone="Europe/Berlin", totalDays=3)
        schemas = {category: ["marke"] for category in categories}
        with patch("services.drip_planning._card_schemas", return_value=json.dumps(schemas)):
            with self.engine.begin() as conn:
                request, preview_result = self.create_request_with_preview(conn, payload)
                manifest = conn.execute(text("SELECT i.operation_id::text,i.scheduled_at,i.category_snapshot FROM restart_plan_request_items i "
                    "WHERE i.request_id=:id ORDER BY i.row_number"), {"id": request["id"]}).mappings().all()
            executor = create_planning_executor(lambda: self.engine)
            result = run_job_by_id(self.engine, job_id=request["jobId"], worker_id="planner-test", executors={"drip.plan_create": executor})

        self.assertEqual(result.state, "SUCCEEDED")
        self.assertEqual(preview_result["itemCount"], 3)
        self.assertEqual(preview_result["readyCount"], 2)
        self.assertEqual(preview_result["needsCorrectionCount"], 1)
        self.assertEqual(sum(day["count"] for day in preview_result["distribution"]), 3)
        self.assertEqual({category for day in preview_result["distribution"] for category in day["categories"]}, set(categories))
        self.assertEqual(preview_result["firstRunAt"], next(day["scheduledAt"] for day in preview_result["distribution"] if day["count"]))
        manifest_by_operation = {row["operation_id"]: (row["scheduled_at"], row["category_snapshot"]) for row in manifest}
        self.assertEqual({category for _, category in manifest_by_operation.values()}, set(categories))

        with self.engine.connect() as conn:
            request_detail = get_request(conn, "XL", request["id"])
            persisted = conn.execute(text("SELECT operation_id::text,scheduled_at FROM restart_items WHERE batch_id IN "
                "(SELECT id FROM restart_batches WHERE plan_request_id=:id)"), {"id": request["id"]}).mappings().all()
            queued_items = list_batch_items(conn, account="XL", batch_id=request_detail["batches"][0]["id"], status="queue")
        self.assertEqual(request_detail["status"], "COMPLETED")
        self.assertEqual(request_detail["itemGroupCounts"]["queue"], 3)
        self.assertEqual(queued_items["page"]["totalItems"], 3)
        self.assertEqual({row["operation_id"]: row["scheduled_at"] for row in persisted},
                         {operation_id: scheduled_at for operation_id, (scheduled_at, _) in manifest_by_operation.items()})

    def test_plan_creation_requires_the_current_preview_fingerprint(self):
        from schemas.drip import DripPlanRequestInput
        from services.drip_planning import create_request, preview
        from services.drip_service import DripError

        operation = self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                         {"attrs": '{"attributes":[{"name":"Marke","value":"Brand"}]}', "id": operation["sourceId"]})
        payload = DripPlanRequestInput(account="XL", name="Stale preview", candidateIds=[operation["episodeId"]],
            startDate="2027-01-04", dailyTime="08:00:00", totalDays=3)
        with patch("services.drip_planning._card_schemas", return_value='{"fixture":["marke"],"fixture-changed":["marke"]}'):
            with self.engine.begin() as conn:
                with self.assertRaises(DripError) as missing:
                    create_request(conn, payload, "test")
                self.assertEqual(missing.exception.code, "PREVIEW_REQUIRED")
                preview_result = preview(conn, payload)
                conn.execute(text("UPDATE source_products SET category='fixture-changed' WHERE id=:id"), {"id": operation["sourceId"]})
                stale_payload = payload.model_copy(update={"preview_hash": preview_result["selectionHash"]})
                with self.assertRaises(DripError) as stale:
                    create_request(conn, stale_payload, "test")
                self.assertEqual(stale.exception.code, "PREVIEW_STALE")
                self.assertIn("currentPreviewHash", stale.exception.details)
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_plan_requests WHERE account='XL'")).scalar_one(), 0)

    def test_expired_plan_request_fails_visibly_before_creating_batch(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import run_job_by_id
        from services.drip_planning import create_planning_executor, get_request, retry_request
        from services.drip_service import DripError

        operation = self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                {"attrs": '{"attributes":[{"name":"Marke","value":"Brand"}]}', "id": operation["sourceId"]})
        local_today = datetime.now(ZoneInfo("Europe/Berlin")).date()
        payload = DripPlanRequestInput(account="XL", name="Expired start", candidateIds=[operation["episodeId"]],
            startDate=(local_today + timedelta(days=2)).isoformat(), dailyTime="08:00:00", timezone="Europe/Berlin", totalDays=1)
        with patch("services.drip_planning._card_schemas", return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request, _ = self.create_request_with_preview(conn, payload)
                conn.execute(text("UPDATE restart_plan_requests SET start_date=:today,daily_time='00:01:00' WHERE id=:id"),
                    {"today": local_today, "id": request["id"]})

        result = run_job_by_id(self.engine, job_id=request["jobId"], worker_id="planner-test",
            executors={"drip.plan_create": create_planning_executor(lambda: self.engine)})
        self.assertEqual(result.state, "DEAD")
        with self.engine.connect() as conn:
            failed = get_request(conn, "XL", request["id"])
            self.assertEqual((failed["status"], failed["jobState"], failed["errorCode"]),
                             ("FAILED", "DEAD", "SCHEDULE_START_NOT_FUTURE"))
            self.assertEqual(failed["batchCount"], 0)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items WHERE account='XL'")).scalar_one(), 0)
        with self.engine.begin() as conn:
            with self.assertRaises(DripError) as retry_error:
                retry_request(conn, account="XL", request_id=request["id"], actor="test")
            self.assertEqual(retry_error.exception.code, "PLAN_START_EXPIRED")

    def test_unlinked_draft_batch_manifest_is_searchable_and_account_scoped(self):
        from schemas.drip import DripBatchManifestResponse
        from services.drip_service import DripError

        operation = self.make_ready_operation(account="JV")
        with self.engine.begin() as conn:
            draft = create_batch_draft(
                conn, account="JV", name="Legacy catalog draft", actor="offline-operator",
                **self.schedule_input([operation]),
            )

        with self.engine.connect() as conn:
            batches = list_batches(conn, account="JV", unlinked_only=True, factory_id="factory-drip",
                                   source_type="AFTERBUY", query=operation["targetSku"])
            self.assertEqual(batches["page"]["totalItems"], 1)
            self.assertEqual(batches["items"][0]["id"], draft["id"])
            self.assertEqual(batches["items"][0]["status"], "DRAFT")
            manifest = list_batch_manifest(conn, account="JV", batch_id=draft["id"], query=operation["targetSku"])
            serialized = DripBatchManifestResponse.model_validate(manifest).model_dump(by_alias=True)
            self.assertEqual(serialized["page"]["totalItems"], 1)
            self.assertEqual(serialized["items"][0]["operationId"], operation["operationId"])
            self.assertEqual(serialized["items"][0]["newEan"], operation["targetSku"])
            self.assertEqual(serialized["items"][0]["factoryName"], "Offline Factory")
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items WHERE batch_id=:id"),
                                          {"id": draft["id"]}).scalar_one(), 0)
            with self.assertRaises(DripError):
                list_batch_manifest(conn, account="XL", batch_id=draft["id"])

    def test_plan_finalizes_one_factory_period_cohort_per_committed_job(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import claim_job_by_id,run_job_by_id
        from services.drip_planning import create_request,get_request,create_planning_executor
        operations=[self.make_ready_operation(period='3M'),self.make_ready_operation(period='12M')]
        with self.engine.begin() as conn:
            for operation in operations:
                conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                    {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Cohort finalization',candidateIds=[item['episodeId'] for item in operations],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                first_job=claim_job_by_id(conn,job_id=request['jobId'],worker_id='planner-one')
        executor=create_planning_executor(lambda:self.engine)
        executor(first_job)
        with self.engine.connect() as conn:
            partial=get_request(conn,'XL',request['id'])
            self.assertEqual(partial['status'],'BUILDING')
            self.assertEqual(partial['preparedCount'],2)
            self.assertEqual(len(partial['batches']),1)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id WHERE b.plan_request_id=:id"),{'id':request['id']}).scalar_one(),1)
        self.assertNotEqual(partial['jobId'],request['jobId'])
        completed=run_job_by_id(self.engine,job_id=partial['jobId'],worker_id='planner-two',executors={'drip.plan_create':executor})
        self.assertEqual(completed.state,'SUCCEEDED')
        with self.engine.connect() as conn:
            result=get_request(conn,'XL',request['id'])
            self.assertEqual(result['status'],'COMPLETED')
            self.assertEqual(len(result['batches']),2)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id WHERE b.plan_request_id=:id"),{'id':request['id']}).scalar_one(),2)

    def test_plan_recovery_after_a_committed_cohort_does_not_duplicate_it(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import run_job_by_id
        from services.drip_planning import create_request,get_request,retry_request,create_planning_executor
        from services.drip_service import DripError
        operations=[self.make_ready_operation(period='3M'),self.make_ready_operation(period='12M')]
        with self.engine.begin() as conn:
            for operation in operations:
                conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                    {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Recover finalized cohort',candidateIds=[item['episodeId'] for item in operations],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
            executor=create_planning_executor(lambda:self.engine)
            first=run_job_by_id(self.engine,job_id=request['jobId'],worker_id='planner-one',executors={'drip.plan_create':executor})
            self.assertEqual(first.state,'SUCCEEDED')
            with self.engine.connect() as conn:
                partial=get_request(conn,'XL',request['id'])
                self.assertEqual((partial['status'],len(partial['batches'])),('BUILDING',1))
            with patch('services.drip_planning.start_batch',side_effect=DripError('COHORT_FINALIZE_FAILED','fixture failure')):
                failed=run_job_by_id(self.engine,job_id=partial['jobId'],worker_id='planner-two',executors={'drip.plan_create':executor})
            self.assertEqual(failed.state,'DEAD')
            with self.engine.connect() as conn:
                stored=get_request(conn,'XL',request['id'])
                self.assertEqual((stored['status'],len(stored['batches'])),('FAILED',1))
                first_batch_id=stored['batches'][0]['id']
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id WHERE b.plan_request_id=:id"),{'id':request['id']}).scalar_one(),1)
            with self.engine.begin() as conn:
                resumed=retry_request(conn,account='XL',request_id=request['id'],actor='test')
            recovered=run_job_by_id(self.engine,job_id=resumed['jobId'],worker_id='planner-three',executors={'drip.plan_create':executor})
            self.assertEqual(recovered.state,'SUCCEEDED')
        with self.engine.connect() as conn:
            done=get_request(conn,'XL',request['id'])
            self.assertEqual(done['status'],'COMPLETED')
            self.assertEqual(len(done['batches']),2)
            self.assertIn(first_batch_id,{batch['id'] for batch in done['batches']})
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id WHERE b.plan_request_id=:id"),{'id':request['id']}).scalar_one(),2)

    def test_terminal_plan_failure_is_visible_and_recovery_resumes_manifest(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import SpecializedRecoveryRequired, replay_dead_job, run_job_by_id
        from services.drip_planning import create_request,get_request,retry_request,create_planning_executor
        from services.drip_service import DripError
        operation=self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Recoverable plan',candidateIds=[operation['episodeId']],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                conn.execute(text('UPDATE background_jobs SET max_attempts=1 WHERE id=:id'),{'id':request['jobId']})
        executor=create_planning_executor(lambda:self.engine)
        with patch('services.drip_planning.start_batch',side_effect=DripError('BATCH_FINALIZE_REJECTED','fixture rejection')):
            dead=run_job_by_id(self.engine,job_id=request['jobId'],worker_id='test',executors={'drip.plan_create':executor})
        self.assertEqual(dead.state,'DEAD')
        with self.engine.connect() as conn:
            failed=get_request(conn,'XL',request['id'])
            self.assertEqual((failed['status'],failed['jobState'],failed['errorCode']),('FAILED','DEAD','BATCH_FINALIZE_REJECTED'))
            self.assertEqual(failed['preparedCount'],0)
            with self.assertRaises(DripError): retry_request(conn,account='JV',request_id=request['id'],actor='test')
            with self.assertRaises(SpecializedRecoveryRequired):
                replay_dead_job(conn,job_id=request['jobId'],account='XL')
        with self.engine.begin() as conn:
            resumed=retry_request(conn,account='XL',request_id=request['id'],actor='test')
            with self.assertRaises(DripError): retry_request(conn,account='XL',request_id=request['id'],actor='test')
        recovered=run_job_by_id(self.engine,job_id=resumed['jobId'],worker_id='test',executors={'drip.plan_create':executor})
        self.assertEqual(recovered.state,'SUCCEEDED')
        with self.engine.connect() as conn:
            done=get_request(conn,'XL',request['id'])
            self.assertEqual(done['status'],'COMPLETED')
            self.assertEqual(len(done['batches']),1)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM restart_items i JOIN restart_batches b ON b.id=i.batch_id WHERE b.plan_request_id=:id AND b.account='XL'"),{'id':request['id']}).scalar_one(),1)

    def test_transient_plan_failure_retries_without_marking_manifest_failed(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import run_job_by_id
        from services.drip_planning import create_request,get_request,create_planning_executor
        operation=self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Transient plan',candidateIds=[operation['episodeId']],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                conn.execute(text('UPDATE restart_plan_request_items SET operation_id=NULL WHERE request_id=:id'),{'id':request['id']})
        executor=create_planning_executor(lambda:self.engine)
        now=datetime.now(timezone.utc)
        with patch('services.operation_service.create_operation',side_effect=RuntimeError('temporary failure')):
            first=run_job_by_id(self.engine,job_id=request['jobId'],worker_id='test',executors={'drip.plan_create':executor},now=now)
        self.assertEqual(first.state,'RETRY_WAIT')
        with self.engine.connect() as conn:
            waiting=get_request(conn,'XL',request['id'])
            self.assertIn(waiting['status'],{'PENDING','BUILDING'})
            self.assertEqual(waiting['jobState'],'RETRY_WAIT')
            self.assertEqual(waiting['errorCode'],'PLAN_CREATION_TEMPORARY')
            self.assertEqual(waiting['preparedCount'],0)
        with patch('services.operation_service.create_operation',return_value={'id':operation['operationId']}):
            second=run_job_by_id(self.engine,job_id=request['jobId'],worker_id='test',executors={'drip.plan_create':executor},now=now+timedelta(seconds=5))
        self.assertEqual(second.state,'SUCCEEDED')
        with self.engine.connect() as conn:
            self.assertEqual(get_request(conn,'XL',request['id'])['status'],'COMPLETED')

    def test_plan_recovery_preserves_a_committed_cursor_after_later_chunk_failure(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import run_job_by_id
        from services.drip_planning import create_request,get_request,create_planning_executor
        operations=[self.make_ready_operation() for _ in range(2)]
        with self.engine.begin() as conn:
            for operation in operations:
                conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                    {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Cursor recovery',candidateIds=[item['episodeId'] for item in operations],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                rows=conn.execute(text('SELECT row_number FROM restart_plan_request_items WHERE request_id=:id ORDER BY row_number'),{'id':request['id']}).all()
                conn.execute(text("UPDATE restart_plan_requests SET prepared_count=1,status='BUILDING' WHERE id=:id"),{'id':request['id']})
                conn.execute(text('UPDATE restart_plan_request_items SET operation_id=:operation WHERE request_id=:id AND row_number=1'),{'id':request['id'],'operation':operations[0]['operationId']})
                conn.execute(text('UPDATE restart_plan_request_items SET operation_id=NULL WHERE request_id=:id AND row_number=2'),{'id':request['id']})
        self.assertEqual([row[0] for row in rows],[1,2])
        executor=create_planning_executor(lambda:self.engine)
        now=datetime.now(timezone.utc)
        with patch('services.operation_service.create_operation',side_effect=RuntimeError('later chunk failure')):
            first=run_job_by_id(self.engine,job_id=request['jobId'],worker_id='test',executors={'drip.plan_create':executor},now=now)
        self.assertEqual(first.state,'RETRY_WAIT')
        with self.engine.connect() as conn:
            failed=get_request(conn,'XL',request['id'])
            manifest=conn.execute(text('SELECT row_number,operation_id::text FROM restart_plan_request_items WHERE request_id=:id ORDER BY row_number'),{'id':request['id']}).all()
        self.assertEqual(failed['preparedCount'],1)
        self.assertEqual([row[1] for row in manifest],[operations[0]['operationId'],None])
        with patch('services.operation_service.create_operation',return_value={'id':operations[1]['operationId']}):
            recovered=run_job_by_id(self.engine,job_id=request['jobId'],worker_id='test',executors={'drip.plan_create':executor},now=now+timedelta(seconds=5))
        self.assertEqual(recovered.state,'SUCCEEDED')
        with self.engine.connect() as conn:
            done=get_request(conn,'XL',request['id'])
            self.assertEqual(done['status'],'COMPLETED')
            self.assertEqual(done['preparedCount'],2)
            self.assertEqual(len(done['batches']),1)
            self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM restart_items WHERE batch_id=:id'),{'id':done['batches'][0]['id']}).scalar_one(),2)

    def test_exhausted_plan_lease_marks_manifest_failed_and_can_be_resumed(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import claim_job_by_id,reap_expired_leases,run_job_by_id
        from services.drip_planning import create_request,get_request,retry_request,create_planning_executor
        operation=self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Lease recovery',candidateIds=[operation['episodeId']],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                conn.execute(text('UPDATE background_jobs SET max_attempts=1 WHERE id=:id'),{'id':request['jobId']})
        now=datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            claimed=claim_job_by_id(conn,job_id=request['jobId'],worker_id='crashed-worker',lease_seconds=1,now=now)
            self.assertIsNotNone(claimed)
        with self.engine.begin() as conn:
            self.assertEqual(reap_expired_leases(conn,now=now+timedelta(seconds=2),job_types=['drip.plan_create']),1)
        with self.engine.connect() as conn:
            failed=get_request(conn,'XL',request['id'])
            self.assertEqual((failed['status'],failed['jobState'],failed['errorCode']),('FAILED','DEAD','LEASE_EXPIRED'))
        with self.engine.begin() as conn:
            resumed=retry_request(conn,account='XL',request_id=request['id'],actor='test')
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            result=run_job_by_id(self.engine,job_id=resumed['jobId'],worker_id='recovery-worker',executors={
                'drip.plan_create':create_planning_executor(lambda:self.engine)
            },now=now+timedelta(seconds=3))
        self.assertEqual(result.state,'SUCCEEDED')
        with self.engine.connect() as conn:
            self.assertEqual(get_request(conn,'XL',request['id'])['status'],'COMPLETED')

    def test_two_concurrent_planners_for_one_manifest_create_only_one_batch(self):
        from schemas.drip import DripPlanRequestInput
        from jobs.service import enqueue_job,run_job_by_id
        from services.drip_planning import create_request,get_request,create_planning_executor
        operations=[self.make_ready_operation() for _ in range(2)]
        with self.engine.begin() as conn:
            for operation in operations:
                conn.execute(text("UPDATE source_products SET category='fixture',attributes_json=CAST(:attrs AS jsonb) WHERE id=:id"),
                    {'id':operation['sourceId'],'attrs':'{"attributes":[{"name":"Marke","value":"Brand"}]}'})
        payload=DripPlanRequestInput(account='XL',name='Concurrent manifest',candidateIds=[item['episodeId'] for item in operations],startDate='2027-01-04',dailyTime='08:00:00',totalDays=7)
        with patch('services.drip_planning._card_schemas',return_value='{"fixture":["marke"]}'):
            with self.engine.begin() as conn:
                request,_=self.create_request_with_preview(conn,payload)
                duplicate=enqueue_job(conn,account='XL',job_type='drip.plan_create',idempotency_key='concurrent-plan-test-duplicate',payload={'requestId':request['id']})
        executor=create_planning_executor(lambda:self.engine)
        run_at=datetime.now(timezone.utc)+timedelta(seconds=1)
        def run(job_id,worker):
            return run_job_by_id(self.engine,job_id=job_id,worker_id=worker,executors={'drip.plan_create':executor},now=run_at)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(lambda pair:run(*pair),[(request['jobId'],'planner-a'),(duplicate.job.id,'planner-b')]))
        self.assertEqual([result.state for result in results],['SUCCEEDED','SUCCEEDED'])
        with self.engine.connect() as conn:
            result=get_request(conn,'XL',request['id'])
            self.assertEqual(result['status'],'COMPLETED')
            self.assertEqual(len(result['batches']),1)
            self.assertEqual(conn.execute(text('SELECT COUNT(*) FROM restart_items WHERE batch_id=:id'),{'id':result['batches'][0]['id']}).scalar_one(),2)

    def test_automatic_title_shortening_keeps_full_editor_map_and_filters_only_revision(self):
        from services.drip_service import create_batch_draft,start_batch
        operation=self.make_ready_operation()
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE source_products SET aftercool_product_id='AC-PREPARATION-TEST',aftercool_sku=:sku WHERE id=:id"),
                {'sku':operation['sourceSku'],'id':operation['sourceId']})
            save_product_edit(conn,account='XL',candidate_id=operation['episodeId'],expected_version=0,
                title='Long operator title '+('word '*15),category='Sofa',
                attributes={'Farbe':'Dunkelblau','Old category field':'Keep in editor'},actor='offline-operator')
            draft=create_batch_draft(conn,account='XL',name='Attribute preservation',actor='offline-operator',**self.schedule_input([operation]))
            batch=start_batch(conn,account='XL',batch_id=draft['id'],expected_version=draft['version'],actor='offline-operator',auto_prepare=True)
            item=conn.execute(text('SELECT * FROM restart_items WHERE batch_id=:id'),{'id':batch['id']}).mappings().one()

        def latest_revision(conn, **_kwargs):
            number=conn.execute(text('SELECT current_revision_number FROM restart_operations WHERE id=:id AND account=\'XL\''),{'id':operation['operationId']}).scalar_one()
            return {'revisionNumber':number}

        def record_current_revision_qc(conn, *, account, operation_id, actor, **_kwargs):
            revision=conn.execute(text(
                "SELECT id,revision_number,payload_hash FROM operation_revisions "
                "WHERE operation_id=:operation_id AND account=:account "
                "ORDER BY revision_number DESC LIMIT 1"
            ),{'operation_id':operation_id,'account':account}).mappings().one()
            qc_id=str(uuid4())
            conn.execute(text(
                "INSERT INTO qc_results(id,operation_id,revision_id,account,data_scope,outcome,policy_version,"
                "payload_hash,result_json,created_by) VALUES (:id,:operation_id,:revision_id,:account,"
                "'PRODUCTION','PASSED_AUTO',:policy,:payload_hash,'{}'::jsonb,:actor)"
            ),{'id':qc_id,'operation_id':operation_id,'revision_id':revision['id'],'account':account,
               'policy':QC_POLICY_VERSION,'payload_hash':revision['payload_hash'].strip(),'actor':actor})
            return {'outcome':'PASSED_AUTO','id':qc_id,'revisionNumber':revision['revision_number']}

        sources={'otto_product':{'productDescription':{'productLine':'Original source title'}},'otto_prices':{},'fulfillment':{},'aftercool':None}
        from sqlalchemy import event
        database_errors=[]
        event.listen(self.engine,'handle_error',lambda error:database_errors.append(
            (str(error.original_exception),error.statement)
        ))
        with patch('jobs.preparation_handler.prepare_operation_revision',side_effect=latest_revision), \
             patch('jobs.preparation_handler.record_operation_qc',side_effect=record_current_revision_qc), \
             patch('jobs.preparation_handler.heartbeat_job'):
            executor=create_preparation_executor(lambda:self.engine,environment={'OTTORESTART_MODE':'TEST'},
                source_reader=lambda *a,**k:sources,title_provider=lambda *_args:'Short operator title')
            executor(self.job(item))

        with self.engine.connect() as conn:
            saved=get_product_edit(conn,account='XL',candidate_id=operation['episodeId'])['edit']
            revision=conn.execute(text("SELECT payload_json FROM operation_revisions WHERE operation_id=:id AND account='XL' ORDER BY revision_number DESC LIMIT 1"),{'id':operation['operationId']}).mappings().one()
            item_state=conn.execute(text('SELECT status,last_error_code,preparation_errors_json FROM restart_items WHERE id=:id'),{'id':item['id']}).one()
        self.assertEqual(saved['title'],'Short operator title',f'item state: {item_state}; database errors: {database_errors[-1:]!r}')
        self.assertEqual(saved['attributes'],{'Farbe':'Dunkelblau','Old category field':'Keep in editor'})
        payload_attrs=revision['payload_json'].get('attributes',[])
        self.assertIn('Farbe',{entry['name'] for entry in payload_attrs})
        self.assertNotIn('Old category field',{entry['name'] for entry in payload_attrs})

    def test_accepted_availability_requires_separate_evidence_and_can_be_reconciled_once(self):
        operation, submission, _item_id = self.prepare_submission_fixture()
        self.confirm_processing(operation, submission)
        with self.engine.begin() as conn:
            jobs = enqueue_availability_jobs(
                conn, account='XL', operation_id=operation['operationId'], actor='offline-operator'
            )
        stock = next(job for job in jobs['jobs'] if job['action'] == 'QUANTITY')
        with self.engine.begin() as conn:
            claim_availability_dispatch(
                conn, account='XL', attempt_id=stock['attemptId'], job_id=stock['jobId']
            )
            result = record_availability_result(
                conn, account='XL', attempt_id=stock['attemptId'], http_status=202, response={}
            )
        self.assertEqual(result['status'], 'ACCEPTED')

        with self.engine.begin() as conn:
            resolved = resolve_unknown_availability_step(
                conn, account='XL', operation_id=operation['operationId'], action='QUANTITY',
                resolution='APPLIED', evidence_reference='OTTO product status checked manually',
                reason='The target SKU shows the requested quantity in the authenticated OTTO account.',
                actor='offline-operator',
            )
        self.assertEqual(resolved['status'], 'ACCEPTED')
        with self.engine.connect() as conn:
            attempt = conn.execute(
                text('SELECT status,error_code FROM availability_attempts WHERE id=:id'),
                {'id': stock['attemptId']},
            ).one()
            resolution = conn.execute(
                text("SELECT metadata_json->>'resolution' FROM restart_events "
                     "WHERE event_type='OTTO_AVAILABILITY_MANUALLY_RESOLVED' AND metadata_json->>'attemptId'=:id"),
                {'id': stock['attemptId']},
            ).scalar_one()
        self.assertEqual(tuple(attempt), ('ACCEPTED', None))
        self.assertEqual(resolution, 'APPLIED')
        with self.engine.begin() as conn:
            with self.assertRaises(AvailabilityError) as raised:
                resolve_unknown_availability_step(
                    conn, account='XL', operation_id=operation['operationId'], action='QUANTITY',
                    resolution='APPLIED', evidence_reference='second evidence reference',
                    reason='A duplicate manual decision must not be accepted for the same attempt.',
                    actor='offline-operator',
                )
        self.assertEqual(raised.exception.code, 'AVAILABILITY_ALREADY_RECONCILED')

        delivery = next(job for job in jobs['jobs'] if job['action'] == 'DELIVERY_INFORMATION')
        with self.engine.begin() as conn:
            claim_availability_dispatch(conn,account='XL',attempt_id=delivery['attemptId'],job_id=delivery['jobId'])
            record_availability_result(conn,account='XL',attempt_id=delivery['attemptId'],http_status=202,response={})
            rejected=resolve_unknown_availability_step(
                conn,account='XL',operation_id=operation['operationId'],action='DELIVERY_INFORMATION',
                resolution='NOT_APPLIED',evidence_reference='OTTO task status checked manually',
                reason='The shipping profile still shows the previous value in the authenticated OTTO account.',
                actor='offline-operator',
            )
        self.assertEqual(rejected['status'],'REJECTED')
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT status FROM availability_attempts WHERE id=:id'),{'id':delivery['attemptId']}).scalar_one(),'REJECTED')

    def test_category_rejection_requeues_only_known_incompatible_fields(self):
        from services.attribute_repair import rejected_attribute_names
        self.assertEqual(rejected_attribute_names([{'result':'FAILED','errors':[{'code':'INVALID_VALUE','attribute':'Farbe'}]}]),set())
        self.assertEqual(rejected_attribute_names([{'result':'FAILED','errors':[{'code':'ATTRIBUTE_NOT_ALLOWED','attribute':'Old category field'}]}]),{'Old category field'})


class DistributionTests(unittest.TestCase):
    def test_category_interleaving_is_balanced_and_deterministic(self):
        rows=[{'id':i,'category':'A' if i<7 else 'B'} for i in range(10)]
        result=distribute(rows,[1,2,3])
        self.assertEqual([sum(d==day for _,d in result) for day in [1,2,3]],[4,3,3])
        self.assertEqual(len({r['id'] for r,_ in result}),10)
        self.assertEqual(result,distribute(rows,[1,2,3]))

    def test_distribution_handles_ten_and_fifty_thousand_compact_items(self):
        for count in (10000,50000):
            with self.subTest(count=count):
                rows=[{'id':i,'category':str(i%41)} for i in range(count)]
                result=distribute(rows,list(range(30)))
                self.assertEqual(len(result),count)
                self.assertEqual(len({r['id'] for r,_ in result}),count)
                sizes=[sum(day==d for _,day in result) for d in range(30)]
                self.assertLessEqual(max(sizes)-min(sizes),1)

# Do not collect the imported fixture suite a second time.
del Fixture

from __future__ import annotations

import hashlib
import json
import os
import unittest
import uuid
from types import SimpleNamespace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from migration_runner import upgrade
from jobs.drip_handler import create_drip_release_executor
from jobs.availability_handler import create_availability_executor
from jobs.submission_handler import create_submission_executor
from jobs.processing_handler import create_processing_executor
from jobs.service import JobExecutionError
from services.gtin_service import review_gtin_evidence, submit_gtin_evidence
from services.drip_service import (
    DripError,
    control_batch,
    create_batch_draft,
    list_batch_items,
    list_batches,
    preview_schedule,
    release_scheduled_item,
    start_batch,
    _resolve_local_datetime,
)
from services.qc_service import QC_POLICY_VERSION
from services.availability_service import (
    AvailabilityError,
    claim_availability_dispatch,
    enqueue_availability_jobs,
    mark_interrupted_availability_unknown,
    record_availability_result,
)
from services.operation_service import record_processing_evidence
from services.submission_service import (
    SubmissionError,
    begin_submission_attempt,
    classify_submission_response,
    claim_submission_dispatch,
    mark_interrupted_dispatch_unknown,
    record_submission_result,
)

SUBMISSION_EVIDENCE_TYPES = frozenset({"TEST_GS1_EVIDENCE"})
SUBMISSION_PDF_FIXTURE = b"%PDF-1.4\noffline submission fixture\n%%EOF"


class DripServicePostgresTests(unittest.TestCase):
    """Persistent schedule checks run only on a disposable PostgreSQL 15 database."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for isolated Drip Feed tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Drip Feed tests require an explicit loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Drip Feed tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_engine", None):
            cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_drip_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.engine = create_engine(self.base_url.set(database=self.database_name), pool_pre_ping=True)
        upgrade(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def test_0011_down_script_reverts_empty_schedule_schema(self) -> None:
        down_path = Path(__file__).resolve().parents[1] / "migrations" / "0011_drip_feed_schedules.down.sql"
        submission_job_down = Path(__file__).resolve().parents[1] / "migrations" / "0015_submission_job_binding.down.sql"
        processing_down = Path(__file__).resolve().parents[1] / "migrations" / "0016_processing_and_online_confirmation.down.sql"
        availability_down = Path(__file__).resolve().parents[1] / "migrations" / "0014_availability_attempts.down.sql"
        dispatch_fence_down = Path(__file__).resolve().parents[1] / "migrations" / "0013_submission_dispatch_fence.down.sql"
        submission_down = Path(__file__).resolve().parents[1] / "migrations" / "0012_submission_attempts.down.sql"
        with self.engine.begin() as connection:
            connection.exec_driver_sql(processing_down.read_text(encoding="utf-8"))
            connection.exec_driver_sql(submission_job_down.read_text(encoding="utf-8"))
            connection.exec_driver_sql(availability_down.read_text(encoding="utf-8"))
            connection.exec_driver_sql(dispatch_fence_down.read_text(encoding="utf-8"))
            connection.exec_driver_sql(submission_down.read_text(encoding="utf-8"))
            connection.exec_driver_sql(down_path.read_text(encoding="utf-8"))
        with self.engine.connect() as connection:
            batch_columns = {
                row["column_name"] for row in connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='restart_batches'"
                )).mappings()
            }
            item_columns = {
                row["column_name"] for row in connection.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='restart_items'"
                )).mappings()
            }
        self.assertNotIn("updated_at", batch_columns)
        self.assertNotIn("selected_operation_ids_json", batch_columns)
        self.assertNotIn("operation_id", item_columns)

    @staticmethod
    def ean(seed: int) -> str:
        body = f"499{seed % 1_000_000_000:09d}"
        digits = [int(char) for char in body]
        checksum = (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits))) % 10) % 10
        return body + str(checksum)

    def make_ready_operation(
        self, *, account: str = "XL", period: str = "3M", factory_id: str | None = "factory-drip",
        verified_product_payload: bool = True,
    ) -> dict[str, str]:
        source_id, episode_id, run_id, observation_id = (str(uuid.uuid4()) for _ in range(4))
        operation_id, revision_id, qc_id = (str(uuid.uuid4()) for _ in range(3))
        seed = int(uuid.uuid4().hex[:8], 16)
        source_ean, new_ean = self.ean(seed), self.ean(seed + 1)
        sku = f"SOURCE-{uuid.uuid4().hex[:10]}"
        target_sku = new_ean
        source_ref = f"fixture://fulfillment/{target_sku}"
        product_reference = new_ean
        otto_payload = {
            "sku": target_sku,
            "ean": new_ean,
            "productReference": product_reference,
            "productTitle": "Offline test product",
        }
        response_hash = hashlib.sha256(observation_id.encode()).hexdigest()
        payload = {
            "account": account,
            "sourceProductId": source_id,
            "sourceSku": sku,
            "sourceEan": source_ean,
            "targetSku": target_sku,
            "newEan": new_ean,
                "productReference": new_ean,
            "priceCents": ((5000 - (1000 if account == "JV" else 2000)) // 100) * 100 + 99,
            "readinessGaps": [],
            "targetIdentity": {
                "verified": True, "account": account, "sourceSku": sku, "targetSku": target_sku,
                "newEan": new_ean, "sourceRef": source_ref, "productReference": product_reference,
            },
        }
        if verified_product_payload:
            payload["ottoProductPayload"] = otto_payload
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        price_snapshot = {
            "currency": "EUR", "standardPriceAmount": "50.00", "ottoStandardPriceCents": 5000,
            "aftercoolStartPriceCents": 5000, "originalPriceCents": 5000,
            "selectedSource": "OTTO_STANDARD_PRICE", "discountCents": 1000 if account == "JV" else 2000,
            "discountedPriceCents": 5000 - (1000 if account == "JV" else 2000),
            "targetPriceCents": ((5000 - (1000 if account == "JV" else 2000)) // 100) * 100 + 99,
            "targetFractionCents": 99,
            "formula": "MAX_SOURCE_PRICE_MINUS_ACCOUNT_DISCOUNT_THEN_99", "source": "MAX_CONFIRMED_SOURCE_PRICE",
        }
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO source_products(id,account,source_type,aftercool_ean,factory_id,factory_name,original_title,data_scope) "
                "VALUES (:id,:account,'AFTERCOOL',:ean,:factory,:factory_name,'Offline Drip source','PRODUCTION')"
            ), {"id": source_id, "account": account, "ean": source_ean, "factory": factory_id,
                "factory_name": "Offline Factory" if factory_id else None})
            connection.execute(text(
                "INSERT INTO import_runs(id,account,source_filename,total_rows,status,data_scope,run_type,run_mode,run_state,started_at,finished_at) "
                "VALUES (:id,:account,'drip-test.txt',1,'COMPLETED','PRODUCTION','FILE_IMPORT','BASELINE','SUCCEEDED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {"id": run_id, "account": account})
            connection.execute(text(
                "INSERT INTO otto_status_observations(id,run_id,account,data_scope,sequence_no,requested_sku,response_sku,"
                "source_product_id,confirmed_ean,http_status,classification,period,reason,status,market_place_status,active,"
                "errors_json,error_codes_json,error_titles_json,unknown_codes_json,response_json,response_hash,reduction_status) "
                "VALUES (:id,:run_id,:account,'PRODUCTION',1,:sku,:sku,:source_id,:ean,200,'EXACT_CODE',:period,"
                "'602016 - Nonseller','REJECTED','INACTIVE',FALSE,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
                "CAST(:response AS jsonb),:hash,'OPENED')"
            ), {"id": observation_id, "run_id": run_id, "account": account, "sku": sku,
                "source_id": source_id, "ean": source_ean, "period": period,
                "response": json.dumps({"sku": sku, "errors": []}), "hash": response_hash})
            connection.execute(text(
                "INSERT INTO candidate_episodes(id,account,data_scope,observed_sku,canonical_identity,source_product_id,"
                "confirmed_ean,period,classification,origin,first_observation_id,last_observation_id,last_response_hash) "
                "VALUES (:id,:account,'PRODUCTION',:sku,:sku,:source_id,:ean,:period,'EXACT_CODE','NEW',:obs,:obs,:hash)"
            ), {"id": episode_id, "account": account, "sku": sku, "source_id": source_id,
                "ean": source_ean, "period": period, "obs": observation_id, "hash": response_hash})
            connection.execute(text(
                "INSERT INTO ean_pool(ean,account,status,data_scope,provenance_source,eligibility_status,verified_by,verified_at) "
                "VALUES (:ean,:account,'AVAILABLE','PRODUCTION','MANUAL_IMPORT','ELIGIBLE','offline-reviewer',CURRENT_TIMESTAMP)"
            ), {"ean": new_ean, "account": account})
            connection.execute(text(
                "INSERT INTO restart_operations(id,account,data_scope,source_product_id,period,status,new_ean,target_title,"
                "attributes_json,candidate_episode_id,attempt_number,target_sku,created_by,version,current_revision_number,submission_state) "
                "VALUES (:id,:account,'PRODUCTION',:source_id,:period,'READY',:new_ean,'Offline ready title','{}'::jsonb,"
                ":episode_id,1,:target,'offline-operator',4,1,'NEVER_SUBMITTED')"
            ), {"id": operation_id, "account": account, "source_id": source_id, "period": period,
                "new_ean": new_ean, "episode_id": episode_id, "target": target_sku})
            connection.execute(text(
                "UPDATE ean_pool SET status='RESERVED',reservation_operation_id=:operation_id WHERE ean=:ean AND account=:account"
            ), {"operation_id": operation_id, "ean": new_ean, "account": account})
            source_snapshot = {
                "account": account,
                "sourceSku": sku,
                "sourceEan": source_ean,
                "sourceType": "AFTERCOOL",
                "aftercoolIdentity": {
                    "account": account, "source_type": "AFTERCOOL", "product_id": "AC-DRIP",
                    "aftercool_product_id": "AC-DRIP", "ean": source_ean,
                    "factory_id": factory_id or "factory-drip",
                },
                "references": [{"system": "TEST", "method": "FIXTURE", "resource": source_ref}],
            }
            fulfillment_snapshot = {
                "target": {
                    "verified": True,
                    "account": account,
                    "targetSku": target_sku,
                    "stockQuantity": 0,
                    "shippingProfile": "38427776-7383-4d65-82c9-dbe949d0573a",
                    "processingTime": "DEFAULT",
                    "sourceRef": source_ref,
                }
            }
            connection.execute(text(
                "INSERT INTO operation_revisions(id,operation_id,account,data_scope,revision_number,source_snapshot_json,"
                "content_json,price_snapshot_json,fulfillment_snapshot_json,gtin_evidence_ids_json,payload_json,payload_hash,created_by) "
                "VALUES (:id,:operation_id,:account,'PRODUCTION',1,CAST(:source AS jsonb),'{}'::jsonb,CAST(:price AS jsonb),"
                "CAST(:fulfillment AS jsonb),'[]'::jsonb,CAST(:payload AS jsonb),:hash,'offline-operator')"
            ), {"id": revision_id, "operation_id": operation_id, "account": account, "hash": payload_hash,
                "source": json.dumps(source_snapshot), "fulfillment": json.dumps(fulfillment_snapshot),
                "price": json.dumps(price_snapshot), "payload": json.dumps(payload)})
            connection.execute(text(
                "INSERT INTO qc_results(id,operation_id,revision_id,account,data_scope,outcome,policy_version,payload_hash,result_json,created_by) "
                "VALUES (:id,:operation_id,:revision_id,:account,'PRODUCTION','PASSED_AUTO',:policy,:hash,'{}'::jsonb,'offline-reviewer')"
            ), {"id": qc_id, "operation_id": operation_id, "revision_id": revision_id,
                "account": account, "policy": QC_POLICY_VERSION, "hash": payload_hash})
        with self.engine.begin() as connection:
            evidence = submit_gtin_evidence(
                connection, account=account, ean=new_ean, source_product_id=source_id,
                evidence_type="TEST_GS1_EVIDENCE", source_name="offline fixture",
                artifact_filename="submission-fixture.pdf", artifact_media_type="application/pdf",
                artifact_bytes=SUBMISSION_PDF_FIXTURE,
                claim_basis="Offline evidence fixture bound to this source product and test GTIN.",
                validity_kind="PERPETUAL", expires_at=None, actor="offline-operator",
            )
            review_gtin_evidence(
                connection, account=account, evidence_id=evidence["id"], decision="APPROVE",
                review_basis="This approval exists only in the disposable submission test database.",
                actor="offline-reviewer", approved_evidence_types=SUBMISSION_EVIDENCE_TYPES,
            )
        return {"account": account, "sourceId": source_id, "episodeId": episode_id, "operationId": operation_id,
                "revisionId": revision_id, "qcId": qc_id, "sourceEan": source_ean, "newEan": new_ean,
                "sourceSku": sku, "targetSku": target_sku, "payloadHash": payload_hash}

    def schedule_input(self, operations: list[dict[str, str]], *, start_date: date | None = None) -> dict[str, object]:
        return {"operation_ids": [item["operationId"] for item in operations],
                "start_date": start_date or date(2027, 1, 4), "daily_time": time(8, 0),
                "timezone_name": "Asia/Qyzylorda", "total_days": 7}

    def create_and_start(self, operations: list[dict[str, str]], *, now: datetime | None = None) -> tuple[dict, dict]:
        values = self.schedule_input(operations)
        with self.engine.begin() as connection:
            draft = create_batch_draft(connection, account=operations[0]["account"], name="Offline Drip Test",
                                       actor="offline-operator", now=now, **values)
        with self.engine.begin() as connection:
            started = start_batch(connection, account=operations[0]["account"], batch_id=draft["id"],
                                  expected_version=draft["version"], actor="offline-operator", now=now)
        return draft, started

    def prepare_submission_fixture(self) -> tuple[dict[str, str], dict[str, object], int]:
        operation = self.make_ready_operation()
        _, batch = self.create_and_start([operation], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        with self.engine.connect() as connection:
            item = connection.execute(
                text("SELECT id,scheduled_at FROM restart_items WHERE batch_id=:batch_id AND operation_id=:operation_id"),
                {"batch_id": batch["id"], "operation_id": operation["operationId"]},
            ).mappings().one()
            generation = connection.execute(
                text("SELECT schedule_generation FROM restart_batches WHERE id=:batch_id"),
                {"batch_id": batch["id"]},
            ).scalar_one()
        with self.engine.begin() as connection:
            released = release_scheduled_item(
                connection, account=operation["account"],
                payload={"batchId": batch["id"], "itemId": str(item["id"]), "scheduleGeneration": generation},
                now=item["scheduled_at"] + timedelta(seconds=1),
                submit_enabled=True,
            )
        self.assertEqual(released["code"], "READY_TO_SUBMIT")
        with self.engine.connect() as connection:
            submission_job = connection.execute(text(
                "SELECT id FROM background_jobs WHERE job_type='otto.submit_product' AND account=:account"
            ), {"account": operation["account"]}).mappings().one()
        job_id = str(submission_job["id"])
        with self.engine.begin() as connection:
            attempt = begin_submission_attempt(
                connection, account=operation["account"], item_id=int(item["id"]),
                background_job_id=job_id,
                expected_operation_version=4, source_price_account=operation["account"],
                source_price_response={"sku": operation["sourceSku"],
                                       "standardPrice": {"amount": "50.00", "currency": "EUR"}},
                aftercool_price_cents=5000,
                source_price_checked_at=datetime.now(timezone.utc), actor="offline-operator",
                approved_evidence_types=SUBMISSION_EVIDENCE_TYPES,
            )
        attempt["jobId"] = job_id
        return operation, attempt, int(item["id"])

    def load_submission_job(self, account: str = "XL") -> SimpleNamespace:
        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT id,payload_json FROM background_jobs "
                "WHERE account=:account AND job_type='otto.submit_product'"
            ), {"account": account}).mappings().one()
        return SimpleNamespace(
            id=str(row["id"]), account=account, job_type="otto.submit_product",
            payload=dict(row["payload_json"]), lease_token=None,
        )

    def release_for_submission_worker(self, operation: dict[str, str]) -> SimpleNamespace:
        _, batch = self.create_and_start([operation], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        with self.engine.connect() as connection:
            item = connection.execute(text(
                "SELECT id,scheduled_at FROM restart_items WHERE batch_id=:batch_id"
            ), {"batch_id": batch["id"]}).mappings().one()
            generation = connection.execute(text(
                "SELECT schedule_generation FROM restart_batches WHERE id=:id"
            ), {"id": batch["id"]}).scalar_one()
        with self.engine.begin() as connection:
            result = release_scheduled_item(
                connection, account=operation["account"],
                payload={"batchId": batch["id"], "itemId": str(item["id"]), "scheduleGeneration": generation},
                now=item["scheduled_at"] + timedelta(seconds=1), submit_enabled=True,
            )
        self.assertTrue(result.get("submissionJobId"))
        return self.load_submission_job(operation["account"])

    @staticmethod
    def submission_environment(*, enabled: bool = True) -> dict[str, str]:
        return {
            "OTTORESTART_MODE": "PRODUCTION",
            "OTTORESTART_OTTO_READ_ENABLED": "true",
            "OTTORESTART_AFTERCOOL_READ_ENABLED": "true",
            "OTTORESTART_PRODUCT_WRITE_ENABLED": "true" if enabled else "false",
            "OTTORESTART_GTIN_APPROVED_EVIDENCE_TYPES": "TEST_GS1_EVIDENCE",
        }

    @staticmethod
    def offline_aftercool_lookup_factory(_config):
        class Lookup:
            def lookup_product(_self, account: str, ean: str, factory_hint=None):
                return {
                    "account": account, "ean": ean, "status": "FOUND", "found": True,
                    "source_type": "AFTERCOOL", "product_id": "AC-DRIP",
                    "aftercool_product_id": "AC-DRIP", "factory_id": factory_hint,
                    "price_cents": 5000, "verified": True, "data_scope": "PRODUCTION",
                }
        return Lookup()

    def test_submission_worker_posts_once_and_redelivery_does_not_publish(self) -> None:
        operation, _prepared, _item_id = self.prepare_submission_fixture()
        job = self.load_submission_job(operation["account"])
        calls: list[tuple[str, object]] = []

        class FakeClient:
            def get_product_prices(_self, sku: str) -> dict:
                calls.append(("GET_PRICE", sku))
                return {"sku": operation["sourceSku"],
                        "standardPrice": {"amount": "50.00", "currency": "EUR"}}

            def submit_product_payload(_self, payload: dict, *, expected_sku: str, expected_ean: str):
                calls.append(("POST_PRODUCT", (dict(payload), expected_sku, expected_ean)))
                return SimpleNamespace(
                    status_code=202,
                    response={"links": [{"rel": "self", "href":
                        "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001"}]},
                    error_code=None,
                )

        executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(),
            client_factory=lambda _account, _config: FakeClient(),
            aftercool_lookup_factory=self.offline_aftercool_lookup_factory,
        )
        executor(job)
        self.assertEqual([call[0] for call in calls], ["GET_PRICE", "POST_PRODUCT"])
        sent = calls[1][1]
        self.assertEqual(sent[0]["sku"], operation["targetSku"])
        self.assertEqual(sent[0]["ean"], operation["newEan"])
        self.assertEqual((sent[1], sent[2]), (operation["targetSku"], operation["newEan"]))

        executor(job)
        self.assertEqual([call[0] for call in calls], ["GET_PRICE", "POST_PRODUCT"])
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT a.status,o.status,o.submission_state FROM submission_attempts a "
                "JOIN restart_operations o ON o.id=a.operation_id WHERE a.background_job_id=:job_id"
            ), {"job_id": job.id}).one()
        self.assertEqual(tuple(state), ("ACCEPTED", "OTTO_PROCESSING", "IN_FLIGHT"))

    def test_worker_redelivery_after_dispatch_claim_enters_unknown_without_post(self) -> None:
        operation, attempt, _item_id = self.prepare_submission_fixture()
        job = self.load_submission_job(operation["account"])
        with self.engine.begin() as connection:
            claim_submission_dispatch(
                connection, account=operation["account"], attempt_id=attempt["attemptId"], job_id=job.id
            )
        built: list[bool] = []
        executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(),
            client_factory=lambda _account, _config: built.append(True),
        )
        executor(job)
        executor(job)
        self.assertEqual(built, [])
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT a.status,a.error_code,o.status,o.submission_state,i.status "
                "FROM submission_attempts a JOIN restart_operations o ON o.id=a.operation_id "
                "JOIN restart_items i ON i.id=a.item_id WHERE a.id=:attempt_id"
            ), {"attempt_id": attempt["attemptId"]}).one()
        self.assertEqual(tuple(state), (
            "UNKNOWN", "DISPATCH_RESULT_MISSING", "RECONCILIATION_REQUIRED", "UNKNOWN", "NEEDS_REVIEW",
        ))

    def test_submission_worker_fails_closed_before_network_without_verified_product_payload(self) -> None:
        operation = self.make_ready_operation(verified_product_payload=False)
        job = self.release_for_submission_worker(operation)
        built: list[bool] = []
        executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(),
            client_factory=lambda _account, _config: built.append(True),
        )
        with self.assertRaises(JobExecutionError) as raised:
            executor(job)
        self.assertEqual(raised.exception.code, "TARGET_IDENTITY_UNVERIFIED")
        self.assertEqual(built, [])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM submission_attempts")).scalar_one(), 0)

    def test_submission_worker_flag_off_does_not_construct_client_or_send(self) -> None:
        operation = self.make_ready_operation()
        job = self.release_for_submission_worker(operation)
        built: list[bool] = []
        executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(enabled=False),
            client_factory=lambda _account, _config: built.append(True),
        )
        with self.assertRaises(JobExecutionError) as raised:
            executor(job)
        self.assertEqual(raised.exception.code, "OTTO_PRODUCT_WRITE_DISABLED")
        self.assertEqual(built, [])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM submission_attempts")).scalar_one(), 0)

    def test_submission_worker_sends_http_429_once_and_keeps_operation_unpublished(self) -> None:
        operation = self.make_ready_operation()
        job = self.release_for_submission_worker(operation)
        calls: list[str] = []

        class FakeClient:
            def get_product_prices(_self, _sku: str) -> dict:
                calls.append("GET_PRICE")
                return {"sku": operation["sourceSku"],
                        "standardPrice": {"amount": "50.00", "currency": "EUR"}}

            def submit_product_payload(_self, _payload: dict, *, expected_sku: str, expected_ean: str):
                calls.append("POST_PRODUCT")
                return SimpleNamespace(status_code=429, response={}, error_code=None)

        executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(),
            client_factory=lambda _account, _config: FakeClient(),
            aftercool_lookup_factory=self.offline_aftercool_lookup_factory,
        )
        executor(job)
        executor(job)
        self.assertEqual(calls, ["GET_PRICE", "POST_PRODUCT"])
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT a.status,a.error_code,o.status,o.submission_state FROM submission_attempts a "
                "JOIN restart_operations o ON o.id=a.operation_id WHERE a.background_job_id=:job_id"
            ), {"job_id": job.id}).one()
        self.assertEqual(tuple(state), ("REJECTED", "OTTO_HTTP_429", "READY", "NEVER_SUBMITTED"))

    def test_prepared_attempt_with_changed_source_price_is_rejected_for_manual_review(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        job = self.load_submission_job(operation["account"])

        class ChangedPriceClient:
            def get_product_prices(_self, _sku: str) -> dict:
                return {"sku": operation["sourceSku"],
                        "standardPrice": {"amount": "51.00", "currency": "EUR"}}

            def submit_product_payload(_self, *_args, **_kwargs):
                raise AssertionError("Changed source price must block product POST.")

        executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(),
            client_factory=lambda _account, _config: ChangedPriceClient(),
            aftercool_lookup_factory=self.offline_aftercool_lookup_factory,
        )
        with self.assertRaises(JobExecutionError) as raised:
            executor(job)
        self.assertEqual(raised.exception.code, "SOURCE_PRICE_CHANGED")
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT a.status,a.error_code,o.status,o.submission_state,i.status,i.last_error_code "
                "FROM submission_attempts a JOIN restart_operations o ON o.id=a.operation_id "
                "JOIN restart_items i ON i.id=a.item_id WHERE a.id=:attempt_id"
            ), {"attempt_id": attempt["attemptId"]}).one()
        self.assertEqual(tuple(state), (
            "REJECTED", "SOURCE_PRICE_CHANGED", "NEEDS_REVIEW", "NEVER_SUBMITTED",
            "NEEDS_REVIEW", "SOURCE_PRICE_CHANGED",
        ))
        self.assertEqual(item_id, attempt["itemId"])

    def confirm_processing(self, operation: dict[str, str], attempt: dict[str, object]) -> str:
        with self.engine.begin() as connection:
            claim_submission_dispatch(connection, account=operation["account"], attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
        with self.engine.begin() as connection:
            record_submission_result(
                connection, account=operation["account"], attempt_id=attempt["attemptId"],
                http_status=202,
                response={"links": [{"rel": "self", "href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001"}]},
            )
            connection.execute(text(
                "UPDATE otto_processing_polls SET state='SUCCEEDED',current_job_id=NULL,"
                "completed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE attempt_id=:attempt_id"
            ), {"attempt_id": attempt["attemptId"]})
        event_id = str(uuid.uuid4())
        metadata = {"targetSku": operation["targetSku"], "result": "SUCCEEDED", "account": operation["account"]}
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO restart_events(id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json) "
                "VALUES (:id,:account,'PRODUCTION','OTTO_RESULT','OTTO_PROCESSING_RESULT',:operation_id,"
                "'Confirmed offline per-SKU product processing',CAST(:metadata AS jsonb))"
            ), {"id": event_id, "account": operation["account"], "operation_id": operation["operationId"],
                "metadata": json.dumps(metadata)})
            record_processing_evidence(
                connection, account=operation["account"], operation_id=operation["operationId"],
                event_id=event_id, target_sku=operation["targetSku"],
            )
            connection.execute(text(
                "UPDATE restart_items SET status='AWAITING_ONLINE' WHERE operation_id=:operation_id"
            ), {"operation_id": operation["operationId"]})
        return event_id

    def test_preview_is_read_only_and_distributes_evenly_across_selected_days(self) -> None:
        operations = [self.make_ready_operation() for _ in range(2)]
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.engine.connect() as connection:
            preview = preview_schedule(connection, account="XL", now=now, **self.schedule_input(operations))
        self.assertEqual(preview["itemCount"], 2)
        self.assertEqual([item["count"] for item in preview["distribution"]], [1, 0, 0, 1, 0, 0, 0])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM restart_batches WHERE data_scope='PRODUCTION'")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM background_jobs WHERE job_type='drip.release_item'")).scalar_one(), 0)

    def test_start_persists_immutable_qc_snapshots_and_release_never_publishes(self) -> None:
        operations = [self.make_ready_operation() for _ in range(2)]
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        draft, batch = self.create_and_start(operations, now=now)
        self.assertEqual(draft["status"], "DRAFT")
        self.assertEqual(batch["status"], "SCHEDULED")
        with self.engine.connect() as connection:
            items = connection.execute(text(
                "SELECT * FROM restart_items WHERE batch_id=:id AND data_scope='PRODUCTION' ORDER BY row_number"
            ), {"id": batch["id"]}).mappings().all()
            jobs = connection.execute(text(
                "SELECT payload_json,available_at,state FROM background_jobs WHERE job_type='drip.release_item' ORDER BY available_at"
            )).mappings().all()
            self.assertEqual(len(items), 2)
            self.assertEqual(len(jobs), 2)
            self.assertEqual({row["status"] for row in items}, {"SCHEDULED"})
            self.assertEqual({str(row["operation_id"]) for row in items}, {item["operationId"] for item in operations})
            self.assertEqual({str(row["revision_id"]) for row in items}, {item["revisionId"] for item in operations})
            self.assertEqual({str(row["qc_result_id"]) for row in items}, {item["qcId"] for item in operations})
            self.assertEqual({row["state"] for row in jobs}, {"PENDING"})
            started_event = connection.execute(text(
                "SELECT metadata_json FROM restart_events WHERE event_type='DRIP_BATCH_STARTED' AND entity_id=:id"
            ), {"id": batch["id"]}).mappings().one()
            self.assertEqual(started_event["metadata_json"]["submissionDecision"], "DEFERRED_TO_WORKER_RUNTIME_GATES")
            self.assertEqual(started_event["metadata_json"]["externalServicesCalled"], [])
            item_id = int(items[0]["id"])
            job_payload = dict(jobs[0]["payload_json"])
            job_payload["itemId"] = str(item_id)
            scheduled = items[0]["scheduled_at"]
        with self.engine.begin() as connection:
            released = release_scheduled_item(connection, account="XL", payload=job_payload,
                                              now=scheduled + timedelta(seconds=1))
        self.assertTrue(released["released"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item_id}).scalar_one(), "READY_TO_SUBMIT")
            self.assertEqual(connection.execute(text("SELECT status FROM restart_operations WHERE id=:id"), {"id": operations[0]["operationId"]}).scalar_one(), "READY")
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM restart_items WHERE status='PUBLISHED'")).scalar_one(), 0)

    def test_submission_202_is_recorded_without_published_state_and_task_query_is_sanitized(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            claimed = claim_submission_dispatch(connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
        self.assertEqual(claimed["status"], "DISPATCHING")
        with self.engine.begin() as connection:
            with self.assertRaises(SubmissionError) as raised:
                begin_submission_attempt(
                    connection, account="XL", item_id=item_id, expected_operation_version=attempt["operationVersion"],
                    background_job_id=attempt["jobId"],
                    source_price_account="XL", source_price_response={"sku": operation["sourceSku"],
                                                                        "standardPrice": {"amount": "50.00", "currency": "EUR"}},
                    aftercool_price_cents=5000,
                    source_price_checked_at=datetime.now(timezone.utc), actor="offline-operator",
                    approved_evidence_types=SUBMISSION_EVIDENCE_TYPES,
                )
            self.assertEqual(raised.exception.code, "DRIP_ITEM_NOT_READY")
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM submission_attempts")).scalar_one(), 1)
        task_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            result = record_submission_result(
                connection, account="XL", attempt_id=attempt["attemptId"], http_status=202,
                response={"links": [{"rel": "self", "href": f"https://api.otto.market/v5/products/update-tasks/{task_id}?token=hidden"}]},
            )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["operationStatus"], "OTTO_PROCESSING")
        self.assertFalse(result["published"])
        with self.engine.connect() as connection:
            stored = connection.execute(
                text("SELECT status,task_links_json::text FROM submission_attempts WHERE id=:id"),
                {"id": attempt["attemptId"]},
            ).one()
            op_state = connection.execute(
                text("SELECT status,submission_state FROM restart_operations WHERE id=:id"),
                {"id": operation["operationId"]},
            ).one()
            item_state = connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item_id}).scalar_one()
        self.assertEqual(stored[0], "ACCEPTED")
        self.assertNotIn("token", stored[1])
        self.assertIn(f"/v5/products/update-tasks/{task_id}", stored[1])
        self.assertEqual(tuple(op_state), ("OTTO_PROCESSING", "IN_FLIGHT"))
        self.assertEqual(item_state, "OTTO_PROCESSING")
        self.assertNotEqual(op_state[0], "PUBLISHED")

    def test_dispatch_fence_cannot_be_claimed_or_recorded_twice(self) -> None:
        _operation, attempt, _item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            with self.assertRaises(SubmissionError) as raised:
                record_submission_result(connection, account="XL", attempt_id=attempt["attemptId"], http_status=202)
            self.assertEqual(raised.exception.code, "SUBMISSION_DISPATCH_NOT_CLAIMED")
        with self.engine.begin() as connection:
            claim_submission_dispatch(connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT status,dispatch_started_at FROM submission_attempts WHERE id=:id"),
                                     {"id": attempt["attemptId"]}).one()
        self.assertEqual(row[0], "DISPATCHING")
        self.assertIsNotNone(row[1])
        with self.engine.begin() as connection:
            with self.assertRaises(SubmissionError) as raised:
                claim_submission_dispatch(connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
            self.assertEqual(raised.exception.code, "SUBMISSION_DISPATCH_ALREADY_CLAIMED")

    def test_interrupted_dispatch_is_fenced_into_reconciliation_without_releasing_gtin(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            claim_submission_dispatch(connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
        with self.engine.begin() as connection:
            result = mark_interrupted_dispatch_unknown(connection, account="XL", attempt_id=attempt["attemptId"])
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["operationStatus"], "RECONCILIATION_REQUIRED")
        with self.engine.connect() as connection:
            states = connection.execute(text(
                "SELECT a.status,a.error_code,o.status,o.submission_state,i.status,p.status "
                "FROM submission_attempts a JOIN restart_operations o ON o.id=a.operation_id "
                "JOIN restart_items i ON i.id=a.item_id JOIN ean_pool p ON p.ean=o.new_ean "
                "WHERE a.id=:id"), {"id": attempt["attemptId"]}).one()
        self.assertEqual(tuple(states), ("UNKNOWN", "DISPATCH_RESULT_MISSING", "RECONCILIATION_REQUIRED", "UNKNOWN", "NEEDS_REVIEW", "RESERVED"))
        with self.engine.begin() as connection:
            replay = mark_interrupted_dispatch_unknown(connection, account="XL", attempt_id=attempt["attemptId"])
        self.assertTrue(replay["idempotentReplay"])
        with self.engine.begin() as connection:
            late_result = record_submission_result(connection, account="XL", attempt_id=attempt["attemptId"], http_status=202)
        self.assertEqual(late_result["status"], "ACCEPTED")
        self.assertFalse(late_result["published"])
        with self.engine.connect() as connection:
            late_operation = connection.execute(text("SELECT status,submission_state FROM restart_operations WHERE id=:id"),
                                                {"id": operation["operationId"]}).one()
            late_item = connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item_id}).scalar_one()
        self.assertEqual(tuple(late_operation), ("OTTO_PROCESSING", "IN_FLIGHT"))
        self.assertEqual(late_item, "NEEDS_REVIEW")

    def _accept_task_for_poll(self) -> tuple[dict[str, str], dict[str, object], int, str]:
        operation, attempt, item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            claim_submission_dispatch(
                connection, account=operation["account"], attempt_id=attempt["attemptId"], job_id=attempt["jobId"]
            )
        with self.engine.begin() as connection:
            result = record_submission_result(
                connection, account=operation["account"], attempt_id=attempt["attemptId"],
                http_status=202,
                response={"links": [{"rel": "self", "href":
                    "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001"}]},
            )
        self.assertTrue(result["processing"]["started"])
        return operation, attempt, item_id, result["processing"]["jobId"]

    def test_processing_poller_requires_exact_succeeded_result_and_queues_availability(self) -> None:
        operation, attempt, item_id, poll_job_id = self._accept_task_for_poll()
        calls: list[str] = []

        class FakeClient:
            def read_update_task_href(_self, href: str) -> dict:
                calls.append("summary")
                self.assertEqual(href, "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001")
                return {"state": "done", "succeeded": 1, "failed": 0, "unchanged": 0}

            def get_update_task_results(_self, task_id: str, result_type: str, *, max_pages: int) -> list[dict]:
                calls.append(result_type)
                self.assertEqual(task_id, "00000000-0000-4000-8000-000000000001")
                self.assertEqual(max_pages, 50)
                return [{"sku": operation["targetSku"]}] if result_type == "succeeded" else []

        executor = create_processing_executor(
            lambda: self.engine,
            environment={"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_OTTO_READ_ENABLED": "true"},
            client_factory=lambda _account, _config: FakeClient(),
        )
        executor(SimpleNamespace(job_type="otto.poll_product_task", account="XL", id=poll_job_id,
                                 payload={"attemptId": attempt["attemptId"]}))
        self.assertEqual(calls, ["summary", "succeeded", "failed", "unchanged"])
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT a.status,o.status,o.submission_state,o.processing_evidence_event_id,i.status "
                "FROM submission_attempts a JOIN restart_operations o ON o.id=a.operation_id "
                "JOIN restart_items i ON i.id=a.item_id WHERE a.id=:attempt"
            ), {"attempt": attempt["attemptId"]}).one()
            poll_state = connection.execute(text(
                "SELECT state,current_job_id FROM otto_processing_polls WHERE attempt_id=:attempt"
            ), {"attempt": attempt["attemptId"]}).one()
            availability = connection.execute(text(
                "SELECT action,status FROM availability_attempts WHERE operation_id=:operation ORDER BY action"
            ), {"operation": operation["operationId"]}).all()
            published_status = connection.execute(text(
                "SELECT status FROM restart_operations WHERE id=:operation"
            ), {"operation": operation["operationId"]}).scalar_one()
        self.assertEqual(tuple(state[:3]) + (state[4],), ("ACCEPTED", "AWAITING_ONLINE", "CONFIRMED", "AWAITING_ONLINE"))
        self.assertTrue(state[3])
        self.assertEqual(tuple(poll_state), ("SUCCEEDED", None))
        self.assertEqual([(row[0], row[1]) for row in availability], [
            ("DELIVERY_INFORMATION", "PREPARED"), ("QUANTITY", "PREPARED")
        ])
        self.assertEqual(published_status, "AWAITING_ONLINE")
        self.assertEqual(item_id, attempt["itemId"])

    def test_done_task_without_target_result_enters_reconciliation(self) -> None:
        operation, attempt, item_id, poll_job_id = self._accept_task_for_poll()

        class FakeClient:
            def read_update_task_href(_self, _href: str) -> dict:
                return {"state": "done", "succeeded": 0, "failed": 0, "unchanged": 0}

            def get_update_task_results(_self, _task_id: str, _result_type: str, *, max_pages: int) -> list[dict]:
                return []

        executor = create_processing_executor(
            lambda: self.engine,
            environment={"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_OTTO_READ_ENABLED": "true"},
            client_factory=lambda _account, _config: FakeClient(),
        )
        executor(SimpleNamespace(job_type="otto.poll_product_task", account="XL", id=poll_job_id,
                                 payload={"attemptId": attempt["attemptId"]}))
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT o.status,o.submission_state,i.status,i.last_error_code,p.state "
                "FROM restart_operations o JOIN restart_items i ON i.operation_id=o.id "
                "JOIN otto_processing_polls p ON p.attempt_id=:attempt WHERE o.id=:operation"
            ), {"attempt": attempt["attemptId"], "operation": operation["operationId"]}).one()
            availability_count = connection.execute(text(
                "SELECT COUNT(*) FROM availability_attempts WHERE operation_id=:operation"
            ), {"operation": operation["operationId"]}).scalar_one()
        self.assertEqual(tuple(state), (
            "RECONCILIATION_REQUIRED", "UNKNOWN", "NEEDS_REVIEW",
            "OTTO_TASK_TARGET_SKU_RESULT_MISSING_OR_DUPLICATE", "NEEDS_REVIEW",
        ))
        self.assertEqual(availability_count, 0)
        self.assertEqual(item_id, attempt["itemId"])

    def test_synchronous_success_requires_exact_per_sku_result_and_enqueues_availability(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            claim_submission_dispatch(
                connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"]
            )
        with self.engine.begin() as connection:
            result = record_submission_result(
                connection, account="XL", attempt_id=attempt["attemptId"], http_status=201,
                response={"perSkuResults": [{"sku": operation["targetSku"], "result": "SUCCEEDED"}]},
            )
        with self.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT a.status,o.status,o.submission_state,o.processing_evidence_event_id,i.status "
                "FROM submission_attempts a JOIN restart_operations o ON o.id=a.operation_id "
                "JOIN restart_items i ON i.id=a.item_id WHERE a.id=:attempt"
            ), {"attempt": attempt["attemptId"]}).one()
            poll_count = connection.execute(text(
                "SELECT COUNT(*) FROM otto_processing_polls WHERE attempt_id=:attempt"
            ), {"attempt": attempt["attemptId"]}).scalar_one()
            availability_count = connection.execute(text(
                "SELECT COUNT(*) FROM availability_attempts WHERE operation_id=:operation"
            ), {"operation": operation["operationId"]}).scalar_one()
        self.assertEqual(result["operationStatus"], "AWAITING_ONLINE")
        self.assertEqual((state[0], state[1], state[2], state[4]), (
            "ACCEPTED", "AWAITING_ONLINE", "CONFIRMED", "AWAITING_ONLINE"
        ))
        self.assertTrue(state[3])
        self.assertEqual(poll_count, 0)
        self.assertEqual(availability_count, 2)
        self.assertEqual(item_id, attempt["itemId"])

    def test_online_confirmation_requires_two_sync_availability_results_and_both_publication_proofs(self) -> None:
        operation, attempt, _item_id, poll_job_id = self._accept_task_for_poll()

        class ProcessingClient:
            def read_update_task_href(_self, _href: str) -> dict:
                return {"state": "done", "succeeded": 1, "failed": 0, "unchanged": 0}

            def get_update_task_results(_self, _task_id: str, result_type: str, *, max_pages: int) -> list[dict]:
                return [{"sku": operation["targetSku"]}] if result_type == "succeeded" else []

        environment = {"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_OTTO_READ_ENABLED": "true"}
        processing_executor = create_processing_executor(
            lambda: self.engine, environment=environment,
            client_factory=lambda _account, _config: ProcessingClient(),
        )
        processing_executor(SimpleNamespace(
            job_type="otto.poll_product_task", account="XL", id=poll_job_id,
            payload={"attemptId": attempt["attemptId"]},
        ))
        with self.engine.connect() as connection:
            attempts = connection.execute(text(
                "SELECT id,job_id,action FROM availability_attempts WHERE operation_id=:operation ORDER BY action"
            ), {"operation": operation["operationId"]}).mappings().all()
        for availability_attempt in attempts:
            with self.engine.begin() as connection:
                claim_availability_dispatch(
                    connection, account="XL", attempt_id=str(availability_attempt["id"]),
                    job_id=str(availability_attempt["job_id"]),
                )
            with self.engine.begin() as connection:
                result = record_availability_result(
                    connection, account="XL", attempt_id=str(availability_attempt["id"]),
                    http_status=204, response={},
                )
        self.assertTrue(result["onlineConfirmation"]["queued"])
        online_job_id = result["onlineConfirmation"]["jobId"]
        online_calls: list[str] = []

        class OnlineClient:
            def get_marketplace_status(_self, sku: str) -> dict:
                online_calls.append(sku)
                status = "INACTIVE" if len(online_calls) == 1 else "ONLINE"
                return {"sku": sku, "marketPlaceStatus": status, "active": status == "ONLINE", "errors": []}

        online_executor = create_processing_executor(
            lambda: self.engine, environment=environment,
            client_factory=lambda _account, _config: OnlineClient(),
        )
        online_executor(SimpleNamespace(
            job_type="otto.confirm_online", account="XL", id=online_job_id,
            payload={"operationId": operation["operationId"]},
        ))
        with self.engine.connect() as connection:
            pending_state = connection.execute(text(
                "SELECT o.status,p.state,p.poll_count,p.current_job_id "
                "FROM restart_operations o JOIN otto_online_polls p ON p.operation_id=o.id WHERE o.id=:operation"
            ), {"operation": operation["operationId"]}).one()
        self.assertEqual((pending_state[0], pending_state[1], pending_state[2]), (
            "AWAITING_ONLINE", "POLLING", 1
        ))
        online_job_id = str(pending_state[3])
        online_executor(SimpleNamespace(
            job_type="otto.confirm_online", account="XL", id=online_job_id,
            payload={"operationId": operation["operationId"]},
        ))
        self.assertEqual(online_calls, [operation["targetSku"], operation["targetSku"]])
        with self.engine.connect() as connection:
            result_state = connection.execute(text(
                "SELECT o.status,o.processing_evidence_event_id,o.online_observation_id,i.status,b.status "
                "FROM restart_operations o JOIN restart_items i ON i.operation_id=o.id "
                "JOIN restart_batches b ON b.id=i.batch_id WHERE o.id=:operation"
            ), {"operation": operation["operationId"]}).one()
            observation = connection.execute(text(
                "SELECT run_id,operation_id,requested_sku,response_sku,market_place_status "
                "FROM otto_status_observations WHERE id=:id"
            ), {"id": result_state[2]}).one()
            poll_state = connection.execute(text(
                "SELECT state FROM otto_online_polls WHERE operation_id=:operation"
            ), {"operation": operation["operationId"]}).scalar_one()
        self.assertEqual((result_state[0], result_state[3], result_state[4]), ("PUBLISHED", "PUBLISHED", "COMPLETED"))
        self.assertTrue(result_state[1])
        self.assertTrue(result_state[2])
        self.assertEqual(tuple(observation), (
            None, uuid.UUID(operation["operationId"]), operation["targetSku"], operation["targetSku"], "ONLINE"
        ))
        self.assertEqual(poll_state, "CONFIRMED")

    def test_offline_publish_chain_uses_each_worker_and_requires_exact_online_confirmation(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        submission_job = self.load_submission_job(operation["account"])
        product_calls: list[tuple[str, object]] = []

        class ProductClient:
            def get_product_prices(_self, sku: str) -> dict:
                product_calls.append(("GET_PRICE", sku))
                return {"sku": operation["sourceSku"],
                        "standardPrice": {"amount": "50.00", "currency": "EUR"}}

            def submit_product_payload(_self, payload: dict, *, expected_sku: str, expected_ean: str):
                product_calls.append(("POST_PRODUCT", (dict(payload), expected_sku, expected_ean)))
                return SimpleNamespace(
                    status_code=202,
                    response={"links": [{"rel": "self", "href":
                        "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001"}]},
                    error_code=None,
                )

        submission_executor = create_submission_executor(
            lambda: self.engine, environment=self.submission_environment(),
            client_factory=lambda _account, _config: ProductClient(),
            aftercool_lookup_factory=self.offline_aftercool_lookup_factory,
        )
        submission_executor(submission_job)
        self.assertEqual([call[0] for call in product_calls], ["GET_PRICE", "POST_PRODUCT"])
        sent_payload = product_calls[-1][1][0]
        self.assertEqual((sent_payload["sku"], sent_payload["ean"]),
                         (operation["targetSku"], operation["newEan"]))

        with self.engine.connect() as connection:
            poll = connection.execute(text(
                "SELECT current_job_id FROM otto_processing_polls WHERE attempt_id=:attempt"
            ), {"attempt": attempt["attemptId"]}).mappings().one()
        task_job_id = str(poll["current_job_id"])
        read_calls: list[tuple[str, str]] = []

        class TaskAndStatusClient:
            def read_update_task_href(_self, href: str) -> dict:
                read_calls.append(("TASK", href))
                return {"state": "done", "succeeded": 1, "failed": 0, "unchanged": 0}

            def get_update_task_results(_self, task_id: str, result_type: str, *, max_pages: int) -> list[dict]:
                self.assertLessEqual(max_pages, 50)
                if result_type == "succeeded":
                    return [{"sku": operation["targetSku"]}]
                return []

            def get_marketplace_status(_self, sku: str) -> dict:
                read_calls.append(("STATUS", sku))
                return {"sku": sku, "marketPlaceStatus": "ONLINE", "active": True, "errors": []}

        processing_executor = create_processing_executor(
            lambda: self.engine, environment={
                "OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_OTTO_READ_ENABLED": "true",
            }, client_factory=lambda _account, _config: TaskAndStatusClient(),
        )
        processing_executor(SimpleNamespace(
            job_type="otto.poll_product_task", account=operation["account"], id=task_job_id,
            payload={"attemptId": attempt["attemptId"]},
        ))

        with self.engine.connect() as connection:
            availability_jobs = connection.execute(text(
                "SELECT a.id,a.job_id,a.action,j.job_type,j.payload_json FROM availability_attempts a "
                "JOIN background_jobs j ON j.id=a.job_id AND j.account=a.account "
                "WHERE a.operation_id=:operation ORDER BY a.action"
            ), {"operation": operation["operationId"]}).mappings().all()
        self.assertEqual({row["action"] for row in availability_jobs}, {"QUANTITY", "DELIVERY_INFORMATION"})
        availability_calls: list[tuple[str, object]] = []

        class AvailabilityClient:
            def update_quantity(_self, sku: str, quantity: int):
                availability_calls.append(("QUANTITY", (sku, quantity)))
                return SimpleNamespace(status_code=204, response={}, error_code=None)

            def update_delivery_information(_self, sku: str, *, shipping_profile_id: str, processing_time: str):
                availability_calls.append(("DELIVERY_INFORMATION", (sku, shipping_profile_id, processing_time)))
                return SimpleNamespace(status_code=204, response={}, error_code=None)

        availability_executor = create_availability_executor(
            lambda: self.engine, environment={
                "OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_OTTO_READ_ENABLED": "true",
                "OTTORESTART_AVAILABILITY_WRITE_ENABLED": "true",
            }, client_factory=lambda _account, _config: AvailabilityClient(),
        )
        for job in availability_jobs:
            availability_executor(SimpleNamespace(
                job_type=job["job_type"], account=operation["account"], id=str(job["job_id"]),
                payload=dict(job["payload_json"]), lease_token=None,
            ))

        with self.engine.connect() as connection:
            online_job_id = connection.execute(text(
                "SELECT current_job_id FROM otto_online_polls WHERE operation_id=:operation"
            ), {"operation": operation["operationId"]}).scalar_one()
        processing_executor(SimpleNamespace(
            job_type="otto.confirm_online", account=operation["account"], id=str(online_job_id),
            payload={"operationId": operation["operationId"]},
        ))

        with self.engine.connect() as connection:
            final = connection.execute(text(
                "SELECT o.status,o.submission_state,o.processing_evidence_event_id,o.online_observation_id,"
                "i.status,b.status FROM restart_operations o JOIN restart_items i ON i.id=:item "
                "AND i.operation_id=o.id JOIN restart_batches b ON b.id=i.batch_id WHERE o.id=:operation"
            ), {"item": item_id, "operation": operation["operationId"]}).one()
            attempt_status = connection.execute(text(
                "SELECT status,http_status FROM submission_attempts WHERE id=:attempt"
            ), {"attempt": attempt["attemptId"]}).one()
        self.assertEqual(tuple(final[:2]), ("PUBLISHED", "CONFIRMED"))
        self.assertTrue(final[2])
        self.assertTrue(final[3])
        self.assertEqual(tuple(final[4:]), ("PUBLISHED", "COMPLETED"))
        self.assertEqual(tuple(attempt_status), ("ACCEPTED", 202))
        self.assertEqual({call[0] for call in availability_calls}, {"QUANTITY", "DELIVERY_INFORMATION"})
        self.assertEqual(read_calls[-1], ("STATUS", operation["targetSku"]))

    def test_429_is_definitive_failure_without_automatic_retry(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            claim_submission_dispatch(connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
        with self.engine.begin() as connection:
            result = record_submission_result(connection, account="XL", attempt_id=attempt["attemptId"], http_status=429)
            job_count = connection.execute(text("SELECT COUNT(*) FROM background_jobs WHERE job_type='otto.submit_product'")).scalar_one()
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["operationStatus"], "READY")
        self.assertEqual(job_count, 1)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item_id}).scalar_one(), "READY_TO_SUBMIT")
            self.assertEqual(connection.execute(text("SELECT status FROM submission_attempts WHERE id=:id"), {"id": attempt["attemptId"]}).scalar_one(), "REJECTED")
            self.assertEqual(connection.execute(text("SELECT submission_state FROM restart_operations WHERE id=:id"), {"id": operation["operationId"]}).scalar_one(), "NEVER_SUBMITTED")

    def test_transport_timeout_and_5xx_enter_reconciliation_without_releasing_gtin(self) -> None:
        operation, attempt, item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            claim_submission_dispatch(connection, account="XL", attempt_id=attempt["attemptId"], job_id=attempt["jobId"])
        with self.engine.begin() as connection:
            result = record_submission_result(
                connection, account="XL", attempt_id=attempt["attemptId"], http_status=None,
                transport_error="timeout with a secret-like payload that must not be stored",
            )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["operationStatus"], "RECONCILIATION_REQUIRED")
        with self.engine.connect() as connection:
            op = connection.execute(text("SELECT status,submission_state,new_ean FROM restart_operations WHERE id=:id"),
                                    {"id": operation["operationId"]}).one()
            pool = connection.execute(text("SELECT status,reservation_operation_id FROM ean_pool WHERE ean=:ean"),
                                      {"ean": operation["newEan"]}).one()
            item = connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item_id}).scalar_one()
            saved_code = connection.execute(text("SELECT error_code FROM submission_attempts WHERE id=:id"),
                                             {"id": attempt["attemptId"]}).scalar_one()
        self.assertEqual(tuple(op[:2]), ("RECONCILIATION_REQUIRED", "UNKNOWN"))
        self.assertEqual(pool[0], "RESERVED")
        self.assertEqual(str(pool[1]), operation["operationId"])
        self.assertEqual(item, "NEEDS_REVIEW")
        self.assertNotIn("secret", saved_code)

    def test_availability_jobs_require_confirmed_product_processing_and_verified_target_inputs(self) -> None:
        operation, attempt, _item_id = self.prepare_submission_fixture()
        with self.engine.begin() as connection:
            with self.assertRaises(AvailabilityError) as raised:
                enqueue_availability_jobs(
                    connection, account="XL", operation_id=operation["operationId"], actor="offline-operator"
                )
            self.assertEqual(raised.exception.code, "PRODUCT_PROCESSING_NOT_CONFIRMED")
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM availability_attempts")).scalar_one(), 0)

        self.confirm_processing(operation, attempt)
        with self.engine.begin() as connection:
            queued = enqueue_availability_jobs(
                connection, account="XL", operation_id=operation["operationId"], actor="offline-operator"
            )
        with self.engine.begin() as connection:
            replay = enqueue_availability_jobs(
                connection, account="XL", operation_id=operation["operationId"], actor="offline-operator"
            )
        self.assertEqual({row["action"] for row in queued["jobs"]}, {"QUANTITY", "DELIVERY_INFORMATION"})
        self.assertEqual({row["jobId"] for row in queued["jobs"]}, {row["jobId"] for row in replay["jobs"]})
        self.assertEqual({row["attemptId"] for row in queued["jobs"]}, {row["attemptId"] for row in replay["jobs"]})
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM background_jobs WHERE job_type LIKE 'otto.availability.%'")).scalar_one(), 2)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM availability_attempts")).scalar_one(), 2)

    def test_availability_attempts_are_single_dispatch_and_never_confirm_online(self) -> None:
        operation, attempt, _item_id = self.prepare_submission_fixture()
        self.confirm_processing(operation, attempt)
        with self.engine.begin() as connection:
            queued = enqueue_availability_jobs(
                connection, account="XL", operation_id=operation["operationId"], actor="offline-operator"
            )
        by_action = {row["action"]: row for row in queued["jobs"]}
        quantity = by_action["QUANTITY"]
        delivery = by_action["DELIVERY_INFORMATION"]
        with self.engine.begin() as connection:
            claimed = claim_availability_dispatch(
                connection, account="XL", attempt_id=quantity["attemptId"], job_id=quantity["jobId"]
            )
        self.assertEqual(claimed["status"], "DISPATCHING")
        self.assertEqual(claimed["payload"], {"sku": operation["targetSku"], "quantity": 0})
        with self.engine.begin() as connection:
            with self.assertRaises(AvailabilityError) as raised:
                claim_availability_dispatch(
                    connection, account="XL", attempt_id=quantity["attemptId"], job_id=quantity["jobId"]
                )
            self.assertEqual(raised.exception.code, "AVAILABILITY_DISPATCH_ALREADY_CLAIMED")
        with self.engine.begin() as connection:
            unknown = mark_interrupted_availability_unknown(
                connection, account="XL", attempt_id=quantity["attemptId"]
            )
        self.assertEqual(unknown["status"], "UNKNOWN")

        with self.engine.begin() as connection:
            delivery_claim = claim_availability_dispatch(
                connection, account="XL", attempt_id=delivery["attemptId"], job_id=delivery["jobId"]
            )
        self.assertEqual(delivery_claim["payload"]["shippingProfileId"], "38427776-7383-4d65-82c9-dbe949d0573a")
        with self.engine.begin() as connection:
            result = record_availability_result(
                connection, account="XL", attempt_id=delivery["attemptId"], http_status=202,
                response={"links": [{"rel": "self", "href": "https://api.otto.market/v1/task?access_token=secret"}],
                          "access_token": "secret-value"},
            )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertFalse(result["published"])
        calls: list[str] = []
        fake_client = SimpleNamespace(
            update_quantity=lambda *args, **kwargs: calls.append("quantity"),
            update_delivery_information=lambda *args, **kwargs: calls.append("delivery"),
        )
        executor = create_availability_executor(
            lambda: self.engine,
            environment={
                "OTTORESTART_MODE": "PRODUCTION",
                "OTTORESTART_OTTO_READ_ENABLED": "true",
                "OTTORESTART_AVAILABILITY_WRITE_ENABLED": "true",
            },
            client_factory=lambda _account, _config: fake_client,
        )
        executor(SimpleNamespace(
            job_type="otto.availability.delivery",
            account="XL",
            id=delivery["jobId"],
            payload={"action": "DELIVERY_INFORMATION", "availabilityAttemptId": delivery["attemptId"]},
        ))
        self.assertEqual(calls, [])
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT status FROM availability_attempts WHERE id=:id"),
                    {"id": delivery["attemptId"]},
                ).scalar_one(),
                "ACCEPTED",
            )
        with self.engine.connect() as connection:
            attempt_rows = connection.execute(text(
                "SELECT action,status,response_summary_json::text FROM availability_attempts ORDER BY action"
            )).all()
            operation_status = connection.execute(
                text("SELECT status FROM restart_operations WHERE id=:id"), {"id": operation["operationId"]}
            ).scalar_one()
        self.assertEqual({row[1] for row in attempt_rows}, {"UNKNOWN", "ACCEPTED"})
        self.assertNotIn("access_token", " ".join(row[2] for row in attempt_rows))
        self.assertNotIn("secret-value", " ".join(row[2] for row in attempt_rows))
        self.assertEqual(operation_status, "AWAITING_ONLINE")

    def test_stale_source_price_fails_before_creating_attempt(self) -> None:
        operation = self.make_ready_operation()
        _, batch = self.create_and_start([operation], now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        with self.engine.connect() as connection:
            item = connection.execute(text("SELECT id,scheduled_at FROM restart_items WHERE batch_id=:id"), {"id": batch["id"]}).mappings().one()
            generation = connection.execute(text("SELECT schedule_generation FROM restart_batches WHERE id=:id"), {"id": batch["id"]}).scalar_one()
        with self.engine.begin() as connection:
            release_scheduled_item(connection, account="XL", payload={"batchId": batch["id"], "itemId": str(item["id"]),
                                                                          "scheduleGeneration": generation},
                                   now=item["scheduled_at"] + timedelta(seconds=1), submit_enabled=True)
        with self.engine.connect() as connection:
            job_id = str(connection.execute(text(
                "SELECT id FROM background_jobs WHERE job_type='otto.submit_product' AND account='XL'"
            )).scalar_one())
        with self.engine.begin() as connection:
            with self.assertRaises(SubmissionError) as raised:
                begin_submission_attempt(
                    connection, account="XL", item_id=int(item["id"]), expected_operation_version=4,
                    background_job_id=job_id,
                    source_price_account="XL", source_price_response={"sku": operation["sourceSku"],
                                                                        "standardPrice": {"amount": "51.00", "currency": "EUR"}},
                    aftercool_price_cents=5000,
                    source_price_checked_at=datetime.now(timezone.utc), actor="offline-operator",
                    approved_evidence_types=SUBMISSION_EVIDENCE_TYPES,
                )
            self.assertEqual(raised.exception.code, "SOURCE_PRICE_CHANGED")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM submission_attempts")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT status FROM restart_operations WHERE id=:id"),
                                                {"id": operation["operationId"]}).scalar_one(), "READY")

    def test_normalized_response_classifier_fails_closed_for_missing_sku_result_and_untrusted_href(self) -> None:
        missing = classify_submission_response(http_status=200, target_sku="target-1", response={})
        self.assertEqual((missing["status"], missing["errorCode"]), ("UNKNOWN", "PER_SKU_RESULT_REQUIRED"))
        failed = classify_submission_response(http_status=200, target_sku="target-1", response={
            "perSkuResults": [{"sku": "target-1", "result": "FAILED", "errors": [
                {"code": "602016", "title": "Not accepted; bearer secret-value"},
            ]}],
        })
        self.assertEqual(failed["status"], "REJECTED")
        self.assertNotIn("secret-value", failed["perSkuResults"][0]["errors"][0]["title"])
        accepted = classify_submission_response(http_status=202, target_sku="target-1", response={
            "links": [{"rel": "self", "href": "https://attacker.invalid/v5/products/update-tasks/00000000-0000-4000-8000-000000000001"}],
        })
        self.assertEqual((accepted["status"], accepted["errorCode"]), ("ACCEPTED", "TASK_LINK_UNTRUSTED"))
        self.assertEqual(accepted["taskLinks"], [])
        self.assertEqual(classify_submission_response(http_status=503, target_sku="target-1")["status"], "UNKNOWN")

    def test_pause_resume_cancel_and_stale_generation_are_idempotent(self) -> None:
        operation = self.make_ready_operation()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _, started = self.create_and_start([operation], now=now)
        with self.engine.begin() as connection:
            paused = control_batch(connection, account="XL", batch_id=started["id"], action="pause",
                                   expected_version=started["version"], actor="offline-operator", now=now)
        self.assertEqual(paused["status"], "PAUSED")
        with self.engine.connect() as connection:
            item = connection.execute(text("SELECT id,status FROM restart_items WHERE batch_id=:id"), {"id": started["id"]}).mappings().one()
            old_job = connection.execute(text("SELECT payload_json,state FROM background_jobs WHERE job_type='drip.release_item'" )).mappings().one()
            old_payload = dict(old_job["payload_json"])
            self.assertEqual(old_job["state"], "CANCELLED")
        with self.engine.begin() as connection:
            ignored = release_scheduled_item(connection, account="XL", payload=old_payload, now=now + timedelta(days=10))
        self.assertEqual(ignored["code"], "STALE_SCHEDULE_GENERATION")
        with self.engine.begin() as connection:
            resumed = control_batch(connection, account="XL", batch_id=started["id"], action="resume",
                                    expected_version=paused["version"], actor="offline-operator", now=now)
        self.assertEqual(resumed["status"], "SCHEDULED")
        with self.engine.connect() as connection:
            generations = connection.execute(text(
                "SELECT payload_json->>'scheduleGeneration' FROM background_jobs WHERE job_type='drip.release_item' AND state='PENDING'"
            )).scalars().all()
        self.assertEqual(generations, ["3"])
        with self.engine.begin() as connection:
            cancelled = control_batch(connection, account="XL", batch_id=started["id"], action="cancel",
                                      expected_version=resumed["version"], actor="offline-operator", now=now)
        self.assertEqual(cancelled["status"], "CANCELLED")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item["id"]}).scalar_one(), "CANCELLED")
            self.assertEqual(connection.execute(text("SELECT status FROM restart_operations WHERE id=:id"), {"id": operation["operationId"]}).scalar_one(), "READY")

    def test_release_marks_stale_revision_for_review_and_does_not_repeat(self) -> None:
        operation = self.make_ready_operation()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _, batch = self.create_and_start([operation], now=now)
        with self.engine.connect() as connection:
            item = connection.execute(text("SELECT id,scheduled_at FROM restart_items WHERE batch_id=:id"), {"id": batch["id"]}).mappings().one()
            payload = connection.execute(text("SELECT payload_json FROM background_jobs WHERE job_type='drip.release_item'" )).scalar_one()
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE restart_operations SET current_revision_number=2 WHERE id=:id"), {"id": operation["operationId"]})
        with self.engine.begin() as connection:
            result = release_scheduled_item(connection, account="XL", payload=dict(payload), now=item["scheduled_at"] + timedelta(seconds=1))
        self.assertEqual(result["code"], "STALE_QC_OR_REVISION")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM restart_items WHERE id=:id"), {"id": item["id"]}).scalar_one(), "NEEDS_REVIEW")

    def test_mixed_period_or_factory_and_unready_qc_are_rejected(self) -> None:
        first = self.make_ready_operation(period="3M", factory_id="factory-a")
        second = self.make_ready_operation(period="12M", factory_id="factory-b")
        with self.engine.connect() as connection:
            with self.assertRaises(DripError) as raised:
                preview_schedule(connection, account="XL", now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 **self.schedule_input([first, second]))
        self.assertEqual(raised.exception.code, "MIXED_COHORT")
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE restart_operations SET status='NEEDS_REVIEW' WHERE id=:id"), {"id": first["operationId"]})
        with self.engine.connect() as connection:
            with self.assertRaises(DripError) as raised:
                preview_schedule(connection, account="XL", now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 **self.schedule_input([first]))
        self.assertEqual(raised.exception.code, "OPERATIONS_NOT_SCHEDULABLE")

    def test_pages_are_account_and_scope_bound(self) -> None:
        operation = self.make_ready_operation()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        _, batch = self.create_and_start([operation], now=now)
        with self.engine.connect() as connection:
            self.assertEqual(list_batches(connection, account="XL")["page"]["totalItems"], 1)
            self.assertEqual(list_batches(connection, account="JV")["page"]["totalItems"], 0)
            self.assertEqual(list_batch_items(connection, account="XL", batch_id=batch["id"])["items"][0]["status"], "SCHEDULED")
            self.assertEqual(list_batch_items(connection, account="JV", batch_id=batch["id"])["page"]["totalItems"], 0)

    def test_daylight_saving_gaps_and_ambiguities_are_not_guessed(self) -> None:
        with self.assertRaises(DripError) as missing:
            _resolve_local_datetime(date(2026, 3, 29), time(2, 30), "Europe/Berlin")
        self.assertEqual(missing.exception.code, "SCHEDULE_TIME_NONEXISTENT")
        with self.assertRaises(DripError) as repeated:
            _resolve_local_datetime(date(2026, 10, 25), time(2, 30), "Europe/Berlin")
        self.assertEqual(repeated.exception.code, "SCHEDULE_TIME_AMBIGUOUS")

    def test_worker_retries_if_its_clock_says_a_dispatched_item_is_not_due(self) -> None:
        payload = {
            "batchId": str(uuid.uuid4()), "itemId": "1", "operationId": str(uuid.uuid4()),
            "revisionId": str(uuid.uuid4()), "qcResultId": str(uuid.uuid4()),
            "payloadHash": "0" * 64, "scheduleGeneration": 1,
        }
        job = SimpleNamespace(job_type="drip.release_item", payload=payload, account="XL")
        executor = create_drip_release_executor(lambda: self.engine)
        with patch("jobs.drip_handler.release_scheduled_item", return_value={"released": False, "code": "NOT_DUE"}):
            with self.assertRaises(JobExecutionError) as raised:
                executor(job)
        self.assertEqual(raised.exception.code, "DRIP_ITEM_NOT_DUE")
        self.assertTrue(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()

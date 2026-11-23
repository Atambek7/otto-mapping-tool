from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from integrations.otto_client import OttoApiError
from jobs.service import BackgroundJob, claim_job_by_id
from migration_runner import upgrade
from services.discovery_scan_service import (
    OVERLAP_SECONDS,
    DiscoveryScanError,
    create_incremental_scan,
    create_known_sku_reconciliation,
)
from services.discovery_service import StatusResponseInput, apply_staged_run, create_import_run, stage_status_responses
from jobs.discovery_handler import create_discovery_executor


class FakeOttoClient:
    def __init__(self, pages=(), *, statuses=None):
        self.pages = list(pages)
        self.statuses = dict(statuses or {})
        self.filters = None
        self.max_pages = None
        self.requested_skus: list[str] = []

    def iter_marketplace_status_pages(self, filters, *, max_pages):
        self.filters = dict(filters)
        self.max_pages = max_pages
        for page in self.pages:
            if isinstance(page, BaseException):
                raise page
            yield page

    def get_marketplace_status(self, sku):
        self.requested_skus.append(sku)
        value = self.statuses[sku]
        if isinstance(value, BaseException):
            raise value
        return value


def _job_from_id(engine, job_id: str) -> BackgroundJob:
    with engine.begin() as connection:
        job = claim_job_by_id(connection, job_id=job_id, worker_id="discovery-test-worker")
    assert job is not None
    return job


def _status(sku: str, *, restricted: bool = False, last_modified: str = "2026-11-02T10:00:00Z") -> dict:
    return {
        "sku": sku,
        "status": "REJECTED" if restricted else "ONLINE",
        "marketPlaceStatus": "REJECTED" if restricted else "ONLINE",
        "lastModified": last_modified,
        "errors": ([{"code": "602016 - Nonseller", "title": "No Nettoumsatz for at least three months."}] if restricted else []),
    }


class DiscoveryScanPostgresTests(unittest.TestCase):
    """All DB tests use a new PostgreSQL 15 database on an explicit loopback test DSN."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for discovery scan PostgreSQL tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql":
            raise RuntimeError("OTTORESTART_TEST_DATABASE_URL must use PostgreSQL.")
        if cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Discovery tests accept a loopback PostgreSQL server only.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        app_url = os.environ.get("OTTORESTART_DATABASE_URL", "").strip()
        if app_url and make_url(app_url) == cls.base_url:
            raise RuntimeError("Test and application database URLs must be different.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Discovery integration tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        engine = getattr(cls, "admin_engine", None)
        if engine is not None:
            engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_discovery_test_{uuid.uuid4().hex}"
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

    def _create_scan(self, *, account="XL", from_date=None, key=None, now=None):
        current = now or datetime.now(timezone.utc)
        start = from_date or current - timedelta(days=1)
        with self.engine.begin() as connection:
            return create_incremental_scan(
                connection,
                account=account,
                actor="operator-test",
                idempotency_key=key or f"scan-key-{uuid.uuid4().hex}",
                from_date=start,
                request_body={"account": account, "fromDate": start.isoformat()},
                now=current,
            )

    def _execute_scan(self, accepted: dict, client: FakeOttoClient):
        job = _job_from_id(self.engine, accepted["jobId"])
        executor = create_discovery_executor(
            lambda: self.engine,
            environment={"OTTORESTART_MODE": "TEST", "OTTORESTART_OTTO_READ_ENABLED": "true"},
            otto_client_factory=lambda account: client,
        )
        return job, executor

    def _run_row(self, run_id: str, account: str = "XL") -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM import_runs WHERE id=:id AND account=:account"),
                {"id": run_id, "account": account},
            ).mappings().one()
        return dict(row)

    def test_initial_window_is_explicit_bounded_and_active_run_is_account_scoped(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        with self.assertRaises(DiscoveryScanError) as raised:
            with self.engine.begin() as connection:
                create_incremental_scan(
                    connection, account="XL", actor="operator-test", idempotency_key="first-scan-key-0001",
                    from_date=None, request_body={"account": "XL"}, now=now,
                )
        self.assertEqual(raised.exception.code, "INITIAL_FROM_DATE_REQUIRED")

        with self.assertRaises(DiscoveryScanError) as raised:
            self._create_scan(from_date=now - timedelta(days=17), key="too-wide-window-key-01", now=now)
        self.assertEqual(raised.exception.code, "INITIAL_WINDOW_TOO_WIDE")

        accepted = self._create_scan(from_date=now - timedelta(days=5), key="valid-window-key-0001", now=now)
        duplicate = self._create_scan(from_date=now - timedelta(days=5), key="valid-window-key-0001", now=now)
        self.assertEqual(accepted["runId"], duplicate["runId"])
        with self.assertRaises(DiscoveryScanError) as raised:
            self._create_scan(from_date=now - timedelta(days=1), key="another-run-key-00001", now=now)
        self.assertEqual(raised.exception.code, "SCAN_ALREADY_ACTIVE")
        jv = self._create_scan(account="JV", from_date=now - timedelta(days=1), key="jv-run-key-000000001", now=now)
        self.assertEqual(jv["account"], "JV")

    def test_terminal_worker_job_closes_stuck_run_before_next_attempt(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        accepted = self._create_scan(from_date=now - timedelta(days=1), key="stuck-run-key-000000001", now=now)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE background_jobs SET state='DEAD',completed_at=CURRENT_TIMESTAMP "
                     "WHERE id=:job_id AND account='XL' AND data_scope='PRODUCTION'"),
                {"job_id": accepted["jobId"]},
            )
        next_scan = self._create_scan(
            from_date=now - timedelta(days=2), key="after-stuck-run-key-00001", now=now
        )
        self.assertNotEqual(next_scan["runId"], accepted["runId"])
        old_run = self._run_row(accepted["runId"])
        self.assertEqual(old_run["run_state"], "PARTIAL")
        self.assertEqual(old_run["error_json"][-1]["code"], "DISCOVERY_JOB_TERMINAL")

    def test_paginated_scan_stages_all_statuses_and_promotes_cursor_atomically(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        accepted = self._create_scan(from_date=now - timedelta(days=1), now=now)
        client = FakeOttoClient(pages=[[_status("sku-restricted", restricted=True)], [_status("sku-online")]])
        job, executor = self._execute_scan(accepted, client)
        executor(job)

        run = self._run_row(accepted["runId"])
        self.assertEqual(run["run_state"], "SUCCEEDED")
        self.assertEqual(run["total_rows"], 2)
        self.assertEqual(run["coverage_json"]["pagesCompleted"], 2)
        self.assertTrue(run["coverage_json"]["cursorPromoted"])
        self.assertEqual(client.filters["fromDate"], accepted["windowFrom"])
        self.assertNotIn("marketPlaceStatus", client.filters)
        self.assertEqual(client.max_pages, 100)
        with self.engine.connect() as connection:
            cursor = connection.execute(
                text("SELECT last_successful_cutoff,last_successful_run_id FROM discovery_cursors WHERE account='XL' AND data_scope='PRODUCTION'")
            ).mappings().one()
            observations = connection.execute(
                text("SELECT requested_sku FROM otto_status_observations WHERE run_id=:run_id ORDER BY requested_sku"),
                {"run_id": accepted["runId"]},
            ).scalars().all()
            episodes = connection.execute(
                text("SELECT observed_sku,period FROM candidate_episodes WHERE account='XL' AND data_scope='PRODUCTION'")
            ).all()
        self.assertEqual(str(cursor["last_successful_run_id"]), accepted["runId"])
        self.assertEqual(observations, ["sku-online", "sku-restricted"])
        self.assertEqual([(row[0], row[1]) for row in episodes], [("sku-restricted", "3M")])

    def test_baseline_restriction_without_resolved_source_stays_pending_route(self):
        with self.engine.begin() as connection:
            run_id = create_import_run(connection, "JV", "FILE_IMPORT", "BASELINE")
            stage_status_responses(
                connection,
                "JV",
                run_id,
                [StatusResponseInput("ean-as-sku", 200, _status("ean-as-sku", restricted=True))],
            )
            summary = apply_staged_run(connection, "JV", run_id)

        self.assertEqual(summary["opened"], 1)
        with self.engine.connect() as connection:
            episode = connection.execute(
                text("SELECT source_product_id, routing_state, period FROM candidate_episodes "
                     "WHERE account='JV' AND observed_sku='ean-as-sku' AND data_scope='PRODUCTION'")
            ).mappings().one()
        self.assertIsNone(episode["source_product_id"])
        self.assertEqual((episode["routing_state"], episode["period"]), ("PENDING", "3M"))

    def test_empty_successful_window_promotes_cutoff(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        accepted = self._create_scan(from_date=now - timedelta(days=1), now=now)
        client = FakeOttoClient(pages=[])
        job, executor = self._execute_scan(accepted, client)
        executor(job)
        run = self._run_row(accepted["runId"])
        self.assertEqual(run["run_state"], "SUCCEEDED")
        self.assertEqual(run["total_rows"], 0)
        self.assertTrue(run["coverage_json"]["cursorPromoted"])
        with self.engine.connect() as connection:
            cutoff = connection.execute(text("SELECT last_successful_cutoff FROM discovery_cursors WHERE account='XL'")).scalar_one()
        self.assertEqual(cutoff, datetime.fromisoformat(accepted["cutoffAt"].replace("Z", "+00:00")))

    def test_retryable_429_keeps_staged_data_and_does_not_move_cursor(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        first = self._create_scan(from_date=now - timedelta(days=1), now=now)
        initial_client = FakeOttoClient(pages=[[_status("sku-existing", restricted=True)]])
        initial_job, executor = self._execute_scan(first, initial_client)
        executor(initial_job)
        with self.engine.connect() as connection:
            prior_cursor = connection.execute(text("SELECT last_successful_cutoff FROM discovery_cursors WHERE account='XL'")).scalar_one()

        expected_from = prior_cursor - timedelta(seconds=OVERLAP_SECONDS)
        with self.assertRaises(DiscoveryScanError) as raised:
            self._create_scan(from_date=prior_cursor, key="widen-cursor-window-key", now=now + timedelta(days=1))
        self.assertEqual(raised.exception.code, "FROM_DATE_MANAGED_BY_CURSOR")
        second = self._create_scan(from_date=expected_from, now=now + timedelta(days=1))
        failing_client = FakeOttoClient(pages=[[_status("sku-new", restricted=True)], OttoApiError("HTTP_429", status_code=429, retryable=True, retry_after_seconds=37)])
        second_job, retrying_executor = self._execute_scan(second, failing_client)
        with self.assertRaises(Exception) as raised:
            retrying_executor(second_job)
        self.assertEqual(getattr(raised.exception, "code", None), "HTTP_429")
        self.assertEqual(getattr(raised.exception, "retry_delay_seconds", None), 37)
        run = self._run_row(second["runId"])
        self.assertEqual(run["run_state"], "STAGED")
        self.assertEqual(run["total_rows"], 1)
        with self.engine.connect() as connection:
            cursor = connection.execute(text("SELECT last_successful_cutoff FROM discovery_cursors WHERE account='XL'")).scalar_one()
            episode_skus = connection.execute(text("SELECT observed_sku FROM candidate_episodes WHERE account='XL' AND data_scope='PRODUCTION'")).scalars().all()
        self.assertEqual(cursor, prior_cursor)
        self.assertEqual(episode_skus, ["sku-existing"])

    def test_invalid_row_after_a_page_makes_run_partial_without_reducer_or_cursor(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        accepted = self._create_scan(from_date=now - timedelta(days=1), now=now)
        client = FakeOttoClient(pages=[[_status("sku-staged", restricted=True)], [{"status": "ONLINE", "errors": []}]])
        job, executor = self._execute_scan(accepted, client)
        with self.assertRaises(Exception) as raised:
            executor(job)
        self.assertEqual(getattr(raised.exception, "code", None), "INVALID_MARKETPLACE_STATUS_ROW")
        run = self._run_row(accepted["runId"])
        self.assertEqual(run["run_state"], "PARTIAL")
        with self.engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM otto_status_observations WHERE run_id=:id"), {"id": accepted["runId"]}).scalar_one()
            episodes = connection.execute(text("SELECT COUNT(*) FROM candidate_episodes WHERE account='XL' AND data_scope='PRODUCTION'")).scalar_one()
            cursor_count = connection.execute(text("SELECT COUNT(*) FROM discovery_cursors WHERE account='XL' AND last_successful_cutoff IS NOT NULL")).scalar_one()
        self.assertEqual(count, 1)
        self.assertEqual(episodes, 0)
        self.assertEqual(cursor_count, 0)

    def test_restriction_on_confirmed_new_target_sku_keeps_source_lineage(self):
        source_product_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            baseline_id = create_import_run(connection, "XL", "FILE_IMPORT", "BASELINE")
            connection.execute(
                text(
                    "INSERT INTO source_products (id,account,data_scope,source_type,aftercool_product_id,aftercool_ean,"
                    "factory_id,factory_name,original_title) VALUES (:id,'XL','PRODUCTION','AFTERCOOL',"
                    "'source-product-test','4069424000001','factory-test','Factory test','Source product test')"
                ),
                {"id": source_product_id},
            )
            stage_status_responses(
                connection,
                "XL",
                baseline_id,
                [StatusResponseInput("old-sku", 200, _status("old-sku", restricted=True), source_product_id, "4069424000001")],
            )
            apply_staged_run(connection, "XL", baseline_id)
            episode_id = connection.execute(
                text("SELECT id FROM candidate_episodes WHERE account='XL' AND observed_sku='old-sku' AND data_scope='PRODUCTION'")
            ).scalar_one()
            operation_id = str(uuid.uuid4())
            event_id = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO ean_pool (ean,account,status,data_scope,eligibility_status,quarantine_reason) "
                    "VALUES ('4999999999996','XL','QUARANTINED','PRODUCTION','UNVERIFIED','isolated scanner fixture')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO restart_events (id,account,event_type,entity_type,entity_id,summary,metadata_json,data_scope) "
                    "VALUES (:event,'XL','OTTO_PROCESSING_RESULT','RESTART_OPERATION',:operation,'Test processing evidence', '{}'::jsonb,'PRODUCTION')"
                ),
                {"event": event_id, "operation": operation_id},
            )
            connection.execute(
                text(
                    "INSERT INTO restart_operations (id,account,data_scope,source_product_id,candidate_episode_id,attempt_number,"
                    "created_by,period,status,new_ean,target_sku,submission_state,processing_evidence_event_id) "
                    "VALUES (:id,'XL','PRODUCTION',:source,:episode,1,'operator-test','3M','FAILED','4999999999996',"
                    "'new-sku','CONFIRMED',:event)"
                ),
                {"id": operation_id, "source": source_product_id, "episode": episode_id, "event": event_id},
            )
            connection.execute(
                text(
                    "INSERT INTO ean_links (account,data_scope,source_product_id,source_ean,new_ean,status,operation_id) "
                    "VALUES ('XL','PRODUCTION',:source,'4069424000001','4999999999996','PUBLISHED',:operation)"
                ),
                {"source": source_product_id, "operation": operation_id},
            )

        now = datetime.now(timezone.utc)
        accepted = self._create_scan(from_date=now - timedelta(days=1), now=now)
        client = FakeOttoClient(pages=[[_status("new-sku", restricted=True)]])
        job, executor = self._execute_scan(accepted, client)
        executor(job)
        with self.engine.connect() as connection:
            episode = connection.execute(
                text("SELECT source_product_id,confirmed_ean,period,origin FROM candidate_episodes "
                     "WHERE account='XL' AND observed_sku='new-sku' AND data_scope='PRODUCTION'")
            ).mappings().one()
        self.assertEqual(str(episode["source_product_id"]), source_product_id)
        self.assertEqual(episode["confirmed_ean"], "4069424000001")
        self.assertEqual((episode["period"], episode["origin"]), ("3M", "NEW"))

    def test_known_reconciliation_is_limited_to_open_candidates_and_never_advances_cursor(self):
        with self.engine.begin() as connection:
            baseline_id = create_import_run(connection, "XL", "FILE_IMPORT", "BASELINE")
            stage_status_responses(connection, "XL", baseline_id, [
                StatusResponseInput("known-sku", 200, _status("known-sku", restricted=True)),
            ])
            apply_staged_run(connection, "XL", baseline_id)
        with self.assertRaises(DiscoveryScanError) as raised:
            with self.engine.begin() as connection:
                create_known_sku_reconciliation(
                    connection, account="XL", actor="operator-test", idempotency_key="unknown-sku-key-0001",
                    skus=["not-known"], request_body={"account": "XL", "skus": ["not-known"]},
                )
        self.assertEqual(raised.exception.code, "UNKNOWN_OR_CLOSED_SKU")
        with self.engine.begin() as connection:
            accepted = create_known_sku_reconciliation(
                connection, account="XL", actor="operator-test", idempotency_key="known-sku-key-000001",
                skus=["known-sku"], request_body={"account": "XL", "skus": ["known-sku"]},
            )
        client = FakeOttoClient(statuses={"known-sku": _status("known-sku", restricted=False, last_modified="2026-11-03T00:00:00Z")})
        job, executor = self._execute_scan(accepted, client)
        executor(job)
        self.assertEqual(client.requested_skus, ["known-sku"])
        run = self._run_row(accepted["runId"])
        self.assertEqual(run["run_state"], "SUCCEEDED")
        with self.engine.connect() as connection:
            state = connection.execute(text("SELECT episode_state FROM candidate_episodes WHERE account='XL' AND observed_sku='known-sku' AND data_scope='PRODUCTION'")).scalar_one()
            cursor = connection.execute(text("SELECT COUNT(*) FROM discovery_cursors WHERE account='XL' AND last_successful_cutoff IS NOT NULL")).scalar_one()
        self.assertEqual(state, "CLOSED")
        self.assertEqual(cursor, 0)

    def test_reconciliation_partial_fetch_does_not_apply_early_skus(self):
        with self.engine.begin() as connection:
            baseline_id = create_import_run(connection, "XL", "FILE_IMPORT", "BASELINE")
            stage_status_responses(connection, "XL", baseline_id, [
                StatusResponseInput("first-sku", 200, _status("first-sku", restricted=True)),
                StatusResponseInput("second-sku", 200, _status("second-sku", restricted=True)),
            ])
            apply_staged_run(connection, "XL", baseline_id)
            accepted = create_known_sku_reconciliation(
                connection, account="XL", actor="operator-test", idempotency_key="partial-recon-key-001",
                skus=["first-sku", "second-sku"], request_body={"account": "XL", "skus": ["first-sku", "second-sku"]},
            )
        client = FakeOttoClient(statuses={
            "first-sku": _status("first-sku", restricted=False, last_modified="2026-11-03T00:00:00Z"),
            "second-sku": OttoApiError("HTTP_429", status_code=429, retryable=True, retry_after_seconds=11),
        })
        job, executor = self._execute_scan(accepted, client)
        with self.assertRaises(Exception) as raised:
            executor(job)
        self.assertEqual(getattr(raised.exception, "code", None), "HTTP_429")
        run = self._run_row(accepted["runId"])
        self.assertEqual(run["run_state"], "STAGED")
        self.assertEqual(run["total_rows"], 1)
        with self.engine.connect() as connection:
            open_count = connection.execute(text("SELECT COUNT(*) FROM candidate_episodes WHERE account='XL' AND data_scope='PRODUCTION' AND episode_state='OPEN'")).scalar_one()
            cursor_count = connection.execute(text("SELECT COUNT(*) FROM discovery_cursors WHERE account='XL' AND last_successful_cutoff IS NOT NULL")).scalar_one()
        self.assertEqual(open_count, 2)
        self.assertEqual(cursor_count, 0)


if __name__ == "__main__":
    unittest.main()

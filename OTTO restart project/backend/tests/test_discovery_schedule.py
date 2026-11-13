from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from migration_runner import upgrade
from services.discovery_schedule_service import (
    DiscoveryScheduleError,
    confirm_baseline,
    enqueue_due_scans,
    get_schedule,
    next_weekly_slot,
    update_schedule,
)
from services.discovery_service import create_import_run


class DiscoveryScheduleUnitTests(unittest.TestCase):
    def test_next_weekly_slot_uses_iana_timezone_and_future_wall_time(self) -> None:
        now = datetime(2026, 11, 9, 2, 30, tzinfo=timezone.utc)  # Monday 03:30 CET
        slot = next_weekly_slot(now, weekday=0, local_time=time(3, 0), timezone_name="Europe/Berlin")
        self.assertEqual(slot, datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc))

    def test_invalid_or_nonexistent_local_slot_is_rejected(self) -> None:
        with self.assertRaises(DiscoveryScheduleError) as invalid_zone:
            next_weekly_slot(datetime.now(timezone.utc), timezone_name="Not/AZone")
        self.assertEqual(invalid_zone.exception.code, "INVALID_TIMEZONE")
        with self.assertRaises(DiscoveryScheduleError) as gap:
            next_weekly_slot(
                datetime(2026, 3, 20, tzinfo=timezone.utc),
                weekday=6,
                local_time=time(2, 30),
                timezone_name="Europe/Berlin",
            )
        self.assertEqual(gap.exception.code, "NONEXISTENT_LOCAL_TIME")


class DiscoverySchedulePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for isolated discovery schedule tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Schedule tests require a loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("Schedule tests require a disposable database name containing 'test'.")
        configured_app_url = os.environ.get("OTTORESTART_DATABASE_URL", "").strip()
        if configured_app_url and make_url(configured_app_url) == cls.base_url:
            raise RuntimeError("Application and schedule test database URLs must be different.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Schedule tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        engine = getattr(cls, "admin_engine", None)
        if engine is not None:
            engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_schedule_test_{uuid.uuid4().hex}"
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

    def _make_verified_baseline(self, *, now: datetime) -> str:
        with self.engine.begin() as connection:
            run_id = create_import_run(
                connection,
                "XL",
                "FILE_IMPORT",
                "BASELINE",
                source_filename="baseline.txt",
                cutoff_at=now,
                parameters={"test": True},
            )
            connection.execute(
                text(
                    "INSERT INTO import_rows (run_id,account,data_scope,row_number,raw_identifier,normalized_identifier,"
                    "row_state,row_json,errors_json) VALUES (:run_id,'XL','PRODUCTION',1,'sku-3m','sku-3m','VALID',"
                    "CAST(:row AS jsonb),'[]'::jsonb)"
                ),
                {
                    "run_id": run_id,
                    "row": '{"sku":"sku-3m","confirmedSku":"sku-3m","classification":"EXACT_CODE",'
                    '"ottoStatus":{"sku":"sku-3m","errors":[{"code":"602016 - Nonseller"}]}}',
                },
            )
            connection.execute(
                text("UPDATE import_runs SET run_state='SUCCEEDED',status='COMPLETED',finished_at=:now WHERE id=:id"),
                {"now": now, "id": run_id},
            )
        return run_id

    def test_baseline_is_required_and_schedule_is_disabled_until_explicit_enable(self) -> None:
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        with self.engine.begin() as connection:
            initial = get_schedule(connection, account="XL")
            self.assertFalse(initial["enabled"])
            with self.assertRaises(DiscoveryScheduleError) as missing:
                update_schedule(connection, account="XL", enabled=True, now=now)
            self.assertEqual(missing.exception.code, "BASELINE_CONFIRMATION_REQUIRED")

        run_id = self._make_verified_baseline(now=now)
        with self.engine.begin() as connection:
            confirmed = confirm_baseline(connection, account="XL", run_id=run_id, actor="operator-1", now=now)
            schedule = get_schedule(connection, account="XL")
        self.assertEqual(confirmed["verifiedRows"], 1)
        self.assertEqual(schedule["baselineRunId"], run_id)
        self.assertFalse(schedule["enabled"])

        with self.engine.begin() as connection:
            enabled = update_schedule(
                connection,
                account="XL",
                enabled=True,
                weekday=0,
                local_time=time(3, 0),
                timezone_name="Europe/Berlin",
                now=now,
            )
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["timezone"], "Europe/Berlin")
        self.assertIsNotNone(enabled["nextAt"])

    def test_weekly_due_tick_enqueues_one_bounded_scan_and_advances_one_slot(self) -> None:
        now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        run_id = self._make_verified_baseline(now=now)
        with self.engine.begin() as connection:
            confirm_baseline(connection, account="XL", run_id=run_id, actor="operator-1", now=now)
            update_schedule(
                connection,
                account="XL",
                enabled=True,
                weekday=0,
                local_time=time(3, 0),
                timezone_name="Europe/Berlin",
                now=now,
            )
            scheduled_at = connection.execute(
                text("SELECT schedule_next_at FROM discovery_cursors WHERE account='XL' AND data_scope='PRODUCTION'")
            ).scalar_one()

        result = enqueue_due_scans(self.engine, now=scheduled_at + timedelta(minutes=1))
        self.assertEqual(len(result), 1)
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT run_type,run_state,window_from FROM import_runs WHERE account='XL' AND data_scope='PRODUCTION' ORDER BY created_at")
            ).mappings().all()
            jobs = connection.execute(
                text("SELECT job_type,payload_json FROM background_jobs WHERE account='XL' AND data_scope='PRODUCTION'")
            ).mappings().all()
            next_slot = connection.execute(
                text("SELECT schedule_next_at FROM discovery_cursors WHERE account='XL' AND data_scope='PRODUCTION'")
            ).scalar_one()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["run_type"], "INCREMENTAL_SCAN")
        self.assertEqual(rows[-1]["run_state"], "PENDING")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_type"], "discovery.scan")
        self.assertGreater(next_slot, scheduled_at)
        self.assertEqual(enqueue_due_scans(self.engine, now=scheduled_at + timedelta(minutes=2)), [])


if __name__ == "__main__":
    unittest.main()

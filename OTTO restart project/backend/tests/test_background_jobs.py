from __future__ import annotations

import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from jobs.service import (
    IdempotencyConflict,
    InvalidJobState,
    JobExecutionError,
    LeaseLost,
    claim_job_dispatches,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
    list_dead_jobs,
    mark_job_dispatched,
    reap_expired_leases,
    replay_dead_job,
    run_next_job,
)
from migration_runner import upgrade
from jobs.dispatcher import dispatch_once
from jobs.service import run_job_by_id
from jobs.transport import JobPublishError


class BackgroundJobsPostgresTests(unittest.TestCase):
    """Use an explicitly configured loopback PG15 server and a fresh throwaway DB."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for PostgreSQL job tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql":
            raise RuntimeError("OTTORESTART_TEST_DATABASE_URL must use PostgreSQL.")
        if cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Job tests accept a loopback PostgreSQL server only.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        app_url = os.environ.get("OTTORESTART_DATABASE_URL", "").strip()
        if app_url and make_url(app_url) == cls.base_url:
            raise RuntimeError("Test and application database URLs must be different.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            cls.admin_engine.dispose()
            raise RuntimeError("Job integration tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        engine = getattr(cls, "admin_engine", None)
        if engine is not None:
            engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_jobs_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.database_url: URL = self.base_url.set(database=self.database_name)
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        upgrade(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def enqueue(self, account="XL", key="job-idempotency-0001", payload=None, max_attempts=5, job_type="test.noop", now=None):
        with self.engine.begin() as connection:
            return enqueue_job(
                connection,
                account=account,
                job_type=job_type,
                idempotency_key=key,
                payload=payload or {"value": 1},
                max_attempts=max_attempts,
                now=now,
            )

    def test_enqueue_is_transactional_idempotent_and_payload_bound(self):
        with self.assertRaises(RuntimeError):
            with self.engine.begin() as connection:
                enqueue_job(
                    connection,
                    account="XL",
                    job_type="test.noop",
                    idempotency_key="rollback-job-key-001",
                    payload={"value": 1},
                )
                raise RuntimeError("force rollback")
        with self.engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM background_jobs")).scalar_one()
        self.assertEqual(count, 0)

        first = self.enqueue()
        duplicate = self.enqueue()
        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(first.job.id, duplicate.job.id)
        with self.assertRaises(IdempotencyConflict):
            self.enqueue(payload={"value": 2})
        different_account = self.enqueue(account="JV")
        self.assertNotEqual(first.job.id, different_account.job.id)

    def test_concurrent_claims_do_not_deliver_one_job_twice(self):
        self.enqueue()
        barrier = threading.Barrier(2)

        def claim(worker_id):
            with self.engine.begin() as connection:
                job = claim_next_job(connection, worker_id=worker_id)
                barrier.wait(timeout=5)
                return job

        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(claim, ["worker-a", "worker-b"]))
        self.assertEqual(sum(job is not None for job in claimed), 1)
        job = next(job for job in claimed if job is not None)
        self.assertEqual(job.state, "RUNNING")
        with self.engine.begin() as connection:
            self.assertIsNone(claim_next_job(connection, worker_id="worker-c"))

    def test_retry_dead_letter_manual_replay_and_attempt_history(self):
        created = self.enqueue(max_attempts=2)
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            first = claim_next_job(connection, worker_id="worker-a", now=now)
        assert first is not None
        with self.engine.begin() as connection:
            retry = fail_job(
                connection,
                job_id=first.id,
                account="XL",
                lease_token=first.lease_token or "",
                error_code="TEMPORARY_FAILURE",
                now=now + timedelta(seconds=1),
            )
        self.assertEqual(retry.state, "RETRY_WAIT")

        with self.engine.begin() as connection:
            second = claim_next_job(connection, worker_id="worker-b", now=now + timedelta(seconds=3))
        assert second is not None
        with self.engine.begin() as connection:
            dead = fail_job(
                connection,
                job_id=second.id,
                account="XL",
                lease_token=second.lease_token or "",
                error_code="REPEATED_FAILURE",
                now=now + timedelta(seconds=4),
            )
        self.assertEqual(dead.state, "DEAD")
        with self.engine.connect() as connection:
            dead_rows = list_dead_jobs(connection, account="XL")
        self.assertEqual([job.id for job in dead_rows], [created.job.id])

        with self.engine.begin() as connection:
            replayed = replay_dead_job(connection, job_id=dead.id, account="XL", now=now + timedelta(seconds=5))
        self.assertEqual(replayed.state, "PENDING")
        self.assertEqual(replayed.replay_count, 1)
        with self.engine.begin() as connection:
            third = claim_next_job(connection, worker_id="worker-c", now=now + timedelta(seconds=6))
        assert third is not None
        with self.engine.begin() as connection:
            succeeded = complete_job(
                connection,
                job_id=third.id,
                account="XL",
                lease_token=third.lease_token or "",
                now=now + timedelta(seconds=7),
            )
        self.assertEqual(succeeded.state, "SUCCEEDED")
        self.assertEqual(succeeded.total_attempts, 3)
        with self.engine.connect() as connection:
            attempts = connection.execute(
                text("SELECT state FROM background_job_attempts WHERE job_id = :id ORDER BY started_at, id"),
                {"id": created.job.id},
            ).scalars().all()
        self.assertCountEqual(attempts, ["RETRYABLE_FAILURE", "FINAL_FAILURE", "SUCCEEDED"])
        with self.assertRaises(InvalidJobState):
            with self.engine.begin() as connection:
                replay_dead_job(connection, job_id=succeeded.id, account="XL", now=now + timedelta(seconds=8))

    def test_expired_lease_is_recovered_and_stale_worker_is_fenced(self):
        self.enqueue(max_attempts=2)
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            first = claim_next_job(connection, worker_id="worker-a", lease_seconds=5, now=now)
        assert first is not None
        with self.engine.begin() as connection:
            count = reap_expired_leases(connection, now=now + timedelta(seconds=6))
        self.assertEqual(count, 1)
        with self.assertRaises(LeaseLost):
            with self.engine.begin() as connection:
                complete_job(
                    connection,
                    job_id=first.id,
                    account="XL",
                    lease_token=first.lease_token or "",
                    now=now + timedelta(seconds=6),
                )
        with self.engine.begin() as connection:
            second = claim_next_job(connection, worker_id="worker-b", now=now + timedelta(seconds=8))
        assert second is not None
        self.assertNotEqual(first.lease_token, second.lease_token)
        with self.engine.begin() as connection:
            complete_job(
                connection,
                job_id=second.id,
                account="XL",
                lease_token=second.lease_token or "",
                now=now + timedelta(seconds=9),
            )

    def test_fake_executor_runs_after_claim_commit_and_duplicate_delivery_is_noop(self):
        job = self.enqueue()
        observed_states = []

        def fake_executor(claimed):
            self.assertEqual(claimed.id, job.job.id)
            with self.engine.connect() as connection:
                state = connection.execute(
                    text("SELECT state FROM background_jobs WHERE id = :id"), {"id": claimed.id}
                ).scalar_one()
            observed_states.append(state)

        result = run_next_job(self.engine, worker_id="offline-test-worker", executor=fake_executor)
        self.assertEqual(observed_states, ["RUNNING"])
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "SUCCEEDED")
        self.assertIsNone(run_next_job(self.engine, worker_id="offline-test-worker", executor=fake_executor))
        with self.engine.connect() as connection:
            self.assertEqual(get_job(connection, job_id=job.job.id, account="XL").total_attempts, 1)

    def test_dispatch_broker_failure_keeps_job_and_retries_without_payload(self):
        job = self.enqueue()

        class FailedBroker:
            def publish_job(self, job_id):
                self_job_ids.append(job_id)
                raise JobPublishError("BROKER_UNAVAILABLE")

        self_job_ids = []
        failed = dispatch_once(self.engine, FailedBroker())
        self.assertEqual((failed.claimed, failed.published, failed.publish_failed), (1, 0, 1))
        self.assertEqual(self_job_ids, [job.job.id])
        with self.engine.connect() as connection:
            pending = get_job(connection, job_id=job.job.id, account="XL")
        self.assertEqual(pending.state, "PENDING")
        self.assertIsNone(pending.dispatched_at)
        self.assertEqual(pending.last_dispatch_error_code, "BROKER_UNAVAILABLE")

        class RecordingBroker:
            def __init__(self):
                self.job_ids = []

            def publish_job(self, job_id):
                self.job_ids.append(job_id)

        broker = RecordingBroker()
        sent = dispatch_once(self.engine, broker)
        self.assertEqual(sent.published, 1)
        self.assertEqual(broker.job_ids, [job.job.id])
        with self.engine.connect() as connection:
            delivered = get_job(connection, job_id=job.job.id, account="XL")
        self.assertIsNotNone(delivered.dispatched_at)
        self.assertEqual(delivered.dispatch_attempts, 2)
        self.assertIsNone(delivered.last_dispatch_error_code)

    def test_dispatcher_allowlist_does_not_publish_write_jobs(self):
        read_job = self.enqueue(key="read-only-dispatch-0001", job_type="discovery.scan")
        write_job = self.enqueue(key="write-dispatch-0001", job_type="otto.submit_product")

        class RecordingBroker:
            def __init__(self):
                self.job_ids = []

            def publish_job(self, job_id):
                self.job_ids.append(job_id)

        broker = RecordingBroker()
        result = dispatch_once(
            self.engine,
            broker,
            allowed_job_types=("discovery.scan", "import.process", "discovery.reconcile", "discovery.enrich"),
        )
        self.assertEqual(result.published, 1)
        self.assertEqual(broker.job_ids, [read_job.job.id])
        with self.engine.connect() as connection:
            write = get_job(connection, job_id=write_job.job.id, account="XL")
        self.assertIsNone(write.dispatched_at)
        self.assertEqual(write.state, "PENDING")

    def test_manual_import_cutoff_leaves_legacy_import_and_discovery_jobs_untouched(self):
        activation = datetime.now(timezone.utc)
        old_import = self.enqueue(
            key="legacy-import-before-cutoff", job_type="import.process",
            now=activation - timedelta(seconds=30),
        )
        old_discovery = self.enqueue(
            key="legacy-enrich-before-cutoff", job_type="discovery.enrich",
            now=activation - timedelta(seconds=20),
        )
        new_import = self.enqueue(
            key="manual-import-after-cutoff", job_type="import.process",
            now=activation + timedelta(seconds=1),
        )

        class RecordingBroker:
            def __init__(self):
                self.job_ids = []

            def publish_job(self, job_id):
                self.job_ids.append(job_id)

        broker = RecordingBroker()
        report = dispatch_once(
            self.engine,
            broker,
            allowed_job_types=("import.process",),
            created_after=activation,
            now=activation + timedelta(seconds=2),
        )
        self.assertEqual(report.published, 1)
        self.assertEqual(broker.job_ids, [new_import.job.id])
        with self.engine.connect() as connection:
            legacy_import = get_job(connection, job_id=old_import.job.id, account="XL")
            legacy_discovery = get_job(connection, job_id=old_discovery.job.id, account="XL")
            current_import = get_job(connection, job_id=new_import.job.id, account="XL")
        self.assertEqual((legacy_import.state, legacy_import.dispatch_attempts, legacy_import.dispatched_at), ("PENDING", 0, None))
        self.assertEqual((legacy_discovery.state, legacy_discovery.dispatch_attempts, legacy_discovery.dispatched_at), ("PENDING", 0, None))
        self.assertEqual(current_import.dispatched_at, activation + timedelta(seconds=2))

    def test_worker_cutoff_rejects_old_or_non_import_broker_messages(self):
        activation = datetime.now(timezone.utc)
        old_import = self.enqueue(
            key="worker-old-import-before-cutoff", job_type="import.process",
            now=activation - timedelta(seconds=30),
        )
        new_import = self.enqueue(
            key="worker-import-after-cutoff", job_type="import.process",
            now=activation + timedelta(seconds=1),
        )
        old_discovery = self.enqueue(
            key="worker-old-discovery", job_type="discovery.enrich",
            now=activation + timedelta(seconds=1),
        )
        executions = []
        executor = lambda job: executions.append(job.id)
        old_result = run_job_by_id(
            self.engine, job_id=old_import.job.id, worker_id="worker-cutoff-test",
            executors={"import.process": executor}, allowed_job_types=("import.process",),
            created_after=activation, now=activation + timedelta(seconds=2),
        )
        discovery_result = run_job_by_id(
            self.engine, job_id=old_discovery.job.id, worker_id="worker-cutoff-test",
            executors={"import.process": executor}, allowed_job_types=("import.process",),
            created_after=activation, now=activation + timedelta(seconds=2),
        )
        new_result = run_job_by_id(
            self.engine, job_id=new_import.job.id, worker_id="worker-cutoff-test",
            executors={"import.process": executor}, allowed_job_types=("import.process",),
            created_after=activation, now=activation + timedelta(seconds=2),
        )
        self.assertIsNone(old_result)
        self.assertIsNone(discovery_result)
        self.assertEqual(new_result.state if new_result else None, "SUCCEEDED")
        self.assertEqual(executions, [new_import.job.id])
        with self.engine.connect() as connection:
            old_after = get_job(connection, job_id=old_import.job.id, account="XL")
            discovery_after = get_job(connection, job_id=old_discovery.job.id, account="XL")
        self.assertEqual(old_after.state, "PENDING")
        self.assertEqual(discovery_after.state, "PENDING")

    def test_ambiguous_publish_and_duplicate_delivery_execute_one_job(self):
        job = self.enqueue()

        class AcceptedThenLostAck:
            def __init__(self):
                self.job_ids = []

            def publish_job(self, job_id):
                self.job_ids.append(job_id)
                # Simulate broker acceptance followed by a lost acknowledgement.
                if len(self.job_ids) == 1:
                    raise JobPublishError("BROKER_UNAVAILABLE")

        broker = AcceptedThenLostAck()
        first = dispatch_once(self.engine, broker)
        second = dispatch_once(self.engine, broker)
        self.assertEqual((first.publish_failed, second.published), (1, 1))
        self.assertEqual(broker.job_ids, [job.job.id, job.job.id])

        executions = []
        result = run_job_by_id(
            self.engine,
            job_id=job.job.id,
            worker_id="celery-test-worker",
            executors={"test.noop": lambda claimed: executions.append(claimed.id)},
        )
        duplicate = run_job_by_id(
            self.engine,
            job_id=job.job.id,
            worker_id="celery-test-worker-2",
            executors={"test.noop": lambda claimed: executions.append(claimed.id)},
        )
        self.assertEqual(result.state if result else None, "SUCCEEDED")
        self.assertIsNone(duplicate)
        self.assertEqual(executions, [job.job.id])

    def test_executor_retry_after_delay_is_persisted(self):
        job = self.enqueue(key="retry-after-job-key-0001")
        before = datetime.now(timezone.utc)

        def rate_limited(_claimed):
            raise JobExecutionError("HTTP_429", retryable=True, retry_delay_seconds=37)

        result = run_job_by_id(
            self.engine,
            job_id=job.job.id,
            worker_id="retry-after-test-worker",
            executors={"test.noop": rate_limited},
        )
        after = datetime.now(timezone.utc)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, "RETRY_WAIT")
        self.assertEqual(result.last_error_code, "HTTP_429")
        self.assertGreaterEqual(result.available_at, before + timedelta(seconds=37))
        self.assertLessEqual(result.available_at, after + timedelta(seconds=37))

    def test_stale_published_job_is_redispatched_and_account_scoped(self):
        xl_job = self.enqueue(account="XL", key="stale-xl-job-key-001")
        self.enqueue(account="JV", key="stale-jv-job-key-001")
        now = datetime.now(timezone.utc)

        class RecordingBroker:
            def __init__(self):
                self.job_ids = []

            def publish_job(self, job_id):
                self.job_ids.append(job_id)

        broker = RecordingBroker()
        first = dispatch_once(self.engine, broker, accounts=["XL"], now=now)
        stale = dispatch_once(
            self.engine,
            broker,
            accounts=["XL"],
            stale_after_seconds=30,
            now=now + timedelta(seconds=31),
        )
        self.assertEqual((first.published, stale.published), (1, 1))
        self.assertEqual(broker.job_ids, [xl_job.job.id, xl_job.job.id])
        with self.engine.connect() as connection:
            xl = get_job(connection, job_id=xl_job.job.id, account="XL")
        self.assertEqual(xl.dispatch_generation, 2)
        self.assertEqual(xl.dispatch_attempts, 2)

    def test_abandoned_dispatch_lease_recovers_after_crash_before_or_after_publish(self):
        job = self.enqueue()
        now = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            first_claim = claim_job_dispatches(connection, lease_seconds=10, now=now)
        self.assertEqual(first_claim[0].job_id, job.job.id)
        # Crash before broker publish: the dispatch lease expires and the durable
        # row is claimable again.
        with self.engine.begin() as connection:
            second_claim = claim_job_dispatches(
                connection, lease_seconds=10, now=now + timedelta(seconds=11)
            )
        self.assertEqual(second_claim[0].job_id, job.job.id)
        self.assertNotEqual(first_claim[0].lease_token, second_claim[0].lease_token)

        # Simulate broker acceptance followed by process death before the DB ack.
        accepted_message_ids = [second_claim[0].job_id]
        with self.engine.begin() as connection:
            third_claim = claim_job_dispatches(
                connection, lease_seconds=10, now=now + timedelta(seconds=22)
            )
            self.assertEqual(third_claim[0].job_id, job.job.id)
            accepted_message_ids.append(third_claim[0].job_id)
            self.assertTrue(mark_job_dispatched(connection, claim=third_claim[0], now=now + timedelta(seconds=22)))
        with self.engine.connect() as connection:
            persisted = get_job(connection, job_id=job.job.id, account="XL")
        self.assertEqual(persisted.dispatch_attempts, 3)
        self.assertIsNotNone(persisted.dispatched_at)
        self.assertEqual(accepted_message_ids, [job.job.id, job.job.id])


if __name__ == "__main__":
    unittest.main()

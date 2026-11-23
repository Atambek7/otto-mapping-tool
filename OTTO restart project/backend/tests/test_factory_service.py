from __future__ import annotations

import hashlib
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from migration_runner import upgrade
from services.discovery_service import StatusResponseInput, apply_staged_run, create_import_run, stage_status_responses
from services.factory_service import FactoryService, recalculate_factory
from services.otto_status_service import CODE_3M


class FactoryServicePostgresTests(unittest.TestCase):
    """Factory integration checks use only a throwaway loopback PostgreSQL 15 database."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for isolated factory tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Factory tests require an explicit loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Factory tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_engine", None):
            cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_s17_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.database_url: URL = self.base_url.set(database=self.database_name)
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        upgrade(self.engine)
        self.sequence = 0

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    @staticmethod
    def _ean(seed: int) -> str:
        body = f"499{seed % 1_000_000_000:09d}"
        digits = [int(char) for char in body]
        check = (10 - sum(digit * (3 if i % 2 == 0 else 1) for i, digit in enumerate(reversed(digits))) % 10) % 10
        return body + str(check)

    def stage_candidate(
        self,
        *,
        factory_id: str = "factory-s17",
        account: str = "XL",
        run_mode: str = "INCREMENTAL",
        run_type: str = "FILE_IMPORT",
        timestamp: datetime | None = None,
        apply_run: bool = True,
    ) -> tuple[str, str | None, str, str]:
        self.sequence += 1
        source_id, ean, run_id = str(uuid.uuid4()), self._ean(self.sequence), str(uuid.uuid4())
        sku = f"S17-{self.sequence:04d}"
        modified = (timestamp or datetime.now(timezone.utc)).isoformat()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products (id,account,source_type,aftercool_ean,factory_id,factory_name,"
                    "original_title,data_scope,period) VALUES (:id,:account,'AFTERCOOL',:ean,:factory_id,"
                    "'Factory S17','Offline fixture','PRODUCTION','3M')"
                ),
                {"id": source_id, "account": account, "ean": ean, "factory_id": factory_id},
            )
            run_id = create_import_run(
                connection, account, run_type, run_mode,
                source_filename="offline-fixture.txt",
                window_from=datetime.now(timezone.utc) - timedelta(days=2) if run_type == "INCREMENTAL_SCAN" else None,
                cutoff_at=datetime.now(timezone.utc) if run_type == "INCREMENTAL_SCAN" else None,
            )
            response = {
                "sku": sku,
                "lastModified": modified,
                "errors": [{"code": CODE_3M, "title": "Nettoumsatz fehlt"}],
            }
            stage_status_responses(
                connection,
                account,
                run_id,
                [StatusResponseInput(sku, 200, response, source_product_id=source_id, confirmed_ean=ean)],
            )
            if apply_run:
                apply_staged_run(connection, account, run_id)
                episode_id = connection.execute(
                    text("SELECT id FROM candidate_episodes WHERE account=:account AND observed_sku=:sku AND data_scope='PRODUCTION'"),
                    {"account": account, "sku": sku},
                ).scalar_one()
            else:
                episode_id = None
        return source_id, str(episode_id) if episode_id else None, run_id, sku

    def test_repeated_status_attaches_verified_source_and_updates_factory_once(self) -> None:
        account, sku, ean, factory = "JV", "repeat-sku", self._ean(123), "repeat-factory"
        response = {"sku": sku, "lastModified": "2026-11-09T10:00:00Z",
                    "errors": [{"code": CODE_3M, "title": "Nettoumsatz fehlt"}]}
        source_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            first = create_import_run(connection, account, "FILE_IMPORT", "BASELINE", source_filename="first.txt")
            stage_status_responses(connection, account, first, [StatusResponseInput(sku, 200, response)])
            apply_staged_run(connection, account, first)
            original = connection.execute(text("SELECT id, last_observation_id, last_response_hash FROM candidate_episodes WHERE account=:account AND observed_sku=:sku"), {"account": account, "sku": sku}).mappings().one()
            connection.execute(text("INSERT INTO source_products (id,account,source_type,aftercool_ean,factory_id,factory_name,original_title,data_scope,period) VALUES (:id,:account,'AFTERCOOL',:ean,:factory,'Factory','Fixture','PRODUCTION','3M')"), {"id": source_id, "account": account, "ean": ean, "factory": factory})
            for index in range(2):
                run = create_import_run(connection, account, "FILE_IMPORT", "BASELINE", source_filename=f"repeat-{index}.txt")
                stage_status_responses(connection, account, run, [StatusResponseInput(sku, 200, response, source_product_id=source_id, confirmed_ean=ean)])
                result = apply_staged_run(connection, account, run)
                self.assertEqual(result["repeated"], 1)
            other_id, other_ean = str(uuid.uuid4()), self._ean(124)
            connection.execute(text("INSERT INTO source_products (id,account,source_type,aftercool_ean,factory_id,original_title,data_scope,period) VALUES (:id,:account,'AFTERCOOL',:ean,'other-factory','Other','PRODUCTION','3M')"), {"id": other_id, "account": account, "ean": other_ean})
            conflict = create_import_run(connection, account, "FILE_IMPORT", "BASELINE", source_filename="conflict.txt")
            stage_status_responses(connection, account, conflict, [StatusResponseInput(sku, 200, response, source_product_id=other_id, confirmed_ean=other_ean)])
            self.assertEqual(apply_staged_run(connection, account, conflict)["identity_conflicts"], 1)
            episode = connection.execute(text("SELECT source_product_id::text,confirmed_ean,routing_state,last_observation_id,last_response_hash FROM candidate_episodes WHERE id=:id"), {"id": original["id"]}).mappings().one()
            self.assertEqual((episode["source_product_id"], episode["confirmed_ean"], episode["routing_state"]), (source_id, ean, "ROUTED"))
            self.assertEqual(episode["last_observation_id"], original["last_observation_id"])
            self.assertEqual(episode["last_response_hash"], original["last_response_hash"])
            factory_row = connection.execute(text("SELECT candidate_generation,projection_json FROM restart_factories WHERE account=:account AND id=:factory AND data_scope='PRODUCTION'"), {"account": account, "factory": factory}).mappings().one()
            self.assertEqual(factory_row["candidate_generation"], 1)
            self.assertEqual(factory_row["projection_json"]["totalCandidates"], 1)

    def set_verified_coverage(self, *, factory_id: str, account: str = "XL", run_id: str | None = None, age_days: int = 0) -> datetime:
        if run_id is None:
            with self.engine.connect() as connection:
                run_id = connection.execute(
                    text("SELECT id FROM import_runs WHERE account=:account AND data_scope='PRODUCTION' ORDER BY created_at DESC LIMIT 1"),
                    {"account": account},
                ).scalar_one()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE restart_factories SET coverage_confirmed_at=CURRENT_TIMESTAMP - (:age * INTERVAL '1 day'), "
                    "coverage_generation=candidate_generation, coverage_run_id=:run_id, "
                    "coverage_evidence_json=CAST(:evidence AS jsonb) "
                    "WHERE account=:account AND id=:factory_id AND data_scope='PRODUCTION'"
                ),
                {"age": age_days, "run_id": run_id, "evidence": json.dumps({"source": "verified-offline-test-fixture"}),
                 "account": account, "factory_id": factory_id},
            )
        with self.engine.connect() as connection:
            return connection.execute(
                text("SELECT coverage_confirmed_at FROM restart_factories WHERE account=:account AND id=:factory_id AND data_scope='PRODUCTION'"),
                {"account": account, "factory_id": factory_id},
            ).scalar_one()

    def publish_fixture(self, *, source_id: str, episode_id: str, sku: str, account: str = "XL") -> None:
        operation_id, processing_id, observation_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        target_sku = f"NEW-{sku}"
        response_hash = hashlib.sha256(f"online:{sku}".encode()).hexdigest()
        with self.engine.begin() as connection:
            run_id = create_import_run(connection, account, "FILE_IMPORT", "INCREMENTAL", source_filename="offline-online-fixture.txt")
            connection.execute(
                text(
                    "UPDATE import_runs SET run_state='SUCCEEDED',status='COMPLETED',started_at=CURRENT_TIMESTAMP,"
                    "finished_at=CURRENT_TIMESTAMP WHERE id=:id"
                ),
                {"id": run_id},
            )
            connection.execute(
                text(
                    "INSERT INTO otto_status_observations (id,run_id,account,data_scope,sequence_no,requested_sku,response_sku,"
                    "source_product_id,http_status,classification,reason,status,market_place_status,active,errors_json,"
                    "error_codes_json,error_titles_json,unknown_codes_json,response_json,response_hash,reduction_status) "
                    "VALUES (:id,:run_id,:account,'PRODUCTION',1,:sku,:sku,:source_id,200,'NONE','',"
                    "'ONLINE','ONLINE',TRUE,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
                    "CAST(:response AS jsonb),:hash,'NONE')"
                ),
                {"id": observation_id, "run_id": run_id, "account": account, "sku": target_sku,
                 "source_id": source_id, "response": json.dumps({"sku": target_sku, "marketPlaceStatus": "ONLINE", "errors": []}),
                 "hash": response_hash},
            )
            connection.execute(
                text(
                    "INSERT INTO restart_events (id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json) "
                    "VALUES (:id,:account,'PRODUCTION','OTTO_PROCESSING_RESULT','OTTO_PROCESSING_RESULT',:operation_id,"
                    "'Offline successful per-SKU fixture',CAST(:metadata AS jsonb))"
                ),
                {"id": processing_id, "account": account, "operation_id": operation_id,
                 "metadata": json.dumps({"targetSku": target_sku, "result": "SUCCEEDED"})},
            )
            connection.execute(
                text(
                    "INSERT INTO restart_operations (id,account,source_product_id,period,new_ean,status,data_scope,"
                    "candidate_episode_id,attempt_number,target_sku,created_by,submission_state,"
                    "processing_evidence_event_id,online_observation_id) VALUES (:id,:account,:source_id,'3M',NULL,"
                    "'PUBLISHED','PRODUCTION',:episode_id,1,:target_sku,'offline-test','CONFIRMED',:processing_id,:observation_id)"
                ),
                {"id": operation_id, "account": account, "source_id": source_id, "episode_id": episode_id,
                 "target_sku": target_sku, "processing_id": processing_id, "observation_id": observation_id},
            )

    def clear_fixture(self, source_id: str, sku: str, *, account: str = "XL") -> tuple[str, str]:
        with self.engine.connect() as connection:
            ean = connection.execute(text("SELECT aftercool_ean FROM source_products WHERE id=:id"), {"id": source_id}).scalar_one()
            old_modified = connection.execute(
                text("SELECT last_modified FROM candidate_episodes WHERE observed_sku=:sku AND account=:account AND data_scope='PRODUCTION'"),
                {"sku": sku, "account": account},
            ).scalar_one()
        modified = (old_modified + timedelta(days=1)).isoformat()
        run_id = None
        with self.engine.begin() as connection:
            run_id = create_import_run(
                connection, account, "INCREMENTAL_SCAN", "INCREMENTAL",
                window_from=datetime.now(timezone.utc) - timedelta(days=1),
                cutoff_at=datetime.now(timezone.utc),
            )
            stage_status_responses(
                connection, account, run_id,
                [StatusResponseInput(sku, 200, {"sku": sku, "lastModified": modified, "errors": []},
                                     source_product_id=source_id, confirmed_ean=ean)],
            )
            apply_staged_run(connection, account, run_id)
        return str(run_id), modified

    def test_generation_new_count_completion_history_and_legacy_scope(self) -> None:
        candidates = [self.stage_candidate(factory_id="477277") for _ in range(13)]
        self.set_verified_coverage(factory_id="477277", run_id=candidates[-1][2])

        with self.engine.begin() as connection:
            summary = recalculate_factory(connection, account="XL", factory_id="477277")
        self.assertEqual(summary["status"], "NOT_COMPLETE")
        self.assertEqual(summary["newCandidatesCount"], 13)
        self.assertEqual(summary["period3mCount"], 13)

        for source_id, episode_id, _run_id, sku in candidates[:5]:
            self.publish_fixture(source_id=source_id, episode_id=episode_id, sku=sku)
        with self.engine.begin() as connection:
            summary = recalculate_factory(connection, account="XL", factory_id="477277")
        self.assertEqual(summary["status"], "NOT_COMPLETE")
        self.assertEqual(summary["newCandidatesCount"], 8)
        self.assertEqual(summary["publishedCount"], 5)

        for source_id, episode_id, _run_id, sku in candidates[5:]:
            self.publish_fixture(source_id=source_id, episode_id=episode_id, sku=sku)
        with self.engine.begin() as connection:
            summary = recalculate_factory(connection, account="XL", factory_id="477277")
            first_completion = summary["lastCompletedAt"]
            repeated = recalculate_factory(connection, account="XL", factory_id="477277")
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertTrue(summary["completionRecorded"])
        self.assertFalse(repeated["completionRecorded"])
        self.assertEqual(repeated["lastCompletedAt"], first_completion)

        added = self.stage_candidate(factory_id="477277")
        with self.engine.begin() as connection:
            pending = recalculate_factory(connection, account="XL", factory_id="477277")
        self.assertEqual(pending["status"], "NOT_COMPLETE")
        self.assertEqual(pending["newCandidatesCount"], 1)
        self.assertEqual(pending["lastCompletedAt"], first_completion)

        # A clean overlapping scan can extend an already verified, still-fresh baseline.
        with self.engine.begin() as connection:
            scan_id = create_import_run(
                connection, "XL", "INCREMENTAL_SCAN", "INCREMENTAL",
                window_from=datetime.now(timezone.utc) - timedelta(days=1), cutoff_at=datetime.now(timezone.utc),
            )
            stage_status_responses(connection, "XL", scan_id, [])
            apply_staged_run(connection, "XL", scan_id)
        self.publish_fixture(source_id=added[0], episode_id=added[1], sku=added[3])
        with self.engine.begin() as connection:
            completed_again = recalculate_factory(connection, account="XL", factory_id="477277")
            history = FactoryService.get_completion_history(connection, "XL", "477277")
            listed = FactoryService.list_factories(connection, "XL")
            counts = connection.execute(
                text("SELECT data_scope,COUNT(*) FROM restart_factories WHERE account='XL' AND id='477277' GROUP BY data_scope ORDER BY data_scope")
            ).all()
        self.assertEqual(completed_again["status"], "COMPLETE")
        self.assertTrue(completed_again["completionRecorded"])
        self.assertEqual(len(history), 2)
        self.assertEqual({item["generation"] for item in history}, {13, 14})
        self.assertEqual([item["id"] for item in listed if item["id"] == "477277"], ["477277"])
        self.assertEqual([tuple(row) for row in counts], [("LEGACY_UNVERIFIED", 1), ("PRODUCTION", 1)])

    def test_clear_restriction_closes_episode_but_stale_coverage_stays_unknown(self) -> None:
        source_id, _episode_id, _run_id, sku = self.stage_candidate(factory_id="factory-clear", run_mode="BASELINE")
        self.set_verified_coverage(factory_id="factory-clear", age_days=17)
        self.clear_fixture(source_id, sku)
        with self.engine.begin() as connection:
            result = recalculate_factory(connection, account="XL", factory_id="factory-clear")
            closed = connection.execute(
                text("SELECT episode_state,closed_by_observation_id FROM candidate_episodes WHERE observed_sku=:sku AND account='XL' AND data_scope='PRODUCTION'"),
                {"sku": sku},
            ).one()
        self.assertEqual(tuple(closed)[0], "CLOSED")
        self.assertIsNotNone(tuple(closed)[1])
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["coverageFresh"])
        self.assertEqual(result["totalCandidates"], 0)

    def test_fresh_clean_scan_closing_last_episode_records_completion_once(self) -> None:
        source_id, _episode_id, baseline_run_id, sku = self.stage_candidate(
            factory_id="factory-clear-complete", run_mode="BASELINE"
        )
        self.set_verified_coverage(factory_id="factory-clear-complete", run_id=baseline_run_id)
        self.clear_fixture(source_id, sku)
        with self.engine.begin() as connection:
            first = recalculate_factory(connection, account="XL", factory_id="factory-clear-complete")
            again = recalculate_factory(connection, account="XL", factory_id="factory-clear-complete")
            history = FactoryService.get_completion_history(connection, "XL", "factory-clear-complete")
        self.assertEqual(first["status"], "COMPLETE")
        # The reducer already wrote the generation's completion atomically.
        self.assertFalse(first["completionRecorded"])
        self.assertEqual(again["status"], "COMPLETE")
        self.assertFalse(again["completionRecorded"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["candidatesCount"], 0)

    def test_uncheckable_scan_never_refreshes_coverage_or_applies_partial_status(self) -> None:
        _source_id, _episode_id, _run_id, sku = self.stage_candidate(factory_id="factory-partial")
        original_coverage = self.set_verified_coverage(factory_id="factory-partial", age_days=1)
        with self.engine.begin() as connection:
            run_id = create_import_run(
                connection, "XL", "INCREMENTAL_SCAN", "INCREMENTAL",
                window_from=datetime.now(timezone.utc) - timedelta(days=1), cutoff_at=datetime.now(timezone.utc),
            )
            stage_status_responses(connection, "XL", run_id, [StatusResponseInput(sku, 503, {})])
            result = apply_staged_run(connection, "XL", run_id)
            current = connection.execute(
                text("SELECT coverage_confirmed_at,coverage_generation,candidate_generation FROM restart_factories WHERE account='XL' AND id='factory-partial' AND data_scope='PRODUCTION'")
            ).one()
            state = connection.execute(text("SELECT run_state FROM import_runs WHERE id=:id"), {"id": run_id}).scalar_one()
        self.assertFalse(result["applied"])
        self.assertEqual(state, "PARTIAL")
        self.assertEqual(current[0], original_coverage)
        self.assertEqual(current[1], current[2])

    def test_completion_recalculation_serializes_with_concurrent_candidate_import(self) -> None:
        first = self.stage_candidate(factory_id="factory-concurrent", run_mode="BASELINE")
        self.set_verified_coverage(factory_id="factory-concurrent", run_id=first[2])
        self.publish_fixture(source_id=first[0], episode_id=first[1], sku=first[3])
        with self.engine.begin() as connection:
            complete = recalculate_factory(connection, account="XL", factory_id="factory-concurrent")
        self.assertEqual(complete["status"], "COMPLETE")
        completed_at = complete["lastCompletedAt"]

        # Race the reducer that opens one new episode against a completion
        # recalculation from the previous generation.
        second = self.stage_candidate(factory_id="factory-concurrent", apply_run=False)
        barrier = Barrier(2)

        def apply_import():
            barrier.wait(timeout=5)
            with self.engine.begin() as connection:
                return apply_staged_run(connection, "XL", second[2])

        def recalculate():
            barrier.wait(timeout=5)
            with self.engine.begin() as connection:
                return recalculate_factory(connection, account="XL", factory_id="factory-concurrent")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda fn: fn(), (apply_import, recalculate)))
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT candidate_generation,status,last_completed_at FROM restart_factories WHERE account='XL' AND id='factory-concurrent' AND data_scope='PRODUCTION'")
            ).one()
            history_count = connection.execute(
                text("SELECT COUNT(*) FROM factory_completion_history WHERE account='XL' AND factory_id='factory-concurrent' AND data_scope='PRODUCTION'")
            ).scalar_one()
        self.assertEqual(int(row[0]), 2)
        self.assertEqual(row[1], "NOT_COMPLETE")
        self.assertEqual(row[2].isoformat(), completed_at)
        self.assertEqual(history_count, 1)
        self.assertEqual(sum(result.get("opened", 0) for result in results if isinstance(result, dict)), 1)


if __name__ == "__main__":
    unittest.main()

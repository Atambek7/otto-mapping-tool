from __future__ import annotations

import io
import json
import os
import time
import unittest
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from jobs.service import JobExecutionError
from migration_runner import upgrade
from runtime_mode import RuntimeConfig, RuntimeMode
from services.aftercool_lookup_service import AftercoolLookupService
from services.import_pipeline import DEFAULT_IMPORT_JOB_MAX_ATTEMPTS, ImportPipeline, parse_import_file, process_import_run
from services.product_catalog_service import invalidate_product_catalog_metrics_cache, list_products


class MassImportScalePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for PostgreSQL scale tests.")
        base_url = make_url(raw_url)
        cls.db_name = f"otto_scale_test_{uuid4().hex[:8]}"
        cls.admin_engine = create_engine(base_url.set(database="postgres"), pool_pre_ping=True, isolation_level="AUTOCOMMIT")
        with cls.admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{cls.db_name}"'))
        cls.engine = create_engine(base_url.set(database=cls.db_name), pool_pre_ping=True)
        upgrade(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        if hasattr(cls, "admin_engine"):
            with cls.admin_engine.connect() as conn:
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:db AND pid<>pg_backend_pid()"
                ), {"db": cls.db_name})
                conn.execute(text(f'DROP DATABASE IF EXISTS "{cls.db_name}"'))
            cls.admin_engine.dispose()

    def test_concurrent_import_chunk_progress_and_retry_bailout(self) -> None:
        class FakeOtto:
            def get_marketplace_status(self, sku: str):
                return {"sku": sku, "moin": f"M-{sku}", "errors": [{"code": "602016 - LOW_PERFORMER", "title": "Low"}]}

            def get_product(self, sku: str):
                return {
                    "sku": sku,
                    "ean": sku,
                    "productDescription": {"productLine": f"Product {sku}", "category": "Sofa", "brandId": "UO4EGHSX"},
                    "pricing": {"standardPrice": {"amount": 499.0, "currency": "EUR"}},
                }

        class FakeAftercool(AftercoolLookupService):
            def __init__(self) -> None:
                super().__init__(runtime_mode=RuntimeMode.PRODUCTION, runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True))

            def lookup_product(self, account: str, identifier: str, factory_hint=None):
                if identifier.endswith("2"):
                    return {"status": "NOT_FOUND_CONFIRMED", "verified": True, "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT"}
                return {
                    "status": "FOUND", "verified": True, "data_scope": "PRODUCTION", "source_type": "AFTERBUY",
                    "aftercool_product_id": f"ac-{identifier}", "ean": identifier, "sku": identifier,
                    "factory_id": "1001", "factory_name": "Factory 1", "reported_account": account,
                    "name": f"AC Product {identifier}", "category": "Sofa", "price_cents": 49900,
                }

        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        rows = parse_import_file("sample.csv", b"sku;period\n4006381333901;12M\n4006381333902;12M\n4006381333903;12M\n")
        with self.engine.begin() as conn:
            res, created = pipeline.enqueue_import(
                conn, account="JV", rows=rows, filename="sample.csv", run_mode="BASELINE",
                idempotency_key="scale-concurrent-1", principal="op", content_hash="h" * 64,
            )
            self.assertTrue(created)
            max_att = conn.execute(text("SELECT max_attempts FROM background_jobs WHERE id=:id"), {"id": res["jobId"]}).scalar_one()
            self.assertEqual(int(max_att), DEFAULT_IMPORT_JOB_MAX_ATTEMPTS)

        out = process_import_run(
            self.engine,
            res["runId"],
            otto_client_factory=lambda _acc: FakeOtto(),
            aftercool_service_factory=FakeAftercool,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True, aftercool_read_enabled=True),
            concurrency=2,
        )
        self.assertEqual(out["runState"], "SUCCEEDED")
        with self.engine.connect() as conn:
            run_row = conn.execute(
                text("SELECT processed_count, afterbuy_count, direct_otto_count, error_count FROM import_runs WHERE id=:id"),
                {"id": res["runId"]},
            ).mappings().one()
            self.assertEqual(dict(run_row), {"processed_count": 3, "afterbuy_count": 2, "direct_otto_count": 1, "error_count": 0})

        # Active runs endpoint filters pre-cutoff PENDING runs but includes recent runs
        import app as fastapi_app
        from unittest.mock import patch

        old_run_id = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO import_runs (id, account, source_filename, total_rows, status, data_scope, run_type, run_mode, run_state, created_at) "
                "VALUES (:id, 'JV', 'old_nov9.txt', 10, 'PENDING', 'PRODUCTION', 'FILE_IMPORT', 'BASELINE', 'PENDING', '2026-11-09T12:00:00Z')"
            ), {"id": old_run_id})
        with patch.object(fastapi_app, "connect", side_effect=lambda: self.engine.connect()), \
             patch.dict(os.environ, {"OTTORESTART_MANUAL_IMPORTS_AFTER": "2026-11-10T04:50:00Z"}):
            active_resp = fastapi_app.list_active_import_runs(account="JV")
        active_ids = {item["id"] for item in active_resp["items"]}
        self.assertIn(res["runId"], active_ids)
        self.assertNotIn(old_run_id, active_ids)

        # Early bailout on 5 consecutive retryable failures
        failing_rows = parse_import_file("fail.csv", "\n".join(f"40063813338{i:02d}" for i in range(10)).encode())
        with self.engine.begin() as conn:
            f_res, _ = pipeline.enqueue_import(
                conn, account="XL", rows=failing_rows, filename="fail.csv", run_mode="BASELINE",
                idempotency_key="scale-bailout-1", principal="op", content_hash="b" * 64,
            )

        class AlwaysTimeoutOtto:
            calls = 0

            def get_marketplace_status(self, _sku: str):
                AlwaysTimeoutOtto.calls += 1
                err = RuntimeError("timeout")
                err.code = "NETWORK_ERROR"
                err.retryable = True
                raise err

        with self.assertRaises(JobExecutionError) as err_ctx:
            process_import_run(
                self.engine,
                f_res["runId"],
                otto_client_factory=lambda _acc: AlwaysTimeoutOtto(),
                aftercool_service_factory=FakeAftercool,
                runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True, aftercool_read_enabled=True),
                job_attempt=1,
                max_attempts=15,
                concurrency=1,
            )
        self.assertTrue(err_ctx.exception.retryable)
        self.assertEqual(AlwaysTimeoutOtto.calls, 5)

    def test_xlsx_32815_commit_and_45000_catalog_pagination_latency(self) -> None:
        # 1) Generate 32,815-row XLSX matching user's jv_new_12m.xlsx scale
        wb = Workbook(write_only=True)
        ws = wb.create_sheet()
        ws.append(["SKU", "Periode"])
        for i in range(32_815):
            ws.append([f"406000{i:07d}", "12M"])
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()

        t0 = time.perf_counter()
        parsed = parse_import_file("xl_new_12m.xlsx", xlsx_bytes)
        self.assertEqual(len(parsed), 32_815)
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        with self.engine.begin() as conn:
            res, _ = pipeline.enqueue_import(
                conn, account="XL", rows=parsed, filename="xl_new_12m.xlsx", run_mode="BASELINE",
                idempotency_key="scale-32815-xlsx", principal="op", content_hash="c" * 64,
            )
        enqueue_elapsed = time.perf_counter() - t0
        self.assertLess(enqueue_elapsed, 15.0)
        with self.engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM import_rows WHERE run_id=:id"), {"id": res["runId"]}).scalar_one()
            self.assertEqual(cnt, 32_815)

        # 2) Benchmark catalog list_products on 45,000 open episodes
        run_id = str(uuid4())
        obs_id = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO import_runs (id, account, source_filename, total_rows, status, data_scope, run_type, run_mode, run_state, started_at, finished_at) "
                "VALUES (:id, 'JV', 'bench.xlsx', 45000, 'COMPLETED', 'PRODUCTION', 'FILE_IMPORT', 'BASELINE', 'SUCCEEDED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"id": run_id})
            conn.execute(text(
                "INSERT INTO otto_status_observations (id, run_id, account, data_scope, sequence_no, requested_sku, response_sku, http_status, classification, period, reason, status, market_place_status, active, errors_json, error_codes_json, error_titles_json, unknown_codes_json, response_json, response_hash, reduction_status) "
                "VALUES (:id, :run_id, 'JV', 'PRODUCTION', 1, 'bench', 'bench', 200, 'EXACT_CODE', '12M', '602016 - LOW_PERFORMER', 'REJECTED', 'INACTIVE', FALSE, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, :hash, 'OPENED')"
            ), {"id": obs_id, "run_id": run_id, "hash": "d" * 64})
            conn.execute(text(
                "INSERT INTO source_products (id, account, data_scope, source_type, aftercool_product_id, factory_id, factory_name, aftercool_sku, aftercool_ean, original_title, original_price_cents, category, attributes_json) "
                "SELECT gen_random_uuid(), 'JV', 'PRODUCTION', 'AFTERBUY', 'ac-' || g::text, '409886', 'BENCH_FACTORY', "
                "to_char(4070000000000 + g, 'FM0000000000000'), to_char(4070000000000 + g, 'FM0000000000000'), "
                "'Benchmark Sofa ' || g::text, 99900, 'Sofa', "
                "'{\"attributes\":[{\"name\":\"Marke\",\"value\":\"JV\"}]}'::jsonb "
                "FROM generate_series(1, 45000) AS g"
            ))
            conn.execute(text(
                "INSERT INTO candidate_episodes (account, data_scope, observed_sku, canonical_identity, source_product_id, confirmed_ean, period, classification, origin, episode_state, first_observation_id, last_observation_id, last_response_hash) "
                "SELECT 'JV', 'PRODUCTION', sp.aftercool_sku, 'OTTO_SKU:' || sp.aftercool_sku, sp.id, sp.aftercool_ean, "
                "CASE WHEN (row_number() OVER ()) % 3 = 0 THEN '3M' ELSE '12M' END, 'EXACT_CODE', 'BASELINE', 'OPEN', "
                ":obs_id, :obs_id, :hash "
                "FROM source_products sp WHERE sp.account='JV' AND sp.factory_id='409886'"
            ), {"obs_id": obs_id, "hash": "e" * 64})
            conn.execute(text("ANALYZE source_products"))
            conn.execute(text("ANALYZE candidate_episodes"))

        invalidate_product_catalog_metrics_cache("JV")
        with self.engine.connect() as conn:
            t_cold = time.perf_counter()
            page1 = list_products(conn, account="JV", page=1, page_size=50, factory_id="409886")
            cold_ms = (time.perf_counter() - t_cold) * 1000

            t_warm = time.perf_counter()
            page2 = list_products(conn, account="JV", page=2, page_size=50, factory_id="409886")
            warm_ms = (time.perf_counter() - t_warm) * 1000

        self.assertEqual(page1.page.total, 45_000)
        self.assertEqual(len(page1.items), 50)
        self.assertEqual(len(page2.items), 50)
        print(f"[BENCH 32,815 XLSX enqueue={enqueue_elapsed:.2f}s | 45k catalog cold={cold_ms:.1f}ms warm={warm_ms:.1f}ms]", flush=True)
        self.assertLess(cold_ms, 5000.0)
        self.assertLess(warm_ms, 1000.0)

    def test_incremental_materialization_exposes_products_during_multi_chunk_import(self) -> None:
        from unittest.mock import patch
        from services.discovery_service import apply_pending_observations

        class FakeOtto:
            def get_marketplace_status(self, sku: str):
                return {"sku": sku, "moin": f"M-{sku}", "errors": [{"code": "602016 - Nonseller", "title": "3M"}]}

            def get_product(self, sku: str):
                return {
                    "sku": sku,
                    "ean": sku,
                    "productDescription": {"productLine": f"Live Product {sku}", "category": "Sofa", "brandId": "6HMOZBOU"},
                    "pricing": {"standardPrice": {"amount": 399.0, "currency": "EUR"}},
                }

        class FakeAftercool(AftercoolLookupService):
            def __init__(self) -> None:
                super().__init__(
                    runtime_mode=RuntimeMode.PRODUCTION,
                    runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
                )

            def lookup_product(self, account: str, identifier: str, factory_hint=None):
                return {
                    "status": "FOUND", "verified": True, "data_scope": "PRODUCTION", "source_type": "AFTERBUY",
                    "aftercool_product_id": f"ac-xl-{identifier}", "ean": identifier, "sku": identifier,
                    "factory_id": "2002", "factory_name": "XL Live Factory", "reported_account": account,
                    "name": f"XL Live Product {identifier}", "category": "Sofa", "price_cents": 39900,
                }

        csv_body = "\n".join(["sku;period"] + [f"406728200010{i};3M" for i in range(1, 7)]).encode("utf-8")
        rows = parse_import_file("xl_live.csv", csv_body)
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        with self.engine.begin() as conn:
            enqueued, created = pipeline.enqueue_import(
                conn, account="XL", rows=rows, filename="xl_live.csv", run_mode="BASELINE",
                idempotency_key="incremental-live-xl-1", principal="op", content_hash="f" * 64,
            )
            self.assertTrue(created)

        runtime_cfg = RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True, aftercool_read_enabled=True)
        with (
            patch("services.import_pipeline.PROCESS_CHUNK_SIZE", 3),
            patch("services.import_pipeline.PROGRESS_REFRESH_INTERVAL", 2),
        ):
            chunk1 = process_import_run(
                self.engine,
                enqueued["runId"],
                otto_client_factory=lambda _acc: FakeOtto(),
                aftercool_service_factory=FakeAftercool,
                runtime_config=runtime_cfg,
                chunk_number=1,
                concurrency=2,
            )
            self.assertEqual(chunk1["runState"], "RUNNING")
            self.assertTrue(chunk1.get("nextJobId"))

            # Products and factory from chunk 1 must ALREADY be visible before chunk 2 executes.
            invalidate_product_catalog_metrics_cache("XL")
            with self.engine.connect() as conn:
                mid_page = list_products(conn, account="XL", factory_id="2002", page=1, page_size=50)
                self.assertEqual(mid_page.page.total, 3)
                factory_row = conn.execute(
                    text("SELECT name FROM restart_factories WHERE account='XL' AND id='2002' AND data_scope='PRODUCTION'"),
                ).scalar_one_or_none()
                self.assertEqual(factory_row, "XL Live Factory")

            # Re-running apply_pending_observations is a no-op (idempotent).
            with self.engine.begin() as conn:
                replay = apply_pending_observations(conn, "XL", enqueued["runId"])
                self.assertEqual(replay["observations"], 0)

            chunk2 = process_import_run(
                self.engine,
                enqueued["runId"],
                otto_client_factory=lambda _acc: FakeOtto(),
                aftercool_service_factory=FakeAftercool,
                runtime_config=runtime_cfg,
                chunk_number=2,
                concurrency=2,
            )
            self.assertEqual(chunk2["runState"], "SUCCEEDED")
            self.assertEqual(chunk2["summary"]["observations"], 6)
            self.assertEqual(chunk2["summary"]["episodesOpened"], 6)

            invalidate_product_catalog_metrics_cache("XL")
            with self.engine.connect() as conn:
                final_page = list_products(conn, account="XL", factory_id="2002", page=1, page_size=50)
                self.assertEqual(final_page.page.total, 6)

    def test_parse_import_file_reads_new_ean_and_falls_back_to_old_ean(self) -> None:
        wb = Workbook()
        ws = wb.active
        ws.append(["old_ean", "new_ean", "period", "fehler", "status", "source_file"])
        ws.append(["", "4067282801455", "12m", "text", "Variante fehlerhaft", "a.xlsx"])
        ws.append(["4067282801462", "", "3m", "text", "Variante fehlerhaft", "b.xlsx"])
        buf = io.BytesIO()
        wb.save(buf)
        parsed = parse_import_file("xl_new_12m.xlsx", buf.getvalue())
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["ean"], "4067282801455")
        self.assertEqual(parsed[0]["raw_identifier"], "4067282801455")
        self.assertEqual(parsed[0]["period_hint"], "12M")
        self.assertEqual(parsed[1]["ean"], "4067282801462")
        self.assertEqual(parsed[1]["period_hint"], "3M")

    def test_duplicate_file_rejected_and_existing_catalog_skus_skipped_without_api_calls(self) -> None:
        from services.import_pipeline import ImportValidationError

        class CountingOtto:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get_marketplace_status(self, sku: str):
                self.calls.append(sku)
                return {"sku": sku, "moin": f"M-{sku}", "errors": [{"code": "602016 - LOW_PERFORMER", "title": "12M"}]}

            def get_product(self, sku: str):
                return {
                    "sku": sku, "ean": sku,
                    "productDescription": {"productLine": f"Dedup Item {sku}", "category": "Sofa", "brandId": "6HMOZBOU"},
                    "pricing": {"standardPrice": {"amount": 299.0, "currency": "EUR"}},
                }

        class DirectAftercool(AftercoolLookupService):
            def __init__(self) -> None:
                super().__init__(
                    runtime_mode=RuntimeMode.PRODUCTION,
                    runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
                )

            def lookup_product(self, account: str, identifier: str, factory_hint=None):
                return {"status": "NOT_FOUND_CONFIRMED", "verified": True, "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT"}

        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        runtime_cfg = RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True, aftercool_read_enabled=True)
        rows1 = parse_import_file("batch1.csv", b"new_ean\n4067282900001\n4067282900002\n")
        with self.engine.begin() as conn:
            run1, created1 = pipeline.enqueue_import(
                conn, account="XL", rows=rows1, filename="batch1.csv", run_mode="BASELINE",
                idempotency_key="dedup-file-key-1", principal="op", content_hash="same-file-hash-111",
            )
            self.assertTrue(created1)

        # 1) Uploading the exact same content_hash with a new Idempotency-Key must be rejected (409).
        with self.assertRaises(ImportValidationError) as ctx:
            with self.engine.begin() as conn:
                pipeline.enqueue_import(
                    conn, account="XL", rows=rows1, filename="batch1_copy.csv", run_mode="BASELINE",
                    idempotency_key="dedup-file-key-2", principal="op", content_hash="same-file-hash-111",
                )
        self.assertEqual(ctx.exception.code, "DUPLICATE_FILE_IMPORT")
        self.assertEqual(ctx.exception.status_code, 409)

        otto1 = CountingOtto()
        res1 = process_import_run(
            self.engine, run1["runId"],
            otto_client_factory=lambda _acc: otto1,
            aftercool_service_factory=DirectAftercool,
            runtime_config=runtime_cfg,
        )
        self.assertEqual(res1["runState"], "SUCCEEDED")
        self.assertEqual(len(otto1.calls), 2)

        # 2) Uploading a NEW file overlapping with 4067282900001 & 4067282900002 + 1 brand-new SKU
        # must skip the 2 existing SKUs immediately (zero OTTO/Aftercool lookups) and import only the new one.
        rows2 = parse_import_file("batch2.csv", b"new_ean\n4067282900001\n4067282900002\n4067282900003\n")
        with self.engine.begin() as conn:
            run2, created2 = pipeline.enqueue_import(
                conn, account="XL", rows=rows2, filename="batch2.csv", run_mode="BASELINE",
                idempotency_key="dedup-file-key-3", principal="op", content_hash="different-file-hash-222",
            )
            self.assertTrue(created2)

        otto2 = CountingOtto()
        res2 = process_import_run(
            self.engine, run2["runId"],
            otto_client_factory=lambda _acc: otto2,
            aftercool_service_factory=DirectAftercool,
            runtime_config=runtime_cfg,
        )
        self.assertEqual(otto2.calls, ["4067282900003"])
        self.assertEqual(res2["summary"]["alreadyInCatalogCount"], 2)
        self.assertEqual(res2["summary"]["technicalErrorCount"], 0)
        with self.engine.connect() as conn:
            total_open = conn.execute(
                text(
                    "SELECT COUNT(*) FROM candidate_episodes "
                    "WHERE account='XL' AND data_scope='PRODUCTION' AND episode_state='OPEN' "
                    "AND observed_sku IN ('4067282900001','4067282900002','4067282900003')"
                )
            ).scalar_one()
            self.assertEqual(total_open, 3)

    def test_cross_account_file_and_snapshot_eans_are_blocked(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from services.import_pipeline import ImportValidationError

        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        xl_rows = parse_import_file("xl_new_3m.csv", b"new_ean\n4067282801455\n4067282801462\n")

        # 1) Filename clearly belonging to XL must be blocked on JV
        with self.assertRaises(ImportValidationError) as ctx_name:
            with self.engine.begin() as conn:
                pipeline.enqueue_import(
                    conn, account="JV", rows=xl_rows, filename="xl_new_3m.xlsx", run_mode="BASELINE",
                    idempotency_key="cross-account-name-1", principal="op", content_hash="cross-name-hash-1",
                )
        self.assertEqual(ctx_name.exception.code, "ACCOUNT_FILE_MISMATCH")
        self.assertEqual(ctx_name.exception.status_code, 422)

        # 2) Even if renamed to a neutral filename, XL EANs detected in XL SQLite snapshot must be blocked on JV
        with tempfile.TemporaryDirectory() as tmpdir:
            xl_db = Path(tmpdir) / "otto_xl_subset.db"
            jv_db = Path(tmpdir) / "otto_jv_subset.db"
            for db_path, skus in (
                (xl_db, ["4067282801455", "4067282801462"]),
                (jv_db, ["4062292000034"]),
            ):
                con = sqlite3.connect(db_path)
                con.execute("CREATE TABLE products (sku TEXT PRIMARY KEY, raw_json TEXT)")
                con.executemany("INSERT INTO products (sku, raw_json) VALUES (?, ?)", [(s, "{}") for s in skus])
                con.commit()
                con.close()

            with patch.dict(os.environ, {"OTTORESTART_OTTO_SNAPSHOT_DIR": tmpdir}):
                with self.assertRaises(ImportValidationError) as ctx_snap:
                    with self.engine.begin() as conn:
                        pipeline.enqueue_import(
                            conn, account="JV", rows=xl_rows, filename="renamed_export.xlsx", run_mode="BASELINE",
                            idempotency_key="cross-account-snap-1", principal="op", content_hash="cross-snap-hash-1",
                        )
                self.assertEqual(ctx_snap.exception.code, "ACCOUNT_FILE_MISMATCH")
                self.assertEqual(ctx_snap.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()

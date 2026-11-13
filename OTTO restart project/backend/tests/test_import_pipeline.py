from __future__ import annotations

import os
import io
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from jobs.service import JobExecutionError
from migration_runner import upgrade
from runtime_mode import RuntimeConfig, RuntimeMode
from services.import_pipeline import (
    ImportConflict,
    ImportPipeline,
    _persist_source_product,
    _resolve_import_row,
    parse_import_file,
    process_import_run,
)


CODE_3M_RESPONSE = {
    "sku": "sku-3m",
    "moin": "MOIN-3M",
    "marketPlaceStatus": "REJECTED",
    "errors": [{"code": "602016 - Nonseller", "title": "No Nettoumsatz in drei Monaten"}],
}


class FakeOtto:
    def __init__(self, response=None, product=None, prices=None, status_error=None, price_error=None):
        self.response = response or CODE_3M_RESPONSE
        self.product = product or {"sku": "sku-3m", "ean": "4006381333931", "title": "Confirmed item"}
        self.prices = prices or {"sku": "sku-3m", "standardPrice": {"amount": 42.5, "currency": "EUR"}}
        self.status_error = status_error
        self.price_error = price_error
        self.status_calls = 0
        self.product_calls = 0
        self.price_calls = 0

    def get_marketplace_status(self, sku):
        self.status_calls += 1
        if self.status_error:
            raise self.status_error
        return {**self.response, "sku": sku}

    def get_product(self, sku):
        self.product_calls += 1
        return {**self.product, "sku": sku}

    def get_product_prices(self, sku):
        self.price_calls += 1
        if self.price_error:
            raise self.price_error
        return {"sku": sku, **self.prices}


class FakeAftercool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def lookup_product(self, account, ean, factory_hint=None):
        self.calls.append((account, ean, factory_hint))
        return {"account": account, "ean": ean, **self.result}


class ImportPipelineUnitTests(unittest.TestCase):
    def test_csv_keeps_text_identifiers_and_separates_sku_from_ean(self):
        content = (
            "SKU;EAN;Periode;Fabrik\n"
            "SKU-0007;04006381333931;3M;F-1\n"
        ).encode()
        rows = parse_import_file("items.csv", content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "SKU-0007")
        self.assertEqual(rows[0]["ean"], "04006381333931")
        self.assertEqual(rows[0]["period_hint"], "3M")
        self.assertEqual(rows[0]["factory_hint"], "F-1")

    def test_xlsx_numeric_format_and_text_preserve_leading_zeroes(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["SKU", "EAN"])
        sheet.append([123, "04006381333931"])
        sheet["A2"].number_format = "00000"
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        row = parse_import_file("items.xlsx", output.getvalue())[0]
        self.assertEqual(row["sku"], "00123")
        self.assertEqual(row["ean"], "04006381333931")

    def test_preview_marks_duplicate_and_invalid_rows_without_api_or_db(self):
        rows = parse_import_file(
            "items.csv",
            b"SKU;EAN\nsku-1;4006381333931\nsku-1;4006381333931\nsku-2;400638133393\n",
        )
        result = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION).preview("JV", rows, "items.csv")
        self.assertEqual(result["counts"], {"valid": 1, "duplicate": 1, "invalid": 1})
        self.assertEqual([item["rowState"] for item in result["items"]], ["VALID", "DUPLICATE", "INVALID"])

    def test_plain_text_period_hint_is_not_a_restriction_classification(self):
        rows = parse_import_file("list.txt", b"sku-ending-in-4 12M\n")
        result = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION).preview("XL", rows, "list.txt")
        self.assertEqual(rows[0]["period_hint"], "12M")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["items"][0]["rowState"], "VALID")
        self.assertNotIn("period", result["items"][0])

    def test_parse_limits_and_unsupported_extensions_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "20 MB"):
            parse_import_file("a.txt", b"x" * 20_000_001)
        with self.assertRaisesRegex(ValueError, "Use a .xlsx"):
            parse_import_file("a.xls", b"x")

    def test_import_row_limit_accepts_50000_and_rejects_the_next_row(self):
        def content(row_count: int) -> bytes:
            return ("SKU\n" + "\n".join(f"large-import-{index:05d}" for index in range(row_count)) + "\n").encode()

        accepted = parse_import_file("large.txt", content(50_000))
        self.assertEqual(len(accepted), 50_000)
        with self.assertRaisesRegex(ValueError, "at most 50000"):
            parse_import_file("too-large.txt", content(50_001))

    def test_candidate_requires_confirmed_ean_before_aftercool_lookup(self):
        otto = FakeOtto(product={"sku": "sku-3m", "ean": None})
        aftercool = FakeAftercool({"status": "NOT_FOUND_CONFIRMED", "verified": True, "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT"})
        result = _resolve_import_row(
            "XL", {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": None},
            otto_client=otto, aftercool_service=aftercool, status_service=__import__("services.otto_status_service", fromlist=["OttoStatusService"]).OttoStatusService(),
        )
        self.assertEqual(result["row_state"], "IDENTITY_UNRESOLVED")
        self.assertEqual(result["error_code"], "OTTO_EAN_UNCONFIRMED")
        self.assertEqual(aftercool.calls, [])

    def test_aftercool_not_implemented_does_not_become_direct_otto(self):
        otto = FakeOtto()
        aftercool = FakeAftercool({"status": "NOT_IMPLEMENTED", "verified": False, "data_scope": "UNVERIFIED"})
        from services.otto_status_service import OttoStatusService

        result = _resolve_import_row(
            "XL", {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": None},
            otto_client=otto, aftercool_service=aftercool, status_service=OttoStatusService(),
        )
        self.assertEqual(result["route_status"], "NOT_IMPLEMENTED")
        self.assertIsNone(result["source_type"])
        self.assertEqual(result["row_state"], "IDENTITY_UNRESOLVED")

    def test_only_confirmed_aftercool_absence_routes_to_direct_otto(self):
        from services.otto_status_service import OttoStatusService

        aftercool = FakeAftercool({
            "status": "NOT_FOUND_CONFIRMED", "found": False, "verified": True,
            "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT",
            "lookup_identifier": "4006381333931", "ean": None,
        })
        result = _resolve_import_row(
            "XL", {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": None},
            otto_client=FakeOtto(), aftercool_service=aftercool, status_service=OttoStatusService(),
        )
        self.assertEqual(result["source_type"], "OTTO_DIRECT")
        self.assertEqual(result["route_status"], "NOT_FOUND_CONFIRMED")
        self.assertEqual(result["source_record"]["title"], "Confirmed item")

    def test_aftercool_ambiguous_and_lookup_error_never_route_to_direct(self):
        from services.otto_status_service import OttoStatusService

        for status in ("AMBIGUOUS", "LOOKUP_ERROR"):
            aftercool = FakeAftercool({"status": status, "verified": False, "data_scope": "UNVERIFIED"})
            result = _resolve_import_row(
                "XL", {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": None},
                otto_client=FakeOtto(), aftercool_service=aftercool, status_service=OttoStatusService(),
            )
            self.assertIsNone(result["source_type"])
            self.assertNotEqual(result["source_type"], "OTTO_DIRECT")
            self.assertEqual(result["row_state"], "ERROR" if status == "LOOKUP_ERROR" else "IDENTITY_UNRESOLVED")

    def test_confirmed_aftercool_match_routes_to_aftercool(self):
        from services.otto_status_service import OttoStatusService

        aftercool = FakeAftercool({
            "status": "FOUND", "found": True, "verified": True,
            "data_scope": "PRODUCTION", "source_type": "AFTERCOOL",
            "aftercool_product_id": "AC-123", "sku": "source-sku", "ean": "4006381333931",
            "factory_id": "F-1", "factory_name": "Factory", "name": "Confirmed source name",
            "source_file": "Source__PL__F-1.csv", "source_factory_id": "F-1",
            "title": "Legacy Aftercool title",
        })
        result = _resolve_import_row(
            "XL", {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": "4006381333931"},
            otto_client=FakeOtto(), aftercool_service=aftercool, status_service=OttoStatusService(),
        )
        self.assertEqual(result["row_state"], "VALID")
        self.assertEqual(result["source_type"], "AFTERCOOL")
        self.assertEqual(result["source_record"]["aftercool_product_id"], "AC-123")
        self.assertEqual(result["source_record"]["title"], "Confirmed source name")
        self.assertEqual(result["source_record"]["price_cents"], 4250)
        self.assertEqual(result["source_record"]["aftercool_lookup"]["source_file"], "Source__PL__F-1.csv")

    def test_protected_factory_keeps_diagnostics_without_routing_to_direct_otto(self):
        from services.otto_status_service import OttoStatusService

        result = _resolve_import_row(
            "XL", {"sku": "sku-3m"}, otto_client=FakeOtto(),
            aftercool_service=FakeAftercool({
                "status": "AMBIGUOUS", "verified": False, "data_scope": "UNVERIFIED",
                "reason_code": "PROTECTED_FACTORY_MATCH", "excluded_factory_ids": ["protected-factory"],
                "factory_exclusion_policy_sha256": "a" * 64,
            }), status_service=OttoStatusService(),
        )
        self.assertEqual(result["error_code"], "PROTECTED_FACTORY_MATCH")
        self.assertEqual(result["row_state"], "IDENTITY_UNRESOLVED")
        self.assertIsNone(result["source_type"])
        self.assertIsNone(result["source_record"])
        self.assertEqual(result["aftercool_lookup"]["excluded_factory_ids"], ["protected-factory"])

    def test_aftercool_name_is_used_as_title_and_otto_content_is_retained_with_one_image(self):
        from services.otto_status_service import OttoStatusService

        account = "JV"
        ean = "4006381333931"
        otto = FakeOtto(
            product={
                "sku": "sku-3m", "ean": ean, "title": "OTTO title", "category": "OTTO category",
                "productDescription": {"productName": "OTTO product name", "attributes": [{"name": "Material", "value": "Holz"}]},
                "mediaAssets": [
                    {"url": "javascript:alert(1)"},
                    {"url": "https://images.otto.example/first.jpg"},
                    {"url": "https://images.otto.example/second.jpg"},
                ],
            },
            prices={"sku": "sku-3m", "standardPrice": {"amount": "19.99", "currency": "EUR"}},
        )
        aftercool = FakeAftercool({
            "status": "FOUND", "found": True, "verified": True, "data_scope": "PRODUCTION",
            "source_type": "AFTERCOOL", "aftercool_product_id": "AC-JV-1", "sku": "ac-source-sku",
            "ean": ean, "factory_id": "JV-FACTORY", "factory_name": "JV Factory",
            "name": "Esstisch Esszimmertisch Tisch Esszimmer Holz Schwarz Esszimmermöbel",
            "title": "Legacy Aftercool title", "category": "Aftercool category",
            "image_url": "https://aftercool.example/not-used.jpg",
        })
        result = _resolve_import_row(
            account, {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": ean},
            otto_client=otto, aftercool_service=aftercool, status_service=OttoStatusService(),
        )

        self.assertEqual(result["row_state"], "VALID")
        self.assertEqual(aftercool.calls, [(account, ean, None)])
        source = result["source_record"]
        self.assertEqual(source["title"], "Esstisch Esszimmertisch Tisch Esszimmer Holz Schwarz Esszimmermöbel")
        self.assertEqual(source["category"], "OTTO category")
        self.assertEqual(source["price_cents"], 1999)
        self.assertEqual(source["image_url"], "https://images.otto.example/first.jpg")
        self.assertEqual(source["attributes"], [{"name": "Material", "value": "Holz"}])
        self.assertEqual(len(source["otto_product_data"]["mediaAssets"]), 1)
        self.assertEqual(source["aftercool_lookup"]["name"], source["title"])

    def test_price_identity_mismatch_or_lookup_error_does_not_create_a_source_route(self):
        from services.otto_status_service import OttoStatusService

        for otto in (
            FakeOtto(prices={"sku": "other-sku", "standardPrice": {"amount": 1, "currency": "EUR"}}),
            FakeOtto(price_error=RuntimeError("secret-safe-typed-error")),
        ):
            aftercool = FakeAftercool({"status": "NOT_FOUND_CONFIRMED", "verified": True, "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT"})
            result = _resolve_import_row(
                "JV", {"raw_identifier": "sku-3m", "sku": "sku-3m", "ean": None},
                otto_client=otto, aftercool_service=aftercool, status_service=OttoStatusService(),
            )
            self.assertNotEqual(result.get("row_state"), "VALID")
            self.assertIsNone(result.get("source_type"))
            self.assertEqual(aftercool.calls, [])

    def test_non_candidate_does_not_call_product_or_aftercool(self):
        from services.otto_status_service import OttoStatusService

        otto = FakeOtto(response={"sku": "plain-sku", "errors": []})
        aftercool = FakeAftercool({})
        result = _resolve_import_row(
            "JV", {"raw_identifier": "plain-sku", "sku": "plain-sku", "ean": None},
            otto_client=otto, aftercool_service=aftercool, status_service=OttoStatusService(),
        )
        self.assertEqual(result["classification"], "NONE")
        self.assertEqual(otto.product_calls, 0)
        self.assertEqual(aftercool.calls, [])


class ImportPipelinePostgresTests(unittest.TestCase):
    """Integration tests require an explicitly supplied loopback PG15 test server."""

    @classmethod
    def setUpClass(cls):
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for isolated import-run tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Import tests require a loopback PostgreSQL database.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("Import tests require a disposable database name containing 'test'.")
        if os.environ.get("OTTORESTART_DATABASE_URL", "").strip() and make_url(os.environ["OTTORESTART_DATABASE_URL"]) == cls.base_url:
            raise RuntimeError("Application and test databases must be different.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Import tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls):
        engine = getattr(cls, "admin_engine", None)
        if engine is not None:
            engine.dispose()

    def setUp(self):
        self.database_name = f"ottorestart_import_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.engine = create_engine(self.base_url.set(database=self.database_name), pool_pre_ping=True)
        upgrade(self.engine)

    def tearDown(self):
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def create_run(self):
        rows = parse_import_file("items.csv", b"SKU;EAN;Periode\nsku-3m;4006381333931;3M\n")
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        with self.engine.begin() as connection:
            return pipeline.enqueue_import(
                connection, account="XL", rows=rows, filename="items.csv", run_mode="BASELINE",
                idempotency_key="initial-file-run-001", principal="operator-test", content_hash="abc123",
            )

    def test_commit_is_atomic_idempotent_and_does_not_touch_ean_pool(self):
        first, created = self.create_run()
        duplicate, duplicate_created = self.create_run()
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first["runId"], duplicate["runId"])
        self.assertEqual(first["jobId"], duplicate["jobId"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM import_runs WHERE data_scope='PRODUCTION'")).scalar_one(), 1)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM import_rows WHERE data_scope='PRODUCTION'")).scalar_one(), 1)
            self.assertEqual(connection.execute(text("SELECT job_type FROM background_jobs")).scalar_one(), "import.process")
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM ean_pool WHERE status='RESERVED'")).scalar_one(), 0)

    def test_reusing_idempotency_key_for_other_payload_conflicts(self):
        self.create_run()
        rows = parse_import_file("items.csv", b"SKU\nother-sku\n")
        with self.engine.begin() as connection:
            with self.assertRaises(ImportConflict):
                ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION).enqueue_import(
                    connection, account="XL", rows=rows, filename="items.csv", run_mode="BASELINE",
                    idempotency_key="initial-file-run-001", principal="operator-test", content_hash="different",
                )

    def test_concurrent_duplicate_commit_creates_one_run_and_job(self):
        rows = parse_import_file("items.csv", b"SKU\nconcurrent-sku\n")
        barrier = Barrier(2)

        def submit():
            with self.engine.begin() as connection:
                barrier.wait(timeout=5)
                return ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION).enqueue_import(
                    connection, account="XL", rows=rows, filename="items.csv", run_mode="BASELINE",
                    idempotency_key="concurrent-import-001", principal="operator-test", content_hash="same-content",
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: submit(), range(2)))
        self.assertEqual(sum(created for _result, created in results), 1)
        self.assertEqual(len({result["runId"] for result, _created in results}), 1)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM import_runs WHERE data_scope='PRODUCTION'")).scalar_one(), 1)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM background_jobs WHERE account='XL'")).scalar_one(), 1)

    def test_source_product_record_can_be_persisted_for_confirmed_direct_identity(self):
        run, _ = self.create_run()
        with self.engine.begin() as connection:
            source_id = _persist_source_product(
                connection, account="XL", run_id=run["runId"], run_mode="BASELINE",
                period="3M", sku="sku-3m", ean="4006381333931",
                source={"title": "Confirmed item"}, source_type="OTTO_DIRECT",
            )
        self.assertTrue(source_id)

    def test_fake_adapters_create_verified_episode_and_resume_after_retryable_failure(self):
        run, _ = self.create_run()
        runtime = RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True)
        direct = FakeAftercool({
            "status": "NOT_FOUND_CONFIRMED", "found": False, "verified": True,
            "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT",
            "lookup_identifier": "4006381333931", "ean": None,
        })

        class RetryOnceOtto(FakeOtto):
            failed = False

            def get_marketplace_status(self, sku):
                if not self.failed:
                    self.failed = True
                    error = RuntimeError("temporary transport failure")
                    error.code = "NETWORK_ERROR"
                    error.retryable = True
                    raise error
                return {**CODE_3M_RESPONSE, "sku": sku}

        otto = RetryOnceOtto()
        with self.assertRaises(JobExecutionError):
            process_import_run(
                self.engine, run["runId"], otto_client_factory=lambda _account: otto,
                aftercool_service_factory=lambda: direct, runtime_config=runtime, job_attempt=1, max_attempts=3,
            )
        with self.engine.connect() as connection:
            row_state = connection.execute(text("SELECT row_state FROM import_rows WHERE data_scope='PRODUCTION'")).scalar_one()
        self.assertEqual(row_state, "ERROR")

        result = process_import_run(
            self.engine, run["runId"], otto_client_factory=lambda _account: otto,
            aftercool_service_factory=lambda: direct, runtime_config=runtime, job_attempt=2, max_attempts=3,
        )
        with self.engine.connect() as connection:
            persisted_row = connection.execute(text("SELECT row_state, row_json, errors_json FROM import_rows WHERE data_scope='PRODUCTION'")).mappings().one()
            source_count = connection.execute(text("SELECT COUNT(*) FROM source_products WHERE data_scope='PRODUCTION'")).scalar_one()
            processed = connection.execute(text("SELECT processed_count FROM import_runs WHERE data_scope='PRODUCTION'")).scalar_one()
        self.assertEqual(result["runState"], "SUCCEEDED", f"row={dict(persisted_row)}, sources={source_count}")
        self.assertEqual(processed, 1)
        with self.engine.connect() as connection:
            episode = connection.execute(text("SELECT period, origin, confirmed_ean FROM candidate_episodes WHERE data_scope='PRODUCTION'")).mappings().one()
            self.assertEqual(episode["period"], "3M")
            self.assertEqual(episode["origin"], "BASELINE")
            self.assertEqual(episode["confirmed_ean"], "4006381333931")
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM ean_pool WHERE status='RESERVED'")).scalar_one(), 0)

    def test_aftercool_factory_selection_provenance_is_persisted_per_import_row(self):
        run, _ = self.create_run()
        aftercool = FakeAftercool({
            "status": "FOUND", "found": True, "verified": True,
            "data_scope": "PRODUCTION", "source_type": "AFTERCOOL",
            "aftercool_product_id": "product-100", "factory_id": "409886",
            "factory_name": "Factory One", "reported_account": "XL",
            "source_factory_id": "409886", "source_file": "source__409886.csv",
            "excluded_factory_match_count": 2,
            "factory_exclusion_policy_sha256": "a" * 64,
            "sku": "sku-3m", "ean": "4006381333931", "name": "Confirmed Aftercool name",
        })
        result = process_import_run(
            self.engine,
            run["runId"],
            otto_client_factory=lambda _account: FakeOtto(),
            aftercool_service_factory=lambda: aftercool,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True),
        )
        with self.engine.connect() as connection:
            persisted = connection.execute(
                text("SELECT row_state, row_json FROM import_rows WHERE run_id=:run_id AND data_scope='PRODUCTION'"),
                {"run_id": run["runId"]},
            ).mappings().one()
            progress = connection.execute(
                text("SELECT processed_count, total_rows FROM import_runs WHERE id=:run_id"),
                {"run_id": run["runId"]},
            ).mappings().one()
        row = persisted["row_json"]
        selection = row["aftercoolSelection"]
        self.assertEqual(result["runState"], "SUCCEEDED")
        self.assertEqual(aftercool.calls, [("XL", "4006381333931", None)])
        self.assertEqual(progress["processed_count"], 1)
        self.assertEqual(progress["total_rows"], 1)
        self.assertEqual(persisted["row_state"], "VALID")
        self.assertEqual(row["sourceType"], "AFTERCOOL")
        self.assertEqual(selection["source_file"], "source__409886.csv")
        self.assertEqual(selection["excluded_factory_match_count"], 2)
        self.assertEqual(selection["factory_exclusion_policy_sha256"], "a" * 64)

    def test_incremental_import_marks_only_confirmed_candidates_new_and_keeps_mixed_errors(self):
        rows = parse_import_file(
            "mixed.csv",
            b"SKU\nnew-candidate\nambiguous-candidate\nno-restriction\n",
        )
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        with self.engine.begin() as connection:
            run, _ = pipeline.enqueue_import(
                connection, account="XL", rows=rows, filename="mixed.csv", run_mode="INCREMENTAL",
                idempotency_key="mixed-import-run-001", principal="operator-test", content_hash="mixed-content",
            )

        class MixedOtto:
            def get_marketplace_status(self, sku):
                if sku == "new-candidate":
                    return {**CODE_3M_RESPONSE, "sku": sku}
                if sku == "ambiguous-candidate":
                    return {
                        "sku": sku,
                        "errors": [{"code": "602016 - Nonseller", "title": "In zwölf Monaten gab es keinen Nettoumsatz."}],
                    }
                return {"sku": sku, "errors": []}

            def get_product(self, sku):
                if sku != "new-candidate":
                    raise AssertionError("ambiguous and unrestricted rows must not trigger product lookup")
                return {"sku": sku, "ean": "4006381333931", "title": "Confirmed item"}

            def get_product_prices(self, sku):
                if sku != "new-candidate":
                    raise AssertionError("ambiguous and unrestricted rows must not trigger price lookup")
                return {"sku": sku, "standardPrice": {"amount": "19.99", "currency": "EUR"}}

        direct = FakeAftercool({
            "status": "NOT_FOUND_CONFIRMED", "found": False, "verified": True,
            "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT",
            "lookup_identifier": "4006381333931", "ean": None,
        })
        result = process_import_run(
            self.engine, run["runId"], otto_client_factory=lambda _account: MixedOtto(),
            aftercool_service_factory=lambda: direct,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True),
        )

        self.assertEqual(result["runState"], "PARTIAL")
        self.assertEqual(result["summary"]["rowStates"], {"VALID": 2, "IDENTITY_UNRESOLVED": 1})
        self.assertEqual(len(direct.calls), 1)
        with self.engine.connect() as connection:
            episode = connection.execute(
                text("SELECT observed_sku, origin, period FROM candidate_episodes WHERE data_scope='PRODUCTION'")
            ).mappings().one()
            source_is_new = connection.execute(
                text("SELECT is_new_candidate FROM source_products WHERE data_scope='PRODUCTION'")
            ).scalar_one()
        self.assertEqual(dict(episode), {"observed_sku": "new-candidate", "origin": "NEW", "period": "3M"})
        self.assertTrue(source_is_new)

    def test_duplicate_rows_are_retained_but_only_first_is_queued_for_processing(self):
        rows = parse_import_file("dupes.csv", b"SKU\nduplicate-sku\nduplicate-sku\n")
        with self.engine.begin() as connection:
            run, _ = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION).enqueue_import(
                connection, account="JV", rows=rows, filename="dupes.csv", run_mode="BASELINE",
                idempotency_key="duplicate-import-001", principal="operator-test", content_hash="duplicate-content",
            )
        with self.engine.connect() as connection:
            states = connection.execute(
                text("SELECT row_state FROM import_rows WHERE run_id=:run_id ORDER BY row_number"),
                {"run_id": run["runId"]},
            ).scalars().all()
            jobs = connection.execute(text("SELECT COUNT(*) FROM background_jobs WHERE account='JV'")).scalar_one()
        self.assertEqual(states, ["PENDING", "DUPLICATE"])
        self.assertEqual(jobs, 1)

    def test_large_import_is_chained_in_deterministic_chunks(self):
        raw = "SKU\n" + "\n".join(f"plain-sku-{index:04d}" for index in range(501)) + "\n"
        rows = parse_import_file("items.csv", raw.encode())
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        with self.engine.begin() as connection:
            run, _ = pipeline.enqueue_import(
                connection, account="JV", rows=rows, filename="items.csv", run_mode="BASELINE",
                idempotency_key="large-import-run-001", principal="operator-test", content_hash="large-content",
            )

        class NoRestrictionOtto:
            def get_marketplace_status(self, sku):
                return {"sku": sku, "errors": []}

            def get_product(self, _sku):
                raise AssertionError("non-candidates must not trigger product lookup")

        runtime = RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True)
        first = process_import_run(
            self.engine, run["runId"], otto_client_factory=lambda _account: NoRestrictionOtto(),
            aftercool_service_factory=lambda: FakeAftercool({}), runtime_config=runtime, chunk_number=1,
        )
        self.assertEqual(first["runState"], "RUNNING")
        self.assertTrue(first["nextJobId"])
        final = process_import_run(
            self.engine, run["runId"], otto_client_factory=lambda _account: NoRestrictionOtto(),
            aftercool_service_factory=lambda: FakeAftercool({}), runtime_config=runtime, chunk_number=2,
        )
        self.assertEqual(final["runState"], "SUCCEEDED")
        self.assertEqual(final["summary"]["observations"], 501)


if __name__ == "__main__":
    unittest.main()

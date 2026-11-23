from __future__ import annotations

import unittest

from runtime_mode import RuntimeMode
from services.aftercool_lookup_service import AftercoolLookupService
from services.import_pipeline import ImportPipeline
from services.otto_demo_fixture_service import CODE_3M, OttoDemoFixtureService
from services.otto_status_service import OttoStatusService


class EmptyResult:
    def fetchall(self):
        return []


class ReadOnlyConnection:
    """Fake connection that rejects every SQL statement except the duplicate check."""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, _params=None):
        sql = str(statement).strip()
        self.statements.append(sql)
        if not sql.lower().startswith("select"):
            raise AssertionError(f"unexpected database write in offline test: {sql[:40]}")
        return EmptyResult()


class OttoStatusServiceTests(unittest.TestCase):
    def test_unknown_sku_and_input_period_hint_stay_unverified(self):
        service = OttoStatusService()

        result = service.get_status("XL", "sku-ending-in-4", hint_period="12M")

        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertFalse(result["is_candidate"])
        self.assertIsNone(result["period"])
        self.assertEqual(result["source"], "NOT_IMPLEMENTED")

    def test_demo_fixture_adapter_rejects_production(self):
        with self.assertRaises(RuntimeError):
            OttoDemoFixtureService(runtime_mode=RuntimeMode.PRODUCTION)

    def test_status_service_does_not_expose_runtime_fixture_registration(self):
        service = OttoStatusService()

        self.assertFalse(hasattr(service, "register_fixture"))

    def test_demo_fixture_is_explicit_and_account_scoped(self):
        service = OttoDemoFixtureService(runtime_mode=RuntimeMode.DEMO)
        service.register_fixture("XL", "demo-sku", "3M", CODE_3M, "Demo only")

        demo_result = service.get_status("XL", "demo-sku")
        other_account = service.get_status("JV", "demo-sku")

        self.assertEqual(demo_result["status"], "DEMO_ONLY")
        self.assertEqual(demo_result["period"], "3M")
        self.assertFalse(demo_result["verified"])
        self.assertEqual(demo_result["data_scope"], "DEMO_ONLY")
        self.assertEqual(other_account["status"], "UNVERIFIED")
        self.assertIsNone(other_account["period"])
        self.assertEqual(service.get_status("XL", "4069424040821")["status"], "UNVERIFIED")

    def test_status_lookup_does_not_enable_unimplemented_network_access(self):
        service = OttoStatusService()

        result = service.get_status("XL", "unknown-sku")

        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["source"], "NOT_IMPLEMENTED")


class AftercoolLookupServiceTests(unittest.TestCase):
    def test_fixture_absence_and_factory_hint_do_not_mean_direct_otto(self):
        service = AftercoolLookupService(runtime_mode=RuntimeMode.PRODUCTION, environment={})

        result = service.lookup_product("XL", "unknown-ean", factory_hint="factory-1")

        self.assertEqual(result["status"], "NOT_IMPLEMENTED")
        self.assertIsNone(result["found"])
        self.assertIsNone(result["source_type"])
        self.assertIsNone(result["factory_id"])

    def test_demo_fixture_results_are_explicit_and_account_scoped(self):
        service = AftercoolLookupService(runtime_mode=RuntimeMode.DEMO)
        service.register_fixture("XL", "demo-ean", factory_id="factory-1", title="Demo item")
        service.register_fixture("XL", "direct-ean", is_direct_otto=True)

        found = service.lookup_product("XL", "demo-ean")
        direct = service.lookup_product("XL", "direct-ean")
        other_account = service.lookup_product("JV", "demo-ean")

        self.assertEqual(found["status"], "DEMO_FOUND")
        self.assertEqual(found["source_type"], "AFTERBUY")
        self.assertEqual(found["data_scope"], "DEMO_ONLY")
        self.assertEqual(direct["status"], "DEMO_NOT_FOUND_CONFIRMED")
        self.assertEqual(direct["source_type"], "OTTO_DIRECT")
        self.assertIsNone(other_account["found"])
        self.assertIsNone(other_account["source_type"])

    def test_fixtures_cannot_be_registered_in_production(self):
        service = AftercoolLookupService(runtime_mode=RuntimeMode.PRODUCTION)

        with self.assertRaises(RuntimeError):
            service.register_fixture("XL", "demo-ean", is_direct_otto=True)


class ImportPipelineTests(unittest.TestCase):
    def test_period_hint_does_not_create_candidate_or_route(self):
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.PRODUCTION)
        connection = ReadOnlyConnection()

        result = pipeline.execute_import(
            connection,
            "XL",
            "sku-ending-in-4 12M",
            dry_run=True,
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["previewCompleted"])
        self.assertEqual(result["status"], "UNVERIFIED")
        summary = result["summary"]
        self.assertEqual(summary["status"], "UNVERIFIED")
        self.assertEqual(summary["uniqueValidCount"], 0)
        self.assertEqual(summary["period3mCount"], 0)
        self.assertEqual(summary["period12mCount"], 0)
        self.assertEqual(summary["afterbuyCount"], 0)
        self.assertEqual(summary["directOttoCount"], 0)
        self.assertEqual(summary["unverifiedCount"], 1)
        self.assertIsNone(summary["unverifiedSample"][0]["period"])
        self.assertEqual(len(connection.statements), 0)

    def test_demo_candidate_is_not_routed_when_aftercool_is_unverified(self):
        otto = OttoDemoFixtureService(runtime_mode=RuntimeMode.DEMO)
        otto.register_fixture("XL", "demo-sku", "3M", CODE_3M, "Demo only")
        pipeline = ImportPipeline(otto_service=otto, runtime_mode=RuntimeMode.DEMO)
        connection = ReadOnlyConnection()

        result = pipeline.execute_import(
            connection,
            "XL",
            "demo-sku",
            dry_run=True,
        )

        summary = result["summary"]
        self.assertEqual(summary["status"], "DEMO_ONLY")
        self.assertEqual(summary["uniqueValidCount"], 0)
        self.assertEqual(summary["directOttoCount"], 0)
        self.assertEqual(summary["afterbuyCount"], 0)
        self.assertEqual(summary["unverifiedCount"], 1)
        self.assertEqual(summary["unverifiedSample"][0]["status"], "NOT_IMPLEMENTED")

    def test_explicit_demo_not_found_fixture_is_the_only_direct_route(self):
        otto = OttoDemoFixtureService(runtime_mode=RuntimeMode.DEMO)
        otto.register_fixture("XL", "demo-sku", "3M", CODE_3M, "Demo only")
        aftercool = AftercoolLookupService(runtime_mode=RuntimeMode.DEMO)
        aftercool.register_fixture("XL", "demo-sku", is_direct_otto=True)
        pipeline = ImportPipeline(otto_service=otto, aftercool_service=aftercool, runtime_mode=RuntimeMode.DEMO)

        result = pipeline.execute_import(
            ReadOnlyConnection(),
            "XL",
            "demo-sku",
            dry_run=True,
        )

        self.assertEqual(result["status"], "DEMO_ONLY")
        self.assertEqual(result["summary"]["directOttoCount"], 1)
        self.assertEqual(result["summary"]["afterbuyCount"], 0)
        self.assertEqual(result["summary"]["samplePreview"][0]["data_scope"], "DEMO_ONLY")
        self.assertFalse(result["success"])

    def test_import_commit_is_not_implemented_and_never_writes(self):
        pipeline = ImportPipeline(runtime_mode=RuntimeMode.DEMO)
        connection = ReadOnlyConnection()

        result = pipeline.execute_import(
            connection,
            "XL",
            "sku-ending-in-4 3M",
            dry_run=False,
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "NOT_IMPLEMENTED")
        self.assertEqual(len(connection.statements), 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import io
import os
import unittest
from unittest.mock import patch

from runtime_mode import RuntimeConfig, RuntimeConfigurationError, RuntimeMode


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_to_legacy_read_only_and_all_external_flags_off(self) -> None:
        config = RuntimeConfig.from_environment({})

        self.assertEqual(config.mode, RuntimeMode.LEGACY_READ_ONLY)
        self.assertFalse(config.local_writes_enabled)
        self.assertFalse(config.otto_read_enabled)
        self.assertFalse(config.aftercool_read_enabled)
        self.assertFalse(config.openai_enabled)
        self.assertFalse(config.product_write_enabled)
        self.assertFalse(config.availability_write_enabled)
        self.assertFalse(config.discovery_schedule_enabled)
        self.assertEqual(config.gtin_approved_evidence_types, frozenset())
        self.assertFalse(config.legacy_mutations_allowed)

    def test_legacy_mutations_require_demo_or_test_and_explicit_flag(self) -> None:
        for mode in (RuntimeMode.DEMO, RuntimeMode.TEST):
            config = RuntimeConfig(mode=mode, local_writes_enabled=True)
            self.assertTrue(config.legacy_mutations_allowed)

        production = RuntimeConfig(mode=RuntimeMode.PRODUCTION, local_writes_enabled=True)
        legacy = RuntimeConfig(mode=RuntimeMode.LEGACY_READ_ONLY, local_writes_enabled=True)
        demo_without_flag = RuntimeConfig(mode=RuntimeMode.DEMO)
        self.assertFalse(production.legacy_mutations_allowed)
        self.assertFalse(legacy.legacy_mutations_allowed)
        self.assertFalse(demo_without_flag.legacy_mutations_allowed)

    def test_invalid_mode_and_boolean_fail_closed(self) -> None:
        with self.assertRaises(RuntimeConfigurationError):
            RuntimeConfig.from_environment({"OTTORESTART_MODE": "MOCK"})
        with self.assertRaises(RuntimeConfigurationError):
            RuntimeConfig.from_environment({"OTTORESTART_LOCAL_WRITES_ENABLED": "sometimes"})
        with self.assertRaises(RuntimeConfigurationError):
            RuntimeConfig.from_environment({"OTTORESTART_GTIN_APPROVED_EVIDENCE_TYPES": "1INVALID"})

    def test_gtin_evidence_policy_is_explicit_and_empty_by_default(self) -> None:
        config = RuntimeConfig.from_environment(
            {"OTTORESTART_GTIN_APPROVED_EVIDENCE_TYPES": "TEST_GS1_DOC,TEST_ASSIGNMENT"}
        )
        self.assertEqual(config.gtin_approved_evidence_types, frozenset({"TEST_GS1_DOC", "TEST_ASSIGNMENT"}))


class LegacyWriteRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # The project ships psycopg v3; this deliberately unreachable test DSN
        # prevents application imports from inheriting any developer database URL.
        os.environ["OTTORESTART_DATABASE_URL"] = "postgresql+psycopg://restart_test:restart_test@127.0.0.1:65432/restart_test"

    @staticmethod
    def request_with_valid_csrf():
        from starlette.requests import Request
        from security import Principal
        import app

        origin = app.allowed_origins[0]

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/restart/test",
                "headers": [
                    (b"origin", origin.encode()),
                    (b"cookie", b"ottorestart_csrf=test-token"),
                ],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("testclient", 50000),
                "scheme": "http",
                "http_version": "1.1",
            }
        )
        request.state.principal = Principal(
            kind="operator",
            subject="test-operator",
            accounts=frozenset({"JV", "XL"}),
            permissions=frozenset({"restart:read", "restart:write"}),
            request_id="test-request-id",
            csrf_verified=True,
        )
        return request

    def assert_read_only(self, action, *, expected_code: str = "READ_ONLY_MODE") -> None:
        import app

        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("database must not be accessed")):
                with patch.object(
                    app.engine,
                    "connect",
                    side_effect=AssertionError("database must not be accessed"),
                ):
                    with self.assertRaises(app.ApiProblem) as raised:
                        action(app)

        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, expected_code)
        if expected_code == "READ_ONLY_MODE":
            self.assertEqual(raised.exception.details, {"mode": "LEGACY_READ_ONLY"})

    def test_batch_commit_is_rejected_before_database_access(self) -> None:
        import app

        payload = app.DripScheduleInput(
            account="JV",
            name="Blocked legacy write",
            operationIds=["00000000-0000-4000-8000-000000000001"],
            startDate="2026-11-04",
            dailyTime="10:00",
            timezone="Asia/Qyzylorda",
            totalDays=1,
        )
        self.assert_read_only(
            lambda current_app: current_app.create_batch(
                payload,
                self.request_with_valid_csrf(),
                idempotency_key="test-idempotency-key",
                x_csrf_token="test-token",
            ),
            expected_code="OPERATION_NOT_IMPLEMENTED",
        )

    def test_ean_pool_import_is_rejected_before_database_access(self) -> None:
        import app

        payload = app.EanImportInput(account="JV", eans=["4006381333931"])
        self.assert_read_only(
            lambda current_app: current_app.import_eans(
                payload,
                self.request_with_valid_csrf(),
                x_csrf_token="test-token",
            )
        )

    def test_library_assignment_is_rejected_before_database_access(self) -> None:
        import app

        payload = app.LibraryAssignRequest(account="JV", items=[])
        self.assert_read_only(lambda current_app: current_app.assign_library_eans(payload))

    def test_library_deactivation_routes_are_explicitly_unimplemented_without_database_access(self) -> None:
        import app

        preview = app.DeactivatePreviewRequest(account="JV", sourceEans=["4006381333931"])
        confirm = app.DeactivateConfirmRequest(account="JV", link_ids=[], reason="No remote deactivation implementation exists.")
        for mode in (RuntimeMode.LEGACY_READ_ONLY, RuntimeMode.DEMO, RuntimeMode.TEST, RuntimeMode.PRODUCTION):
            with self.subTest(mode=mode.value):
                with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig(mode=mode, local_writes_enabled=True)):
                    with patch.object(app, "connect", side_effect=AssertionError("database must not be accessed")):
                        with patch.object(app.engine, "connect", side_effect=AssertionError("database must not be accessed")):
                            for action, payload in ((app.deactivate_preview, preview), (app.deactivate_confirm, confirm)):
                                with self.assertRaises(app.ApiProblem) as raised:
                                    action(payload)
                                self.assertEqual(raised.exception.status, 501)
                                self.assertEqual(raised.exception.code, "NOT_IMPLEMENTED")

    def test_factory_completion_is_rejected_before_database_access(self) -> None:
        import app

        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("database must not be accessed")):
                with self.assertRaises(app.ApiProblem) as raised:
                    app.complete_factory(
                        "factory-1",
                        self.request_with_valid_csrf(),
                        "JV",
                        idempotency_key="factory-recalc-1",
                        x_csrf_token="test-token",
                    )
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "OPERATION_NOT_IMPLEMENTED")

    def test_upload_preview_is_local_and_available_in_read_only_mode(self) -> None:
        import app
        from fastapi import UploadFile

        upload = UploadFile(filename="items.txt", file=io.BytesIO(b"4006381333931\n"))
        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("preview must not access database")):
                result = asyncio.run(app.upload_import_file(
                    self.request_with_valid_csrf(), account="JV", dry_run=True,
                    run_mode="BASELINE", file=upload,
                    idempotency_key=None, x_csrf_token="test-token",
                ))
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["dataSourcesChecked"], ["LOCAL_FORMAT_ONLY"])

    def test_imports_preview_accepts_sku_csv_without_database_access(self) -> None:
        import app
        from fastapi import UploadFile

        upload = UploadFile(filename="items.csv", file=io.BytesIO(b"SKU;Periode\nsku-1;12M\n"))
        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("preview must not access database")):
                result = asyncio.run(app.preview_import_file(
                    self.request_with_valid_csrf(), account="JV", period="12M", file=upload,
                    x_csrf_token="test-token",
                ))
        self.assertEqual(result["items"][0]["sku"], "sku-1")
        self.assertEqual(result["items"][0]["periodHint"], "12M")
        self.assertEqual(result["summary"]["period12mCount"], 0)

    def test_paste_preview_is_local_and_period_hint_is_not_evidence(self) -> None:
        import app

        payload = app.ImportPasteRequest(account="JV", raw_text="sku-ending-in-4 12M", dry_run=True)
        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("preview must not access database")):
                result = app.paste_import_content(
                    payload, self.request_with_valid_csrf(), idempotency_key=None,
                    x_csrf_token="test-token",
                )
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["summary"]["period12mCount"], 0)

    def test_import_commit_is_blocked_before_database_access_in_default_mode(self) -> None:
        import app

        payload = app.ImportPasteRequest(account="JV", raw_text="sku-1", dry_run=False)
        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("read-only commit must not access database")):
                with self.assertRaises(app.ApiProblem) as raised:
                    app.paste_import_content(
                        payload, self.request_with_valid_csrf(), idempotency_key="import-key-0001",
                        x_csrf_token="test-token",
                    )
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "READ_ONLY_MODE")

    def test_production_commit_requires_explicit_run_mode_before_database_access(self) -> None:
        import app

        payload = app.ImportPasteRequest(account="JV", raw_text="sku-1", dry_run=False)
        enabled = RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True)
        with patch.object(app, "RUNTIME_CONFIG", enabled):
            with patch.object(app, "connect", side_effect=AssertionError("validation must precede DB access")):
                with self.assertRaises(app.ApiProblem) as raised:
                    app.paste_import_content(
                        payload, self.request_with_valid_csrf(), idempotency_key="import-key-0001",
                        x_csrf_token="test-token",
                    )
        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(raised.exception.code, "IMPORT_MODE_REQUIRED")

    def test_restart_operation_write_is_unavailable_in_default_read_only_mode(self) -> None:
        import app

        payload = app.RestartOperationCreateInput(account="XL", candidateEpisodeId="not-a-live-request")
        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()):
            with patch.object(app, "connect", side_effect=AssertionError("read-only route must not access the database")):
                with self.assertRaises(app.ApiProblem) as raised:
                    app.create_restart_operation(
                        payload,
                        self.request_with_valid_csrf(),
                        idempotency_key="s12-read-only-key",
                        x_csrf_token="test-token",
                    )
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "OPERATION_NOT_IMPLEMENTED")

    def test_restart_operation_write_rejects_account_outside_principal_before_database(self) -> None:
        import app

        payload = app.RestartOperationCreateInput(account="XL", candidateEpisodeId="not-a-live-request")
        request = self.request_with_valid_csrf()
        request.state.principal = type(request.state.principal)(
            kind="operator",
            subject="test-operator",
            accounts=frozenset({"JV"}),
            permissions=frozenset({"restart:read", "restart:write"}),
            request_id="test-request-id",
            csrf_verified=True,
        )
        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig(mode=RuntimeMode.PRODUCTION)):
            with patch.object(app, "connect", side_effect=AssertionError("scope validation must precede database access")):
                with self.assertRaises(app.ApiProblem) as raised:
                    app.create_restart_operation(
                        payload,
                        request,
                        idempotency_key="s12-account-key",
                        x_csrf_token="test-token",
                    )
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.code, "FORBIDDEN")


if __name__ == "__main__":
    unittest.main()

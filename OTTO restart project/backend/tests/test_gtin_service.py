from __future__ import annotations

import asyncio
import io
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from migration_runner import upgrade
from services.ean_pool_service import import_pool_entries
from services.gtin_service import (
    GtinEvidenceError,
    get_gtin_evidence_artifact,
    list_gtin_evidence,
    review_gtin_evidence,
    submit_gtin_evidence,
    validate_gtin_eligibility,
)

EVIDENCE_TYPES = frozenset({"TEST_GS1_EVIDENCE"})
PDF = b"%PDF-1.4\noffline evidence fixture\n%%EOF"


def ean13(seed: int) -> str:
    body = f"499{seed:09d}"
    digits = [int(char) for char in body]
    check = (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits))) % 10) % 10
    return body + str(check)


class GtinEvidencePostgresTests(unittest.TestCase):
    """Evidence and pool integration tests use disposable databases on explicit local PG15."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for GTIN evidence integration tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("GTIN evidence tests require an explicit loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("GTIN evidence tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_engine", None):
            cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_gtin_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.engine = create_engine(self.base_url.set(database=self.database_name), pool_pre_ping=True)
        upgrade(self.engine)
        self.source_id = self.create_source()
        self.ean = ean13(71_000 + int(uuid.uuid4().hex[:6], 16) % 100_000)
        with self.engine.begin() as connection:
            import_pool_entries(connection, account="XL", eans=[self.ean], actor="fixture-operator", data_scope="PRODUCTION")

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def create_source(self, account: str = "XL", source_ean: str | None = None) -> str:
        source_id = str(uuid.uuid4())
        source_ean = source_ean or ean13(800_000 + int(uuid.uuid4().hex[:6], 16) % 100_000)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, :account, 'AFTERCOOL', :ean, 'offline source', 'PRODUCTION')"
                ),
                {"id": source_id, "account": account, "ean": source_ean},
            )
        return source_id

    def submit(self, *, source_id: str | None = None, ean: str | None = None, expires_at=None, media_type="application/pdf", content=PDF):
        with self.engine.begin() as connection:
            return submit_gtin_evidence(
                connection,
                account="XL",
                ean=ean or self.ean,
                source_product_id=source_id or self.source_id,
                evidence_type="TEST_GS1_EVIDENCE",
                source_name="offline fixture",
                artifact_filename="../private-evidence.pdf",
                artifact_media_type=media_type,
                artifact_bytes=content,
                claim_basis="Fixture evidence for an isolated offline test; no real GTIN rights are asserted.",
                validity_kind="EXPIRING" if expires_at else "PERPETUAL",
                expires_at=expires_at,
                actor="fixture-operator",
            )

    def review(self, evidence_id: str, *, approved_types=EVIDENCE_TYPES, decision="APPROVE"):
        with self.engine.begin() as connection:
            return review_gtin_evidence(
                connection,
                account="XL",
                evidence_id=evidence_id,
                decision=decision,
                review_basis="Fixture reviewer decision for isolated offline tests only.",
                actor="fixture-reviewer",
                approved_evidence_types=approved_types,
            )

    def test_pool_barcode_is_eligible_without_approved_evidence(self) -> None:
        result = self.submit()
        with self.engine.connect() as connection:
            eligibility = validate_gtin_eligibility(
                connection,
                account="XL",
                ean=self.ean,
                source_product_id=self.source_id,
                approved_evidence_types=EVIDENCE_TYPES,
            )
            items, total = list_gtin_evidence(connection, account="XL", ean=self.ean)
        self.assertEqual(result["reviewStatus"], "PENDING")
        self.assertTrue(eligibility.eligible)
        self.assertEqual(eligibility.code, "EAN13_FORMAT_ACCEPTED")
        self.assertEqual(total, 1)
        self.assertNotIn("artifactBytes", items[0])

    def test_evidence_review_does_not_require_a_configured_gtin_policy(self) -> None:
        evidence = self.submit()
        result = self.review(evidence["id"], approved_types=())
        self.assertEqual(result["reviewStatus"], "APPROVED")
        self.assertEqual(result["code"], "GTIN_ELIGIBLE")
        with self.engine.connect() as connection:
            status = connection.execute(text("SELECT eligibility_status FROM ean_pool WHERE ean=:ean"), {"ean": self.ean}).scalar_one()
        self.assertEqual(status, "ELIGIBLE")

    def test_approved_current_product_bound_evidence_unlocks_only_matching_product(self) -> None:
        evidence = self.submit()
        result = self.review(evidence["id"])
        self.assertEqual(result["reviewStatus"], "APPROVED")
        with self.engine.connect() as connection:
            valid = validate_gtin_eligibility(
                connection,
                account="XL",
                ean=self.ean,
                source_product_id=self.source_id,
                approved_evidence_types=EVIDENCE_TYPES,
            )
            wrong_account = validate_gtin_eligibility(
                connection,
                account="JV",
                ean=self.ean,
                source_product_id=self.source_id,
                approved_evidence_types=EVIDENCE_TYPES,
            )
            wrong_product = validate_gtin_eligibility(
                connection,
                account="XL",
                ean=self.ean,
                source_product_id=self.create_source(),
                approved_evidence_types=EVIDENCE_TYPES,
            )
        self.assertTrue(valid.eligible)
        self.assertFalse(wrong_account.eligible)
        self.assertFalse(wrong_product.eligible)
        self.assertEqual(wrong_product.code, "GTIN_PRODUCT_BINDING_MISMATCH")

    def test_expired_evidence_cannot_be_approved(self) -> None:
        evidence = self.submit(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        result = self.review(evidence["id"])
        self.assertEqual(result["reviewStatus"], "PENDING")
        self.assertEqual(result["code"], "GTIN_EVIDENCE_EXPIRED")

    def test_evidence_type_allowlist_is_not_a_gtin_eligibility_gate(self) -> None:
        evidence = self.submit()
        result = self.review(evidence["id"], approved_types={"OTHER_TEST_TYPE"})
        self.assertEqual(result["reviewStatus"], "APPROVED")
        self.assertEqual(result["code"], "GTIN_ELIGIBLE")

    def test_identity_conflict_quarantines_instead_of_approving(self) -> None:
        evidence = self.submit()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, 'JV', 'AFTERCOOL', :ean, 'retained conflicting source', 'LEGACY_UNVERIFIED')"
                ),
                {"id": str(uuid.uuid4()), "ean": self.ean},
            )
        result = self.review(evidence["id"])
        self.assertEqual(result["reviewStatus"], "QUARANTINED")
        self.assertEqual(result["eligibilityStatus"], "INELIGIBLE")
        self.assertEqual(result["code"], "GTIN_IDENTITY_CONFLICT")

    def test_artifact_type_spoof_is_rejected(self) -> None:
        with self.assertRaises(GtinEvidenceError) as raised:
            self.submit(media_type="image/png", content=PDF)
        self.assertEqual(raised.exception.code, "EVIDENCE_CONTENT_TYPE_MISMATCH")

    def test_artifact_is_private_account_scoped_and_integrity_checked(self) -> None:
        evidence = self.submit()
        with self.engine.connect() as connection:
            artifact = get_gtin_evidence_artifact(connection, account="XL", evidence_id=evidence["id"])
            with self.assertRaises(GtinEvidenceError) as wrong_account:
                get_gtin_evidence_artifact(connection, account="JV", evidence_id=evidence["id"])
        self.assertEqual(artifact["filename"], "private-evidence.pdf")
        self.assertEqual(artifact["content"], PDF)
        self.assertEqual(wrong_account.exception.status_code, 404)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE gtin_evidence SET artifact_bytes=:bytes WHERE id=:id"),
                {"bytes": b"%PDF-1.4\ntampered", "id": evidence["id"]},
            )
        with self.engine.connect() as connection:
            with self.assertRaises(GtinEvidenceError) as integrity_error:
                get_gtin_evidence_artifact(connection, account="XL", evidence_id=evidence["id"])
        self.assertEqual(integrity_error.exception.code, "GTIN_EVIDENCE_INTEGRITY_FAILURE")

    def test_reject_and_quarantine_are_auditable_final_decisions(self) -> None:
        evidence = self.submit()
        rejected = self.review(evidence["id"], decision="REJECT_EVIDENCE")
        self.assertEqual(rejected["reviewStatus"], "REJECTED")
        with self.engine.connect() as connection:
            pool = connection.execute(
                text("SELECT status, eligibility_status FROM ean_pool WHERE ean=:ean"), {"ean": self.ean}
            ).one()
        self.assertEqual(tuple(pool), ("QUARANTINED", "UNVERIFIED"))

        second_ean = ean13(71_500 + int(uuid.uuid4().hex[:6], 16) % 100_000)
        with self.engine.begin() as connection:
            import_pool_entries(connection, account="XL", eans=[second_ean], actor="fixture-operator", data_scope="PRODUCTION")
        second = self.submit(ean=second_ean)
        quarantined = self.review(second["id"], decision="QUARANTINE_GTIN")
        self.assertEqual(quarantined["reviewStatus"], "QUARANTINED")
        with self.engine.connect() as connection:
            pool = connection.execute(
                text("SELECT status, eligibility_status FROM ean_pool WHERE ean=:ean"), {"ean": second_ean}
            ).one()
        self.assertEqual(tuple(pool), ("QUARANTINED", "INELIGIBLE"))

    def test_operator_api_upload_review_list_and_artifact_are_scoped_and_idempotent(self) -> None:
        import app
        from runtime_mode import RuntimeConfig, RuntimeMode
        from security import Principal
        from starlette.datastructures import Headers, UploadFile
        from starlette.requests import Request

        def request(path: str) -> Request:
            result = Request(
                {
                    "type": "http", "method": "POST", "path": path,
                    "headers": [(b"origin", app.allowed_origins[0].encode())],
                    "query_string": b"account=XL", "server": ("test", 80), "client": ("test", 1),
                    "scheme": "http", "http_version": "1.1",
                }
            )
            result.state.principal = Principal(
                kind="operator", subject="fixture-operator", accounts=frozenset({"XL"}),
                permissions=frozenset({"restart:read", "restart:write"}), request_id="gtin-route-test",
                csrf_verified=True,
            )
            return result

        def upload_file():
            return UploadFile(
                filename="proof.pdf", file=io.BytesIO(PDF),
                headers=Headers({"content-type": "application/pdf"}),
            )

        config = RuntimeConfig(mode=RuntimeMode.PRODUCTION, gtin_approved_evidence_types=EVIDENCE_TYPES)
        with patch.object(app, "RUNTIME_CONFIG", config), patch.object(app, "connect", self.engine.connect):
            create_request = request("/api/restart/gtin-evidence")

            async def upload():
                return await app.upload_gtin_evidence(
                    create_request,
                    account="XL",
                    ean=self.ean,
                    source_product_id=self.source_id,
                    evidence_type="TEST_GS1_EVIDENCE",
                    source_name="offline API fixture",
                    claim_basis="Fixture evidence for exercising the authenticated offline API route.",
                    validity_kind="PERPETUAL",
                    expires_at=None,
                    file=upload_file(),
                    idempotency_key="s11-upload-test-01",
                    x_csrf_token="fixture-csrf",
                )

            created = asyncio.run(upload())
            replay = asyncio.run(upload())
            self.assertEqual(created["id"], replay["id"])
            review_request = request(f"/api/restart/gtin-evidence/{created['id']}/review")
            reviewed = app.decide_gtin_evidence(
                created["id"],
                app.GtinEvidenceReviewInput(
                    account="XL", decision="APPROVE", reviewBasis="Reviewed fixture evidence for isolated test coverage."
                ),
                review_request,
                x_csrf_token="fixture-csrf",
            )
            review_replay = app.decide_gtin_evidence(
                created["id"],
                app.GtinEvidenceReviewInput(
                    account="XL", decision="APPROVE", reviewBasis="Reviewed fixture evidence for isolated test coverage."
                ),
                review_request,
                x_csrf_token="fixture-csrf",
            )
            listed = app.get_gtin_evidence(
                account="XL", ean=self.ean, source_product_id=None, review_status=None, limit=50, offset=0
            )
            artifact = app.download_gtin_evidence_artifact(created["id"], account="XL")

        self.assertEqual(reviewed["reviewStatus"], "APPROVED")
        self.assertTrue(review_replay["reused"])
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["reviewStatus"], "APPROVED")
        self.assertEqual(artifact.body, PDF)
        self.assertEqual(artifact.headers["x-content-type-options"], "nosniff")
        with self.engine.connect() as connection:
            event_counts = connection.execute(
                text("SELECT event_type, COUNT(*) FROM restart_events WHERE entity_id=:id GROUP BY event_type"),
                {"id": created["id"]},
            ).all()
        self.assertEqual({(row[0], row[1]) for row in event_counts}, {
            ("GTIN_EVIDENCE_SUBMITTED", 1), ("GTIN_EVIDENCE_REVIEWED", 1)
        })


class GtinEvidenceRouteTests(unittest.TestCase):
    def test_upload_route_is_disabled_outside_production_before_database_access(self) -> None:
        import app
        from runtime_mode import RuntimeConfig
        from starlette.datastructures import Headers
        from starlette.requests import Request
        from starlette.datastructures import UploadFile
        from security import Principal

        request = Request(
            {
                "type": "http", "method": "POST", "path": "/api/restart/gtin-evidence",
                "headers": [(b"origin", app.allowed_origins[0].encode())],
                "query_string": b"account=XL", "server": ("test", 80), "client": ("test", 1),
                "scheme": "http", "http_version": "1.1",
            }
        )
        request.state.principal = Principal(
            kind="operator", subject="fixture-operator", accounts=frozenset({"XL"}),
            permissions=frozenset({"restart:read", "restart:write"}), request_id="test", csrf_verified=True,
        )
        upload = UploadFile(filename="fixture.pdf", file=io.BytesIO(PDF), headers=Headers({"content-type": "application/pdf"}))

        async def call():
            return await app.upload_gtin_evidence(
                request,
                account="XL",
                ean=ean13(90_001),
                source_product_id=str(uuid.uuid4()),
                evidence_type="TEST_GS1_EVIDENCE",
                source_name="fixture",
                claim_basis="Fixture evidence for disabled-mode route protection test.",
                validity_kind="PERPETUAL",
                expires_at=None,
                file=upload,
                idempotency_key="offline-test-key",
                x_csrf_token="fixture-csrf",
            )

        with patch.object(app, "RUNTIME_CONFIG", RuntimeConfig()), patch.object(
            app, "connect", side_effect=AssertionError("database must not be accessed")
        ):
            with self.assertRaises(app.ApiProblem) as raised:
                asyncio.run(call())
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.code, "GTIN_EVIDENCE_NOT_IMPLEMENTED")

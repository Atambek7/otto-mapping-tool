from __future__ import annotations

import os
import hashlib
import inspect
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from migration_runner import upgrade
from services.ean_pool_service import (
    EanPoolError,
    import_pool_entries,
    is_valid_ean13,
    reserve_ean_for_operation,
)
from services.gtin_service import review_gtin_evidence, submit_gtin_evidence

APPROVED_EVIDENCE_TYPES = frozenset({"TEST_GS1_EVIDENCE"})
PDF_FIXTURE = b"%PDF-1.4\nfixture document for offline unit tests\n%%EOF"


def ean13(seed: int) -> str:
    body = f"499{seed:09d}"
    digits = [int(char) for char in body]
    check = (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits))) % 10) % 10
    return body + str(check)


class EanPoolServicePostgresTests(unittest.TestCase):
    """All database cases use a uniquely named database on an explicit local PG15 test server."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for EAN pool integration tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("EAN pool tests require an explicit loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("EAN pool integration tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_engine", None):
            cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_s11_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.database_url: URL = self.base_url.set(database=self.database_name)
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        self.source_by_operation: dict[str, str] = {}
        upgrade(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def create_operation(self, account: str = "XL") -> str:
        source_id, operation_id = str(uuid.uuid4()), str(uuid.uuid4())
        run_id, observation_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        sku = f"OFFLINE-{uuid.uuid4().hex[:12]}"
        source_ean = ean13(800000 + int(uuid.uuid4().hex[:6], 16) % 1000000)
        response_hash = hashlib.sha256(observation_id.encode()).hexdigest()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, :account, 'AFTERCOOL', :ean, 'source', 'PRODUCTION')"
                ),
                {"id": source_id, "account": account, "ean": source_ean},
            )
            connection.execute(
                text(
                    "INSERT INTO import_runs (id, account, source_filename, total_rows, status, data_scope, "
                    "run_type, run_mode, run_state, started_at, finished_at) VALUES "
                    "(:id, :account, 'offline-fixture.txt', 1, 'COMPLETED', 'PRODUCTION', "
                    "'FILE_IMPORT', 'BASELINE', 'SUCCEEDED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": run_id, "account": account},
            )
            connection.execute(
                text(
                    "INSERT INTO otto_status_observations (id, run_id, account, data_scope, sequence_no, "
                    "requested_sku, response_sku, source_product_id, confirmed_ean, http_status, classification, "
                    "period, reason, status, market_place_status, active, errors_json, error_codes_json, "
                    "error_titles_json, unknown_codes_json, response_json, response_hash, reduction_status) VALUES "
                    "(:id, :run_id, :account, 'PRODUCTION', 1, :sku, :sku, :source_id, :ean, 200, 'EXACT_CODE', "
                    "'3M', '602016 - Nonseller', 'REJECTED', 'INACTIVE', FALSE, '[]'::jsonb, "
                    "'[\"602016 - Nonseller\"]'::jsonb, '[\"Nettoumsatz\"]'::jsonb, '[]'::jsonb, "
                    "CAST(:response AS jsonb), :hash, 'OPENED')"
                ),
                {
                    "id": observation_id,
                    "run_id": run_id,
                    "account": account,
                    "sku": sku,
                    "source_id": source_id,
                    "ean": source_ean,
                    "response": '{"sku":"' + sku + '","marketPlaceStatus":"INACTIVE"}',
                    "hash": response_hash,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO candidate_episodes (id, account, data_scope, observed_sku, canonical_identity, "
                    "source_product_id, confirmed_ean, period, classification, origin, first_observation_id, "
                    "last_observation_id, last_response_hash) VALUES "
                    "(:id, :account, 'PRODUCTION', :sku, :sku, :source_id, :ean, '3M', 'EXACT_CODE', 'NEW', "
                    ":observation_id, :observation_id, :hash)"
                ),
                {
                    "id": episode_id,
                    "account": account,
                    "sku": sku,
                    "source_id": source_id,
                    "ean": source_ean,
                    "observation_id": observation_id,
                    "hash": response_hash,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO restart_operations "
                    "(id, account, source_product_id, candidate_episode_id, period, status, attempt_number, "
                    "created_by, data_scope) "
                    "VALUES (:id, :account, :source_id, :episode_id, '3M', 'DRAFT', 1, 'fixture-operator', 'PRODUCTION')"
                ),
                {"id": operation_id, "account": account, "source_id": source_id, "episode_id": episode_id},
            )
        self.source_by_operation[operation_id] = source_id
        return operation_id

    def add_eligible(self, ean: str, account: str = "XL", operation_id: str | None = None) -> None:
        if operation_id is None:
            operation_id = self.create_operation(account)
        source_id = self.source_by_operation[operation_id]
        with self.engine.begin() as connection:
            import_pool_entries(connection, account=account, eans=[ean], actor="fixture-operator", data_scope="PRODUCTION")
            evidence = submit_gtin_evidence(
                connection,
                account=account,
                ean=ean,
                source_product_id=source_id,
                evidence_type="TEST_GS1_EVIDENCE",
                source_name="offline fixture",
                artifact_filename="fixture.pdf",
                artifact_media_type="application/pdf",
                artifact_bytes=PDF_FIXTURE,
                claim_basis="Test fixture evidence binding this GTIN to the source product.",
                validity_kind="PERPETUAL",
                expires_at=None,
                actor="fixture-operator",
            )
            result = review_gtin_evidence(
                connection,
                account=account,
                evidence_id=evidence["id"],
                decision="APPROVE",
                review_basis="Approved only for an isolated offline test fixture.",
                actor="fixture-reviewer",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
            if result["reviewStatus"] != "APPROVED":
                raise AssertionError(f"Fixture evidence was not approved: {result['code']}")

    def test_ean13_validation(self) -> None:
        self.assertTrue(is_valid_ean13(ean13(1)))
        self.assertTrue(is_valid_ean13("4069424010016"))
        self.assertFalse(is_valid_ean13("12345678"))

    def test_production_import_accepts_conflict_free_13_digit_barcode(self) -> None:
        ean = ean13(1001)
        with self.engine.begin() as connection:
            result = import_pool_entries(
                connection, account="XL", eans=[ean], actor="operator-1", data_scope="PRODUCTION"
            )
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["unverifiedCount"], 0)
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT status, eligibility_status, quarantine_reason, data_scope FROM ean_pool WHERE ean = :ean"),
                {"ean": ean},
            ).mappings().one()
        self.assertEqual(dict(row), {
            "status": "AVAILABLE",
            "eligibility_status": "ELIGIBLE",
            "quarantine_reason": None,
            "data_scope": "PRODUCTION",
        })

    def test_production_import_route_is_idempotent_and_audited(self) -> None:
        import app
        from runtime_mode import RuntimeMode

        ean = ean13(1011)
        request = app.Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/restart/ean-pool/import",
                "headers": [
                    (b"origin", app.allowed_origins[0].encode()),
                    (b"cookie", b"ottorestart_csrf=test-token"),
                ],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("testclient", 50000),
                "scheme": "http",
                "http_version": "1.1",
            }
        )
        request.state.principal = app.Principal(
            kind="operator",
            subject="s10-test-operator",
            accounts=frozenset({"XL"}),
            permissions=frozenset({"restart:read", "restart:write"}),
            request_id="s10-test-request",
            csrf_verified=True,
        )
        payload = app.EanImportInput(account="XL", eans=[ean])
        with patch.object(app, "RUNTIME_CONFIG", app.RuntimeConfig(mode=RuntimeMode.PRODUCTION)), patch.object(
            app, "connect", self.engine.connect
        ):
            first = app.import_eans(payload, request, idempotency_key="s10-import-key-01", x_csrf_token="test-token")
            replay = app.import_eans(payload, request, idempotency_key="s10-import-key-01", x_csrf_token="test-token")
            with self.assertRaises(app.ApiProblem) as raised:
                app.import_eans(
                    app.EanImportInput(account="XL", eans=[ean13(1012)]),
                    request,
                    idempotency_key="s10-import-key-01",
                    x_csrf_token="test-token",
                )
        self.assertEqual(first, replay)
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_CONFLICT")
        with self.engine.connect() as connection:
            counts = connection.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM ean_pool WHERE ean = :ean), "
                    "(SELECT COUNT(*) FROM restart_ean_registry WHERE ean = :ean), "
                    "(SELECT COUNT(*) FROM restart_events WHERE entity_type = 'EAN_POOL' AND data_scope = 'PRODUCTION')"
                ),
                {"ean": ean},
            ).one()
        self.assertEqual(tuple(counts), (1, 0, 1))

    def test_legacy_demo_import_is_scoped_and_not_written_to_registry(self) -> None:
        ean = ean13(1002)
        with self.engine.begin() as connection:
            result = import_pool_entries(
                connection, account="JV", eans=[ean], actor="demo-operator", data_scope="LEGACY_UNVERIFIED"
            )
            pool = connection.execute(
                text("SELECT status, data_scope, eligibility_status, verified_by, verified_at FROM ean_pool WHERE ean = :ean"),
                {"ean": ean},
            ).mappings().one()
            registry_count = connection.execute(text("SELECT COUNT(*) FROM restart_ean_registry WHERE ean = :ean"), {"ean": ean}).scalar_one()
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["unverifiedCount"], 1)
        self.assertEqual(
            dict(pool),
            {
                "status": "AVAILABLE",
                "data_scope": "LEGACY_UNVERIFIED",
                "eligibility_status": "UNVERIFIED",
                "verified_by": None,
                "verified_at": None,
            },
        )
        self.assertEqual(registry_count, 0)

    def test_source_identity_conflict_is_imported_but_marked_ineligible(self) -> None:
        ean = ean13(1003)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, 'JV', 'AFTERCOOL', :ean, 'source owner', 'LEGACY_UNVERIFIED')"
                ),
                {"id": str(uuid.uuid4()), "ean": ean},
            )
            result = import_pool_entries(
                connection, account="XL", eans=[ean], actor="operator-1", data_scope="PRODUCTION"
            )
        self.assertEqual(result["ineligibleCount"], 1)
        self.assertEqual(result["status"], "PARTIAL_QUARANTINE")
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT eligibility_status, quarantine_reason FROM ean_pool WHERE ean=:ean"), {"ean": ean}).one()
        self.assertEqual(tuple(row), ("INELIGIBLE", "IDENTITY_CONFLICT"))

    def test_global_pool_and_legacy_registry_duplicates_are_rejected(self) -> None:
        ean = ean13(1004)
        with self.engine.begin() as connection:
            import_pool_entries(connection, account="XL", eans=[ean], actor="operator-1", data_scope="PRODUCTION")
        with self.engine.begin() as connection:
            with self.assertRaises(EanPoolError) as raised:
                import_pool_entries(connection, account="JV", eans=[ean], actor="operator-2", data_scope="PRODUCTION")
        self.assertEqual(raised.exception.code, "EAN_ALREADY_EXISTS")
        self.assertNotIn("ean", inspect.signature(reserve_ean_for_operation).parameters)

    def test_operation_reservation_is_reused_for_technical_retry(self) -> None:
        operation_id = self.create_operation()
        ean = ean13(1005)
        self.add_eligible(ean, operation_id=operation_id)
        with self.engine.begin() as connection:
            first = reserve_ean_for_operation(connection, account="XL", operation_id=operation_id, approved_evidence_types=APPROVED_EVIDENCE_TYPES)
        with self.engine.begin() as connection:
            retry = reserve_ean_for_operation(connection, account="XL", operation_id=operation_id, approved_evidence_types=APPROVED_EVIDENCE_TYPES)
        self.assertEqual(first["ean"], ean)
        self.assertFalse(first["reused"])
        self.assertTrue(retry["reused"])
        with self.engine.connect() as connection:
            pool = connection.execute(text("SELECT status, reservation_operation_id FROM ean_pool WHERE ean=:ean"), {"ean": ean}).one()
            operation_ean = connection.execute(text("SELECT new_ean FROM restart_operations WHERE id=:id"), {"id": operation_id}).scalar_one()
        self.assertEqual((pool[0], str(pool[1])), ("RESERVED", operation_id))
        self.assertEqual(operation_ean, ean)

    def test_reserved_retry_is_not_blocked_by_optional_evidence_expiry(self) -> None:
        operation_id = self.create_operation()
        ean = ean13(1013)
        self.add_eligible(ean, operation_id=operation_id)
        with self.engine.begin() as connection:
            first = reserve_ean_for_operation(
                connection,
                account="XL",
                operation_id=operation_id,
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        self.assertEqual(first["status"], "RESERVED")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE gtin_evidence SET validity_kind='EXPIRING', "
                    "expires_at=CURRENT_TIMESTAMP - INTERVAL '1 day' WHERE ean=:ean"
                ),
                {"ean": ean},
            )
        with self.engine.begin() as connection:
            retry = reserve_ean_for_operation(
                connection,
                account="XL",
                operation_id=operation_id,
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        self.assertEqual(retry["ean"], ean)
        self.assertEqual(retry["status"], "RESERVED")
        self.assertTrue(retry["reused"])
        with self.engine.connect() as connection:
            pool = connection.execute(
                text("SELECT status, eligibility_status, reservation_operation_id FROM ean_pool WHERE ean=:ean"),
                {"ean": ean},
            ).one()
        self.assertEqual((pool[0], pool[1], str(pool[2])), ("RESERVED", "ELIGIBLE", operation_id))

    def test_allocator_quarantines_identity_collision_and_fails_closed(self) -> None:
        operation_id = self.create_operation()
        collision = ean13(1006)
        self.add_eligible(collision, operation_id=operation_id)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, 'JV', 'AFTERCOOL', :ean, 'conflicting source', 'LEGACY_UNVERIFIED')"
                ),
                {"id": str(uuid.uuid4()), "ean": collision},
            )
        with self.engine.begin() as connection:
            result = reserve_ean_for_operation(connection, account="XL", operation_id=operation_id, approved_evidence_types=APPROVED_EVIDENCE_TYPES)
        self.assertEqual(result["code"], "POOL_EXHAUSTED")
        self.assertIsNone(result["ean"])
        self.assertEqual(result["quarantinedCount"], 1)
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT status, eligibility_status, quarantine_reason FROM ean_pool WHERE ean=:ean"), {"ean": collision}).one()
        self.assertEqual(tuple(row), ("QUARANTINED", "INELIGIBLE", "GTIN_IDENTITY_CONFLICT"))

    def test_rollback_releases_reservation_and_operation_binding(self) -> None:
        operation_id = self.create_operation()
        ean = ean13(1007)
        self.add_eligible(ean, operation_id=operation_id)
        with self.assertRaises(RuntimeError):
            with self.engine.begin() as connection:
                reserve_ean_for_operation(connection, account="XL", operation_id=operation_id, approved_evidence_types=APPROVED_EVIDENCE_TYPES)
                raise RuntimeError("rollback fixture")
        with self.engine.connect() as connection:
            pool_status = connection.execute(text("SELECT status FROM ean_pool WHERE ean=:ean"), {"ean": ean}).scalar_one()
            operation_ean = connection.execute(text("SELECT new_ean FROM restart_operations WHERE id=:id"), {"id": operation_id}).scalar_one()
        self.assertEqual(pool_status, "AVAILABLE")
        self.assertIsNone(operation_ean)

    def test_concurrent_reservations_get_distinct_codes_and_account_scope(self) -> None:
        operation_ids = [self.create_operation("XL"), self.create_operation("XL")]
        codes = [ean13(1008), ean13(1009)]
        for code, operation_id in zip(codes, operation_ids):
            self.add_eligible(code, operation_id=operation_id)

        def reserve(operation_id: str) -> str:
            with self.engine.begin() as connection:
                return reserve_ean_for_operation(connection, account="XL", operation_id=operation_id, approved_evidence_types=APPROVED_EVIDENCE_TYPES)["ean"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            reserved = list(executor.map(reserve, operation_ids))
        self.assertEqual(len(set(reserved)), 2)

        jv_operation_id = self.create_operation("JV")
        xl_only = ean13(1010)
        self.add_eligible(xl_only, account="XL")
        with self.engine.begin() as connection:
            result = reserve_ean_for_operation(connection, account="JV", operation_id=jv_operation_id, approved_evidence_types=APPROVED_EVIDENCE_TYPES)
        self.assertEqual(result["code"], "POOL_EXHAUSTED")
        self.assertIsNone(result["ean"])

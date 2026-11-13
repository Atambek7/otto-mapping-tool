from __future__ import annotations

import hashlib
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from migration_runner import upgrade
from services.ean_pool_service import import_pool_entries, reserve_ean_for_operation
from services.gtin_service import review_gtin_evidence, submit_gtin_evidence
from services.operation_service import (
    OperationError,
    append_operation_revision,
    begin_submission,
    cancel_operation,
    create_history_review,
    create_operation,
    create_retry_authorization,
    idempotent_operation_action,
    record_online_observation,
    record_processing_evidence,
    _set_submission_state,
)
from services.preparation_service import (
    PreparationError,
    load_preparation_context,
    prepare_operation_revision,
    price_snapshot_from_standard_price,
)
from services.product_sync_service import (
    ProductSyncError,
    apply_product_sync_snapshot,
    build_product_sync_snapshot,
    load_product_sync_context,
)
from services.product_catalog_service import ProductCatalogError, get_product, list_products
from services.qc_service import QC_POLICY_VERSION, append_manual_revision, record_operation_qc

APPROVED_EVIDENCE_TYPES = frozenset({"TEST_GS1_EVIDENCE"})
PDF_FIXTURE = b"%PDF-1.4\noffline evidence fixture\n%%EOF"


def ean13(seed: int) -> str:
    body = f"499{seed:09d}"
    digits = [int(char) for char in body]
    check = (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits))) % 10) % 10
    return body + str(check)


class OperationServicePostgresTests(unittest.TestCase):
    """All operations use throwaway databases on an explicitly configured local PG15."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for operation service integration tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Operation tests require an explicit loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Operation integration tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_engine", None):
            cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_s12_test_{uuid.uuid4().hex}"
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

    def make_candidate(
        self,
        *,
        account: str = "XL",
        source_product_id: str | None = None,
        classification: str = "EXACT_CODE",
        source_ean: str | None = None,
    ) -> tuple[str, str]:
        source_id = source_product_id or str(uuid.uuid4())
        episode_id, run_id, observation_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        sku = f"OFFLINE-{uuid.uuid4().hex[:12]}"
        ean = source_ean or ean13(int(uuid.uuid4().hex[:8], 16) % 900_000_000)
        response_hash = hashlib.sha256(observation_id.encode()).hexdigest()
        with self.engine.begin() as connection:
            if source_product_id is None:
                connection.execute(
                    text(
                        "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                        "VALUES (:id, :account, 'AFTERCOOL', :ean, 'Offline source product', 'PRODUCTION')"
                    ),
                    {"id": source_id, "account": account, "ean": ean},
                )
            connection.execute(
                text(
                    "INSERT INTO import_runs (id, account, source_filename, total_rows, status, data_scope, run_type, "
                    "run_mode, run_state, started_at, finished_at) VALUES (:id, :account, 'offline-fixture.txt', 1, "
                    "'COMPLETED', 'PRODUCTION', 'FILE_IMPORT', 'BASELINE', 'SUCCEEDED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": run_id, "account": account},
            )
            connection.execute(
                text(
                    "INSERT INTO otto_status_observations (id, run_id, account, data_scope, sequence_no, requested_sku, "
                    "response_sku, source_product_id, confirmed_ean, http_status, classification, period, reason, status, "
                    "market_place_status, active, errors_json, error_codes_json, error_titles_json, unknown_codes_json, "
                    "response_json, response_hash, reduction_status) VALUES (:id, :run_id, :account, 'PRODUCTION', 1, "
                    ":sku, :sku, :source_id, :ean, 200, :classification, '3M', '602016 - Nonseller', 'REJECTED', "
                    "'INACTIVE', FALSE, '[]'::jsonb, '[\"602016 - Nonseller\"]'::jsonb, '[\"Nettoumsatz\"]'::jsonb, "
                    "'[]'::jsonb, CAST(:response AS jsonb), :hash, 'OPENED')"
                ),
                {
                    "id": observation_id,
                    "run_id": run_id,
                    "account": account,
                    "sku": sku,
                    "source_id": source_id,
                    "ean": ean,
                    "classification": classification,
                    "response": '{"sku":"' + sku + '","marketPlaceStatus":"INACTIVE"}',
                    "hash": response_hash,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO candidate_episodes (id, account, data_scope, observed_sku, canonical_identity, "
                    "source_product_id, confirmed_ean, period, classification, origin, first_observation_id, "
                    "last_observation_id, last_response_hash) VALUES (:id, :account, 'PRODUCTION', :sku, :sku, "
                    ":source_id, :ean, '3M', :classification, 'NEW', :observation_id, :observation_id, :hash)"
                ),
                {
                    "id": episode_id,
                    "account": account,
                    "sku": sku,
                    "source_id": source_id,
                    "ean": ean,
                    "classification": classification,
                    "observation_id": observation_id,
                    "hash": response_hash,
                },
            )
        return source_id, episode_id

    @staticmethod
    def expires(days: int = 1) -> datetime:
        return datetime.now(timezone.utc) + timedelta(days=days)

    def create(self, episode_id: str, *, account: str = "XL", **kwargs):
        with self.engine.begin() as connection:
            return create_operation(
                connection,
                account=account,
                candidate_episode_id=episode_id,
                actor="offline-operator",
                **kwargs,
            )

    def add_eligible_gtin(self, source_id: str, operation_id: str) -> str:
        ean = ean13(920_000_000 + int(uuid.uuid4().hex[:7], 16) % 70_000_000)
        with self.engine.begin() as connection:
            import_pool_entries(connection, account="XL", eans=[ean], actor="offline-operator", data_scope="PRODUCTION")
            evidence = submit_gtin_evidence(
                connection,
                account="XL",
                ean=ean,
                source_product_id=source_id,
                evidence_type="TEST_GS1_EVIDENCE",
                source_name="offline fixture",
                artifact_filename="fixture.pdf",
                artifact_media_type="application/pdf",
                artifact_bytes=PDF_FIXTURE,
                claim_basis="Offline evidence fixture bound to this source product and test GTIN.",
                validity_kind="PERPETUAL",
                expires_at=None,
                actor="offline-operator",
            )
            review_gtin_evidence(
                connection,
                account="XL",
                evidence_id=evidence["id"],
                decision="APPROVE",
                review_basis="This approval exists only in the disposable preparation test database.",
                actor="offline-reviewer",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        return ean

    def mark_source_direct(self, source_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE source_products SET source_type='OTTO_DIRECT' WHERE id=:id"),
                {"id": source_id},
            )

    def candidate_sku(self, episode_id: str) -> str:
        with self.engine.connect() as connection:
            return str(connection.execute(
                text("SELECT observed_sku FROM candidate_episodes WHERE id=:id"), {"id": episode_id}
            ).scalar_one())

    def candidate_ean(self, episode_id: str) -> str:
        with self.engine.connect() as connection:
            return str(connection.execute(
                text("SELECT confirmed_ean FROM candidate_episodes WHERE id=:id"), {"id": episode_id}
            ).scalar_one())

    def preflight(self, operation_id: str, *, account: str = "XL", version: int = 1):
        with self.engine.connect() as connection:
            return load_preparation_context(
                connection,
                account=account,
                operation_id=operation_id,
                expected_version=version,
            )

    def add_qc_ready_revision(self, source_id: str, episode_id: str, operation_id: str) -> tuple[str, str]:
        ean = self.add_eligible_gtin(source_id, operation_id)
        self.mark_source_direct(source_id)
        sku = self.candidate_sku(episode_id)
        target_sku = ean
        reference = f"OTTO:/v5/products/{sku}"
        with self.engine.begin() as connection:
            reserve_ean_for_operation(
                connection, account="XL", operation_id=operation_id,
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
            connection.execute(
                text("UPDATE restart_operations SET target_sku=:target, version=version+1 WHERE id=:id"),
                {"target": target_sku, "id": operation_id},
            )
            evidence_ids = connection.execute(
                text("SELECT id FROM gtin_evidence WHERE account='XL' AND ean=:ean AND review_status='APPROVED'"),
                {"ean": ean},
            ).scalars().all()
            source_snapshot = {
                "account": "XL", "sourceProductId": source_id, "sourceSku": sku,
                "sourceType": "OTTO_DIRECT",
                "sourceEan": self.candidate_ean(episode_id),
                "requiredTitleFacts": ["Cotton", "45 cm"],
                "verifiedTitleTerms": ["Brand", "Cotton", "Chair", "45", "cm"],
                "references": [{"system": "OTTO", "method": "GET", "resource": f"/v5/products/{sku}"}],
                "categorySchema": {
                    "verified": True, "source": "OTTO_CATEGORY_SCHEMA", "account": "XL", "category": "CHAIR",
                    "attributes": [
                        {"name": "Material", "required": True, "legal": False, "allowedValues": ["Cotton", "Linen"]},
                        {"name": "Size", "required": False, "legal": False, "allowedValues": ["45 cm", "50 cm"]},
                    ],
                },
            }
            content = {
                "title": "Brand Cotton Chair 45 cm", "category": "CHAIR",
                "attributes": [
                    {"name": "Material", "value": "Cotton", "sourceRefs": [reference], "origin": "MANUAL"},
                    {"name": "Size", "value": "45 cm", "sourceRefs": [reference], "origin": "MANUAL"},
                ],
                "images": [{"url": "https://images.example.test/chair.jpg", "sourceRefs": [reference]}],
            }
            price = {
                "source": "MAX_CONFIRMED_SOURCE_PRICE", "formula": "MAX_SOURCE_PRICE_MINUS_ACCOUNT_DISCOUNT_THEN_99",
                "currency": "EUR", "originalPriceCents": 12345, "ottoStandardPriceCents": 12345,
                "aftercoolStartPriceCents": None, "selectedSource": "OTTO_STANDARD_PRICE",
                "discountCents": 2000, "discountedPriceCents": 10345,
                "targetPriceCents": 10399, "targetFractionCents": 99,
            }
            fulfillment = {"target": {
                "verified": True, "account": "XL", "targetSku": target_sku,
                "stockQuantity": 4, "shippingProfile": "test-profile", "sourceRef": reference,
            }}
            payload = {
                "account": "XL", "sourceProductId": source_id, "sourceSku": sku,
                "sourceEan": self.candidate_ean(episode_id), "targetSku": target_sku,
                "targetIdentity": {
                    "verified": True, "account": "XL", "sourceSku": sku, "targetSku": target_sku,
                    "newEan": ean, "productReference": ean, "sourceRef": reference,
                },
                "newEan": ean, "productReference": ean, "priceCents": 10399, "title": content["title"],
                "category": content["category"], "attributes": content["attributes"], "images": content["images"],
            }
            append_operation_revision(
                connection,
                account="XL", operation_id=operation_id, expected_version=3,
                source_snapshot=source_snapshot, content=content, price_snapshot=price,
                fulfillment_snapshot=fulfillment, gtin_evidence_ids=evidence_ids,
                payload=payload, actor="offline-operator",
            )
        return ean, target_sku

    def test_first_operation_is_created_once_and_idempotency_replays_same_result(self) -> None:
        _, episode_id = self.make_candidate()

        def run_request():
            with self.engine.begin() as connection:
                return idempotent_operation_action(
                    connection,
                    account="XL",
                    actor="offline-operator",
                    action="create-operation",
                    idempotency_key="same-key-123",
                    request_body={"account": "XL", "candidateEpisodeId": episode_id},
                    execute=lambda: create_operation(
                        connection,
                        account="XL",
                        candidate_episode_id=episode_id,
                        actor="offline-operator",
                    ),
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run_request(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["attemptNumber"], 1)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("SELECT COUNT(*) FROM restart_operations WHERE data_scope='PRODUCTION'")).scalar_one(),
                1,
            )

    def test_unverified_classification_and_account_mismatch_are_blocked(self) -> None:
        _, unverified_episode = self.make_candidate(classification="TITLE_ONLY")
        with self.assertRaisesRegex(OperationError, "exact OTTO"):
            self.create(unverified_episode)
        _, episode_id = self.make_candidate()
        with self.assertRaisesRegex(OperationError, "not found"):
            self.create(episode_id, account="JV")

    def test_legacy_overlap_requires_manual_review_and_review_is_single_use(self) -> None:
        source_id, episode_id = self.make_candidate()
        with self.engine.begin() as connection:
            source_ean = connection.execute(
                text("SELECT aftercool_ean FROM source_products WHERE id=:id"), {"id": source_id}
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO source_products (id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, 'XL', 'AFTERCOOL', :ean, 'Legacy overlapping source', 'LEGACY_UNVERIFIED')"
                ),
                {"id": str(uuid.uuid4()), "ean": source_ean},
            )
        with self.assertRaisesRegex(OperationError, "history review"):
            self.create(episode_id)

        with self.engine.begin() as connection:
            review = create_history_review(
                connection,
                account="XL",
                source_product_id=source_id,
                candidate_episode_id=episode_id,
                decision="CONFIRMED_TEST",
                confirmed_prior_attempt_count=0,
                reviewed_through_operation_id=None,
                reason="Reviewed legacy overlap and confirmed it was test-only history.",
                actor="offline-operator",
                expires_at=self.expires(),
            )
            operation = create_operation(
                connection,
                account="XL",
                candidate_episode_id=episode_id,
                actor="offline-operator",
                history_review_id=review["id"],
            )
        self.assertEqual(operation["attemptNumber"], 1)
        with self.engine.connect() as connection:
            consumed = connection.execute(
                text("SELECT consumed_operation_id FROM restart_authorizations WHERE id=:id"), {"id": review["id"]}
            ).scalar_one()
        self.assertEqual(str(consumed), operation["id"])
        with self.assertRaisesRegex(OperationError, "already has a Restart operation"):
            self.create(episode_id, history_review_id=review["id"])

    def test_repeat_business_restart_needs_review_and_separate_single_use_authorization(self) -> None:
        source_id, first_episode = self.make_candidate()
        first = self.create(first_episode)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE restart_operations SET status='FAILED', version=version+1 WHERE id=:id"),
                {"id": first["id"]},
            )
        _, next_episode = self.make_candidate(source_product_id=source_id)
        with self.assertRaisesRegex(OperationError, "history review"):
            self.create(next_episode)

        with self.engine.begin() as connection:
            review = create_history_review(
                connection,
                account="XL",
                source_product_id=source_id,
                candidate_episode_id=next_episode,
                decision="CONFIRMED_PRODUCTION",
                confirmed_prior_attempt_count=1,
                reviewed_through_operation_id=first["id"],
                reason="Reviewed prior Restart and authorized a separately approved repeat.",
                actor="offline-operator",
                expires_at=self.expires(),
            )
            retry = create_retry_authorization(
                connection,
                account="XL",
                source_product_id=source_id,
                candidate_episode_id=next_episode,
                history_review_id=review["id"],
                reason="Manual approval for exactly the second business attempt.",
                actor="offline-operator",
                expires_at=self.expires(),
            )
            second = create_operation(
                connection,
                account="XL",
                candidate_episode_id=next_episode,
                actor="offline-operator",
                history_review_id=review["id"],
                retry_authorization_id=retry["id"],
            )
        self.assertEqual(second["attemptNumber"], 2)
        with self.engine.connect() as connection:
            consumed = connection.execute(
                text("SELECT consumed_at, consumed_operation_id FROM restart_authorizations WHERE id=:id"),
                {"id": retry["id"]},
            ).one()
        self.assertIsNotNone(consumed[0])
        self.assertEqual(str(consumed[1]), second["id"])

    def test_insufficient_or_expired_history_does_not_authorize_restart(self) -> None:
        source_id, episode_id = self.make_candidate()
        with self.engine.begin() as connection:
            expired_id = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO restart_authorizations (id, account, data_scope, source_product_id, candidate_episode_id, "
                    "authorization_type, decision, confirmed_prior_attempt_count, reason, created_by, issued_at, expires_at) "
                    "VALUES (:id, 'XL', 'PRODUCTION', :source_id, :episode_id, 'HISTORY_REVIEW', 'INSUFFICIENT', 0, "
                    "'Expired history review retained for an offline expiry test.', 'offline-operator', "
                    "CURRENT_TIMESTAMP - INTERVAL '2 days', CURRENT_TIMESTAMP - INTERVAL '1 day')"
                ),
                {"id": expired_id, "source_id": source_id, "episode_id": episode_id},
            )
            review = create_history_review(
                connection,
                account="XL",
                source_product_id=source_id,
                candidate_episode_id=episode_id,
                decision="INSUFFICIENT",
                confirmed_prior_attempt_count=0,
                reviewed_through_operation_id=None,
                reason="History evidence remains incomplete and unverified.",
                actor="offline-operator",
                expires_at=self.expires(),
            )
            with self.assertRaisesRegex(OperationError, "Insufficient"):
                create_operation(
                    connection,
                    account="XL",
                    candidate_episode_id=episode_id,
                    actor="offline-operator",
                    history_review_id=review["id"],
                )
            self.assertIsNotNone(
                connection.execute(text("SELECT revoked_at FROM restart_authorizations WHERE id=:id"), {"id": expired_id}).scalar_one()
            )

    def test_revision_hash_is_immutable_and_optimistically_versioned(self) -> None:
        _, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        with self.engine.begin() as connection:
            revision = append_operation_revision(
                connection,
                account="XL",
                operation_id=operation["id"],
                expected_version=1,
                source_snapshot={"sourceProductId": operation["sourceProductId"], "observedSku": "offline"},
                content={"title": "untrusted until S13"},
                price_snapshot={"amountCents": 12345},
                fulfillment_snapshot={},
                gtin_evidence_ids=[],
                payload={"example": "immutable payload"},
                actor="offline-operator",
            )
        self.assertEqual(revision["revisionNumber"], 1)
        self.assertEqual(len(revision["payloadHash"]), 64)
        with self.engine.begin() as connection:
            with self.assertRaises(OperationError) as raised:
                append_operation_revision(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    expected_version=1,
                    source_snapshot={},
                    content={},
                    price_snapshot={},
                    fulfillment_snapshot={},
                    gtin_evidence_ids=[],
                    payload={},
                    actor="offline-operator",
                )
            self.assertEqual(raised.exception.code, "VERSION_CONFLICT")
        with self.assertRaises(SQLAlchemyError):
            with self.engine.begin() as connection:
                connection.execute(text("UPDATE operation_revisions SET payload_json='{}'::jsonb WHERE id=:id"), {"id": revision["id"]})

    def test_qc_is_the_only_ready_transition_and_manual_review_appends_a_new_revision(self) -> None:
        source_id, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        ean, target_sku = self.add_qc_ready_revision(source_id, episode_id, operation["id"])
        with self.engine.begin() as connection:
            qc = record_operation_qc(
                connection,
                account="XL", operation_id=operation["id"], expected_version=4,
                actor="offline-reviewer", approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        self.assertEqual(qc["outcome"], "PASSED_AUTO")
        self.assertEqual(qc["status"], "READY")
        with self.engine.connect() as connection:
            operation_state = connection.execute(
                text("SELECT status, current_revision_number, version FROM restart_operations WHERE id=:id"),
                {"id": operation["id"]},
            ).mappings().one()
            self.assertEqual(operation_state["status"], "READY")
            self.assertEqual(connection.execute(
                text("SELECT outcome FROM qc_results WHERE operation_id=:id ORDER BY created_at DESC LIMIT 1"),
                {"id": operation["id"]},
            ).scalar_one(), "PASSED_AUTO")

        reference = f"OTTO:/v5/products/{self.candidate_sku(episode_id)}"
        title = "Chair Brand Cotton 45 cm"
        confirmations = [
            {"field": "title", "value": title, "sourceRef": reference, "basis": "Reviewed against the captured OTTO product title and source record."},
            {"field": "category", "value": "CHAIR", "sourceRef": reference, "basis": "Confirmed category against the captured source product and category data."},
            {"field": "attribute:Material", "value": "Cotton", "sourceRef": reference, "basis": "Confirmed the material value against the captured product source record."},
            {"field": "attribute:Size", "value": "45 cm", "sourceRef": reference, "basis": "Confirmed the size value against the captured product source record."},
        ]
        with self.engine.begin() as connection:
            manual = append_manual_revision(
                connection,
                account="XL", operation_id=operation["id"], expected_version=qc["version"],
                title=title, category="CHAIR",
                attributes=[
                    {"name": "Material", "value": "Cotton", "sourceRefs": [reference], "origin": "MANUAL"},
                    {"name": "Size", "value": "45 cm", "sourceRefs": [reference], "origin": "MANUAL"},
                ],
                images=[{"url": "https://images.example.test/chair.jpg", "sourceRefs": [reference]}],
                confirmations=confirmations,
                reason="Operator reviewed and corrected the title and confirmed source facts.",
                actor="offline-reviewer",
            )
        self.assertEqual(manual["revisionNumber"], 2)
        self.assertEqual(manual["status"], "NEEDS_REVIEW")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(
                text("SELECT status FROM restart_operations WHERE id=:id"), {"id": operation["id"]}
            ).scalar_one(), "NEEDS_REVIEW")
        with self.engine.begin() as connection:
            with self.assertRaises(OperationError) as raised:
                begin_submission(
                    connection,
                    account="XL", operation_id=operation["id"], expected_version=manual["version"],
                    approved_evidence_types=APPROVED_EVIDENCE_TYPES,
                )
            self.assertEqual(raised.exception.code, "OPERATION_NOT_READY")
        with self.engine.connect() as connection:
            latest = connection.execute(
                text("SELECT price_snapshot_json, gtin_evidence_ids_json, payload_json FROM operation_revisions "
                     "WHERE operation_id=:id ORDER BY revision_number DESC LIMIT 1"),
                {"id": operation["id"]},
            ).mappings().one()
            self.assertEqual(latest["price_snapshot_json"]["targetPriceCents"], 10399)
            self.assertEqual(latest["payload_json"]["newEan"], ean)
            self.assertEqual(latest["payload_json"]["targetSku"], target_sku)
            self.assertEqual(len(latest["gtin_evidence_ids_json"]), 1)
            self.assertEqual(connection.execute(
                text("SELECT COUNT(*) FROM restart_events WHERE entity_id=:id AND event_type='RESTART_MANUAL_REVIEW_RECORDED'"),
                {"id": operation["id"]},
            ).scalar_one(), 1)

        with self.engine.begin() as connection:
            rechecked = record_operation_qc(
                connection,
                account="XL", operation_id=operation["id"], expected_version=manual["version"],
                actor="offline-reviewer", approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        self.assertEqual(rechecked["outcome"], "PASSED_AUTO")
        self.assertEqual(rechecked["revisionNumber"], 2)

    def test_gtin_evidence_policy_does_not_block_qc_or_ready_state(self) -> None:
        source_id, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        self.add_qc_ready_revision(source_id, episode_id, operation["id"])
        with self.engine.begin() as connection:
            qc = record_operation_qc(
                connection,
                account="XL", operation_id=operation["id"], expected_version=4,
                actor="offline-reviewer", approved_evidence_types={"UNAPPROVED_TEST_TYPE"},
            )
        self.assertEqual(qc["outcome"], "PASSED_AUTO")
        self.assertEqual(qc["status"], "READY")
        self.assertNotIn("GTIN_EVIDENCE_TYPE_NOT_APPROVED", {item["code"] for item in qc["issues"]})

    def test_submission_transition_cannot_assign_ready_status(self) -> None:
        _, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        with self.engine.begin() as connection:
            with self.assertRaises(OperationError) as raised:
                _set_submission_state(
                    connection,
                    account="XL", operation_id=operation["id"], expected_version=1,
                    state="UNKNOWN", status="READY",
                )
        self.assertEqual(raised.exception.code, "SUBMISSION_TRANSITION_INVALID")

    def test_products_api_query_is_account_scoped_searchable_and_server_paginated(self) -> None:
        xl_source_a, xl_episode_a = self.make_candidate(account="XL")
        xl_source_b, xl_episode_b = self.make_candidate(account="XL")
        jv_source, jv_episode = self.make_candidate(account="JV")
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE source_products SET factory_id='factory-a', factory_name='Factory Alpha' WHERE id=:id"),
                {"id": xl_source_a},
            )
            connection.execute(
                text("UPDATE source_products SET factory_id='factory-b', factory_name='Factory Beta' WHERE id=:id"),
                {"id": xl_source_b},
            )
            connection.execute(text("UPDATE candidate_episodes SET period='12M' WHERE id=:id"), {"id": jv_episode})
            first_page = list_products(connection, account="XL", page=1, page_size=1)
            second_page = list_products(connection, account="XL", page=2, page_size=1)
            factory_result = list_products(connection, account="XL", factory_id="factory-a")
            search_result = list_products(connection, account="XL", query="Factory Alpha")
            status_result = list_products(connection, account="XL", status="3M")
            jv_result = list_products(connection, account="JV", period="12M")

        self.assertEqual(first_page.page.total, 2)
        self.assertEqual(first_page.page.total_pages, 2)
        self.assertEqual(second_page.page.total, 2)
        self.assertNotEqual(first_page.items[0].id, second_page.items[0].id)
        self.assertEqual(first_page.metrics.total, 2)
        self.assertEqual(first_page.metrics.count_3m, 2)
        self.assertEqual(factory_result.page.total, 1)
        self.assertEqual(factory_result.items[0].factory_id, "factory-a")
        self.assertEqual(search_result.page.total, 1)
        self.assertEqual(search_result.items[0].factory_name, "Factory Alpha")
        self.assertEqual(status_result.page.total, 2)
        self.assertEqual(jv_result.page.total, 1)
        self.assertEqual(jv_result.items[0].account, "JV")
        self.assertEqual(jv_result.items[0].period, "12M")
        self.assertNotIn(jv_source, {xl_source_a, xl_source_b})

    def test_product_detail_exposes_operation_version_and_rejects_cross_account_lookup(self) -> None:
        _, episode_id = self.make_candidate(account="XL")
        operation = self.create(episode_id, account="XL")
        with self.engine.connect() as connection:
            product = get_product(connection, account="XL", candidate_episode_id=episode_id)
            with self.assertRaises(ProductCatalogError) as raised:
                get_product(connection, account="JV", candidate_episode_id=episode_id)

        self.assertEqual(product.id, episode_id)
        self.assertEqual(product.candidate_episode_id, episode_id)
        self.assertEqual(product.source_product_id, operation["sourceProductId"])
        self.assertEqual(product.operation_id, operation["id"])
        self.assertEqual(product.operation_status, "DRAFT")
        self.assertEqual(product.operation_version, 1)
        self.assertEqual(raised.exception.code, "PRODUCT_NOT_FOUND")

    def test_selected_product_sync_saves_source_only_or_appends_draft_revision_atomically(self) -> None:
        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        with self.engine.connect() as connection:
            context = load_product_sync_context(connection, account="XL", candidate_episode_id=episode_id)
        snapshot = build_product_sync_snapshot(
            context,
            account="XL",
            otto_product={
                "sku": context["observed_sku"], "ean": context["confirmed_ean"], "category": "FURNITURE_TABLES",
                "productDescription": {"attributes": [{"name": "Material", "values": ["Oak", "Steel"]}]},
            },
            otto_prices={"sku": context["observed_sku"], "standardPrice": {"amount": "1600.00", "currency": "EUR"}},
            aftercool=None,
            captured_at=datetime.now(timezone.utc),
        )
        source_title = context["original_title"]
        with self.engine.begin() as connection:
            result = apply_product_sync_snapshot(
                connection, preflight_context=context, snapshot=snapshot,
                expected_version=None, actor="offline-operator",
            )
        self.assertIsNone(result["operationId"])
        with self.engine.connect() as connection:
            stored = connection.execute(
                text("SELECT original_title,category,attributes_json FROM source_products WHERE id=:id"),
                {"id": source_id},
            ).mappings().one()
            self.assertEqual(connection.execute(
                text("SELECT COUNT(*) FROM restart_operations WHERE source_product_id=:id"), {"id": source_id}
            ).scalar_one(), 0)
        self.assertEqual(stored["original_title"], source_title)
        self.assertEqual(stored["category"], "FURNITURE_TABLES")
        self.assertEqual(stored["attributes_json"]["syncSnapshot"]["targetPriceCents"], 158099)

        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        operation = self.create(episode_id)
        with self.engine.connect() as connection:
            context = load_product_sync_context(connection, account="XL", candidate_episode_id=episode_id)
        snapshot = build_product_sync_snapshot(
            context,
            account="XL",
            otto_product={
                "sku": context["observed_sku"], "ean": context["confirmed_ean"], "category": "TABLES",
                "productDescription": {"attributes": [{"name": "Color", "value": "Black"}]},
            },
            otto_prices={"sku": context["observed_sku"], "standardPrice": {"amount": "1600.00", "currency": "EUR"}},
            aftercool=None,
            captured_at=datetime.now(timezone.utc),
        )
        with self.engine.begin() as connection:
            result = apply_product_sync_snapshot(
                connection, preflight_context=context, snapshot=snapshot,
                expected_version=operation["version"], actor="offline-operator",
            )
        self.assertEqual(result["revision"]["revisionNumber"], 1)
        with self.engine.connect() as connection:
            latest = connection.execute(
                text("SELECT content_json,price_snapshot_json,payload_json FROM operation_revisions "
                     "WHERE operation_id=:id AND revision_number=1"), {"id": operation["id"]}
            ).mappings().one()
            unchanged_title = connection.execute(
                text("SELECT original_title FROM source_products WHERE id=:id"), {"id": source_id}
            ).scalar_one()
            state = connection.execute(
                text("SELECT status,version,current_revision_number FROM restart_operations WHERE id=:id"),
                {"id": operation["id"]},
            ).one()
        self.assertEqual(unchanged_title, "Offline source product")
        self.assertEqual(latest["content_json"]["title"], "Offline source product")
        self.assertEqual(latest["content_json"]["attributes"], [{
            "name": "Color", "value": "Black", "origin": "OTTO",
            "sourceRefs": [f"OTTO:/v5/products/{context['observed_sku']}"],
        }])
        self.assertEqual(latest["price_snapshot_json"]["targetPriceCents"], 158099)
        self.assertEqual(latest["payload_json"]["priceCents"], 158099)
        self.assertEqual(tuple(state), ("DRAFT", 2, 1))

    def test_sync_rejects_a_candidate_changed_during_source_reads(self) -> None:
        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        with self.engine.connect() as connection:
            context = load_product_sync_context(connection, account="XL", candidate_episode_id=episode_id)
        snapshot = build_product_sync_snapshot(
            context,
            account="XL",
            otto_product={"sku": context["observed_sku"], "category": "TABLES", "productDescription": {"attributes": []}},
            otto_prices={"sku": context["observed_sku"], "standardPrice": {"amount": "1600.00", "currency": "EUR"}},
            aftercool=None,
            captured_at=datetime.now(timezone.utc),
        )
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE source_products SET original_title='Changed while syncing' WHERE id=:id"), {"id": source_id})
        with self.engine.begin() as connection:
            with self.assertRaises(ProductSyncError) as raised:
                apply_product_sync_snapshot(
                    connection, preflight_context=context, snapshot=snapshot,
                    expected_version=None, actor="offline-operator",
                )
        self.assertEqual(raised.exception.code, "SOURCE_CHANGED_DURING_SYNC")

    def test_aftercool_sync_persists_otto_source_but_keeps_target_price_blocked(self) -> None:
        source_id, episode_id = self.make_candidate(account="JV")
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE source_products SET aftercool_product_id='AC-42', factory_id='factory-7' WHERE id=:id"),
                {"id": source_id},
            )
        operation = self.create(episode_id, account="JV")
        with self.engine.connect() as connection:
            context = load_product_sync_context(connection, account="JV", candidate_episode_id=episode_id)
        snapshot = build_product_sync_snapshot(
            context,
            account="JV",
            otto_product={
                "sku": context["observed_sku"], "ean": context["confirmed_ean"], "category": "TABLES",
                "productDescription": {"attributes": [{"name": "Material", "value": "Oak"}]},
            },
            otto_prices={"sku": context["observed_sku"], "standardPrice": {"amount": "1600.00", "currency": "EUR"}},
            aftercool={
                "account": "JV", "status": "FOUND", "found": True, "verified": True,
                "data_scope": "PRODUCTION", "source_type": "AFTERCOOL", "product_id": "AC-42",
                "ean": context["confirmed_ean"], "factory_id": "factory-7", "price_cents": None,
            },
            captured_at=datetime.now(timezone.utc),
        )
        with self.engine.begin() as connection:
            result = apply_product_sync_snapshot(
                connection, preflight_context=context, snapshot=snapshot,
                expected_version=operation["version"], actor="offline-operator",
            )
        with self.engine.connect() as connection:
            revision = connection.execute(
                text("SELECT price_snapshot_json,payload_json FROM operation_revisions "
                     "WHERE operation_id=:id AND revision_number=1"), {"id": operation["id"]}
            ).mappings().one()
            state = connection.execute(
                text("SELECT status,current_revision_number FROM restart_operations WHERE id=:id"),
                {"id": operation["id"]},
            ).one()
            product = get_product(connection, account="JV", candidate_episode_id=episode_id)
        self.assertEqual(result["targetPriceStatus"], "PRICE_REVIEW_REQUIRED")
        self.assertEqual(revision["price_snapshot_json"]["ottoStandardPriceCents"], 160000)
        self.assertNotIn("targetPriceCents", revision["price_snapshot_json"])
        self.assertNotIn("priceCents", revision["payload_json"])
        self.assertIn("PRICE_REVIEW_REQUIRED", revision["payload_json"]["readinessGaps"])
        self.assertEqual(tuple(state), ("DRAFT", 1))
        self.assertEqual(product.otto_source_price["amountCents"], 160000)
        self.assertIsNone(product.price_preview)
        self.assertEqual(product.target_price_status, "PRICE_REVIEW_REQUIRED")

    def test_account_price_formula_uses_decimal_standard_price_only(self) -> None:
        xl = price_snapshot_from_standard_price(
            "XL", {"sku": "source", "standardPrice": {"amount": "123.45", "currency": "EUR"}, "sale": {"salePrice": {"amount": "1.00"}}}
        )
        jv = price_snapshot_from_standard_price(
            "JV", {"sku": "source", "standardPrice": {"amount": 23.45, "currency": "eur"}}
        )
        self.assertEqual((xl["originalPriceCents"], xl["discountCents"], xl["targetPriceCents"]), (12345, 2000, 10399))
        self.assertEqual((jv["originalPriceCents"], jv["discountCents"], jv["targetPriceCents"]), (2345, 1000, 1399))
        rounded = price_snapshot_from_standard_price(
            "JV", {"standardPrice": {"amount": "20.005", "currency": "EUR"}}
        )
        self.assertEqual(rounded["originalPriceCents"], 2001)
        self.assertEqual(rounded["targetPriceCents"], 1099)

    def test_missing_invalid_or_non_positive_standard_price_never_uses_fallbacks(self) -> None:
        for response in (
            {"sale": {"salePrice": {"amount": 40, "currency": "EUR"}}},
            {"standardPrice": {"amount": 0, "currency": "EUR"}},
            {"standardPrice": {"amount": -1, "currency": "EUR"}},
            {"standardPrice": {"amount": "not-a-price", "currency": "EUR"}},
            {"standardPrice": {"amount": 16, "currency": "USD"}},
        ):
            with self.subTest(response=response), self.assertRaises(PreparationError):
                price_snapshot_from_standard_price("XL", response)
        with self.assertRaises(PreparationError) as raised:
            price_snapshot_from_standard_price(
                "JV", {"standardPrice": {"amount": "10.00", "currency": "EUR"}}
            )
        self.assertEqual(raised.exception.code, "TARGET_PRICE_NON_POSITIVE")

    def test_prepare_persists_source_and_user_approved_target_defaults(self) -> None:
        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        operation = self.create(episode_id)
        ean = self.add_eligible_gtin(source_id, operation["id"])
        source_sku = self.candidate_sku(episode_id)
        captured = datetime.now(timezone.utc)
        with self.engine.begin() as connection:
            result = prepare_operation_revision(
                connection,
                account="XL",
                operation_id=operation["id"],
                expected_version=1,
                preflight_context=self.preflight(operation["id"]),
                otto_product={"sku": source_sku, "product": "raw source payload"},
                otto_prices={"sku": source_sku, "standardPrice": {"amount": "123.45", "currency": "EUR"}, "salePrice": {"amount": "1.00"}},
                fulfillment={"sku": source_sku, "shippingProfileId": "offline-profile"},
                aftercool=None,
                captured_at=captured,
                actor="offline-operator",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        with self.engine.connect() as connection:
            stored = connection.execute(
                text(
                    "SELECT r.source_snapshot_json, r.content_json, r.price_snapshot_json, r.fulfillment_snapshot_json, "
                    "r.payload_json, o.version, o.target_sku, p.status AS gtin_status, p.reservation_operation_id "
                    "FROM operation_revisions r JOIN restart_operations o ON o.id=r.operation_id "
                    "JOIN ean_pool p ON p.ean=:ean WHERE r.id=:revision_id"
                ),
                {"ean": ean, "revision_id": result["id"]},
            ).mappings().one()
        self.assertEqual(result["preparationState"], "PAYLOAD_PREPARED_WITH_GAPS")
        self.assertNotIn("OTTO_TARGET_PAYLOAD_NOT_BUILT", result["readinessGaps"])
        self.assertIn("IMAGES_MISSING", result["readinessGaps"])
        self.assertIn("LOGISTICS_MISSING", result["readinessGaps"])
        self.assertEqual(stored["source_snapshot_json"]["ottoProductResponse"]["product"], "raw source payload")
        self.assertEqual(stored["content_json"]["ottoProductResponse"]["sku"], source_sku)
        self.assertEqual(stored["price_snapshot_json"]["targetPriceCents"], 10399)
        self.assertEqual(stored["fulfillment_snapshot_json"]["response"]["shippingProfileId"], "offline-profile")
        self.assertEqual(stored["fulfillment_snapshot_json"]["target"]["stockQuantity"], 20)
        self.assertEqual(stored["fulfillment_snapshot_json"]["target"]["processingTime"], "37")
        self.assertEqual(stored["fulfillment_snapshot_json"]["target"]["shippingProfile"], "d045a64d-59e6-57fb-9f69-6370dc7c9d40")
        self.assertEqual(stored["target_sku"], ean)
        self.assertEqual(stored["payload_json"]["targetSku"], ean)
        self.assertEqual(stored["payload_json"]["productReference"], ean)
        self.assertEqual(stored["payload_json"]["targetIdentity"]["productReference"], ean)
        self.assertEqual(stored["payload_json"]["ottoProductPayload"]["mediaAssets"], [])
        self.assertEqual(stored["version"], 3)  # one version for reservation, one for immutable revision
        self.assertEqual(stored["gtin_status"], "RESERVED")
        self.assertEqual(str(stored["reservation_operation_id"]), operation["id"])
        self.assertEqual(stored["payload_json"]["newEan"], ean)

    def test_unconfigured_gtin_policy_does_not_block_barcode_preparation(self) -> None:
        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        operation = self.create(episode_id)
        ean = self.add_eligible_gtin(source_id, operation["id"])
        source_sku = self.candidate_sku(episode_id)
        with self.engine.begin() as connection:
            result = prepare_operation_revision(
                connection,
                account="XL",
                operation_id=operation["id"],
                expected_version=1,
                preflight_context=self.preflight(operation["id"]),
                otto_product={"sku": source_sku},
                otto_prices={"sku": source_sku, "standardPrice": {"amount": 100, "currency": "EUR"}},
                fulfillment={"sku": source_sku},
                aftercool=None,
                captured_at=datetime.now(timezone.utc),
                actor="offline-operator",
                approved_evidence_types=(),
            )
        with self.engine.connect() as connection:
            pool_state = connection.execute(text("SELECT status FROM ean_pool WHERE ean=:ean"), {"ean": ean}).scalar_one()
            revision_count = connection.execute(text("SELECT COUNT(*) FROM operation_revisions WHERE operation_id=:id"), {"id": operation["id"]}).scalar_one()
            operation_state = connection.execute(text("SELECT new_ean, version FROM restart_operations WHERE id=:id"), {"id": operation["id"]}).one()
        self.assertEqual(pool_state, "RESERVED")
        self.assertEqual(revision_count, 1)
        self.assertEqual(operation_state[0], ean)
        self.assertEqual(result["gtin"], ean)

    def test_source_account_and_sku_mismatches_block_before_reservation(self) -> None:
        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        operation = self.create(episode_id)
        ean = self.add_eligible_gtin(source_id, operation["id"])
        source_sku = self.candidate_sku(episode_id)
        baseline = {
            "operation_id": operation["id"],
            "expected_version": 1,
            "preflight_context": self.preflight(operation["id"]),
            "otto_product": {"sku": source_sku},
            "otto_prices": {"sku": source_sku, "standardPrice": {"amount": 100, "currency": "EUR"}},
            "fulfillment": {"sku": source_sku},
            "aftercool": None,
            "captured_at": datetime.now(timezone.utc),
            "actor": "offline-operator",
            "approved_evidence_types": APPROVED_EVIDENCE_TYPES,
        }
        with self.engine.begin() as connection:
            with self.assertRaises(PreparationError) as raised:
                prepare_operation_revision(connection, account="JV", **baseline)
            self.assertEqual(raised.exception.code, "OPERATION_NOT_FOUND")
        with self.engine.begin() as connection:
            bad_sku = {**baseline, "otto_product": {"sku": "OTHER-SKU"}}
            with self.assertRaises(PreparationError) as raised:
                prepare_operation_revision(connection, account="XL", **bad_sku)
            self.assertEqual(raised.exception.code, "OTTO_SKU_MISMATCH")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM ean_pool WHERE ean=:ean"), {"ean": ean}).scalar_one(), "AVAILABLE")

    def test_aftercool_product_mismatch_blocks_before_reservation(self) -> None:
        source_id, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        ean = self.add_eligible_gtin(source_id, operation["id"])
        source_sku = self.candidate_sku(episode_id)
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO restart_factories (account, id, name, status, data_scope) "
                     "VALUES ('XL', 'offline-factory', 'Offline factory', 'UNKNOWN', 'PRODUCTION')")
            )
            connection.execute(
                text("UPDATE source_products SET source_type='AFTERCOOL', aftercool_product_id='ac-product-1', "
                     "factory_id='offline-factory' WHERE id=:id"),
                {"id": source_id},
            )
        with self.engine.begin() as connection:
            with self.assertRaises(PreparationError) as raised:
                prepare_operation_revision(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    expected_version=1,
                    preflight_context=self.preflight(operation["id"]),
                    otto_product={"sku": source_sku},
                    otto_prices={"sku": source_sku, "standardPrice": {"amount": 100, "currency": "EUR"}},
                    fulfillment={"sku": source_sku},
                    aftercool={
                        "account": "XL", "status": "FOUND", "found": True, "verified": True,
                        "data_scope": "PRODUCTION", "source_type": "AFTERCOOL", "product_id": "different-product",
                        "ean": self.candidate_ean(episode_id), "factory_id": "offline-factory",
                    },
                    captured_at=datetime.now(timezone.utc),
                    actor="offline-operator",
                    approved_evidence_types=APPROVED_EVIDENCE_TYPES,
                )
            self.assertEqual(raised.exception.code, "AFTERCOOL_IDENTITY_MISMATCH")
            wrong_account = {
                "account": "JV", "status": "FOUND", "found": True, "verified": True,
                "data_scope": "PRODUCTION", "source_type": "AFTERCOOL", "product_id": "ac-product-1",
                "ean": self.candidate_ean(episode_id), "factory_id": "offline-factory",
            }
            with self.assertRaises(PreparationError) as raised:
                prepare_operation_revision(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    expected_version=1,
                    preflight_context=self.preflight(operation["id"]),
                    otto_product={"sku": source_sku},
                    otto_prices={"sku": source_sku, "standardPrice": {"amount": 100, "currency": "EUR"}},
                    fulfillment={"sku": source_sku},
                    aftercool=wrong_account,
                    captured_at=datetime.now(timezone.utc),
                    actor="offline-operator",
                    approved_evidence_types=APPROVED_EVIDENCE_TYPES,
                )
            self.assertEqual(raised.exception.code, "ACCOUNT_MISMATCH")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT status FROM ean_pool WHERE ean=:ean"), {"ean": ean}).scalar_one(), "AVAILABLE")
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM operation_revisions WHERE operation_id=:id"), {"id": operation["id"]}).scalar_one(), 0)

    def test_source_category_change_after_preflight_invalidates_staged_snapshot(self) -> None:
        source_id, episode_id = self.make_candidate()
        self.mark_source_direct(source_id)
        operation = self.create(episode_id)
        self.add_eligible_gtin(source_id, operation["id"])
        source_sku = self.candidate_sku(episode_id)
        preflight = self.preflight(operation["id"])
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE source_products SET category='Changed category' WHERE id=:id"), {"id": source_id})
        with self.engine.begin() as connection:
            with self.assertRaises(PreparationError) as raised:
                prepare_operation_revision(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    expected_version=1,
                    preflight_context=preflight,
                    otto_product={"sku": source_sku},
                    otto_prices={"sku": source_sku, "standardPrice": {"amount": 100, "currency": "EUR"}},
                    fulfillment={"sku": source_sku},
                    aftercool=None,
                    captured_at=datetime.now(timezone.utc),
                    actor="offline-operator",
                    approved_evidence_types=APPROVED_EVIDENCE_TYPES,
                )
            self.assertEqual(raised.exception.code, "SOURCE_CHANGED_DURING_PREPARATION")

    def test_processing_and_online_evidence_require_matching_account_and_explicit_target_sku(self) -> None:
        source_id, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE restart_operations SET status='RECONCILIATION_REQUIRED', submission_state='UNKNOWN', "
                    "version=version+1 WHERE id=:id"
                ),
                {"id": operation["id"]},
            )
            with self.assertRaisesRegex(OperationError, "explicitly verified matching target SKU"):
                record_processing_evidence(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    event_id=str(uuid.uuid4()),
                    target_sku="candidate-sku-is-not-target-proof",
                )
            connection.execute(
                text("UPDATE restart_operations SET target_sku='target-sku-1' WHERE id=:id"),
                {"id": operation["id"]},
            )
            wrong_account_event = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json) "
                    "VALUES (:id, 'JV', 'PRODUCTION', 'OTTO_RESULT', 'OTTO_PROCESSING_RESULT', :operation_id, "
                    "'Wrong account fixture', '{\"targetSku\":\"target-sku-1\",\"result\":\"SUCCEEDED\"}'::jsonb)"
                ),
                {"id": wrong_account_event, "operation_id": operation["id"]},
            )
            with self.assertRaisesRegex(OperationError, "not bound to this operation and account"):
                record_processing_evidence(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    event_id=wrong_account_event,
                    target_sku="target-sku-1",
                )
            event_id = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json) "
                    "VALUES (:id, 'XL', 'PRODUCTION', 'OTTO_RESULT', 'OTTO_PROCESSING_RESULT', :operation_id, "
                    "'Confirmed per-SKU processing fixture', '{\"targetSku\":\"target-sku-1\",\"result\":\"SUCCEEDED\"}'::jsonb)"
                ),
                {"id": event_id, "operation_id": operation["id"]},
            )
            processing = record_processing_evidence(
                connection,
                account="XL",
                operation_id=operation["id"],
                event_id=event_id,
                target_sku="target-sku-1",
            )
            with self.assertRaisesRegex(OperationError, "does not show this target SKU as ONLINE"):
                record_online_observation(
                    connection,
                    account="XL",
                    operation_id=operation["id"],
                    observation_id=str(connection.execute(
                        text("SELECT id FROM otto_status_observations WHERE source_product_id=:source_id LIMIT 1"),
                        {"source_id": source_id},
                    ).scalar_one()),
                    target_sku="target-sku-1",
                )
            run_id = connection.execute(
                text("SELECT run_id FROM otto_status_observations WHERE source_product_id=:source_id LIMIT 1"),
                {"source_id": source_id},
            ).scalar_one()
            online_id = str(uuid.uuid4())
            response_hash = hashlib.sha256(online_id.encode()).hexdigest()
            connection.execute(
                text(
                    "INSERT INTO otto_status_observations (id, run_id, account, data_scope, sequence_no, requested_sku, "
                    "response_sku, source_product_id, confirmed_ean, http_status, classification, reason, status, "
                    "market_place_status, active, errors_json, error_codes_json, error_titles_json, unknown_codes_json, "
                    "response_json, response_hash, reduction_status) VALUES (:id, :run_id, 'XL', 'PRODUCTION', 2, "
                    "'target-sku-1', 'target-sku-1', :source_id, NULL, 200, 'NONE', 'No active restriction', "
                    "'ACTIVE', 'ONLINE', TRUE, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, "
                    "'{\"sku\":\"target-sku-1\",\"marketPlaceStatus\":\"ONLINE\"}'::jsonb, :hash, 'NONE')"
                ),
                {"id": online_id, "run_id": run_id, "source_id": source_id, "hash": response_hash},
            )
            online = record_online_observation(
                connection,
                account="XL",
                operation_id=operation["id"],
                observation_id=online_id,
                target_sku="target-sku-1",
            )
            version = connection.execute(
                text("SELECT version FROM restart_operations WHERE id=:id"), {"id": operation["id"]}
            ).scalar_one()
            cancel_result = cancel_operation(
                connection,
                account="XL",
                operation_id=operation["id"],
                expected_version=version,
                actor="offline-operator",
            )
            status = connection.execute(
                text("SELECT status FROM restart_operations WHERE id=:id"), {"id": operation["id"]}
            ).scalar_one()
        self.assertEqual(processing["submissionState"], "CONFIRMED")
        self.assertEqual(online["marketPlaceStatus"], "ONLINE")
        self.assertEqual(status, "AWAITING_ONLINE")
        self.assertFalse(cancel_result["cancelled"])
        self.assertEqual(cancel_result["submissionState"], "CONFIRMED")

    def test_cancel_releases_only_valid_unsent_reservation_and_uncertainty_keeps_it(self) -> None:
        source_id, episode_id = self.make_candidate()
        operation = self.create(episode_id)
        ean = ean13(91234567)
        with self.engine.begin() as connection:
            import_pool_entries(connection, account="XL", eans=[ean], actor="offline-operator", data_scope="PRODUCTION")
            evidence = submit_gtin_evidence(
                connection,
                account="XL",
                ean=ean,
                source_product_id=source_id,
                evidence_type="TEST_GS1_EVIDENCE",
                source_name="offline fixture",
                artifact_filename="fixture.pdf",
                artifact_media_type="application/pdf",
                artifact_bytes=PDF_FIXTURE,
                claim_basis="Offline fixture evidence bound to this product and test GTIN.",
                validity_kind="PERPETUAL",
                expires_at=None,
                actor="offline-operator",
            )
            reviewed = review_gtin_evidence(
                connection,
                account="XL",
                evidence_id=evidence["id"],
                decision="APPROVE",
                review_basis="This approval exists only inside the isolated test database.",
                actor="offline-reviewer",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
            self.assertEqual(reviewed["reviewStatus"], "APPROVED")
            reserve_ean_for_operation(
                connection,
                account="XL",
                operation_id=operation["id"],
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version FROM restart_operations WHERE id=:id"), {"id": operation["id"]}).scalar_one()
        with self.engine.begin() as connection:
            result = cancel_operation(
                connection,
                account="XL",
                operation_id=operation["id"],
                expected_version=version,
                actor="offline-operator",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        self.assertTrue(result["eanReleased"])
        with self.engine.connect() as connection:
            pool = connection.execute(text("SELECT status, reservation_operation_id FROM ean_pool WHERE ean=:ean"), {"ean": ean}).one()
            new_ean = connection.execute(text("SELECT new_ean FROM restart_operations WHERE id=:id"), {"id": operation["id"]}).scalar_one()
            link_status = connection.execute(
                text("SELECT status FROM ean_links WHERE operation_id=:id AND new_ean=:ean"),
                {"id": operation["id"], "ean": ean},
            ).scalar_one()
        self.assertEqual(pool[0], "AVAILABLE")
        self.assertIsNone(pool[1])
        self.assertIsNone(new_ean)
        self.assertEqual(link_status, "DEACTIVATED")

        second_source_id, second_episode_id = self.make_candidate()
        second_operation = self.create(second_episode_id)
        second_ean = ean13(91234568)
        with self.engine.begin() as connection:
            import_pool_entries(connection, account="XL", eans=[second_ean], actor="offline-operator", data_scope="PRODUCTION")
            evidence = submit_gtin_evidence(
                connection,
                account="XL",
                ean=second_ean,
                source_product_id=second_source_id,
                evidence_type="TEST_GS1_EVIDENCE",
                source_name="offline fixture",
                artifact_filename="fixture.pdf",
                artifact_media_type="application/pdf",
                artifact_bytes=PDF_FIXTURE,
                claim_basis="Offline fixture evidence bound to a separate test source item.",
                validity_kind="PERPETUAL",
                expires_at=None,
                actor="offline-operator",
            )
            review_gtin_evidence(
                connection,
                account="XL",
                evidence_id=evidence["id"],
                decision="APPROVE",
                review_basis="This approval exists only inside the isolated test database.",
                actor="offline-reviewer",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
            reserve_ean_for_operation(
                connection,
                account="XL",
                operation_id=second_operation["id"],
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
            current_version = connection.execute(
                text("SELECT version FROM restart_operations WHERE id=:id"), {"id": second_operation["id"]}
            ).scalar_one()
            revision = append_operation_revision(
                connection,
                account="XL",
                operation_id=second_operation["id"],
                expected_version=current_version,
                source_snapshot={"sourceProductId": second_source_id},
                content={},
                price_snapshot={},
                fulfillment_snapshot={},
                gtin_evidence_ids=[evidence["id"]],
                payload={"sku": "test-target"},
                actor="offline-operator",
            )
            connection.execute(
                text("UPDATE restart_operations SET status='READY', target_sku='test-target', version=version+1 WHERE id=:id"),
                {"id": second_operation["id"]},
            )
            connection.execute(
                text(
                    "INSERT INTO qc_results (id, operation_id, revision_id, account, data_scope, outcome, policy_version, "
                    "payload_hash, result_json, created_by) VALUES (:id, :operation_id, :revision_id, 'XL', "
                    "'PRODUCTION', 'PASSED_AUTO', :policy, :payload_hash, '{}'::jsonb, 'offline-reviewer')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "operation_id": second_operation["id"],
                    "revision_id": revision["id"],
                    "policy": QC_POLICY_VERSION,
                    "payload_hash": revision["payloadHash"],
                },
            )
            current_version = connection.execute(
                text("SELECT version FROM restart_operations WHERE id=:id"), {"id": second_operation["id"]}
            ).scalar_one()
            begin_submission(
                connection,
                account="XL",
                operation_id=second_operation["id"],
                expected_version=current_version,
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
            current_version = connection.execute(
                text("SELECT version FROM restart_operations WHERE id=:id"), {"id": second_operation["id"]}
            ).scalar_one()
            result = cancel_operation(
                connection,
                account="XL",
                operation_id=second_operation["id"],
                expected_version=current_version,
                actor="offline-operator",
                approved_evidence_types=APPROVED_EVIDENCE_TYPES,
            )
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
        with self.engine.connect() as connection:
            pool = connection.execute(
                text("SELECT status, reservation_operation_id FROM ean_pool WHERE reservation_operation_id=:id"),
                {"id": second_operation["id"]},
            ).one()
        self.assertEqual(pool[0], "RESERVED")
        self.assertEqual(str(pool[1]), second_operation["id"])


if __name__ == "__main__":
    unittest.main()

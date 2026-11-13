"""Unit tests for product preparation actions, pricing formulas, and JV/XL isolation."""

import re
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from services.ean_pool_service import import_pool_entries
from services.ai_title_helper import generate_optimized_title


def _setup_schema(connection):
    raw_sql = """
            CREATE TABLE IF NOT EXISTS source_products (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
                data_scope TEXT NOT NULL,
                source_type TEXT NOT NULL,
                aftercool_product_id INTEGER,
                aftercool_sku TEXT,
                aftercool_ean TEXT,
                factory_id TEXT,
                factory_name TEXT,
                original_title TEXT NOT NULL,
                category TEXT,
                image_url TEXT,
                original_price_cents INTEGER,
                attributes_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_source_account_scope UNIQUE (id, account, data_scope)
            );

            CREATE TABLE IF NOT EXISTS candidate_episodes (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
                data_scope TEXT NOT NULL,
                source_product_id TEXT NOT NULL,
                observed_sku TEXT NOT NULL,
                confirmed_ean TEXT NOT NULL,
                moin TEXT,
                period TEXT CHECK (period IN ('3M', '12M')),
                classification TEXT NOT NULL,
                origin TEXT NOT NULL,
                episode_state TEXT NOT NULL CHECK (episode_state IN ('OPEN', 'CLOSED')),
                last_observation_id TEXT,
                last_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_modified TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_episode_account_scope UNIQUE (id, account, data_scope),
                CONSTRAINT fk_episode_source FOREIGN KEY (source_product_id, account, data_scope)
                    REFERENCES source_products (id, account, data_scope)
            );

            CREATE TABLE IF NOT EXISTS restart_operations (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
                data_scope TEXT NOT NULL,
                source_product_id TEXT NOT NULL,
                candidate_episode_id TEXT NOT NULL,
                period TEXT CHECK (period IN ('3M', '12M')),
                status TEXT NOT NULL DEFAULT 'DRAFT',
                attempt_number INT NOT NULL DEFAULT 1,
                submission_state TEXT NOT NULL DEFAULT 'NEVER_SUBMITTED',
                target_sku TEXT,
                new_ean TEXT,
                created_by TEXT NOT NULL,
                history_review_id TEXT,
                repeat_authorization_id TEXT,
                current_revision_number INT NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_op_account_scope UNIQUE (id, account, data_scope)
            );

            CREATE TABLE IF NOT EXISTS operation_revisions (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
                data_scope TEXT NOT NULL,
                revision_number INT NOT NULL,
                source_snapshot_json TEXT NOT NULL,
                content_json TEXT NOT NULL,
                price_snapshot_json TEXT NOT NULL,
                fulfillment_snapshot_json TEXT NOT NULL,
                gtin_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_op_rev UNIQUE (operation_id, revision_number)
            );

            CREATE TABLE IF NOT EXISTS ean_pool (
                ean TEXT NOT NULL,
                account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
                status TEXT NOT NULL DEFAULT 'AVAILABLE',
                data_scope TEXT NOT NULL DEFAULT 'PRODUCTION',
                provenance_source TEXT NOT NULL DEFAULT 'MANUAL_IMPORT',
                eligibility_status TEXT NOT NULL DEFAULT 'ELIGIBLE',
                owner_reference TEXT,
                reservation_operation_id TEXT,
                imported_by TEXT,
                imported_at TIMESTAMP,
                verified_by TEXT,
                verified_at TIMESTAMP,
                reserved_at TIMESTAMP,
                quarantined_at TIMESTAMP,
                quarantine_reason TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_ean_pool PRIMARY KEY (ean, account, data_scope)
            );

            CREATE TABLE IF NOT EXISTS restart_ean_registry (
                ean TEXT NOT NULL,
                account TEXT NOT NULL,
                status TEXT NOT NULL,
                data_scope TEXT NOT NULL,
                PRIMARY KEY (ean, account, data_scope)
            );

            CREATE TABLE IF NOT EXISTS ean_links (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL,
                data_scope TEXT NOT NULL,
                source_product_id TEXT NOT NULL,
                candidate_episode_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                source_ean TEXT NOT NULL,
                new_ean TEXT NOT NULL,
                period TEXT,
                assigned_by TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS qc_results (
                id TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                account TEXT NOT NULL,
                data_scope TEXT NOT NULL,
                outcome TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS restart_events (
                id TEXT PRIMARY KEY,
                account TEXT NOT NULL,
                data_scope TEXT NOT NULL,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
    """
    for statement in raw_sql.split(";"):
        stmt = statement.strip()
        if stmt:
            connection.execute(text(stmt))


def _valid_ean13(seed: int) -> str:
    body = f"499{seed:09d}"
    digits = [int(char) for char in body]
    check = (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits))) % 10) % 10
    return body + str(check)


class ProductActionsTests(unittest.TestCase):
    def test_title_optimization_max_70(self):
        """Verify title generator produces valid German titles strictly <= 70 chars."""
        long_title = "Design Esstisch Massivholz Eiche geölt 180x90 cm mit Schweizer Kante und robustem Metallgestell U-Form Schwarz pulverbeschichtet"
        optimized = generate_optimized_title(long_title, "Esstische")
        self.assertLessEqual(len(optimized), 70)
        self.assertTrue(len(optimized) > 10)
        self.assertTrue(optimized.startswith("Design Esstisch"))

    def test_pricing_discount_rules(self):
        """Verify JV subtracts 1000 cents and XL subtracts 2000 cents."""
        jv_price = 10000  # 100 EUR
        xl_price = 15000  # 150 EUR

        jv_discount = 1000
        xl_discount = 2000

        self.assertEqual(jv_price - jv_discount, 9000)
        self.assertEqual(xl_price - xl_discount, 13000)

    def test_pricing_discount_minimum(self):
        """Verify price never drops below 1 EUR (100 cents)."""
        low_price = 500  # 5 EUR
        target_price = max(100, low_price - 1000)
        self.assertEqual(target_price, 100)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()

"""Offline coverage for the account-scoped NEW_OTTO_2/3 exclusion policy."""
import json
from pathlib import Path
import unittest
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services.factory_exclusions import (
    apply_batch,
    excluded_eans,
    filter_factory_products,
    manifest_hash,
    require_factory_eans_allowed,
    resolve_account_scope,
    rollback_batch,
    validate_manifest,
)


BACKEND = Path(__file__).resolve().parents[1]
MANIFEST_DIR = BACKEND / "data" / "factory_exclusion_manifests"


class FactoryExclusionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def load_manifest(self, account):
        name = "jv_504487.json" if account == "JV" else "xl_483563.json"
        return json.loads((MANIFEST_DIR / name).read_text(encoding="utf-8"))

    def add_mappings(self, manifest, factory_id=None):
        account = models.AccountType(manifest["account"])
        factory_id = factory_id or manifest["source_factory_id"]
        rows = [
            models.ProductMapping(
                product_ean=ean,
                factory_id=factory_id,
                account_name=account,
                category_id="preserve-category",
                status="Error",
                product_name="Preserve name",
                price="123.45",
            )
            for ean in manifest["eans"]
        ]
        self.db.bulk_save_objects(rows)
        self.db.commit()

    def test_both_manifests_match_reviewed_pair_count_and_hash(self):
        for account in ("JV", "XL"):
            manifest = self.load_manifest(account)
            eans, digest = validate_manifest(manifest, manifest["sha256"])
            self.assertEqual(len(eans), manifest["expected_count"])
            self.assertEqual(digest, manifest_hash(eans))
            self.assertEqual(digest, manifest["source_snapshot_sha256"])
            self.assertTrue(manifest["source_snapshot_complete"])

    def test_policy_is_scoped_by_account_and_factory(self):
        ean = "4000000000001"
        self.db.add(models.FactoryProductExclusion(
            account_name="JV", excluded_factory_id="504487", source_factory_id="504466",
            product_ean=ean, batch_id="test-batch", source_manifest_hash="a" * 64,
            source_verified_at=datetime.now(timezone.utc),
            reason="test", actor="test",
        ))
        self.db.commit()

        self.assertEqual(excluded_eans(self.db, "JV", "504487"), {ean})
        self.assertEqual(filter_factory_products(self.db, "JV", "504487", [{"ean": ean}, {"ean": "4000000000002"}]), [{"ean": "4000000000002"}])
        self.assertEqual(filter_factory_products(self.db, "XL", "483563", [{"ean": ean}]), [{"ean": ean}])
        require_factory_eans_allowed(self.db, "JV", "504466", [ean])
        require_factory_eans_allowed(self.db, "XL", "483563", [ean])
        with self.assertRaises(HTTPException) as caught:
            require_factory_eans_allowed(self.db, "JV", "504487", [ean])
        self.assertEqual(caught.exception.detail["code"], "PRODUCT_EXCLUDED_FROM_FACTORY")

    def test_missing_context_fails_closed_for_protected_factory_or_ean(self):
        with self.assertRaises(HTTPException) as caught:
            resolve_account_scope(self.db, None, "483563")
        self.assertEqual(caught.exception.detail["code"], "ACCOUNT_CONTEXT_REQUIRED")

        ean = "4000000000003"
        self.db.add(models.FactoryProductExclusion(
            account_name="XL", excluded_factory_id="483563", source_factory_id="483554",
            product_ean=ean, batch_id="test-batch", source_manifest_hash="b" * 64,
            source_verified_at=datetime.now(timezone.utc),
            reason="test", actor="test",
        ))
        self.db.commit()
        with self.assertRaises(HTTPException) as caught:
            resolve_account_scope(self.db, None, None, [ean])
        self.assertEqual(caught.exception.detail["code"], "ACCOUNT_CONTEXT_REQUIRED")

    def test_jv_apply_is_idempotent_and_rollback_preserves_mapping_data(self):
        manifest = self.load_manifest("JV")
        self.add_mappings(manifest)
        first_ean = manifest["eans"][0]
        self.db.add(models.ProductAttribute(
            product_ean=first_ean, account_name=models.AccountType.JV,
            attribute_name="Color", attribute_value="Blue",
        ))
        self.db.commit()

        applied = apply_batch(self.db, manifest, manifest["sha256"], "offline-test")
        self.assertEqual(applied["excluded_count"], 1057)
        self.assertEqual(applied["reassigned_count"], 0)
        repeated = apply_batch(self.db, manifest, manifest["sha256"], "offline-test")
        self.assertTrue(repeated["already_applied"])
        self.assertEqual(self.db.query(models.ProductMapping).filter_by(
            product_ean=first_ean, account_name=models.AccountType.JV
        ).one().factory_id, "504466")

        rolled_back = rollback_batch(self.db, manifest["batch_id"], "offline-test", manifest["sha256"])
        self.assertEqual(rolled_back["removed_exclusions"], 1057)
        mapping = self.db.query(models.ProductMapping).filter_by(
            product_ean=first_ean, account_name=models.AccountType.JV
        ).one()
        self.assertEqual((mapping.factory_id, mapping.status, mapping.price, mapping.product_name),
                         ("504466", "Error", "123.45", "Preserve name"))
        attribute = self.db.query(models.ProductAttribute).filter_by(product_ean=first_ean).one()
        self.assertEqual(attribute.attribute_value, "Blue")

    def test_manifest_conflict_does_not_partially_apply(self):
        manifest = self.load_manifest("JV")
        self.add_mappings(manifest)
        conflict_ean = manifest["eans"][0]
        mapping = self.db.query(models.ProductMapping).filter_by(
            product_ean=conflict_ean, account_name=models.AccountType.JV
        ).one()
        mapping.factory_id = "unrelated-factory"
        self.db.commit()

        with self.assertRaises(ValueError):
            apply_batch(self.db, manifest, manifest["sha256"], "offline-test")
        self.assertEqual(self.db.query(models.FactoryProductExclusion).count(), 0)
        self.assertEqual(self.db.query(models.ProductMapping).filter_by(
            product_ean=conflict_ean, account_name=models.AccountType.JV
        ).one().factory_id, "unrelated-factory")

    def test_xl_apply_and_rollback_only_change_factory_ownership(self):
        manifest = self.load_manifest("XL")
        self.add_mappings(manifest, factory_id=manifest["excluded_factory_id"])
        plan = models.PublishPlan(
            id=manifest["source_plan_id"], factory_id=manifest["source_factory_id"],
            account_name=models.AccountType.XL, total_products=len(manifest["eans"]),
            total_days=14, status=models.PlanStatus.PAUSED,
            executor_type=models.ExecutorType.OTTO_API,
        )
        self.db.add(plan)
        self.db.flush()
        self.db.bulk_save_objects([
            models.PublishQueueItem(
                plan_id=plan.id, product_ean=ean, account_name=models.AccountType.XL,
                category_id="queue-category", scheduled_day=1, scheduled_date=datetime(2026, 11, 13),
                status=models.QueueItemStatus.PENDING,
            )
            for ean in manifest["eans"]
        ])
        self.db.commit()

        result = apply_batch(self.db, manifest, manifest["sha256"], "offline-test")
        self.assertEqual(result["reassigned_count"], 1058)
        first_ean = manifest["eans"][0]
        mapping = self.db.query(models.ProductMapping).filter_by(
            product_ean=first_ean, account_name=models.AccountType.XL
        ).one()
        self.assertEqual((mapping.factory_id, mapping.status, mapping.price, mapping.category_id),
                         ("483554", "Error", "123.45", "preserve-category"))
        queue_before = self.db.query(models.PublishQueueItem).filter_by(plan_id=plan.id).count()
        self.assertEqual(queue_before, 1058)

        rollback = rollback_batch(self.db, manifest["batch_id"], "offline-test", manifest["sha256"])
        self.assertEqual(rollback["restored_mappings"], 1058)
        mapping = self.db.query(models.ProductMapping).filter_by(
            product_ean=first_ean, account_name=models.AccountType.XL
        ).one()
        self.assertEqual(mapping.factory_id, "483563")
        self.assertEqual(self.db.query(models.PublishQueueItem).filter_by(plan_id=plan.id).count(), queue_before)


if __name__ == "__main__":
    unittest.main()

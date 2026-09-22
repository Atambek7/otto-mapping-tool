import unittest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from scripts.schema_drift_check import (
    get_local_category_attributes,
    compare_category_schemas,
    audit_category_drift,
    run_schema_drift_audit,
)


class TestSchemaDrift(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_compare_category_schemas_detects_drift(self):
        local_schema = {
            "required_attributes": {"Farbe", "Breite", "Höhe"},
            "allowed_attributes": {"Farbe", "Breite", "Höhe", "Tiefe", "Produktsicherheitsverantwortlicher"},
        }

        # Remote OTTO API added new required attribute "Produktsicherheitsverantwortlicher"
        remote_data = {
            "attributes": [
                {"name": "Farbe", "mandatory": True},
                {"name": "Breite", "mandatory": True},
                {"name": "Höhe", "mandatory": True},
                {"name": "Produktsicherheitsverantwortlicher", "mandatory": True},  # NEW REQUIRED
            ]
        }

        diff = compare_category_schemas(local_schema, remote_data)
        self.assertTrue(diff["has_drift"])
        self.assertIn("Produktsicherheitsverantwortlicher", diff["new_required"])
        self.assertTrue(len(diff["notes"]) > 0)

    def test_compare_category_schemas_no_drift(self):
        local_schema = {
            "required_attributes": {"Farbe", "Breite", "Höhe"},
            "allowed_attributes": {"Farbe", "Breite", "Höhe"},
        }
        remote_data = {
            "attributes": [
                {"name": "Farbe", "mandatory": True},
                {"name": "Breite", "mandatory": True},
                {"name": "Höhe", "mandatory": True},
            ]
        }

        diff = compare_category_schemas(local_schema, remote_data)
        self.assertFalse(diff["has_drift"])
        self.assertEqual(len(diff["new_required"]), 0)

    def test_audit_category_drift_records_to_db(self):
        # Category 20110
        log_entry = audit_category_drift("20110", db=self.db, otto_client=None)
        self.db.commit()

        # Check DB entry
        saved = self.db.query(models.SchemaDriftLog).filter_by(category_id="20110").first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.category_id, "20110")
        self.assertIsNotNone(saved.diff_details)

    def test_run_schema_drift_audit_for_published_products(self):
        # Create published mappings with categories
        p1 = models.ProductMapping(
            product_ean="4071489000001",
            account_name=models.AccountType.JV,
            status="Published",
            category_id="20110",
        )
        p2 = models.ProductMapping(
            product_ean="4071489000002",
            account_name=models.AccountType.XL,
            status="Published",
            category_id="25785",
        )
        # Not mapped product (should be ignored by all_published)
        p3 = models.ProductMapping(
            product_ean="4071489000003",
            account_name=models.AccountType.JV,
            status="Not Mapped",
            category_id="99999",
        )
        self.db.add_all([p1, p2, p3])
        self.db.commit()

        res = run_schema_drift_audit(db=self.db, all_published=True)
        self.assertEqual(res["categories_checked"], 2)
        logs = self.db.query(models.SchemaDriftLog).all()
        self.assertEqual(len(logs), 2)
        logged_cat_ids = {l.category_id for l in logs}
        self.assertEqual(logged_cat_ids, {"20110", "25785"})


if __name__ == "__main__":
    unittest.main()

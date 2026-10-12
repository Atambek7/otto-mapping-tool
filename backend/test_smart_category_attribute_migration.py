import unittest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models
from routers.mapping import (
    migrate_product_attributes_for_new_category,
    assign_category,
    assign_category_bulk,
    CategoryMappingBase,
    CategoryMappingBulk,
)

class TestSmartCategoryAttributeMigration(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    @patch("routers.mapping.get_allowed_attribute_names")
    @patch("routers.mapping.ensure_default_attributes")
    @patch("services.validator.validate_product", return_value=[])
    def test_compatible_attributes_preserved_on_category_change(
        self, mock_validate, mock_ensure_defaults, mock_get_allowed
    ):
        ean = "4071489000001"
        account = "JV"

        # Allowed attributes in the new category: Farbe, Material, Breite, Tiefe, Höhe, Marke
        mock_get_allowed.return_value = {"Farbe", "Material", "Breite", "Tiefe", "Höhe", "Marke"}

        # Seed ProductMapping with old category "cat_sofas"
        pm = models.ProductMapping(
            product_ean=ean,
            factory_id="F1",
            account_name=account,
            category_id="cat_sofas",
            status="Ready",
            ai_attributes=["Farbe", "Sitzhöhe"]
        )
        self.db.add(pm)

        # Seed attributes on old category: compatible ones + 1 incompatible (Sitzhöhe)
        attrs = [
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Farbe", attribute_value="Grau"),
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Material", attribute_value="Leder"),
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Breite", attribute_value="200"),
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Tiefe", attribute_value="90"),
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Höhe", attribute_value="85"),
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Marke", attribute_value="JV Möbel"),
            models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Sitzhöhe", attribute_value="45"), # Incompatible with table/new cat
        ]
        self.db.add_all(attrs)
        self.db.commit()

        # Migrate to new category "cat_tables"
        migrate_product_attributes_for_new_category(
            self.db, ean, account, old_cat_id="cat_sofas", new_cat_id="cat_tables", db_mapping=pm
        )
        self.db.commit()

        # Verify surviving attributes
        remaining = self.db.query(models.ProductAttribute).filter_by(product_ean=ean, account_name=account).all()
        rem_dict = {a.attribute_name: a.attribute_value for a in remaining}

        # Compatible attributes MUST be preserved
        self.assertEqual(rem_dict.get("Farbe"), "Grau")
        self.assertEqual(rem_dict.get("Material"), "Leder")
        self.assertEqual(rem_dict.get("Breite"), "200")
        self.assertEqual(rem_dict.get("Tiefe"), "90")
        self.assertEqual(rem_dict.get("Höhe"), "85")
        self.assertEqual(rem_dict.get("Marke"), "JV Möbel")

        # Incompatible attribute (Sitzhöhe) MUST be removed to avoid Otto validation failure
        self.assertNotIn("Sitzhöhe", rem_dict)

        # Verify mapping updated
        self.assertEqual(pm.category_id, "cat_tables")
        self.assertEqual(pm.status, "Ready")
        # ai_attributes pruned
        self.assertEqual(pm.ai_attributes, ["Farbe"])

    @patch("routers.mapping.get_allowed_attribute_names")
    @patch("routers.mapping.ensure_default_attributes")
    @patch("services.validator.validate_product", return_value=["Missing Liegefläche"])
    def test_status_becomes_need_attributes_if_new_category_has_unfulfilled_required_fields(
        self, mock_validate, mock_ensure_defaults, mock_get_allowed
    ):
        ean = "4071489000002"
        account = "XL"

        mock_get_allowed.return_value = {"Farbe", "Breite", "Liegefläche"}

        pm = models.ProductMapping(
            product_ean=ean,
            factory_id="F1",
            account_name=account,
            category_id="cat_chairs",
            status="Ready"
        )
        self.db.add(pm)
        self.db.add(models.ProductAttribute(product_ean=ean, account_name=account, attribute_name="Farbe", attribute_value="Braun"))
        self.db.commit()

        migrate_product_attributes_for_new_category(
            self.db, ean, account, old_cat_id="cat_chairs", new_cat_id="cat_beds", db_mapping=pm
        )
        self.db.commit()

        # Farbe was preserved
        remaining = self.db.query(models.ProductAttribute).filter_by(product_ean=ean, account_name=account).all()
        rem_dict = {a.attribute_name: a.attribute_value for a in remaining}
        self.assertEqual(rem_dict.get("Farbe"), "Braun")

        # Status properly updated to Need Attributes because new category requires fields
        self.assertEqual(pm.status, "Need Attributes")

if __name__ == "__main__":
    unittest.main()

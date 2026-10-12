import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from routers.mapping import (
    get_mass_edit_schema,
    save_attributes_bulk,
    MassEditSchemaRequest,
    AttributeMappingBulk,
)


class TestMassEditSchema(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_empty_eans(self):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = MassEditSchemaRequest(product_eans=[], account="JV")
            res = loop.run_until_complete(get_mass_edit_schema(req, self.db))
            self.assertEqual(res["shared_attrs"], [])
            self.assertEqual(res["other_attrs"], [])
            self.assertEqual(res["current_values"], {})
        finally:
            loop.close()

    @patch("services.categories_repo.get_groups")
    @patch("services.categories_repo.get_category_by_id")
    def test_single_category(self, mock_get_cat, mock_get_groups):
        mock_get_cat.return_value = {
            "categoryId": "cat_1",
            "category_group_id": "group_1",
            "name": "Beds",
        }
        mock_get_groups.return_value = {
            "group_1": {
                "attributes": {
                    "attributes": [
                        {"attributeId": "a1", "name": "Marke", "relevance": "HIGH"},
                        {"attributeId": "a2", "name": "Farbe", "relevance": "LOW"},
                    ]
                }
            }
        }

        # Add mappings
        self.db.add(models.ProductMapping(product_ean="EAN1", account_name="JV", category_id="cat_1"))
        self.db.add(models.ProductMapping(product_ean="EAN2", account_name="JV", category_id="cat_1"))
        self.db.add(models.ProductAttribute(product_ean="EAN1", account_name="JV", attribute_name="Farbe", attribute_value="Blau"))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = MassEditSchemaRequest(product_eans=["EAN1", "EAN2"], account="JV")
            res = loop.run_until_complete(get_mass_edit_schema(req, self.db))
            shared_names = [a["name"] for a in res["shared_attrs"]]
            self.assertIn("Marke", shared_names)
            self.assertIn("Farbe", shared_names)
            self.assertEqual(res["other_attrs"], [])
            self.assertEqual(res["current_values"]["EAN1"]["Farbe"], "Blau")
        finally:
            loop.close()

    @patch("services.categories_repo.get_groups")
    @patch("services.categories_repo.get_category_by_id")
    def test_multiple_categories_shared_and_other_attrs(self, mock_get_cat, mock_get_groups):
        def fake_get_cat(cid):
            if cid == "cat_bed":
                return {"categoryId": "cat_bed", "category_group_id": "grp_bed", "name": "Beds"}
            elif cid == "cat_chair":
                return {"categoryId": "cat_chair", "category_group_id": "grp_chair", "name": "Chairs"}
            return None

        mock_get_cat.side_effect = fake_get_cat
        mock_get_groups.return_value = {
            "grp_bed": {
                "attributes": {
                    "attributes": [
                        {"attributeId": "s1", "name": "Marke", "relevance": "HIGH"},
                        {"attributeId": "b1", "name": "Liegefläche", "relevance": "HIGH"},
                    ]
                }
            },
            "grp_chair": {
                "attributes": {
                    "attributes": [
                        {"attributeId": "s1", "name": "Marke", "relevance": "HIGH"},
                        {"attributeId": "c1", "name": "Sitzhöhe", "relevance": "LOW"},
                    ]
                }
            },
        }

        # Add 2 beds and 1 chair
        self.db.add(models.ProductMapping(product_ean="BED1", account_name="JV", category_id="cat_bed"))
        self.db.add(models.ProductMapping(product_ean="BED2", account_name="JV", category_id="cat_bed"))
        self.db.add(models.ProductMapping(product_ean="CHAIR1", account_name="JV", category_id="cat_chair"))
        self.db.add(models.ProductAttribute(product_ean="BED1", account_name="JV", attribute_name="Liegefläche", attribute_value="140x200"))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = MassEditSchemaRequest(product_eans=["BED1", "BED2", "CHAIR1"], account="JV")
            res = loop.run_until_complete(get_mass_edit_schema(req, self.db))

            # Shared: only Marke
            shared_names = [a["name"] for a in res["shared_attrs"]]
            self.assertEqual(shared_names, ["Marke"])

            # Other: Liegefläche and Sitzhöhe
            other_names = [a["name"] for a in res["other_attrs"]]
            self.assertIn("Liegefläche", other_names)
            self.assertIn("Sitzhöhe", other_names)

            # Check Liegefläche metadata
            liege = next(a for a in res["other_attrs"] if a["name"] == "Liegefläche")
            self.assertEqual(liege["applicable_categories"], ["Beds"])
            self.assertEqual(liege["applicable_eans_count"], 2)
            self.assertTrue(liege["required"])

            # Check Sitzhöhe metadata
            sitz = next(a for a in res["other_attrs"] if a["name"] == "Sitzhöhe")
            self.assertEqual(sitz["applicable_categories"], ["Chairs"])
            self.assertEqual(sitz["applicable_eans_count"], 1)

            # Check current_values loaded for Liegefläche
            self.assertEqual(res["current_values"]["BED1"]["Liegefläche"], "140x200")
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()

import unittest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd
import models
from routers.mapping import (
    auto_map_bulk,
    AutoMapBulkRequest,
    AutoMapProductModel,
    save_attributes,
    AttributeMappingBase,
    save_attributes_bulk,
    AttributeMappingBulk,
    apply_defaults_bulk,
    ApplyDefaultsRequest,
    sync_okb_bulk,
    SyncOkbBulkRequest,
    transfer_to_account,
    TransferAccountRequest,
)
from routers.csv_workflow import process_attribute_dataframes


class TestAiAttributesLifecycle(unittest.TestCase):

    def test_auto_map_bulk_saves_ai_attributes(self):
        """Verify that /mapping/auto-map correctly saves attributes_to_save keys to db_mapping.ai_attributes."""
        mock_db = MagicMock()
        existing_mapping = models.ProductMapping(
            product_ean="4012345678901",
            factory_id="fact-1",
            account_name="JV",
            category_id="cat-1",
            ai_attributes=[]
        )

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            if model == models.ProductMapping:
                q.all.return_value = [existing_mapping]
            else:
                q.all.return_value = []
            return q

        mock_db.query.side_effect = mock_query

        fake_result = {
            "status": "success",
            "ean": "4012345678901",
            "category_id": "cat-1",
            "category_name": "Sofas",
            "attributes": {"Farbe": "Schwarz", "Breite": "200"},
            "errors": [],
            "db_updates": {
                "category_id": "cat-1",
                "mapping_status": "Ready",
                "attributes_to_save": {"Farbe": "Schwarz", "Breite": "200"}
            }
        }

        fake_user = models.User(id="1", username="test", openai_api_key="fake-key")

        with patch("routers.mapping.auto_map_product", return_value=fake_result):
            req = AutoMapBulkRequest(
                factory_id="fact-1",
                account="JV",
                products=[
                    AutoMapProductModel(
                        ean="4012345678901",
                        title="Sofa Test",
                        description="Desc",
                        specifics="",
                        gallery_url="http://img.jpg",
                        picture_urls=""
                    )
                ]
            )
            res = asyncio.run(auto_map_bulk(req, db=mock_db, current_user=fake_user))
            self.assertEqual(res["status"], "success")
            self.assertIn("Farbe", existing_mapping.ai_attributes)
            self.assertIn("Breite", existing_mapping.ai_attributes)

    def test_auto_map_bulk_propagates_openai_error(self):
        """Verify that OpenAIError is propagated as HTTPException with OPENAI_API_ERROR header."""
        from fastapi import HTTPException
        import openai

        mock_db = MagicMock()
        fake_user = models.User(id=1, username="test", openai_api_key="sk-test-fake")

        with patch("routers.mapping.auto_map_product", side_effect=openai.RateLimitError("Quota exceeded", response=MagicMock(status_code=429), body={"error": {"code": "credit_balance_exhausted"}})):
            req = AutoMapBulkRequest(
                factory_id="fact-1",
                account="JV",
                products=[
                    AutoMapProductModel(
                        ean="4012345678901",
                        title="Sofa Test",
                        description="Desc",
                        specifics="",
                        gallery_url="http://img.jpg",
                        picture_urls=""
                    )
                ]
            )
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(auto_map_bulk(req, db=mock_db, current_user=fake_user))

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.headers.get("x-error-code"), "OPENAI_API_ERROR")
            self.assertIn("Quota exceeded", str(ctx.exception.detail))

    def test_save_attributes_single_lifecycle(self):
        """Verify that single product save sanitizes ai_attributes and reflects manual edits."""
        mock_db = MagicMock()
        db_mapping = models.ProductMapping(
            product_ean="4012345678901",
            account_name="JV",
            category_id="cat-1",
            ai_attributes=["Farbe", "Material", "Breite"]
        )
        mock_db.query.return_value.filter.return_value.first.return_value = db_mapping

        with patch("services.validator.validate_product", return_value=[]):
            # User manually changed Material and only passes Farbe and Breite in ai_attributes
            req = AttributeMappingBase(
                product_ean="4012345678901",
                account="JV",
                attributes={"Farbe": "Schwarz", "Material": "Holz", "Breite": "180"},
                ai_attributes=["Farbe", "Breite"]
            )
            res = asyncio.run(save_attributes(req, db=mock_db))
            self.assertEqual(res["status"], "success")
            self.assertEqual(sorted(db_mapping.ai_attributes), ["Breite", "Farbe"])

            # If user empties Breite, it should be removed from ai_attributes
            req2 = AttributeMappingBase(
                product_ean="4012345678901",
                account="JV",
                attributes={"Farbe": "Schwarz", "Breite": ""},
                ai_attributes=["Farbe", "Breite"]
            )
            res2 = asyncio.run(save_attributes(req2, db=mock_db))
            self.assertEqual(res2["status"], "success")
            self.assertEqual(db_mapping.ai_attributes, ["Farbe"])

    def test_save_attributes_bulk_clears_manual_attributes(self):
        """Verify that bulk editing via MassEditDrawer clears edited keys from each product's ai_attributes."""
        mock_db = MagicMock()
        m1 = models.ProductMapping(
            product_ean="4011111111111",
            account_name="JV",
            category_id="cat-1",
            ai_attributes=["Farbe", "Material", "Breite"]
        )
        m2 = models.ProductMapping(
            product_ean="4022222222222",
            account_name="JV",
            category_id="cat-1",
            ai_attributes=["Farbe", "Tiefe"]
        )

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            if model == models.ProductMapping:
                q.all.return_value = [m1, m2]
            else:
                q.all.return_value = []
                q.delete.return_value = 2
            return q

        mock_db.query.side_effect = mock_query

        with patch("services.validator.validate_product", return_value=[]):
            # Human edits Farbe and Material across both products
            req = AttributeMappingBulk(
                product_eans=["4011111111111", "4022222222222"],
                account="JV",
                attributes={"Farbe": "Rot", "Material": "Metall"}
            )
            res = asyncio.run(save_attributes_bulk(req, db=mock_db))
            self.assertEqual(res["status"], "success")
            # m1 lost Farbe and Material, retained Breite
            self.assertEqual(m1.ai_attributes, ["Breite"])
            # m2 lost Farbe, retained Tiefe
            self.assertEqual(m2.ai_attributes, ["Tiefe"])

    def test_apply_defaults_bulk_clears_default_keys(self):
        """Verify that applying defaults removes standard constants from ai_attributes."""
        mock_db = MagicMock()
        m = models.ProductMapping(
            product_ean="4011111111111",
            account_name="JV",
            category_id="cat-1",
            status="Ready",
            ai_attributes=["Zielgruppe", "Marke", "Farbe"]
        )

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            if model == models.ProductMapping:
                q.all.return_value = [m]
            else:
                q.all.return_value = []
            return q

        mock_db.query.side_effect = mock_query

        with patch("routers.mapping.get_attributes_for_apply", return_value={"Zielgruppe": "Erwachsene", "Marke": "Otto Brand"}):
            with patch("routers.mapping.get_allowed_attribute_names", return_value={"Zielgruppe", "Marke", "Farbe"}):
                with patch("services.validator.validate_product", return_value=[]):
                    req = ApplyDefaultsRequest(
                        product_eans=["4011111111111"],
                        account="JV"
                    )
                    res = asyncio.run(apply_defaults_bulk(req, db=mock_db))
                    self.assertEqual(res["status"], "success")
                    # Zielgruppe and Marke removed, Farbe preserved
                    self.assertEqual(m.ai_attributes, ["Farbe"])

    def test_sync_okb_bulk_clears_synced_keys(self):
        """Verify that OKB catalog data replaces and removes keys from ai_attributes."""
        mock_db = MagicMock()
        m = models.ProductMapping(
            product_ean="4011111111111",
            account_name="JV",
            category_id="cat-1",
            ai_attributes=["Farbe", "Breite", "Tiefe"]
        )

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            if model == models.ProductMapping:
                q.first.return_value = None
                q.all.return_value = [m]
            else:
                q.all.return_value = []
            return q

        mock_db.query.side_effect = mock_query

        fake_okb_sync = {
            "status": "success",
            "ean": "4011111111111",
            "category_id": "cat-1",
            "attributes": {"Farbe": "Grau", "Breite": "150 cm"}
        }

        with patch("services.okb_sync.sync_product_data", return_value=fake_okb_sync):
            with patch("services.validator.validate_product", return_value=[]):
                req = SyncOkbBulkRequest(
                    product_eans=["4011111111111"],
                    factory_id="fact-1",
                    account="JV"
                )
                res = asyncio.run(sync_okb_bulk(req, db=mock_db))
                self.assertEqual(res["status"], "success")
                # Farbe and Breite were synced from official OKB, so only Tiefe remains as AI
                self.assertEqual(m.ai_attributes, ["Tiefe"])

    def test_transfer_to_account_ai_attributes(self):
        """Verify that copying attributes during transfer correctly maps and resets ai_attributes in target."""
        mock_db = MagicMock()
        src_m = models.ProductMapping(
            product_ean="4011111111111",
            factory_id="fact-jv",
            product_name="Product 1",
            account_name=models.AccountType.JV,
            category_id="cat-1",
            ai_attributes=["Farbe", "Breite"]
        )
        tgt_m = models.ProductMapping(
            product_ean="4011111111111",
            factory_id="fact-xl",
            product_name="Product 1",
            account_name=models.AccountType.XL,
            category_id="cat-1",
            ai_attributes=["OldStaleAttr"]
        )

        pm_call_count = 0
        def mock_query(model):
            nonlocal pm_call_count
            q = MagicMock()
            q.filter.return_value = q
            if model == models.ProductMapping:
                if pm_call_count == 0:
                    q.all.return_value = [src_m]
                else:
                    q.all.return_value = [tgt_m]
                pm_call_count += 1
                q.first.return_value = tgt_m
            else:
                attr1 = models.ProductAttribute(
                    product_ean="4011111111111",
                    account_name="JV",
                    attribute_name="Farbe",
                    attribute_value="Schwarz"
                )
                attr2 = models.ProductAttribute(
                    product_ean="4011111111111",
                    account_name="JV",
                    attribute_name="Breite",
                    attribute_value="120"
                )
                q.all.return_value = [attr1, attr2]
                q.delete.return_value = 1
            return q

        mock_db.query.side_effect = mock_query

        with patch("services.aftercool_twin.resolve_target_factory_id", new_callable=AsyncMock, return_value="fact-xl"):
            with patch("services.aftercool_twin.get_factory_product_map", new_callable=AsyncMock, return_value={"product 1": "4011111111111"}):
                with patch("services.categories_repo.get_allowed_attribute_names", return_value={"Farbe", "Breite"}):
                    with patch("services.validator.validate_product", return_value=[]):
                        req = TransferAccountRequest(
                            source_account=models.AccountType.JV,
                            target_account=models.AccountType.XL,
                            factory_id="fact-jv",
                            copy_attributes=True
                        )
                        res = asyncio.run(transfer_to_account(req, db=mock_db))
                    self.assertEqual(res["status"], "success")
                    # Target mapping should now have ["Farbe", "Breite"] and OldStaleAttr must be gone
                    self.assertNotIn("OldStaleAttr", tgt_m.ai_attributes)
                    self.assertIn("Farbe", tgt_m.ai_attributes)
                    self.assertIn("Breite", tgt_m.ai_attributes)

    def test_csv_workflow_clears_imported_keys(self):
        """Verify that CSV attribute import clears imported keys from db_mapping.ai_attributes."""
        mock_db = MagicMock()
        db_mapping = models.ProductMapping(
            product_ean="4011111111111",
            factory_id="fact-1",
            account_name="JV",
            category_id="cat-1",
            ai_attributes=["Farbe", "Breite"]
        )

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value = q
            if model == models.ProductMapping:
                q.first.return_value = db_mapping
                q.all.return_value = [db_mapping]
            else:
                q.delete.return_value = 1
                q.all.return_value = []
            return q

        mock_db.query.side_effect = mock_query

        df = pd.DataFrame([
            {"EAN": "4011111111111", "Farbe": "Blau"}
        ])

        with patch("services.validator.validate_product", return_value=[]):
            res = process_attribute_dataframes([df], factory_id="fact-1", db=mock_db, account="JV")
            self.assertEqual(res["status"], "success")
            # Farbe was imported from CSV, so it must be removed from ai_attributes, leaving only Breite
            self.assertEqual(db_mapping.ai_attributes, ["Breite"])


if __name__ == "__main__":
    unittest.main()

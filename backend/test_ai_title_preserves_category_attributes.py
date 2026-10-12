import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from routers.mapping import (
    generate_ai_titles,
    GenerateAiTitlesRequest,
    update_product_details,
    ProductDetailsUpdate
)
from services.ai_title_generator import generate_ai_titles_for_mappings


class TestAiTitlePreservesCategoryAttributes(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Create test user with OpenAI key
        self.user = models.User(
            id=1,
            username="test_user",
            openai_api_key="sk-test-key",
            hashed_password="hash",
            is_admin=True
        )
        self.db.add(self.user)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    @patch("services.categories_service.resolve_category_names", return_value={"cat_beds": "Betten"})
    @patch("services.ai_title_generator.get_openai_client")
    def test_generate_ai_titles_preserves_category_and_attributes_jv(self, mock_get_client, mock_resolve_cats):
        # Mock OpenAI chat completion
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content="Komfortables Polsterbett 140x200 cm Grau"))
        ]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        # Seed JV product with category and attributes
        ean = "4071489000001"
        self.db.add(models.ProductMapping(
            factory_id="F1",
            account_name="JV",
            product_ean=ean,
            product_name="Ein altes sehr langes Polsterbett mit Lattenrost und Matratze fuer Schlafzimmer",
            category_id="cat_beds",
            status="Ready"
        ))
        # Seed 4 ProductAttribute records
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Farbe", attribute_value="Grau"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Breite", attribute_value="140"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Liegefläche", attribute_value="140x200"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Material", attribute_value="Stoff"))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = GenerateAiTitlesRequest(
                factory_id="F1",
                account="JV",
                eans=[ean],
                force=True
            )
            res = loop.run_until_complete(
                generate_ai_titles(req, current_user=self.user, db=self.db)
            )
            self.assertEqual(res["status"], "success")
            self.assertIn(ean, res["updated"])

            # Verify ProductMapping: title updated, but category and status STRICTLY preserved
            pm = self.db.query(models.ProductMapping).filter_by(product_ean=ean, account_name="JV").first()
            self.assertEqual(pm.product_name, "Komfortables Polsterbett 140x200 cm Grau")
            self.assertTrue(pm.has_ai_title)
            self.assertEqual(pm.category_id, "cat_beds")
            self.assertEqual(pm.status, "Ready")

            # Verify ProductAttributes: all 4 attributes remain 100% intact
            attrs = self.db.query(models.ProductAttribute).filter_by(product_ean=ean, account_name="JV").all()
            self.assertEqual(len(attrs), 4)
            attr_map = {a.attribute_name: a.attribute_value for a in attrs}
            self.assertEqual(attr_map["Farbe"], "Grau")
            self.assertEqual(attr_map["Breite"], "140")
            self.assertEqual(attr_map["Liegefläche"], "140x200")
            self.assertEqual(attr_map["Material"], "Stoff")
        finally:
            loop.close()

    @patch("services.categories_service.resolve_category_names", return_value={"cat_tables": "Tische"})
    @patch("services.ai_title_generator.get_openai_client")
    def test_generate_ai_titles_preserves_category_and_attributes_xl(self, mock_get_client, mock_resolve_cats):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content="Esstisch Massivholz Eiche 160x90"))
        ]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        ean = "4071489000002"
        self.db.add(models.ProductMapping(
            factory_id="F1",
            account_name="XL",
            product_ean=ean,
            product_name="Alter Tisch",
            category_id="cat_tables",
            status="Ready"
        ))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="XL", attribute_name="Holzart", attribute_value="Eiche"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="XL", attribute_name="Form", attribute_value="Rechteckig"))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = GenerateAiTitlesRequest(
                factory_id="F1",
                account="XL",
                eans=[ean],
                force=True
            )
            res = loop.run_until_complete(
                generate_ai_titles(req, current_user=self.user, db=self.db)
            )
            self.assertEqual(res["status"], "success")

            pm = self.db.query(models.ProductMapping).filter_by(product_ean=ean, account_name="XL").first()
            self.assertEqual(pm.product_name, "Esstisch Massivholz Eiche 160x90")
            self.assertTrue(pm.has_ai_title)
            self.assertEqual(pm.category_id, "cat_tables")
            self.assertEqual(pm.status, "Ready")

            attrs = self.db.query(models.ProductAttribute).filter_by(product_ean=ean, account_name="XL").all()
            self.assertEqual(len(attrs), 2)
            attr_map = {a.attribute_name: a.attribute_value for a in attrs}
            self.assertEqual(attr_map["Holzart"], "Eiche")
            self.assertEqual(attr_map["Form"], "Rechteckig")
        finally:
            loop.close()

    @patch("services.categories_service.resolve_category_names", return_value={"cat_sofas": "Sofas"})
    @patch("services.ai_title_generator.get_openai_client")
    def test_generate_ai_titles_xl_inherits_jv_category_when_not_yet_mapped(self, mock_get_client, mock_resolve_cats):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content="Ecksofa L-Form Samt Grün"))
        ]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        ean = "4071489000003"
        # Product mapped on JV, but no ProductMapping row on XL yet
        self.db.add(models.ProductMapping(
            factory_id="F1",
            account_name="JV",
            product_ean=ean,
            product_name="Riesiges Sofa auf Lager fuer Wohnzimmer",
            category_id="cat_sofas",
            status="Need Attributes"
        ))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = GenerateAiTitlesRequest(
                factory_id="F1",
                account="XL",
                eans=[ean],
                force=True
            )
            res = loop.run_until_complete(
                generate_ai_titles(req, current_user=self.user, db=self.db)
            )
            self.assertEqual(res["status"], "success")

            # XL mapping should have been created and inherited cat_sofas from JV
            pm_xl = self.db.query(models.ProductMapping).filter_by(product_ean=ean, account_name="XL").first()
            self.assertIsNotNone(pm_xl)
            self.assertEqual(pm_xl.category_id, "cat_sofas")
            self.assertEqual(pm_xl.product_name, "Ecksofa L-Form Samt Grün")
            self.assertTrue(pm_xl.has_ai_title)
        finally:
            loop.close()

    def test_update_product_details_preserves_category_and_attributes(self):
        ean = "4071489000004"
        self.db.add(models.ProductMapping(
            factory_id="F1",
            account_name="JV",
            product_ean=ean,
            product_name="Alter Titel",
            category_id="cat_chairs",
            status="Ready"
        ))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Material", attribute_value="Leder"))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            req = ProductDetailsUpdate(
                product_ean=ean,
                factory_id="F1",
                account="JV",
                product_name="Neuer Titel Stuhl"
            )
            res = loop.run_until_complete(update_product_details(req, db=self.db))
            self.assertEqual(res["status"], "success")

            pm = self.db.query(models.ProductMapping).filter_by(product_ean=ean, account_name="JV").first()
            self.assertEqual(pm.product_name, "Neuer Titel Stuhl")
            self.assertEqual(pm.category_id, "cat_chairs")
            self.assertEqual(pm.status, "Ready")

            attrs = self.db.query(models.ProductAttribute).filter_by(product_ean=ean, account_name="JV").all()
            self.assertEqual(len(attrs), 1)
            self.assertEqual(attrs[0].attribute_value, "Leder")
        finally:
            loop.close()

    @patch("services.ai_title_generator.generate_single_ai_title", return_value="Generierter Titel 40 Zeichen")
    def test_ai_title_generator_service_preserves_category_and_status(self, mock_gen):
        ean = "4071489000005"
        pm = models.ProductMapping(
            factory_id="F1",
            account_name="JV",
            product_ean=ean,
            product_name="Sehr langes altes Moebelstueck",
            category_id="cat_wardrobes",
            status="Ready"
        )
        self.db.add(pm)
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            upd, errs = loop.run_until_complete(
                generate_ai_titles_for_mappings([pm], db=self.db, api_key="sk-test")
            )
            self.assertIn(ean, upd)
            self.assertEqual(pm.category_id, "cat_wardrobes")
            self.assertEqual(pm.status, "Ready")
            self.assertEqual(pm.product_name, "Generierter Titel 40 Zeichen")
            self.assertTrue(pm.has_ai_title)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()

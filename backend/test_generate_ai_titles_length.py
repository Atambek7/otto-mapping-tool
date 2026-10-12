import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from routers.mapping import (
    generate_ai_titles,
    GenerateAiTitlesRequest,
)


class TestGenerateAiTitlesLength(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Create dummy user
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

    @patch("services.categories_service.resolve_category_names", return_value={})
    @patch("services.ai_title_generator.get_openai_client")
    def test_selected_product_under_70_chars_jv_and_xl(self, mock_get_client, mock_resolve_cats):
        # Mock OpenAI chat completion
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content="Neuer KI Produkttitel 50 Zeichen"))
        ]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        # Product with title only 25 characters (< 70 chars) on JV
        self.db.add(models.ProductMapping(
            factory_id="F1",
            account_name="JV",
            product_ean="EAN_JV_SHORT",
            product_name="Kurzer Titel 25 Zeichen",
            category_id="cat_1"
        ))
        # Product with title only 20 characters (< 70 chars) on XL
        self.db.add(models.ProductMapping(
            factory_id="F1",
            account_name="XL",
            product_ean="EAN_XL_SHORT",
            product_name="Kurzer Titel 20 Zei",
            category_id="cat_1"
        ))
        self.db.commit()

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            # 1. Test JV with selected short title
            req_jv = GenerateAiTitlesRequest(
                factory_id="F1",
                account="JV",
                eans=["EAN_JV_SHORT"],
                mode="all"
            )
            res_jv = loop.run_until_complete(
                generate_ai_titles(req_jv, current_user=self.user, db=self.db)
            )
            self.assertEqual(res_jv["status"], "success")
            self.assertIn("EAN_JV_SHORT", res_jv["updated"])
            self.assertEqual(res_jv["updated"]["EAN_JV_SHORT"], "Neuer KI Produkttitel 50 Zeichen")

            # Check DB updated
            pm_jv = self.db.query(models.ProductMapping).filter_by(product_ean="EAN_JV_SHORT", account_name="JV").first()
            self.assertEqual(pm_jv.product_name, "Neuer KI Produkttitel 50 Zeichen")
            self.assertTrue(pm_jv.has_ai_title)

            # 2. Test XL with selected short title
            req_xl = GenerateAiTitlesRequest(
                factory_id="F1",
                account="XL",
                eans=["EAN_XL_SHORT"],
                mode="all"
            )
            res_xl = loop.run_until_complete(
                generate_ai_titles(req_xl, current_user=self.user, db=self.db)
            )
            self.assertEqual(res_xl["status"], "success")
            self.assertIn("EAN_XL_SHORT", res_xl["updated"])
            self.assertEqual(res_xl["updated"]["EAN_XL_SHORT"], "Neuer KI Produkttitel 50 Zeichen")

            # Check DB updated
            pm_xl = self.db.query(models.ProductMapping).filter_by(product_ean="EAN_XL_SHORT", account_name="XL").first()
            self.assertEqual(pm_xl.product_name, "Neuer KI Produkttitel 50 Zeichen")
            self.assertTrue(pm_xl.has_ai_title)

        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()

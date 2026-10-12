import unittest
from decimal import Decimal
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services.aftercool_service import (
    parse_price,
    normalize_image_url,
    parse_aftercool_product,
    AftercoolProduct,
    sync_products_to_db,
    request_aftercool,
)


class TestAftercoolService(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_parse_price(self):
        self.assertEqual(parse_price("1.299,99 €"), Decimal("1299.99"))
        self.assertEqual(parse_price("1299,99"), Decimal("1299.99"))
        self.assertEqual(parse_price("1299.99"), Decimal("1299.99"))
        self.assertEqual(parse_price(499), Decimal("499"))
        self.assertEqual(parse_price(59.95), Decimal("59.95"))
        self.assertIsNone(parse_price(""))
        self.assertIsNone(parse_price("0"))
        self.assertIsNone(parse_price("0.00 €"))
        self.assertIsNone(parse_price(None))
        self.assertIsNone(parse_price("invalid"))

    def test_normalize_image_url(self):
        self.assertEqual(normalize_image_url("https://img.com/1.jpg|https://img.com/2.jpg"), "https://img.com/1.jpg")
        self.assertEqual(normalize_image_url("https://img.com/1.jpg,https://img.com/2.jpg"), "https://img.com/1.jpg")
        self.assertEqual(normalize_image_url(["https://img.com/1.jpg", "https://img.com/2.jpg"]), "https://img.com/1.jpg")
        self.assertEqual(normalize_image_url(" https://img.com/1.jpg "), "https://img.com/1.jpg")
        self.assertIsNone(normalize_image_url(""))
        self.assertIsNone(normalize_image_url(None))

    def test_parse_aftercool_product(self):
        item = {
            "ean": "4071489000001",
            "name": "Fallback Name",
            "row": {
                "EAN": "4071489000001",
                "Artikelbeschreibung": "Modernes Sofa Grau",
                "Startpreis": "899.00",
                "SofortkaufenPreis": "999.00 €",
                "PictureURLs": "https://img.com/sofa1.jpg|https://img.com/sofa2.jpg"
            }
        }
        prod = parse_aftercool_product(item, default_factory_id="504065")
        self.assertIsNotNone(prod)
        self.assertEqual(prod.ean, "4071489000001")
        self.assertEqual(prod.name, "Modernes Sofa Grau")
        self.assertEqual(prod.price, Decimal("999.00"))
        self.assertEqual(prod.price_af, Decimal("899.00"))
        self.assertEqual(prod.image_url, "https://img.com/sofa1.jpg")
        self.assertEqual(prod.factory_id, "504065")

    def test_parse_aftercool_product_skips_invalid_ean(self):
        self.assertIsNone(parse_aftercool_product({"ean": "—"}))
        self.assertIsNone(parse_aftercool_product({"ean": "0"}))
        self.assertIsNone(parse_aftercool_product({"ean": ""}))
        self.assertIsNone(parse_aftercool_product({}))

    @patch("services.aftercool_service.SessionLocal")
    def test_sync_products_to_db_manual_price_protection(self, mock_session_local):
        mock_session_local.return_value = self.db

        # 1. Existing mapping with manual price set by user
        m_manual = models.ProductMapping(
            product_ean="4071489000001",
            factory_id="504065",
            account_name=models.AccountType.JV,
            product_name="Custom User Name",
            price="1500.00",
            has_manual_price=True,
            price_af="899.00"
        )

        # 2. Existing mapping with automatic Aftercool price
        m_auto = models.ProductMapping(
            product_ean="4071489000002",
            factory_id="504065",
            account_name=models.AccountType.JV,
            product_name="Old Auto Name",
            price="800.00",
            has_manual_price=False,
            price_af="750.00"
        )

        self.db.add_all([m_manual, m_auto])
        self.db.commit()

        # New sync payload from Aftercool with new prices
        new_payload = [
            AftercoolProduct(
                ean="4071489000001",
                name="Aftercool Updated Title",
                price=Decimal("999.00"),
                price_af=Decimal("850.00"),
                image_url="https://img.com/1.jpg",
                factory_id="504065",
                raw_item={}
            ),
            AftercoolProduct(
                ean="4071489000002",
                name="New Auto Title",
                price=Decimal("850.00"),
                price_af=Decimal("800.00"),
                image_url="https://img.com/2.jpg",
                factory_id="504065",
                raw_item={}
            ),
            AftercoolProduct(
                ean="4071489000003",
                name="Brand New Product",
                price=Decimal("450.00"),
                price_af=Decimal("400.00"),
                image_url="https://img.com/3.jpg",
                factory_id="504065",
                raw_item={}
            ),
        ]

        res = sync_products_to_db(factory_id="504065", account="JV", data=new_payload)
        self.assertEqual(res["synced"], 3)

        # Verify manual product: price must NOT be overwritten!
        res_manual = self.db.query(models.ProductMapping).filter_by(product_ean="4071489000001").first()
        self.assertEqual(res_manual.price, "1500.00")  # Kept user price!
        self.assertEqual(res_manual.price_af, "850.00")  # Updated startpreis from Aftercool
        self.assertTrue(res_manual.has_manual_price)

        # Verify auto product: price MUST be updated to Aftercool price!
        res_auto = self.db.query(models.ProductMapping).filter_by(product_ean="4071489000002").first()
        self.assertEqual(res_auto.price, "850.00")  # Updated to Aftercool price!
        self.assertEqual(res_auto.product_name, "New Auto Title")
        self.assertFalse(res_auto.has_manual_price)

        # Verify new product created
        res_new = self.db.query(models.ProductMapping).filter_by(product_ean="4071489000003").first()
        self.assertIsNotNone(res_new)
        self.assertEqual(res_new.price, "450.00")
        self.assertFalse(res_new.has_manual_price)

    @patch("services.aftercool_service.SessionLocal")
    def test_sync_products_to_db_account_isolation(self, mock_session_local):
        mock_session_local.return_value = self.db

        # XL item in DB
        xl_item = models.ProductMapping(
            product_ean="4071489000099",
            factory_id="483417",
            account_name=models.AccountType.XL,
            product_name="XL Exclusive Bed",
            price="2000.00"
        )
        self.db.add(xl_item)
        self.db.commit()

        # Syncing JV with same EAN should NOT touch XL item
        jv_payload = [
            AftercoolProduct(
                ean="4071489000099",
                name="JV Twin Bed",
                price=Decimal("1800.00"),
                price_af=Decimal("1700.00"),
                image_url=None,
                factory_id="504065",
                raw_item={}
            )
        ]

        sync_products_to_db(factory_id="504065", account="JV", data=jv_payload)

        # XL item remains completely untouched
        xl_check = self.db.query(models.ProductMapping).filter_by(
            product_ean="4071489000099",
            account_name=models.AccountType.XL
        ).first()
        self.assertEqual(xl_check.product_name, "XL Exclusive Bed")
        self.assertEqual(xl_check.price, "2000.00")
        self.assertEqual(xl_check.factory_id, "483417")

        # JV item created separately
        jv_check = self.db.query(models.ProductMapping).filter_by(
            product_ean="4071489000099",
            account_name=models.AccountType.JV
        ).first()
        self.assertIsNotNone(jv_check)
        self.assertEqual(jv_check.product_name, "JV Twin Bed")
        self.assertEqual(jv_check.factory_id, "504065")


if __name__ == "__main__":
    unittest.main()

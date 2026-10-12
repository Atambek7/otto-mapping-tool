import unittest
from database import SessionLocal
import models
from services.brand_guard import (
    prepare_attributes_for_transfer,
    sanitize_brand_attributes,
    sanitize_brand_text,
    get_forbidden_tokens,
)
from services.constant_attributes import apply_default_attributes
from services.aftercool_service import resolve_aftercool_account, sync_products_to_db, AftercoolProduct
from decimal import Decimal


class TestTransferFactory(unittest.TestCase):
    def test_prepare_attributes_for_transfer_xl_defaults(self):
        src_attrs = {
            "Marke": "JVMOEBEL",
            "Marke laut BattVO": "JVMOEBEL",
            "Materialhinweis": "JVmoebel erfuellt alle CE-Normen...",
            "Farbe": "Weiss",
            "Breite": "180 cm",
            "Hinweis": "Hergestellt durch JVMOEBEL fuer beste Qualitaet",
            "Herstellergarantie": "2 Jahre Garantie",
            "WEEE-Reg.-Nr. DE": "46974041"
        }

        res = prepare_attributes_for_transfer("JV", "XL", src_attrs)

        # 1. Target brand defaults guaranteed
        self.assertEqual(res.get("Marke"), "XLMOEBEL")
        self.assertEqual(res.get("Marke laut BattVO"), "XLMOEBEL")
        self.assertEqual(res.get("WEEE-Reg.-Nr. DE"), "80120806")
        self.assertIn("XLmoebel", res.get("Materialhinweis"))
        self.assertIn("Markeninformationen", res)
        self.assertIn("XLmoebel", res.get("Markeninformationen"))

        # 2. Product-specific attributes kept
        self.assertEqual(res.get("Farbe"), "Weiss")
        self.assertEqual(res.get("Breite"), "180 cm")

        # 3. Warranty strictly stripped
        self.assertNotIn("Herstellergarantie", res)
        self.assertFalse(any("garantie" in k.lower() for k in res.keys()))

        # 4. Text brand replacement in free-text fields
        self.assertNotIn("JVMOEBEL", res.get("Hinweis"))
        self.assertIn("XLMOEBEL", res.get("Hinweis"))

    def test_apply_default_attributes_xl(self):
        empty_attrs = {}
        res = apply_default_attributes(empty_attrs, account="XL", overwrite_empty_only=True)

        self.assertEqual(res.get("Marke"), "XLMOEBEL")
        self.assertEqual(res.get("WEEE-Reg.-Nr. DE"), "80120806")
        self.assertEqual(res.get("Zielgruppe"), "Erwachsene ; Jugendliche")
        self.assertEqual(res.get("Design"), "Modern")

    def test_sanitize_brand_text(self):
        raw_title = "JV Möbel Designer Sofa XXL Schlafsofa Couch JVMOEBEL"
        clean_title = sanitize_brand_text("XL", raw_title)
        self.assertNotIn("JV Möbel", clean_title)
        self.assertNotIn("JVMOEBEL", clean_title)
        self.assertIn("XL Möbel", clean_title)
        self.assertIn("XLMOEBEL", clean_title)

    def test_resolve_aftercool_account_logic(self):
        db = SessionLocal()
        try:
            test_fid = "999999_transfer_test"
            test_ean = "9999999999999"

            # Clean any prior test artifacts
            db.query(models.ProductMapping).filter(
                models.ProductMapping.factory_id == test_fid
            ).delete()
            db.commit()

            # Regular XL factory -> returns XL
            resolved = resolve_aftercool_account(test_fid, "XL", db=db)
            self.assertEqual(resolved, "XL")

            # Create mapping with source_account='JV' under XL
            mapping = models.ProductMapping(
                product_ean=test_ean,
                factory_id=test_fid,
                account_name="XL",
                source_account="JV",
                product_name="Test Product",
                price="100.00",
                has_manual_price=False
            )
            db.add(mapping)
            db.commit()

            # Transferred factory -> returns JV
            resolved_transferred = resolve_aftercool_account(test_fid, "XL", db=db)
            self.assertEqual(resolved_transferred, "JV")

            # Cleanup
            db.query(models.ProductMapping).filter(
                models.ProductMapping.factory_id == test_fid
            ).delete()
            db.commit()
        finally:
            db.close()

    def test_price_protection_logic(self):
        db = SessionLocal()
        try:
            test_fid = "999998_price_test"
            test_ean = "8888888888888"

            db.query(models.ProductMapping).filter(
                models.ProductMapping.factory_id == test_fid
            ).delete()
            db.commit()

            # Create product with manual price
            mapping = models.ProductMapping(
                product_ean=test_ean,
                factory_id=test_fid,
                account_name="XL",
                product_name="Protected Sofa",
                price="799.00",
                has_manual_price=True
            )
            db.add(mapping)
            db.commit()

            mock_product = AftercoolProduct(
                ean=test_ean,
                name="New Aftercool Name",
                price=Decimal("399.00"),
                price_af=Decimal("299.00"),
                image_url=None,
                factory_id=test_fid,
                raw_item={}
            )

            sync_products_to_db(test_fid, "XL", [mock_product])

            # Verify price was NOT overwritten
            refreshed = db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean == test_ean,
                models.ProductMapping.account_name == "XL"
            ).first()

            self.assertEqual(refreshed.price, "799.00")
            self.assertTrue(refreshed.has_manual_price)
            self.assertEqual(refreshed.price_af, "299.00")

            # Cleanup
            db.query(models.ProductMapping).filter(
                models.ProductMapping.factory_id == test_fid
            ).delete()
            db.commit()
        finally:
            db.close()

    def test_brand_guard_self_healing(self):
        from services.brand_guard import validate_brand_safety

        # 1. Dirty attributes from AI or older edit containing JV brand tokens
        dirty_attrs = {
            "Marke": "JVmoebel",
            "Relevanz Gefahrgut": "Nein",
            "Hinweis": "Qualitaet von JVMOEBEL"
        }

        # Raw validation must flag foreign brand
        raw_errors = validate_brand_safety("XL", dirty_attrs)
        self.assertTrue(len(raw_errors) > 0)

        # Self-healing sanitization replaces brand references
        healed_attrs = sanitize_brand_attributes("XL", dirty_attrs, ensure_marke=True)
        self.assertEqual(healed_attrs.get("Marke"), "XLMOEBEL")
        self.assertEqual(healed_attrs.get("Relevanz Gefahrgut"), "Nein")
        self.assertNotIn("JVMOEBEL", healed_attrs.get("Hinweis", ""))
        self.assertIn("XLMOEBEL", healed_attrs.get("Hinweis", ""))

        # Validation after healing must have zero errors
        clean_errors = validate_brand_safety("XL", healed_attrs)
        self.assertEqual(len(clean_errors), 0)

        # 2. Bulk partial edit without Marke should not force Marke into dict
        bulk_attrs = {"Relevanz Gefahrgut": "Nein"}
        healed_bulk = sanitize_brand_attributes("XL", bulk_attrs, ensure_marke=False)
        self.assertNotIn("Marke", healed_bulk)
        self.assertEqual(healed_bulk.get("Relevanz Gefahrgut"), "Nein")

    def test_extract_images_and_transferred_factory_routing(self):
        from services.executors.otto_api import extract_images_from_aftercool_row
        from services.aftercool_service import resolve_aftercool_account

        # 1. Test image extraction with mixed delimiters and lowercase pictureurls
        row_data = {
            "PictureURL": "https://img.example.com/main.jpg",
            "pictureurls": "https://img.example.com/sec1.jpg;https://img.example.com/sec2.jpg\nhttps://img.example.com/sec3.jpg|https://img.example.com/logo_jv.jpg"
        }
        # For XL account: JV logo is filtered out, main + secondary kept
        imgs_xl = extract_images_from_aftercool_row(row_data, account="XL")
        self.assertEqual(len(imgs_xl), 4)
        self.assertEqual(imgs_xl[0], "https://img.example.com/main.jpg")
        self.assertIn("https://img.example.com/sec1.jpg", imgs_xl)
        self.assertIn("https://img.example.com/sec2.jpg", imgs_xl)
        self.assertIn("https://img.example.com/sec3.jpg", imgs_xl)
        self.assertNotIn("https://img.example.com/logo_jv.jpg", imgs_xl)

        # 2. Test routing for transferred factory
        db = SessionLocal()
        try:
            test_fid = "999997_img_route_test"
            test_ean = "7777777777777"
            db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == test_fid).delete()
            db.commit()

            m = models.ProductMapping(
                product_ean=test_ean,
                factory_id=test_fid,
                account_name="XL",
                source_account="JV",
                product_name="Transferred Sofa"
            )
            db.add(m)
            db.commit()

            target_acc = resolve_aftercool_account(test_fid, "XL", db=db)
            self.assertEqual(target_acc, "JV")

            db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == test_fid).delete()
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()



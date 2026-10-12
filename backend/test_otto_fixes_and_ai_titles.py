import unittest
from unittest.mock import patch, MagicMock
from PIL import Image
import io

from services.ai_title_generator import _truncate_title_safely
from services.otto_formatter import _truncate_text_safely, format_otto_product
from services.image_processor import ensure_image_min_dimensions
import models

class TestOttoFixesAndAiTitles(unittest.TestCase):

    def test_truncate_title_safely_under_70(self):
        title = "Modernes Sofa 3-Sitzer Grau"
        self.assertEqual(_truncate_title_safely(title, 70), title)

    def test_truncate_title_safely_over_70_no_broken_words(self):
        # 77 chars: ends with 'Wohnzimmer'
        raw = "Graues modernes 3-Sitzer-Sofa Stoffsofa goldene Füße Designer-Sofa Wohnzimmer"
        res = _truncate_title_safely(raw, 70)
        self.assertLessEqual(len(res), 70)
        self.assertFalse(res.endswith("Woh"))
        self.assertTrue(res.endswith("Designer-Sofa"))
        self.assertEqual(res, "Graues modernes 3-Sitzer-Sofa Stoffsofa goldene Füße Designer-Sofa")

    def test_truncate_text_safely_for_attributes(self):
        long_set_info = "3-Sitzer Sofa mit Schlaffunktion und Bettkasten sowie 2 Sesseln und Kissen"
        res = _truncate_text_safely(long_set_info, 70)
        self.assertLessEqual(len(res), 70)
        # Must not end with a truncated fragment
        self.assertFalse(res.endswith("Kisse"))
        self.assertFalse(res.endswith(" "))

    def test_ensure_image_min_dimensions_upscales_small_image(self):
        # Create a small image 804x600 px (same as 4071489338287)
        img = Image.new("RGB", (804, 600), color="blue")
        upscaled = ensure_image_min_dimensions(img)
        w, h = upscaled.size
        # Must satisfy OTTO minimums: long side >= 960, short side >= 480
        self.assertGreaterEqual(max(w, h), 960)
        self.assertGreaterEqual(min(w, h), 480)
        # Aspect ratio should be maintained (804/600 = 1.34)
        self.assertAlmostEqual(w / h, 804 / 600, delta=0.02)

    def test_ensure_image_min_dimensions_leaves_large_image(self):
        img = Image.new("RGB", (1200, 900), color="red")
        res = ensure_image_min_dimensions(img)
        self.assertEqual(res.size, (1200, 900))

    def test_textile_composition_fallback(self):
        mapping = {
            "product_name": "Bett-Set Baumwolle",
            "category_id": "Bett-Set",
            "category": "Bett-Set",
            "account_name": "JV",
            "price": "199.00",
            "attributes": {
                "brandId": "UO4EGHSX",
                "Bezug": "100% Baumwolle"
            }
        }
        aftercool_info = {
            "name": "Bett-Set Baumwolle",
            "description": "Schönes Bett-Set",
            "price": "199.00",
            "images": ["https://example.com/img1.jpg"]
        }
        with patch("services.categories_repo.get_allowed_attribute_names", return_value=["materialzusammensetzung"]):
            payload = format_otto_product(
                mapping=mapping,
                category_name="Bett-Set",
                ean="4071489336290",
                aftercool_info=aftercool_info
            )
            attrs = payload["productDescription"]["attributes"]
            mat_attr = next((a for a in attrs if a["name"] == "materialzusammensetzung"), None)
            self.assertIsNotNone(mat_attr)
            self.assertEqual(mat_attr["values"], ["100% Baumwolle"])

    def test_publish_payload_product_line_never_exceeds_70(self):
        mapping = {
            "product_name": "Graues modernes 3-Sitzer-Sofa Stoffsofa goldene Füße Designer-Sofa Wohnzimmer",
            "category_id": "Polstergarnitur",
            "category": "Polstergarnitur",
            "account_name": "XL",
            "price": "599.00",
            "attributes": {"brandId": "6HMOZBOU"}
        }
        aftercool_info = {
            "name": "Graues modernes 3-Sitzer-Sofa Stoffsofa goldene Füße Designer-Sofa Wohnzimmer",
            "description": "Tolles Sofa",
            "price": "599.00",
            "images": ["https://example.com/img1.jpg"]
        }
        payload = format_otto_product(
            mapping=mapping,
            category_name="Polstergarnitur",
            ean="4071489335118",
            aftercool_info=aftercool_info
        )
        product_line = payload["productDescription"]["productLine"]
        self.assertLessEqual(len(product_line), 70)
        self.assertFalse(product_line.endswith("Woh"))

if __name__ == "__main__":
    unittest.main()

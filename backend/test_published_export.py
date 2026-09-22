import unittest
import os
from fastapi.testclient import TestClient
from main import app

class TestPublishedExportEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.api_key = os.environ.get("EXTERNAL_SERVICE_API_KEY", "omt_live_ext_key_2026")

    def test_unauthorized_access(self):
        # 1. No auth headers
        res = self.client.get("/export/published-products")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.json().get("detail", ""))

        # 2. Invalid API key
        res_wrong = self.client.get("/export/published-products", headers={"X-API-Key": "invalid_key"})
        self.assertEqual(res_wrong.status_code, 401)

    def test_authorized_get_published_products(self):
        res = self.client.get(
            "/export/published-products?limit=2",
            headers={"X-API-Key": self.api_key}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("total", data)
        self.assertIn("count", data)
        self.assertIn("items", data)
        self.assertLessEqual(data["count"], 2)

        if data["items"]:
            item = data["items"][0]
            # Core top-level blocks
            self.assertIn("identification", item)
            self.assertIn("titles", item)
            self.assertIn("category", item)
            self.assertIn("pricing", item)
            self.assertIn("content", item)
            self.assertIn("media", item)
            self.assertIn("attributes", item)
            self.assertIn("brand_and_regulatory", item)
            self.assertIn("logistics_and_availability", item)
            self.assertIn("compliance_gpsr", item)
            self.assertIn("otto_v5_payload", item)

            # Identification checks
            ident = item["identification"]
            self.assertEqual(ident["status"], "Published")
            self.assertIsNotNone(ident["published_at"])
            self.assertIn(ident["account"], ("JV", "XL"))

            # Content checks
            content = item["content"]
            self.assertIn("description", content)
            self.assertIn("bullet_points", content)
            self.assertIsInstance(content["bullet_points"], list)

            # Media checks
            media = item["media"]
            self.assertIn("main_image", media)
            self.assertIn("gallery_images", media)
            self.assertIn("media_assets", media)

            # Pricing checks
            pricing = item["pricing"]
            self.assertEqual(pricing["currency"], "EUR")
            self.assertGreaterEqual(pricing["price_otto"], 0)

    def test_account_filtering(self):
        res_jv = self.client.get(
            "/export/published-products?account=JV&limit=5",
            headers={"X-API-Key": self.api_key}
        )
        self.assertEqual(res_jv.status_code, 200)
        data_jv = res_jv.json()
        for it in data_jv["items"]:
            self.assertEqual(it["identification"]["account"], "JV")

        res_xl = self.client.get(
            "/export/published-products?account=XL&limit=5",
            headers={"X-API-Key": self.api_key}
        )
        self.assertEqual(res_xl.status_code, 200)
        data_xl = res_xl.json()
        for it in data_xl["items"]:
            self.assertEqual(it["identification"]["account"], "XL")

    def test_api_alias_endpoint(self):
        res = self.client.get(
            "/api/published-products?limit=2",
            headers={"X-API-Key": self.api_key}
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("items", res.json())

    def test_exclude_raw_payload(self):
        res = self.client.get(
            "/export/published-products?limit=1&include_raw_payload=false",
            headers={"X-API-Key": self.api_key}
        )
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        if items:
            self.assertNotIn("otto_v5_payload", items[0])

if __name__ == "__main__":
    unittest.main()

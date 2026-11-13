import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

import models
from database import get_db
from main import app
from routers.published_export import build_drip_factories_response, _load_factory_names_map


class TestDripFactoriesLogic(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        models.PublishPlan.__table__.create(self.engine)
        models.ProductMapping.__table__.create(self.engine)
        models.PublishQueueItem.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()

        # Mock Redis cache for factory names
        self.redis_mock = Mock()
        def mock_get(key):
            if key in ("aftercool_factories_cache", "aftercool_factories_last_good"):
                return json.dumps({
                    "items": [
                        {"id": "503916", "name": "NEW_ROPEZ_SPECIAL", "account": "JV"},
                        {"id": "483385", "name": "NEW_ROPEZ_SPECIAL", "account": "XL"},
                        {"id": "504088", "name": "NEW_AIONEX_SPECIAL", "account": "JV"},
                        {"id": "483419", "name": "NEW_AIONEX_SPECIAL", "account": "XL"},
                    ]
                })
            return None
        self.redis_mock.get.side_effect = mock_get
        self.patch_redis = patch("routers.published_export.redis_client", self.redis_mock)
        self.patch_redis.start()

    def tearDown(self):
        self.patch_redis.stop()
        self.db.close()

    def test_load_factory_names_map(self):
        fac_map = _load_factory_names_map()
        self.assertEqual(fac_map.get("503916"), "NEW_ROPEZ_SPECIAL")
        self.assertEqual(fac_map.get("483385"), "NEW_ROPEZ_SPECIAL")

    def test_drip_export_pairing_logic(self):
        # 1. Setup plans: JV (503916) and XL (483385) for NEW_ROPEZ_SPECIAL (OTTO_API)
        plan_jv = models.PublishPlan(
            id=1,
            factory_id="503916",
            factory_name="503916",
            account_name="JV",
            total_products=4,
            total_days=1,
            executor_type=models.ExecutorType.OTTO_API,
        )
        plan_xl = models.PublishPlan(
            id=2,
            factory_id="483385",
            factory_name="483385",
            account_name="XL",
            total_products=4,
            total_days=1,
            executor_type=models.ExecutorType.OTTO_API,
        )
        # Batch file plan which should be ignored
        plan_batch = models.PublishPlan(
            id=3,
            factory_id="503916",
            factory_name="503916",
            account_name="JV",
            total_products=1,
            total_days=1,
            executor_type=models.ExecutorType.BATCH_FILE,
        )
        self.db.add_all([plan_jv, plan_xl, plan_batch])
        self.db.commit()

        # 2. Setup mappings:
        # Case A: Same EAN (4000000000001)
        # Case B: Matched by image_url (JV: 4000000000002, XL: 4000000000003, img: http://img.com/sofa.jpg)
        # Case C: Matched by normalized title (JV: 4000000000004, XL: 4000000000005, title: 'Luxus Sofa Grau')
        # Case D: Unmatched JV (4000000000006)
        # Case E: Unmatched XL (4000000000007)
        mappings = [
            models.ProductMapping(product_ean="4000000000001", factory_id="503916", account_name="JV", product_name="Table 1"),
            models.ProductMapping(product_ean="4000000000001", factory_id="483385", account_name="XL", product_name="Table 1"),
            models.ProductMapping(product_ean="4000000000002", factory_id="503916", account_name="JV", image_url="http://img.com/sofa.jpg"),
            models.ProductMapping(product_ean="4000000000003", factory_id="483385", account_name="XL", image_url="http://img.com/sofa.jpg"),
            models.ProductMapping(product_ean="4000000000004", factory_id="503916", account_name="JV", product_name="Luxus Sofa Grau"),
            models.ProductMapping(product_ean="4000000000005", factory_id="483385", account_name="XL", product_name="luxus sofa  grau"),
            models.ProductMapping(product_ean="4000000000006", factory_id="503916", account_name="JV", product_name="Solo JV"),
            models.ProductMapping(product_ean="4000000000007", factory_id="483385", account_name="XL", product_name="Solo XL"),
        ]
        self.db.add_all(mappings)
        self.db.commit()

        # 3. Setup queue items:
        now = datetime.now(timezone.utc)
        items = [
            models.PublishQueueItem(id=1, plan_id=1, product_ean="4000000000001", account_name="JV", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=2, plan_id=2, product_ean="4000000000001", account_name="XL", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=3, plan_id=1, product_ean="4000000000002", account_name="JV", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=4, plan_id=2, product_ean="4000000000003", account_name="XL", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=5, plan_id=1, product_ean="4000000000004", account_name="JV", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=6, plan_id=2, product_ean="4000000000005", account_name="XL", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=7, plan_id=1, product_ean="4000000000006", account_name="JV", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            models.PublishQueueItem(id=8, plan_id=2, product_ean="4000000000007", account_name="XL", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
            # Batch plan item (should not be exported)
            models.PublishQueueItem(id=9, plan_id=3, product_ean="4999999999999", account_name="JV", category_id="cat1", scheduled_day=1, scheduled_date=now, status="published"),
        ]
        self.db.add_all(items)
        self.db.commit()

        # Run test: full export (only_paired=False)
        import asyncio
        res = asyncio.run(build_drip_factories_response(include_existing=False, only_paired=False, db=self.db))

        self.assertIn("NEW_ROPEZ_SPECIAL", res)
        pairs = res["NEW_ROPEZ_SPECIAL"]
        self.assertEqual(len(pairs), 5)

        # Check matched pairs
        self.assertIn({"jv": "4000000000001", "xl": "4000000000001"}, pairs)
        self.assertIn({"jv": "4000000000002", "xl": "4000000000003"}, pairs)
        self.assertIn({"jv": "4000000000004", "xl": "4000000000005"}, pairs)
        self.assertIn({"jv": "4000000000006", "xl": None}, pairs)
        self.assertIn({"jv": None, "xl": "4000000000007"}, pairs)

        # Run test: only_paired=True
        res_paired = asyncio.run(build_drip_factories_response(include_existing=False, only_paired=True, db=self.db))
        pairs_only = res_paired["NEW_ROPEZ_SPECIAL"]
        self.assertEqual(len(pairs_only), 3)
        for p in pairs_only:
            self.assertIsNotNone(p["jv"])
            self.assertIsNotNone(p["xl"])


class TestDripFactoriesEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.api_key = os.environ.get("EXTERNAL_SERVICE_API_KEY", "omt_live_ext_key_2026")

    def test_unauthorized_drip_factories(self):
        # 1. No auth headers
        res = self.client.get("/export/drip-factories")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.json().get("detail", ""))

        # 2. Invalid API key
        res_wrong = self.client.get("/export/drip-factories", headers={"X-API-Key": "wrong_key"})
        self.assertEqual(res_wrong.status_code, 401)

    def test_authorized_drip_factories_export(self):
        # 1. Via X-API-Key header on /export/drip-factories
        res = self.client.get("/export/drip-factories", headers={"X-API-Key": self.api_key})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, dict)

        # 2. Via ?api_key= on /api/drip-factories
        res_alias = self.client.get(f"/api/drip-factories?api_key={self.api_key}")
        self.assertEqual(res_alias.status_code, 200)
        data_alias = res_alias.json()
        self.assertIsInstance(data_alias, dict)

        # If data is populated, verify sample structure
        for fname, pairs in list(data.items())[:3]:
            self.assertIsInstance(fname, str)
            self.assertIsInstance(pairs, list)
            if pairs:
                first_pair = pairs[0]
                self.assertIn("jv", first_pair)
                self.assertIn("xl", first_pair)


if __name__ == "__main__":
    unittest.main()

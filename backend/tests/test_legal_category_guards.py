import asyncio
from datetime import datetime, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from routers import mapping, publish_pipeline
from services import automapper
from services.executors.otto_api import OttoApiExecutor
from services.otto_formatter import format_otto_product
from services.validator import (
    detect_category_attribute_conflict,
    get_legal_blocking_errors,
    validate_product,
)


class LegalCategoryGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_furniture_in_bedding_category_detected_and_blocked(self):
        attrs = {
            "Marke": "JVMOEBEL",
            "Material": "Holz, Textil, Metall",
            "Holzart": "Buche",
        }
        conflict = detect_category_attribute_conflict(
            "15778", attrs, product_name="Schlafzimmer Set Rosa Bett Nachttische"
        )
        self.assertIsNotNone(conflict)
        errs = validate_product(
            "4071489802917", "15778", attrs, product_name="Schlafzimmer Set Rosa Bett"
        )
        self.assertTrue(any("15778" in e for e in errs))
        legal_errs = get_legal_blocking_errors(
            "15778", attrs, product_name="Schlafzimmer Set Rosa Bett", allow_formatter_defaults=True
        )
        self.assertGreaterEqual(len(legal_errs), 2)
        self.assertTrue(any("Materialzusammensetzung" in e for e in legal_errs))

    def test_valid_bedding_set_with_composition_passes_legal_gate(self):
        attrs = {
            "Marke": "JVMOEBEL",
            "Materialzusammensetzung": "100% Baumwolle",
        }
        legal_errs = get_legal_blocking_errors(
            "15778", attrs, product_name="Bett-Set 2-tlg. Bettdecke Kopfkissen", allow_formatter_defaults=True
        )
        self.assertEqual(legal_errs, [])

    def test_otto_formatter_skips_furniture_material_and_polsterbett_textile_fallback(self):
        aftercool = {
            "name": "Polsterbett Rosa",
            "description": "Schönes Polsterbett",
            "price": "999.00",
            "images": ["https://example.com/1.jpg"],
        }
        # 15720 = Polsterbett (group 2137 Betten) must NOT auto-inject Materialzusammensetzung
        payload_bed = format_otto_product(
            ean="4071489796285",
            aftercool_info=aftercool,
            mapping={
                "product_name": "Polsterbett Rosa",
                "category_id": "15720",
                "category": "Polsterbett",
                "price": "999.00",
                "attributes": {"brandId": "UO4EGHSX", "Bezug": "Textil"},
            },
            category_name="Polsterbett",
            account_name="JV",
        )
        bed_attr_names = {a["name"] for a in payload_bed["productDescription"]["attributes"]}
        self.assertNotIn("Materialzusammensetzung", bed_attr_names)

        # Even if 15778 is forced, rigid furniture material like Holz/Metall must NOT be copied
        payload_set = format_otto_product(
            ean="4071489802917",
            aftercool_info=aftercool,
            mapping={
                "product_name": "Schlafzimmer Set Rosa",
                "category_id": "15778",
                "category": "Bett-Set",
                "price": "999.00",
                "attributes": {"brandId": "UO4EGHSX", "Material": "Holz, Textil, Metall"},
            },
            category_name="Bett-Set",
            account_name="JV",
        )
        set_attr_names = {a["name"] for a in payload_set["productDescription"]["attributes"]}
        self.assertNotIn("Materialzusammensetzung", set_attr_names)

    def test_predict_category_overrides_llm_bedding_choice_for_furniture(self):
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"reasoning": "test", "category_id": "15778"}'
                        )
                    )
                ]
            )
        )
        with (
            patch.object(automapper, "generate_search_keywords", AsyncMock(return_value=["Polsterbett", "Holz"])),
            patch.object(automapper, "get_openai_client", return_value=fake_client),
        ):
            cat_id = asyncio.run(
                automapper.predict_category(
                    "Title: Luxus Polsterbett Holz 180x200",
                    "Luxus Polsterbett Holz 180x200",
                    custom_api_key="fake-key",
                )
            )
        self.assertNotEqual(cat_id, "15778")

    def test_drip_create_and_executor_block_missing_legal_item(self):
        self.db.add(
            models.AccountSettings(
                account_name="JV",
                otto_client_id="cid",
                otto_client_secret="sec",
            )
        )
        self.db.add(
            models.ProductMapping(
                product_ean="4071489802917",
                factory_id="504180",
                account_name=models.AccountType.JV,
                category_id="15778",
                status="Error",
                product_name="Schlafzimmer Set Rosa Bett Nachttische",
                price="6449.99",
                image_url="https://example.com/a.jpg",
            )
        )
        self.db.add(
            models.ProductAttribute(
                product_ean="4071489802917",
                account_name=models.AccountType.JV,
                attribute_name="Material",
                attribute_value="Holz, Textil, Metall",
            )
        )
        self.db.commit()

        req = publish_pipeline.PipelineCreateRequest(
            account_name=models.AccountType.JV,
            days=3,
            product_eans=["4071489802917"],
        )
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(publish_pipeline.create_pipeline_plan("504180", req, db=self.db))
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("Materialzusammensetzung", caught.exception.detail)

        # Executor also enforces the LEGAL pre-check for pre-existing queue rows
        plan = models.PublishPlan(
            factory_id="504180",
            account_name=models.AccountType.JV,
            total_products=1,
            total_days=1,
        )
        self.db.add(plan)
        self.db.flush()
        q_item = models.PublishQueueItem(
            plan_id=plan.id,
            product_ean="4071489802917",
            account_name=models.AccountType.JV,
            category_id="15778",
            scheduled_day=1,
            scheduled_date=datetime.now(timezone.utc),
            status=models.QueueItemStatus.PENDING,
        )
        self.db.add(q_item)
        self.db.commit()

        with (
            patch("services.executors.otto_api.OttoMarketClient") as client_cls,
            patch("services.executors.otto_api.format_otto_product") as fmt_mock,
            patch("routers.aftercool.get_aftercool_session", AsyncMock(side_effect=RuntimeError("skip"))),
        ):
            res = OttoApiExecutor().publish_batch(plan, [q_item], self.db)
        self.assertEqual(res["failed"], 1)
        self.assertEqual(q_item.status, models.QueueItemStatus.FAILED)
        self.assertIn("Materialzusammensetzung", q_item.error_message)
        client_cls.return_value.publish_products.assert_not_called()
        fmt_mock.assert_not_called()

    def test_assign_category_syncs_unfinished_queue_items(self):
        self.db.add(
            models.ProductMapping(
                product_ean="4071489802917",
                factory_id="504180",
                account_name=models.AccountType.JV,
                category_id="15778",
                status="Error",
                product_name="Schlafzimmer Set Rosa Bett Nachttische",
            )
        )
        plan = models.PublishPlan(
            factory_id="504180",
            account_name=models.AccountType.JV,
            total_products=2,
            total_days=1,
        )
        self.db.add(plan)
        self.db.flush()
        failed_item = models.PublishQueueItem(
            plan_id=plan.id,
            product_ean="4071489802917",
            account_name=models.AccountType.JV,
            category_id="15778",
            scheduled_day=1,
            scheduled_date=datetime.now(timezone.utc),
            status=models.QueueItemStatus.FAILED,
        )
        self.db.add(failed_item)
        self.db.commit()

        asyncio.run(
            mapping.assign_category(
                mapping.CategoryMappingBase(
                    product_ean="4071489802917",
                    factory_id="504180",
                    account="JV",
                    category_id="24447",
                ),
                db=self.db,
            )
        )
        self.db.refresh(failed_item)
        self.assertEqual(failed_item.category_id, "24447")


if __name__ == "__main__":
    unittest.main()

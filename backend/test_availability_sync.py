import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services.otto_availability import (
    DEFAULT_STOCK_QUANTITY,
    extract_stock_from_aftercool_item,
    apply_otto_availability,
)
from tasks_availability_sync import run_availability_sync


class TestAvailabilitySync(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_extract_stock_from_aftercool_item(self):
        # 1. From row.Menge
        item1 = {"row": {"Menge": "15"}}
        self.assertEqual(extract_stock_from_aftercool_item(item1), 15)

        # 2. From item.Menge
        item2 = {"Menge": 7}
        self.assertEqual(extract_stock_from_aftercool_item(item2), 7)

        # 3. From row.Stock / Quantity
        item3 = {"row": {"Stock": "0"}}
        self.assertEqual(extract_stock_from_aftercool_item(item3), 0)

        # 4. Invalid or missing
        self.assertIsNone(extract_stock_from_aftercool_item({"row": {"Menge": "invalid"}}))
        self.assertIsNone(extract_stock_from_aftercool_item({}))
        self.assertIsNone(extract_stock_from_aftercool_item(None))

    def test_apply_otto_availability_custom_quantities_map(self):
        mock_client = MagicMock()
        mock_client.update_quantities.return_value = {"status_code": 200}
        mock_client.update_product_delivery_information.return_value = {"status_code": 200}

        items = [
            {"product_ean": "4000000000001"},
            {"product_ean": "4000000000002"},
        ]
        quantities_map = {
            "4000000000001": 5,
            # 4000000000002 missing -> fallback to DEFAULT_STOCK_QUANTITY
        }

        res = apply_otto_availability(
            client=mock_client,
            items=items,
            account_name="JV",
            db=self.db,
            quantities_map=quantities_map,
        )

        self.assertTrue(res["success"])
        mock_client.update_quantities.assert_called_once()
        payload = mock_client.update_quantities.call_args[0][0]
        self.assertEqual(payload, [
            {"sku": "4000000000001", "quantity": 5},
            {"sku": "4000000000002", "quantity": DEFAULT_STOCK_QUANTITY},
        ])

    def test_apply_otto_availability_from_aftercool_product_map(self):
        mock_client = MagicMock()
        mock_client.update_quantities.return_value = {"status_code": 200}
        mock_client.update_product_delivery_information.return_value = {"status_code": 200}

        items = [{"product_ean": "4000000000003"}]
        af_map = {
            "4000000000003": {"row": {"Menge": "12"}}
        }

        res = apply_otto_availability(
            client=mock_client,
            items=items,
            account_name="XL",
            db=self.db,
            aftercool_product_map=af_map,
        )

        self.assertTrue(res["success"])
        payload = mock_client.update_quantities.call_args[0][0]
        self.assertEqual(payload, [{"sku": "4000000000003", "quantity": 12}])

    def test_periodic_availability_sync_is_disabled_without_network_or_price_writes(self):
        settings = models.AccountSettings(
            account_name="JV", otto_client_id="fixture", otto_client_secret="fixture"
        )
        mapping = models.ProductMapping(
            product_ean="4071489000001", account_name=models.AccountType.JV,
            factory_id="101", status="Published", price="100.00",
        )
        self.db.add_all([settings, mapping])
        self.db.commit()
        with patch("tasks_availability_sync.fetch_aftercool_products_for_factory") as fetch, \
             patch("services.otto_client.OttoMarketClient") as client:
            result = run_availability_sync(db=self.db)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["writes"], 0)
        self.assertEqual(mapping.price, "100.00")
        fetch.assert_not_called()
        client.assert_not_called()
        self.assertEqual(self.db.query(models.AvailabilitySyncLog).count(), 0)

if __name__ == "__main__":
    unittest.main()

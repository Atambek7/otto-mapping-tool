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

    @patch("tasks_availability_sync.OttoMarketClient")
    @patch("tasks_availability_sync.fetch_aftercool_products_for_factory")
    def test_run_availability_sync_price_update_and_logging(self, mock_fetch_af, mock_otto_client_cls):
        # Setup mock Otto client
        mock_client_instance = MagicMock()
        mock_client_instance.update_quantities.return_value = {"status_code": 200}
        mock_otto_client_cls.return_value = mock_client_instance

        # Setup AccountSettings
        settings = models.AccountSettings(
            account_name="JV",
            otto_client_id="test-id",
            otto_client_secret="test-secret",
            otto_partner_id="test-partner",
        )
        self.db.add(settings)

        # Setup ProductMappings: one with price change, one with unchanged price
        p1 = models.ProductMapping(
            product_ean="4071489000001",
            account_name=models.AccountType.JV,
            factory_id="101",
            status="Published",
            price="100.00",
        )
        p2 = models.ProductMapping(
            product_ean="4071489000002",
            account_name=models.AccountType.JV,
            factory_id="101",
            status="Published",
            price="200.00",
        )
        self.db.add_all([p1, p2])
        self.db.commit()

        # Mock Aftercool returning new price for p1 and new stock for both
        mock_af_data = {
            "4071489000001": {
                "row": {
                    "Menge": "8",
                    "SofortkaufenPreis": "129.99",
                }
            },
            "4071489000002": {
                "row": {
                    "Menge": "0",  # Out of stock
                    "SofortkaufenPreis": "200.00",
                }
            },
        }
        
        async def _fake_fetch(*args, **kwargs):
            return mock_af_data
            
        mock_fetch_af.side_effect = _fake_fetch

        summary = run_availability_sync(db=self.db, batch_delay_sec=0)

        self.assertEqual(summary["total_published"], 2)
        self.assertEqual(summary["factories_processed"], 1)
        self.assertEqual(summary["synced_ok"], 2)
        self.assertEqual(summary["price_updated"], 1)
        self.assertEqual(summary["out_of_stock_detected"], 1)

        # Verify DB mapping was updated for p1
        self.db.refresh(p1)
        self.assertEqual(p1.price, "129.99")

        # Verify AvailabilitySyncLog records
        logs = self.db.query(models.AvailabilitySyncLog).all()
        self.assertEqual(len(logs), 2)
        log_eans = {l.product_ean: l for l in logs}

        self.assertEqual(log_eans["4071489000001"].new_stock, 8)
        self.assertEqual(log_eans["4071489000001"].new_price, "129.99")
        self.assertEqual(log_eans["4071489000001"].sync_result, "ok")

        self.assertEqual(log_eans["4071489000002"].new_stock, 0)
        self.assertEqual(log_eans["4071489000002"].sync_result, "ok")


if __name__ == "__main__":
    unittest.main()

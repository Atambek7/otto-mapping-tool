import unittest
from unittest.mock import MagicMock
from services.otto_availability import extract_unknown_skus, apply_otto_availability


class TestOttoAvailability207(unittest.TestCase):

    def test_extract_unknown_skus_status_207(self):
        # Sample OTTO 207 Multi-Status response with one unknown SKU and other errors
        res_207 = {
            "status_code": 207,
            "response": [
                {
                    "title": "Validation Failure",
                    "path": "/v1/availability/product-delivery-information",
                    "code": 404,
                    "detail": "Error sku: 4071489306262 is unknown",
                    "logref": "sku"
                }
            ]
        }
        candidate_skus = ["4071489292503", "4071489306262", "4071489266979"]
        unknowns = extract_unknown_skus(res_207, candidate_skus)
        self.assertEqual(unknowns, ["4071489306262"])

    def test_extract_unknown_skus_status_200(self):
        res_200 = {
            "status_code": 200,
            "response": {"results": [{"sku": "4071489292503", "quantity": 20}]}
        }
        unknowns = extract_unknown_skus(res_200, ["4071489292503"])
        self.assertEqual(unknowns, [])

    def test_apply_otto_availability_handles_207_quantities(self):
        mock_client = MagicMock()
        # Quantities returns 207 with 1 succeeded and 1 unknown
        mock_client.update_quantities.return_value = {
            "status_code": 207,
            "response": {
                "results": [{"sku": "4071489292503", "quantity": 20}],
                "errors": [{"detail": "Error sku: 4071489306262 is unknown"}]
            }
        }
        # Delivery returns 200
        mock_client.update_product_delivery_information.return_value = {
            "status_code": 200,
            "response": {}
        }

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []

        item1 = MagicMock(product_ean="4071489292503")
        item2 = MagicMock(product_ean="4071489306262")

        res = apply_otto_availability(
            client=mock_client,
            items=[item1, item2],
            account_name="JV",
            db=mock_db,
        )

        self.assertIn("4071489306262", res["unknown_skus"])
        self.assertIn("4071489306262", res["quantity_failed_skus"])
        self.assertNotIn("4071489292503", res["quantity_failed_skus"])


if __name__ == "__main__":
    unittest.main()

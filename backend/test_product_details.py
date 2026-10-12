import unittest
import asyncio
from unittest.mock import MagicMock
from fastapi import HTTPException
import models
from routers.mapping import (
    ProductDetailsUpdate, 
    update_product_details,
    get_factory_mappings
)

class TestProductDetails(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.query_mock = MagicMock()
        self.db.query.return_value = self.query_mock
        self.filter_mock = MagicMock()
        self.query_mock.filter.return_value = self.filter_mock

    def test_update_product_name_success(self):
        existing_mapping = models.ProductMapping(
            product_ean="4071489823417",
            account_name=models.AccountType.JV,
            factory_id="504220",
            product_name="Old Title",
            price="1299"
        )
        self.filter_mock.first.return_value = existing_mapping

        req = ProductDetailsUpdate(
            product_ean="4071489823417",
            account="JV",
            product_name="New Elegantes Sofa Beige"
        )
        res = asyncio.run(update_product_details(req, db=self.db))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["product_name"], "New Elegantes Sofa Beige")
        self.assertEqual(existing_mapping.product_name, "New Elegantes Sofa Beige")
        self.db.commit.assert_called_once()

    def test_update_price_formats(self):
        existing_mapping = models.ProductMapping(
            product_ean="4071489823417",
            account_name=models.AccountType.JV,
            factory_id="504220"
        )
        self.filter_mock.first.return_value = existing_mapping

        # Format 1: 1450,50 €
        req1 = ProductDetailsUpdate(product_ean="4071489823417", price="1450,50 €")
        res1 = asyncio.run(update_product_details(req1, db=self.db))
        self.assertEqual(res1["price"], "1450.50")

        # Format 2: 1299.00 -> 1299
        req2 = ProductDetailsUpdate(product_ean="4071489823417", price="1299.00")
        res2 = asyncio.run(update_product_details(req2, db=self.db))
        self.assertEqual(res2["price"], "1299")

    def test_update_price_invalid_rejects(self):
        existing_mapping = models.ProductMapping(product_ean="4071489823417", account_name=models.AccountType.JV)
        self.filter_mock.first.return_value = existing_mapping

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(update_product_details(ProductDetailsUpdate(product_ean="4071489823417", price="-100"), db=self.db))
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(update_product_details(ProductDetailsUpdate(product_ean="4071489823417", price="not-a-number"), db=self.db))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_update_empty_title_rejects(self):
        existing_mapping = models.ProductMapping(product_ean="4071489823417", account_name=models.AccountType.JV)
        self.filter_mock.first.return_value = existing_mapping

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(update_product_details(ProductDetailsUpdate(product_ean="4071489823417", product_name="   "), db=self.db))
        self.assertEqual(ctx.exception.status_code, 400)

if __name__ == "__main__":
    unittest.main()

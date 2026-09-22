import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services.aftercool_twin import resolve_twin_product
from routers.mapping import transfer_to_account, TransferAccountRequest


class TestTransferSafety(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_resolve_twin_product_missing_source_factory(self):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            target_fid, target_ean, prod_name = loop.run_until_complete(
                resolve_twin_product(
                    source_account="JV",
                    target_account="XL",
                    source_factory_id=None,
                    source_ean="4071489000001",
                    source_product_name="Some Product",
                )
            )
            # CRITICAL: Must not fall back to source_ean!
            self.assertIsNone(target_fid)
            self.assertIsNone(target_ean)
            self.assertIsNone(prod_name)
        finally:
            loop.close()

    @patch("services.aftercool_twin.resolve_target_factory_id")
    def test_resolve_twin_product_unresolved_target_factory(self, mock_resolve_tfid):
        import asyncio
        mock_resolve_tfid.return_value = None

        loop = asyncio.new_event_loop()
        try:
            target_fid, target_ean, prod_name = loop.run_until_complete(
                resolve_twin_product(
                    source_account="JV",
                    target_account="XL",
                    source_factory_id="504220",
                    source_ean="4071489000001",
                    source_product_name="Some Product",
                )
            )
            # CRITICAL: Must not fall back to source_ean or source_factory_id!
            self.assertIsNone(target_fid)
            self.assertIsNone(target_ean)
            self.assertIsNone(prod_name)
        finally:
            loop.close()

    @patch("services.aftercool_twin.get_factory_product_map")
    @patch("services.aftercool_twin.resolve_target_factory_id")
    def test_resolve_twin_product_no_title_match(self, mock_resolve_tfid, mock_get_map):
        import asyncio
        mock_resolve_tfid.return_value = "483411"
        # Target factory has products, but none match "Different Modern Sofa"
        mock_get_map.return_value = {
            "vintage table brown": "4071489999999",
        }

        loop = asyncio.new_event_loop()
        try:
            target_fid, target_ean, prod_name = loop.run_until_complete(
                resolve_twin_product(
                    source_account="JV",
                    target_account="XL",
                    source_factory_id="504220",
                    source_ean="4071489000001",
                    source_product_name="Different Modern Sofa",
                )
            )
            # Target factory was resolved, but EAN must be None (no dangerous fallback to source_ean!)
            self.assertEqual(target_fid, "483411")
            self.assertIsNone(target_ean)
            self.assertEqual(prod_name, "Different Modern Sofa")
        finally:
            loop.close()

    @patch("services.aftercool_twin.get_factory_product_map")
    @patch("services.aftercool_twin.resolve_target_factory_id")
    def test_resolve_twin_product_successful_match(self, mock_resolve_tfid, mock_get_map):
        import asyncio
        mock_resolve_tfid.return_value = "483411"
        mock_get_map.return_value = {
            "modern sofa lux beige": "4071489333333",
        }

        loop = asyncio.new_event_loop()
        try:
            target_fid, target_ean, prod_name = loop.run_until_complete(
                resolve_twin_product(
                    source_account="JV",
                    target_account="XL",
                    source_factory_id="504220",
                    source_ean="4071489000001",
                    source_product_name="Modern Sofa Lux Beige",
                )
            )
            self.assertEqual(target_fid, "483411")
            self.assertEqual(target_ean, "4071489333333")
            self.assertEqual(prod_name, "Modern Sofa Lux Beige")
        finally:
            loop.close()

    @patch("services.aftercool_twin.get_factory_product_map")
    @patch("services.aftercool_twin.resolve_target_factory_id")
    def test_transfer_endpoint_aborts_single_unmatched_twin(self, mock_resolve_tfid, mock_get_map):
        import asyncio
        mock_resolve_tfid.return_value = "483411"
        mock_get_map.return_value = {}  # Empty target map -> no twin found

        # Create source product
        src_m = models.ProductMapping(
            product_ean="4071489111111",
            account_name=models.AccountType.JV,
            factory_id="504220",
            status="Ready",
            product_name="Unique Nonexistent Item",
        )
        self.db.add(src_m)
        self.db.commit()

        req = TransferAccountRequest(
            source_account=models.AccountType.JV,
            target_account=models.AccountType.XL,
            product_eans=["4071489111111"],
        )

        loop = asyncio.new_event_loop()
        try:
            with self.assertRaises(HTTPException) as ctx:
                loop.run_until_complete(transfer_to_account(req=req, db=self.db))
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("Twin product not found", ctx.exception.detail)

            # Ensure NO record was created in XL with source EAN
            xl_m = (
                self.db.query(models.ProductMapping)
                .filter(
                    models.ProductMapping.product_ean == "4071489111111",
                    models.ProductMapping.account_name == models.AccountType.XL,
                )
                .first()
            )
            self.assertIsNone(xl_m)
        finally:
            loop.close()

    @patch("services.aftercool_twin.get_factory_product_map")
    @patch("services.aftercool_twin.resolve_target_factory_id")
    def test_transfer_endpoint_batch_skips_unmatched_and_transfers_matched(
        self, mock_resolve_tfid, mock_get_map
    ):
        import asyncio
        mock_resolve_tfid.return_value = "483411"
        mock_get_map.return_value = {
            "matched sofa": "4071489222222",  # Target EAN for item 1
        }

        # Create 2 source products
        p1 = models.ProductMapping(
            product_ean="4071489000001",
            account_name=models.AccountType.JV,
            factory_id="504220",
            status="Ready",
            product_name="Matched Sofa",
        )
        p2 = models.ProductMapping(
            product_ean="4071489000002",
            account_name=models.AccountType.JV,
            factory_id="504220",
            status="Ready",
            product_name="Unmatched Chair",
        )
        self.db.add_all([p1, p2])
        self.db.commit()

        req = TransferAccountRequest(
            source_account=models.AccountType.JV,
            target_account=models.AccountType.XL,
            factory_id="504220",
        )

        loop = asyncio.new_event_loop()
        try:
            res = loop.run_until_complete(transfer_to_account(req=req, db=self.db))
            self.assertEqual(res["transferred_count"], 1)
            self.assertEqual(res["unresolved_twins_count"], 1)
            self.assertIn("4071489000002", res["unresolved_twins"])

            # Verify: Matched twin exists in XL with target EAN
            matched_xl = (
                self.db.query(models.ProductMapping)
                .filter(
                    models.ProductMapping.product_ean == "4071489222222",
                    models.ProductMapping.account_name == models.AccountType.XL,
                )
                .first()
            )
            self.assertIsNotNone(matched_xl)
            self.assertEqual(matched_xl.factory_id, "483411")

            # Verify: Unmatched item was NOT created with source EAN
            unmatched_xl = (
                self.db.query(models.ProductMapping)
                .filter(
                    models.ProductMapping.product_ean == "4071489000002",
                    models.ProductMapping.account_name == models.AccountType.XL,
                )
                .first()
            )
            self.assertIsNone(unmatched_xl)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()

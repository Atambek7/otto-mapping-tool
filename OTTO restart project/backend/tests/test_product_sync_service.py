from __future__ import annotations

import unittest
from datetime import datetime, timezone

from services.preparation_service import PreparationError, price_snapshot_from_sources
from services.product_sync_service import ProductSyncError, build_product_sync_snapshot


class RestartPricePolicyTests(unittest.TestCase):
    @staticmethod
    def otto(amount: str, sku: str = "source-sku") -> dict:
        return {"sku": sku, "standardPrice": {"amount": amount, "currency": "EUR"}}

    def test_confirmed_account_discounts_and_99_for_both_sources(self):
        for account, expected in (("JV", 9099), ("XL", 8099)):
            with self.subTest(account=account):
                result = price_snapshot_from_sources(account, self.otto("100.00"), source_type="AFTERCOOL", aftercool_price_cents=9000)
                self.assertEqual(result["targetPriceCents"], expected)
                self.assertEqual(result["selectedSource"], "OTTO_STANDARD_PRICE")
                self.assertEqual(result["discountCents"], 1000 if account == "JV" else 2000)

    def test_higher_otto_source_uses_jv_discount_and_99_ending(self):
        result = price_snapshot_from_sources(
            "JV", self.otto("1600.00"), source_type="AFTERCOOL", aftercool_price_cents=120000,
        )
        self.assertEqual(result["targetPriceCents"], 159099)
        self.assertEqual(result["selectedSource"], "OTTO_STANDARD_PRICE")

    def test_higher_aftercool_source_uses_xl_discount_and_99_ending(self):
        result = price_snapshot_from_sources(
            "XL", self.otto("1600.00"), source_type="AFTERCOOL", aftercool_price_cents=180050,
        )
        self.assertEqual(result["targetPriceCents"], 178099)
        self.assertEqual(result["selectedSource"], "AFTERCOOL_START_PRICE")
        self.assertEqual(result["aftercoolStartPriceCents"], 180050)

    def test_equal_price_prefers_otto_and_fractional_price_is_repriced_from_source(self):
        equal = price_snapshot_from_sources(
            "JV", self.otto("1600.00"), source_type="AFTERBUY", aftercool_price_cents=160000,
        )
        fractional = price_snapshot_from_sources(
            "JV", self.otto("1200.00"), source_type="AFTERCOOL", aftercool_price_cents=180050,
        )
        ending_99 = price_snapshot_from_sources(
            "JV", self.otto("1600.99"), source_type="OTTO_DIRECT",
        )
        self.assertEqual(equal["selectedSource"], "OTTO_STANDARD_PRICE")
        self.assertEqual(equal["targetPriceCents"], 159099)
        self.assertEqual(fractional["targetPriceCents"], 179099)
        self.assertEqual(ending_99["targetPriceCents"], 159099)

    def test_direct_otto_uses_only_otto_and_invalid_prices_fail_closed(self):
        direct = price_snapshot_from_sources("XL", self.otto("1600.00"), source_type="OTTO_DIRECT")
        self.assertEqual(direct["targetPriceCents"], 158099)
        with self.assertRaises(PreparationError):
            price_snapshot_from_sources("JV", self.otto("10.00"), source_type="OTTO_DIRECT")

    def test_only_available_source_price_is_used_without_comparison(self):
        otto_only = price_snapshot_from_sources("JV", self.otto("100.00"), source_type="AFTERCOOL")
        aftercool_only = price_snapshot_from_sources(
            "XL", {"sku": "source-sku"}, source_type="AFTERCOOL", aftercool_price_cents=180050,
        )
        self.assertEqual((otto_only["originalPriceCents"], otto_only["targetPriceCents"]), (10000, 9099))
        self.assertEqual(otto_only["selectedSource"], "OTTO_STANDARD_PRICE")
        self.assertEqual((aftercool_only["originalPriceCents"], aftercool_only["targetPriceCents"]), (180050, 178099))
        self.assertEqual(aftercool_only["selectedSource"], "AFTERCOOL_START_PRICE")
        with self.assertRaises(PreparationError):
            price_snapshot_from_sources("JV", {"sku": "source-sku"}, source_type="AFTERCOOL")


class ProductSyncSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "account": "JV",
            "data_scope": "PRODUCTION",
            "candidate_episode_id": "candidate-1",
            "source_product_id": "source-1",
            "source_type": "AFTERCOOL",
            "observed_sku": "source-sku",
            "confirmed_ean": "4067282484559",
            "aftercool_product_id": "AC-42",
            "factory_id": "factory-7",
        }
        self.product = {
            "sku": "source-sku",
            "ean": "4067282484559",
            "category": {"categoryId": "FURNITURE_TABLES", "name": "Esstisch"},
            "productDescription": {
                "productName": "OTTO source title",
                "attributes": [
                    {"name": "Material", "values": ["Eiche", "Metall"]},
                    {"name": "Farbe", "value": "Schwarz"},
                ],
            },
        }
        self.prices = {"sku": "source-sku", "standardPrice": {"amount": "1600.00", "currency": "EUR"}}
        self.aftercool = {
            "account": "JV", "status": "FOUND", "found": True, "verified": True,
            "data_scope": "PRODUCTION", "source_type": "AFTERCOOL", "product_id": "AC-42",
            "ean": "4067282484559", "factory_id": "factory-7", "price_cents": 180050,
        }

    def test_snapshot_requires_same_account_and_confirmed_identity_and_keeps_all_attributes(self):
        result = build_product_sync_snapshot(
            self.context, account="JV", otto_product=self.product, otto_prices=self.prices,
            aftercool=self.aftercool, captured_at=datetime.now(timezone.utc),
        )
        self.assertEqual(result["category"], "Esstisch")
        self.assertEqual(len(result["attributes"]), 2)
        self.assertEqual(result["attributes"][0]["values"], ["Eiche", "Metall"])
        self.assertEqual(result["revisionAttributes"][0]["value"], "Eiche; Metall")
        self.assertEqual(result["revisionAttributes"][0]["sourceRefs"], ["OTTO:/v5/products/source-sku"])
        self.assertEqual(result["priceSnapshot"]["ottoStandardPriceCents"], 160000)
        self.assertEqual(result["priceSnapshot"]["aftercoolStartPriceCents"], 180050)
        self.assertEqual(result["priceSnapshot"]["targetPriceCents"], 179099)

        wrong_account = {**self.aftercool, "account": "XL"}
        unresolved = build_product_sync_snapshot(
            self.context, account="JV", otto_product=self.product, otto_prices=self.prices,
            aftercool=wrong_account, captured_at=datetime.now(timezone.utc),
        )
        self.assertIsNone(unresolved["aftercoolIdentity"])
        self.assertEqual(unresolved["aftercoolIssue"], "AFTERCOOL_IDENTITY_MISMATCH")
        self.assertEqual(unresolved["priceSnapshot"]["ottoStandardPriceCents"], 160000)
        self.assertEqual(unresolved["priceSnapshot"]["targetPriceCents"], 159099)

    def test_missing_aftercool_startpreis_uses_available_exact_otto_price(self):
        cases = (
            ({**self.aftercool, "price_cents": None}, "AFTERCOOL_START_PRICE_UNVERIFIED"),
            ({"account": "JV", "status": "LOOKUP_ERROR", "verified": False, "reason_code": "TIMEOUT"}, "TIMEOUT"),
            (None, "AFTERCOOL_IDENTITY_UNVERIFIED"),
        )
        for aftercool, expected_issue in cases:
            with self.subTest(aftercool=aftercool):
                result = build_product_sync_snapshot(
                    self.context, account="JV", otto_product=self.product, otto_prices=self.prices,
                    aftercool=aftercool, captured_at=datetime.now(timezone.utc),
                )
                self.assertEqual(result["priceSnapshot"]["ottoStandardPriceCents"], 160000)
                self.assertEqual(result["priceSnapshot"]["targetPriceCents"], 159099)
                self.assertEqual(result["priceSnapshot"]["selectedSource"], "OTTO_STANDARD_PRICE")
                self.assertEqual(result["aftercoolIssue"], expected_issue)

    def test_missing_otto_price_uses_verified_aftercool_price_alone(self):
        result = build_product_sync_snapshot(
            self.context, account="JV", otto_product=self.product,
            otto_prices={"sku": "source-sku"}, aftercool=self.aftercool,
            captured_at=datetime.now(timezone.utc),
        )
        self.assertIsNone(result["priceSnapshot"]["ottoStandardPriceCents"])
        self.assertEqual(result["priceSnapshot"]["aftercoolStartPriceCents"], 180050)
        self.assertEqual(result["priceSnapshot"]["originalPriceCents"], 180050)
        self.assertEqual(result["priceSnapshot"]["targetPriceCents"], 179099)
        self.assertEqual(result["priceSnapshot"]["selectedSource"], "AFTERCOOL_START_PRICE")

    def test_wrong_sku_or_incomplete_category_and_attributes_are_rejected(self):
        with self.assertRaises(ProductSyncError):
            build_product_sync_snapshot(
                self.context, account="JV", otto_product={**self.product, "sku": "other"},
                otto_prices=self.prices, aftercool=self.aftercool, captured_at=datetime.now(timezone.utc),
            )
        with self.assertRaises(ProductSyncError):
            build_product_sync_snapshot(
                self.context, account="JV", otto_product={**self.product, "category": None},
                otto_prices=self.prices, aftercool=self.aftercool, captured_at=datetime.now(timezone.utc),
            )
        with self.assertRaises(ProductSyncError):
            build_product_sync_snapshot(
                self.context,
                account="JV",
                otto_product={"sku": "source-sku", "category": "CAT", "productDescription": {}},
                otto_prices=self.prices,
                aftercool=self.aftercool,
                captured_at=datetime.now(timezone.utc),
            )

    def test_context_account_and_scope_must_be_production_and_match_request(self):
        with self.assertRaises(ProductSyncError) as wrong_account:
            build_product_sync_snapshot(
                self.context, account="XL", otto_product=self.product, otto_prices=self.prices,
                aftercool=self.aftercool, captured_at=datetime.now(timezone.utc),
            )
        self.assertEqual(wrong_account.exception.code, "ACCOUNT_MISMATCH")

        with self.assertRaises(ProductSyncError) as legacy_scope:
            build_product_sync_snapshot(
                {**self.context, "data_scope": "LEGACY_UNVERIFIED"}, account="JV",
                otto_product=self.product, otto_prices=self.prices,
                aftercool=self.aftercool, captured_at=datetime.now(timezone.utc),
            )
        self.assertEqual(legacy_scope.exception.code, "SOURCE_SCOPE_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()

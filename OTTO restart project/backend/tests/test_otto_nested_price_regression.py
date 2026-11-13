from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.import_pipeline import _otto_product_snapshot, _resolve_import_row
from services.otto_price_evidence import normalize_otto_product_variation, resolve_otto_standard_price
from services.product_catalog_service import _product_dto
from services.product_sync_service import build_product_sync_snapshot
from services.otto_status_service import OttoStatusService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "otto_v5_product_nested_price.json"
PRODUCT = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SOURCE_SKU = "4260606059116"
SOURCE_EAN = "4260606059116"


class NoPriceEndpointOtto:
    def __init__(self):
        self.price_calls = 0

    def get_marketplace_status(self, sku):
        return {"sku": sku, "marketPlaceStatus": "REJECTED",
                "errors": [{"code": "602016 - Nonseller", "title": "No sales"}]}

    def get_product(self, sku):
        return PRODUCT

    def get_product_prices(self, sku):
        self.price_calls += 1
        raise AssertionError("nested V5 standardPrice should avoid a second GET")


class DirectOttoAftercool:
    def lookup_product(self, account, ean, factory_hint=None):
        return {
            "account": account, "ean": ean, "status": "NOT_FOUND_CONFIRMED", "found": False,
            "verified": True, "data_scope": "PRODUCTION", "source_type": "OTTO_DIRECT",
            "lookup_identifier": ean,
        }


def catalog_row(*, account="JV", source_type="AFTERBUY"):
    return {
        "id": "candidate-1", "candidate_episode_id": "candidate-1", "source_product_id": "source-1",
        "source_aftercool_product_id": None, "account": account, "data_scope": "PRODUCTION",
        "source_type": source_type, "factory_id": "factory-1", "factory_name": "Factory",
        "sku": SOURCE_SKU, "ean": SOURCE_EAN, "moin": None, "period": "3M", "status": "3M",
        "classification": "EXACT_CODE", "origin": "BASELINE", "episode_state": "OPEN",
        "operation_id": None, "operation_status": None, "operation_version": None,
        "current_revision_number": None, "target_sku": None, "new_ean": None,
        "title": "Source title", "source_title": "Source title", "draft_title": None,
        "prepared_title": None, "saved_edit": None, "price_cents": None, "price_preview": None,
        "category": "Skulptur", "image_url": None, "attributes": [], "source_data": {"ottoProductData": PRODUCT},
        "errors": [], "error_codes": [], "error_titles": [], "last_modified": None, "last_checked_at": None,
    }


class NestedOttoPriceTests(unittest.TestCase):
    def test_nested_v5_pricing_resolves_exact_sku_and_never_uses_msrp(self):
        normalized = normalize_otto_product_variation(PRODUCT, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["sku"], SOURCE_SKU)
        self.assertEqual(normalized["ean"], SOURCE_EAN)
        resolved = resolve_otto_standard_price(PRODUCT, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(resolved["evidence"]["amountCents"], 415900)
        self.assertEqual(resolved["evidence"]["path"], "productResponse.productVariations[0].pricing.standardPrice")
        self.assertEqual(resolved["response"]["standardPrice"]["amount"], 4159)
        self.assertEqual(resolved["response"]["standardPrice"]["amount"], PRODUCT["productVariations"][0]["pricing"]["standardPrice"]["amount"])

    def test_msrp_is_not_a_fallback_and_wrong_identity_is_rejected(self):
        without_standard = json.loads(json.dumps(PRODUCT))
        del without_standard["productVariations"][0]["pricing"]["standardPrice"]
        missing = resolve_otto_standard_price(without_standard, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(missing["evidence"]["status"], "MISSING_AMOUNT")
        self.assertIsNone(missing["evidence"]["amountCents"])
        mismatch = resolve_otto_standard_price(PRODUCT, None, expected_sku="other-sku", expected_ean=SOURCE_EAN)
        self.assertEqual(mismatch["evidence"]["status"], "SKU_OR_EAN_MISMATCH")

    def test_missing_null_zero_currency_and_ambiguous_price_states_are_distinct(self):
        missing = json.loads(json.dumps(PRODUCT))
        del missing["productVariations"][0]["pricing"]["standardPrice"]["amount"]
        self.assertEqual(resolve_otto_standard_price(missing, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)["evidence"]["status"], "MISSING_AMOUNT")

        null_amount = json.loads(json.dumps(PRODUCT))
        null_amount["productVariations"][0]["pricing"]["standardPrice"]["amount"] = None
        self.assertEqual(resolve_otto_standard_price(null_amount, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)["evidence"]["status"], "AMOUNT_NULL")

        zero_amount = json.loads(json.dumps(PRODUCT))
        zero_amount["productVariations"][0]["pricing"]["standardPrice"]["amount"] = 0
        zero = resolve_otto_standard_price(zero_amount, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)["evidence"]
        self.assertEqual((zero["status"], zero["amountCents"]), ("ZERO", 0))

        wrong_currency = json.loads(json.dumps(PRODUCT))
        wrong_currency["productVariations"][0]["pricing"]["standardPrice"]["currency"] = "USD"
        wrong = resolve_otto_standard_price(wrong_currency, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)["evidence"]
        self.assertEqual(wrong["status"], "UNSUPPORTED_CURRENCY")
        self.assertIsNone(wrong["amountCents"])

        ambiguous = json.loads(json.dumps(PRODUCT))
        duplicate = json.loads(json.dumps(ambiguous["productVariations"][0]))
        duplicate["pricing"]["standardPrice"]["amount"] = 4200
        ambiguous["productVariations"].append(duplicate)
        result = resolve_otto_standard_price(ambiguous, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(result["evidence"]["status"], "AMBIGUOUS_VARIATION_PRICE")

    def test_matched_variant_never_inherits_parent_level_price(self):
        payload = {**PRODUCT, "pricing": {"standardPrice": {"amount": 9000, "currency": "EUR"}}}
        normalized = normalize_otto_product_variation(payload, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(normalized["pricing"]["standardPrice"]["amount"], 4159)
        resolved = resolve_otto_standard_price(normalized, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(resolved["evidence"]["amountCents"], 415900)

        with_parent_identity = {**payload, "sku": SOURCE_SKU, "ean": SOURCE_EAN}
        resolved = resolve_otto_standard_price(with_parent_identity, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(resolved["evidence"]["amountCents"], 415900)

    def test_import_uses_nested_price_without_separate_price_request(self):
        otto = NoPriceEndpointOtto()
        result = _resolve_import_row(
            "JV", {"sku": SOURCE_SKU, "ean": SOURCE_EAN, "raw_identifier": SOURCE_SKU},
            otto_client=otto, aftercool_service=DirectOttoAftercool(), status_service=OttoStatusService(),
        )
        self.assertEqual(result["row_state"], "VALID")
        self.assertEqual(otto.price_calls, 0)
        self.assertEqual(result["source_record"]["price_cents"], 415900)
        self.assertEqual(result["source_record"]["otto_price_evidence"]["status"], "CONFIRMED")

    def test_product_sync_accepts_nested_price_for_direct_otto_account(self):
        context = {
            "account": "XL", "data_scope": "PRODUCTION", "candidate_episode_id": "candidate-1",
            "source_product_id": "source-1", "source_type": "OTTO_DIRECT", "observed_sku": SOURCE_SKU,
            "confirmed_ean": SOURCE_EAN,
        }
        variation = dict(PRODUCT["productVariations"][0])
        variation["category"] = "Skulptur"
        variation["productDescription"] = {**variation["productDescription"], "attributes": [{"name": "Material", "values": ["Kunststoff"]}]}
        snapshot = build_product_sync_snapshot(
            context, account="XL", otto_product={"productVariations": [variation]}, otto_prices=None,
            aftercool=None, captured_at=datetime.now(timezone.utc),
        )
        self.assertEqual(snapshot["priceSnapshot"]["ottoStandardPriceCents"], 415900)
        self.assertEqual(snapshot["priceSnapshot"]["targetPriceCents"], 413999)

    def test_catalog_shows_ottoo_source_without_fabricating_target_when_aftercool_is_unverified(self):
        row = catalog_row(account="JV", source_type="AFTERBUY")
        dto = _product_dto(row)
        self.assertEqual(dto.otto_source_price["amountCents"], 415900)
        self.assertEqual(dto.target_price_status, "PRICE_REVIEW_REQUIRED")
        self.assertIsNone(dto.price_preview)

    def test_catalog_direct_otto_calculates_only_from_account_scoped_otto_price(self):
        dto = _product_dto(catalog_row(account="XL", source_type="OTTO_DIRECT"))
        self.assertEqual(dto.otto_source_price["amountCents"], 415900)
        self.assertEqual(dto.target_price_status, "CALCULATED")
        self.assertEqual(dto.price_preview["targetCents"], 413999)

    def test_snapshot_parser_exposes_nested_standard_price_and_raw_product(self):
        normalized = normalize_otto_product_variation(PRODUCT, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        snapshot = _otto_product_snapshot(normalized, None, expected_sku=SOURCE_SKU, expected_ean=SOURCE_EAN)
        self.assertEqual(snapshot["price_cents"], 415900)
        self.assertEqual(snapshot["otto_price_evidence"]["amountCents"], 415900)
        self.assertEqual(snapshot["title"], "Moderne Skulptur Dekofigur Hund für den Garten")


if __name__ == "__main__":
    unittest.main()

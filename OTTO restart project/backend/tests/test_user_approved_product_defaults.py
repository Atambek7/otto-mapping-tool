from __future__ import annotations

import json
import unittest
from unittest.mock import Mock
from uuid import uuid4

from services.availability_service import _validated_fulfillment_payloads
from services.ean_pool_service import is_valid_ean13
from services.fulfillment_defaults import (
    DEFAULT_PROCESSING_TIME_DAYS,
    DEFAULT_SHIPPING_PROFILE_IDS,
    DEFAULT_STOCK_QUANTITY,
    default_fulfillment_reference,
    default_target_fulfillment,
)
from services.gtin_service import valid_ean13, validate_gtin_eligibility
from services.import_pipeline import _validate_row
from services.preparation_service import PreparationError, target_barcode_identity


class _FakeResult:
    def __init__(self, *, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return self

    def first(self):
        return self.row

    def scalar_one(self):
        return self.scalar


class UserApprovedProductDefaultsTests(unittest.TestCase):
    def test_barcode_validation_requires_13_ascii_digits_without_checksum_test(self):
        # This 13-digit value has an invalid GS1 check digit; current policy accepts its format.
        invalid_check_digit = "4069424010016"
        self.assertTrue(valid_ean13(invalid_check_digit))
        self.assertTrue(is_valid_ean13(invalid_check_digit))
        self.assertFalse(valid_ean13("123456789012"))
        self.assertFalse(valid_ean13("12345678901234"))
        self.assertFalse(valid_ean13("١٢٣٤٥٦٧٨٩٠١٢٣"))

    def test_source_import_accepts_13_digit_barcode_without_checksum_test(self):
        barcode = "4069424010016"
        self.assertEqual(
            _validate_row({"raw_identifier": barcode, "identifier_type": "EAN", "ean": None, "sku": None}),
            (barcode, barcode, None),
        )

    def test_product_identity_is_derived_from_one_pool_barcode(self):
        barcode = "4069424010016"
        identity = target_barcode_identity(account="JV", source_sku="old-sku", barcode=barcode)
        self.assertEqual(identity["targetSku"], barcode)
        self.assertEqual(identity["newEan"], barcode)
        self.assertEqual(identity["productReference"], barcode)
        with self.assertRaisesRegex(PreparationError, "13 ASCII digits") as error:
            target_barcode_identity(account="JV", source_sku="old-sku", barcode="123456789012")
        self.assertEqual(error.exception.code, "INVALID_EAN")

    def test_gtin_eligibility_does_not_require_review_or_configured_evidence(self):
        barcode = "4069424010016"
        source_id = str(uuid4())
        pool = {
            "status": "AVAILABLE",
            "eligibility_status": "ELIGIBLE",
            "owner_reference": None,
            "verified_by": None,
            "verified_at": None,
            "reservation_operation_id": None,
            "provenance_source": "MANUAL_IMPORT",
        }
        conn = Mock()
        conn.execute.side_effect = [
            _FakeResult(row=pool),
            _FakeResult(scalar=True),  # production source identity exists in this account
            _FakeResult(scalar=False),  # no retained identifier conflict
            _FakeResult(scalar=False),  # no other approved product binding
        ]
        result = validate_gtin_eligibility(
            conn,
            account="JV",
            ean=barcode,
            source_product_id=source_id,
            approved_evidence_types=(),
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.code, "EAN13_FORMAT_ACCEPTED")
        self.assertEqual(result.evidence_ids, ())

    def test_fulfillment_defaults_match_user_policy_and_copied_account_profiles(self):
        self.assertEqual(DEFAULT_STOCK_QUANTITY, 20)
        self.assertEqual(DEFAULT_PROCESSING_TIME_DAYS, "37")
        self.assertEqual(DEFAULT_SHIPPING_PROFILE_IDS["XL"], "d045a64d-59e6-57fb-9f69-6370dc7c9d40")
        self.assertEqual(DEFAULT_SHIPPING_PROFILE_IDS["JV"], "786c6468-3baf-52e0-88b5-13757eb7f873")
        reference = default_fulfillment_reference()
        sku = "4069424010016"
        for account in ("JV", "XL"):
            target = default_target_fulfillment(account=account, target_sku=sku)
            self.assertEqual(target["stockQuantity"], 20)
            self.assertEqual(target["processingTime"], "37")
            self.assertEqual(target["shippingProfileName"], "EUROPE_4_8_WEEKS")
            self.assertTrue(target["verified"])
            row = {
                "target_sku": sku,
                "fulfillment_snapshot_json": json.dumps({"target": target}),
                "source_snapshot_json": json.dumps({"references": [reference]}),
            }
            payloads = _validated_fulfillment_payloads(row, account=account)
            self.assertEqual(payloads["QUANTITY"], {"sku": sku, "quantity": 20})
            self.assertEqual(payloads["DELIVERY_INFORMATION"]["sku"], sku)
            self.assertEqual(payloads["DELIVERY_INFORMATION"]["processingTime"], "37")
            self.assertEqual(payloads["DELIVERY_INFORMATION"]["shippingProfileId"], target["shippingProfile"])


if __name__ == "__main__":
    unittest.main()

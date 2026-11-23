from __future__ import annotations

import hashlib
import json
import unittest

from services.qc_service import QC_POLICY_VERSION, evaluate_qc


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def valid_snapshot() -> dict:
    source_id = "source-1"
    episode_id = "episode-1"
    operation_id = "operation-1"
    source_sku = "old-sku"
    source_ean = "4069424040821"
    new_ean = "4991234567896"
    target_sku = new_ean
    ref = "OTTO:/v5/products/old-sku"
    source_snapshot = {
        "account": "XL",
        "sourceProductId": source_id,
        "sourceType": "OTTO_DIRECT",
        "sourceSku": source_sku,
        "sourceEan": source_ean,
        "requiredTitleFacts": ["Cotton", "45 cm"],
        "verifiedTitleTerms": ["Brand", "Cotton", "Chair", "45", "cm"],
        "references": [{"system": "OTTO", "method": "GET", "resource": "/v5/products/old-sku"}],
        "categorySchema": {
            "verified": True,
            "source": "OTTO_CATEGORY_SCHEMA",
            "account": "XL",
            "category": "CHAIR",
            "attributes": [
                {"name": "Material", "required": True, "legal": False, "allowedValues": ["Cotton", "Linen"]},
                {"name": "Size", "required": False, "legal": False, "allowedValues": ["45 cm", "50 cm"]},
            ],
        },
    }
    content = {
        "title": "Brand Cotton Chair 45 cm",
        "category": "CHAIR",
        "attributes": [
            {"name": "Material", "value": "Cotton", "sourceRefs": [ref], "origin": "MANUAL"},
            {"name": "Size", "value": "45 cm", "sourceRefs": [ref], "origin": "MANUAL"},
        ],
        "images": [{"url": "https://images.example.test/item.jpg", "sourceRefs": [ref]}],
    }
    from services.preparation_service import price_snapshot_from_sources
    price = price_snapshot_from_sources("XL", {"standardPrice":{"amount":"123.45","currency":"EUR"}}, source_type="OTTO_DIRECT")
    fulfillment = {
        "target": {
            "verified": True,
            "account": "XL",
            "targetSku": target_sku,
            "stockQuantity": 4,
            "shippingProfile": "profile-1",
            "sourceRef": ref,
        }
    }
    payload = {
        "account": "XL",
        "sourceProductId": source_id,
        "sourceSku": source_sku,
        "sourceEan": source_ean,
        "targetSku": target_sku,
        "targetIdentity": {
            "verified": True,
            "account": "XL",
            "sourceSku": source_sku,
            "targetSku": target_sku,
            "newEan": new_ean,
            "productReference": new_ean,
            "sourceRef": ref,
        },
        "newEan": new_ean,
        "productReference": new_ean,
        "priceCents": 10399,
        "title": content["title"],
        "category": content["category"],
        "attributes": content["attributes"],
        "images": content["images"],
        "ottoProductPayload": {
            "productReference": new_ean,
            "sku": target_sku,
            "ean": new_ean,
            "productDescription": {"productLine": content["title"], "brandId":"test-brand", "category":"CHAIR",
                "description":"Brand Cotton Chair 45 cm", "bulletPoints":[],
                "attributes":[{"name":a["name"],"values":[a["value"]],"additional":False} for a in content["attributes"]]},
            "mediaAssets":[{"type":"IMAGE","location":image["url"]} for image in content["images"]],
            "pricing":{"standardPrice":{"amount":price["targetPriceCents"]/100,"currency":"EUR"},"msrp":{"amount":price["uvpPriceCents"]/100,"currency":"EUR"}},
            "logistics":{"packingUnitCount":1},"compliance":__import__("services.compliance_defaults",fromlist=["product_safety_compliance"]).product_safety_compliance("XL"),
        },
    }
    return {
        "operation": {
            "id": operation_id,
            "account": "XL",
            "dataScope": "PRODUCTION",
            "sourceProductId": source_id,
            "candidateEpisodeId": episode_id,
            "status": "NEEDS_REVIEW",
            "submissionState": "NEVER_SUBMITTED",
            "attemptNumber": 1,
            "currentRevisionNumber": 1,
            "targetSku": target_sku,
            "newEan": new_ean,
        },
        "source": {
            "id": source_id,
            "account": "XL",
            "dataScope": "PRODUCTION",
            "originalTitle": "Brand Chair Cotton 45 cm",
            "sourceType": "OTTO_DIRECT",
        },
        "episode": {
            "id": episode_id,
            "account": "XL",
            "dataScope": "PRODUCTION",
            "sourceProductId": source_id,
            "observedSku": source_sku,
            "confirmedEan": source_ean,
            "classification": "EXACT_CODE",
            "episodeState": "OPEN",
        },
        "revision": {
            "id": "revision-1",
            "revisionNumber": 1,
            "sourceSnapshot": source_snapshot,
            "content": content,
            "priceSnapshot": price,
            "fulfillmentSnapshot": fulfillment,
            "payload": payload,
            "payloadHash": canonical_hash(payload),
            "gtinEvidenceIds": ["evidence-1"],
        },
        "gtinEligibility": {"eligible": True, "code": "GTIN_ELIGIBLE", "evidenceIds": ["evidence-1"]},
        "gtinEvidenceManifest": [{"id": "evidence-1", "artifactSha256": "a" * 64}],
        "legacyHistoryExists": False,
        "knownPriorAttempts": 0,
    }


class QCPolicyTests(unittest.TestCase):
    def test_old_xl_discount_is_rejected_after_policy_change(self):
        snapshot = valid_snapshot()
        price = snapshot["revision"]["priceSnapshot"]
        price.update({"discountCents": 1500, "discountedPriceCents": 10845, "targetPriceCents": 10899})
        snapshot["revision"]["payload"]["priceCents"] = 10899
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        self.assertIn("PRICE_POLICY_MISMATCH", {issue.code for issue in decision.issues})

    def test_complete_verified_revision_passes_with_current_hash(self):
        decision = evaluate_qc(valid_snapshot())
        self.assertEqual(decision.outcome, "PASSED_AUTO")
        self.assertEqual(decision.policy_version, QC_POLICY_VERSION)
        self.assertEqual(decision.issues, ())

    def test_category_name_is_sent_and_numeric_reference_id_is_not_accepted(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["sourceSnapshot"]["categorySchema"]["categoryId"] = "25927"
        self.assertEqual(evaluate_qc(snapshot).outcome, "PASSED_AUTO")
        snapshot["revision"]["payload"]["ottoProductPayload"]["productDescription"]["category"] = "25927"
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        self.assertIn("OTTO_PACKAGE_CATEGORY_MISMATCH", {issue.code for issue in evaluate_qc(snapshot).issues})

    def test_prepared_title_must_match_exact_otto_product_line(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["payload"]["ottoProductPayload"]["productDescription"]["productLine"] = "A different target title"
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        self.assertIn("TITLE_PAYLOAD_MISMATCH", {issue.code for issue in decision.issues})

    def test_aftercool_and_otto_prices_must_match_shared_max_discount_and_99_policy(self):
        snapshot = valid_snapshot()
        snapshot["source"]["sourceType"] = "AFTERCOOL"
        snapshot["revision"]["sourceSnapshot"]["sourceType"] = "AFTERCOOL"
        from services.preparation_service import price_snapshot_from_sources
        price = price_snapshot_from_sources(
            "XL", {"sku": "old-sku", "standardPrice": {"amount": "1600.00", "currency": "EUR"}},
            source_type="AFTERCOOL", aftercool_price_cents=180050,
        )
        snapshot["revision"]["priceSnapshot"] = price
        payload = snapshot["revision"]["payload"]
        payload["priceCents"] = price["targetPriceCents"]
        package_pricing = payload["ottoProductPayload"]["pricing"]
        package_pricing["standardPrice"] = {"amount": price["targetPriceCents"] / 100, "currency": "EUR"}
        package_pricing["msrp"] = {"amount": price["uvpPriceCents"] / 100, "currency": "EUR"}
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "PASSED_AUTO")

        snapshot["revision"]["priceSnapshot"]["aftercoolStartPriceCents"] = 170000
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        self.assertIn("PRICE_POLICY_MISMATCH", {issue.code for issue in decision.issues})

    def test_missing_legal_attribute_blocks_even_if_other_content_is_valid(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["content"]["attributes"] = [
            item for item in snapshot["revision"]["content"]["attributes"] if item["name"] != "Material"
        ]
        snapshot["revision"]["payload"]["attributes"] = snapshot["revision"]["content"]["attributes"]
        snapshot["revision"]["sourceSnapshot"]["categorySchema"]["attributes"][0]["legal"] = True
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        self.assertIn("REQUIRED_LEGAL_ATTRIBUTE_MISSING", {issue.code for issue in decision.issues})

    def test_ambiguous_ai_proposals_require_manual_review(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["content"]["attributes"][0]["origin"] = "AI"
        snapshot["revision"]["content"]["aiSuggestion"] = {"manualReviewRequired": True}
        snapshot["revision"]["payload"]["attributes"] = snapshot["revision"]["content"]["attributes"]
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "MANUAL_REQUIRED")
        self.assertIn("AI_ATTRIBUTE_NEEDS_REVIEW", {issue.code for issue in decision.issues})
        self.assertIn("AI_SUGGESTION_UNREVIEWED", {issue.code for issue in decision.issues})

    def test_source_bound_manual_title_review_resolves_unsupported_terms(self):
        snapshot = valid_snapshot()
        title = "Brand Certified Cotton Chair 45 cm"
        reference = "OTTO:/v5/products/old-sku"
        snapshot["revision"]["content"]["title"] = title
        snapshot["revision"]["content"]["manualReview"] = {
            "confirmations": [{
                "field": "title",
                "value": title,
                "sourceRef": reference,
                "reviewedBy": "offline-reviewer",
                "basis": "Reviewed the title claim against the captured source evidence.",
            }]
        }
        snapshot["revision"]["payload"]["title"] = title
        snapshot["revision"]["payload"]["ottoProductPayload"]["productDescription"]["productLine"] = title
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "PASSED_AUTO")

    def test_gtin_pool_conflict_is_a_hard_block(self):
        snapshot = valid_snapshot()
        snapshot["gtinEligibility"] = {"eligible": False, "code": "GTIN_IDENTITY_CONFLICT"}
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        self.assertIn("GTIN_IDENTITY_CONFLICT", {issue.code for issue in decision.issues})

    def test_optional_gtin_evidence_does_not_gate_qc(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["gtinEvidenceIds"] = ["evidence-2"]
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "PASSED_AUTO")
        self.assertNotIn("GTIN_EVIDENCE_VERSION_MISMATCH", {issue.code for issue in decision.issues})

    def test_payload_must_match_the_content_that_qc_checked(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["payload"]["title"] = "Brand Invented Item 45 cm"
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        self.assertIn("PAYLOAD_CONTENT_MISMATCH", {issue.code for issue in decision.issues})

    def test_payload_target_sku_and_source_ean_must_match_confirmed_identity(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["payload"]["targetSku"] = "OTHER-TARGET"
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        snapshot["source"]["aftercoolEan"] = "4069424040822"
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        codes = {issue.code for issue in decision.issues}
        self.assertIn("PAYLOAD_TARGET_SKU_MISMATCH", codes)
        self.assertIn("SOURCE_EAN_MISMATCH", codes)

    def test_preparation_readiness_gaps_cannot_auto_pass(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["payload"]["readinessGaps"] = ["TARGET_SKU_UNVERIFIED"]
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "MANUAL_REQUIRED")
        self.assertIn("PREPARATION_READINESS_GAPS", {issue.code for issue in decision.issues})

    def test_changed_revision_hash_price_and_unapproved_retry_cannot_pass(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["payloadHash"] = "0" * 64
        snapshot["revision"]["priceSnapshot"]["targetPriceCents"] += 1
        snapshot["operation"]["attemptNumber"] = 2
        snapshot["knownPriorAttempts"] = 1
        snapshot["retryAuthorization"] = None
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        codes = {issue.code for issue in decision.issues}
        self.assertIn("REVISION_PAYLOAD_HASH_MISMATCH", codes)
        self.assertIn("PRICE_POLICY_MISMATCH", codes)
        self.assertIn("RESTART_RETRY_NOT_AUTHORIZED", codes)

    def test_missing_schema_or_target_fulfillment_stays_manual(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["sourceSnapshot"].pop("categorySchema")
        snapshot["revision"]["fulfillmentSnapshot"].pop("target")
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "MANUAL_REQUIRED")
        codes = {issue.code for issue in decision.issues}
        self.assertIn("CATEGORY_SCHEMA_UNAVAILABLE", codes)
        self.assertIn("TARGET_FULFILLMENT_UNVERIFIED", codes)

    def test_title_and_attribute_invented_facts_do_not_pass(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["content"]["title"] = "Brand Certified Chair 45 cm"
        snapshot["revision"]["payload"]["title"] = snapshot["revision"]["content"]["title"]
        snapshot["revision"]["content"]["attributes"][0]["value"] = "Polyester"
        snapshot["revision"]["payload"]["attributes"] = snapshot["revision"]["content"]["attributes"]
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        decision = evaluate_qc(snapshot)
        self.assertEqual(decision.outcome, "BLOCKED")
        codes = {issue.code for issue in decision.issues}
        self.assertIn("TITLE_HAS_UNVERIFIED_TERMS", codes)
        self.assertIn("ATTRIBUTE_ENUM_INVALID", codes)

    def test_catalog_image_reference_verifies_hero_image_and_missing_reference_fails(self):
        snapshot = valid_snapshot()
        snapshot["revision"]["content"]["images"] = [
            {"url": "https://cdn.example/hero.jpg", "sourceRefs": ["OttoRestart:catalog-image"]},
            {"url": "https://cdn.example/gallery.jpg", "sourceRefs": ["OTTO:/v5/products/old-sku"]},
        ]
        snapshot["revision"]["payload"]["images"] = snapshot["revision"]["content"]["images"]
        snapshot["revision"]["payload"]["ottoProductPayload"]["mediaAssets"] = [
            {"type": "IMAGE", "location": "https://cdn.example/hero.jpg"},
            {"type": "IMAGE", "location": "https://cdn.example/gallery.jpg"},
        ]
        snapshot["revision"]["payloadHash"] = canonical_hash(snapshot["revision"]["payload"])
        unverified = evaluate_qc(snapshot)
        self.assertEqual(unverified.outcome, "MANUAL_REQUIRED")
        self.assertIn("IMAGE_SOURCE_UNVERIFIED", {issue.code for issue in unverified.issues})

        snapshot["revision"]["sourceSnapshot"]["references"].append({
            "system": "OttoRestart",
            "method": "STORED_SOURCE_PRODUCT",
            "resource": "catalog-image",
        })
        verified = evaluate_qc(snapshot)
        self.assertEqual(verified.outcome, "PASSED_AUTO")


if __name__ == "__main__":
    unittest.main()

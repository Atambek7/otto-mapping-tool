"""Deterministic, fail-closed QC for immutable Restart operation revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Iterable, Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

from services.preparation_service import PreparationError, calculate_price_from_cents
from services.title_policy import PreparedTitleError, normalize_title_whitespace, validate_prepared_title


QC_POLICY_VERSION = "ottorestart-qc-v6-package-schema-availability"
_EDITABLE_OPERATION_STATES = frozenset({"DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"})


class QCError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True)
class QCIssue:
    code: str
    outcome: str


@dataclass(frozen=True)
class QCDecision:
    outcome: str
    policy_version: str
    issues: tuple[QCIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "policyVersion": self.policy_version,
            "issues": [{"code": issue.code, "outcome": issue.outcome} for issue in self.issues],
        }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _money_cents(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not amount.is_finite():
        return None
    cents = amount * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_reference_ids(references: Any) -> frozenset[str]:
    result: set[str] = set()
    for reference in references if isinstance(references, list) else []:
        if not isinstance(reference, dict):
            continue
        system = reference.get("system")
        resource = reference.get("resource")
        if isinstance(system, str) and isinstance(resource, str) and system.strip() and resource.strip():
            result.add(f"{system.strip()}:{resource.strip()}")
    return frozenset(result)


def _matching_manual_confirmation(content: Mapping[str, Any], field: str, value: str, refs: set[str]) -> bool:
    review = content.get("manualReview")
    confirmations = review.get("confirmations") if isinstance(review, dict) else None
    if not isinstance(confirmations, list):
        return False
    for item in confirmations:
        if not isinstance(item, dict):
            continue
        if (
            item.get("field") == field
            and item.get("value") == value
            and item.get("sourceRef") in refs
            and isinstance(item.get("reviewedBy"), str)
            and item.get("reviewedBy", "").strip()
            and isinstance(item.get("basis"), str)
            and len(item.get("basis", "").strip()) >= 20
        ):
            return True
    return False


def evaluate_qc(snapshot: Mapping[str, Any]) -> QCDecision:
    """Evaluate already-loaded evidence. Missing evidence requires review; contradictions block."""
    issues: list[QCIssue] = []

    def block(code: str) -> None:
        issues.append(QCIssue(code, "BLOCKED"))

    def review(code: str) -> None:
        issues.append(QCIssue(code, "MANUAL_REQUIRED"))

    operation = snapshot.get("operation") if isinstance(snapshot.get("operation"), dict) else {}
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    episode = snapshot.get("episode") if isinstance(snapshot.get("episode"), dict) else {}
    revision = snapshot.get("revision") if isinstance(snapshot.get("revision"), dict) else {}
    source_snapshot = _json_object(revision.get("sourceSnapshot"))
    content = _json_object(revision.get("content"))
    price = _json_object(revision.get("priceSnapshot"))
    fulfillment = _json_object(revision.get("fulfillmentSnapshot"))
    payload = _json_object(revision.get("payload"))

    account = operation.get("account")
    operation_id = operation.get("id")
    if account not in {"JV", "XL"} or operation.get("dataScope") != "PRODUCTION":
        block("OPERATION_SCOPE_INVALID")
    if operation.get("status") not in _EDITABLE_OPERATION_STATES:
        block("OPERATION_STATE_NOT_REVIEWABLE")
    if operation.get("submissionState") != "NEVER_SUBMITTED":
        block("OPERATION_ALREADY_SUBMITTED")
    if revision.get("revisionNumber") != operation.get("currentRevisionNumber"):
        block("REVISION_NOT_CURRENT")
    if not isinstance(revision.get("payloadHash"), str) or len(revision.get("payloadHash", "")) != 64:
        block("REVISION_HASH_INVALID")
    else:
        try:
            if _canonical_hash(payload) != revision["payloadHash"].strip():
                block("REVISION_PAYLOAD_HASH_MISMATCH")
        except (TypeError, ValueError):
            block("REVISION_PAYLOAD_HASH_INVALID")

    if source.get("account") != account or source.get("dataScope") != "PRODUCTION":
        block("SOURCE_SCOPE_MISMATCH")
    if source.get("sourceType") != source_snapshot.get("sourceType"):
        block("SOURCE_TYPE_MISMATCH")
    if episode.get("account") != account or episode.get("dataScope") != "PRODUCTION":
        block("EPISODE_SCOPE_MISMATCH")
    if episode.get("classification") != "EXACT_CODE" or episode.get("episodeState") != "OPEN":
        block("RESTRICTION_NOT_CURRENT")
    if (
        not source.get("id")
        or source.get("id") != operation.get("sourceProductId")
        or episode.get("sourceProductId") != source.get("id")
        or episode.get("id") != operation.get("candidateEpisodeId")
    ):
        block("SOURCE_EPISODE_IDENTITY_MISMATCH")

    source_sku = episode.get("observedSku")
    source_ean = episode.get("confirmedEan")
    if (
        not source_sku or not source_ean
        or source_snapshot.get("account") != account
        or source_snapshot.get("sourceProductId") != source.get("id")
        or source_snapshot.get("sourceSku") != source_sku
        or source_snapshot.get("sourceEan") != source_ean
        or payload.get("account") != account
        or payload.get("sourceProductId") != source.get("id")
        or payload.get("sourceSku") != source_sku
        or payload.get("sourceEan") != source_ean
    ):
        block("SOURCE_IDENTITY_MISMATCH")
    if source.get("aftercoolEan") and source.get("aftercoolEan") != source_ean:
        block("SOURCE_EAN_MISMATCH")

    target_sku = operation.get("targetSku")
    new_ean = operation.get("newEan")
    target_identity = payload.get("targetIdentity")
    if payload.get("targetSku") != target_sku:
        block("PAYLOAD_TARGET_SKU_MISMATCH")
    if (
        not isinstance(new_ean, str)
        or not re.fullmatch(r"[0-9]{13}", new_ean)
        or target_sku != new_ean
        or payload.get("productReference") != new_ean
    ):
        block("TARGET_BARCODE_IDENTITY_MISMATCH")
    if not isinstance(target_sku, str) or not target_sku.strip():
        review("TARGET_SKU_UNVERIFIED")
    elif not isinstance(target_identity, dict):
        review("TARGET_SKU_MAPPING_UNVERIFIED")
    elif target_identity.get("verified") is not True:
        review("TARGET_SKU_MAPPING_UNVERIFIED")
    elif (
        target_identity.get("account") != account
        or target_identity.get("sourceSku") != source_sku
        or target_identity.get("targetSku") != target_sku
        or target_identity.get("newEan") != new_ean
        or target_identity.get("productReference") != new_ean
        or not isinstance(target_identity.get("sourceRef"), str)
        or target_identity.get("sourceRef") not in source_reference_ids(source_snapshot.get("references"))
    ):
        block("TARGET_SKU_MAPPING_CONFLICT")
    if not new_ean or payload.get("newEan") != new_ean:
        block("GTIN_ASSIGNMENT_MISMATCH")
    target_package = payload.get("ottoProductPayload")
    if not isinstance(target_package, Mapping):
        block("OTTO_PRODUCT_PACKAGE_MISSING")
        target_package = {}
    if any(target_package.get(field) != new_ean for field in ("productReference", "sku", "ean")):
        block("OTTO_PACKAGE_BARCODE_IDENTITY_MISMATCH")

    if any(content.get(field) != payload.get(field) for field in ("title", "category", "attributes", "images")):
        block("PAYLOAD_CONTENT_MISMATCH")
    readiness_gaps = payload.get("readinessGaps")
    if isinstance(readiness_gaps, list):
        blocking_gaps = {
            "TITLE_MISSING", "CATEGORY_SCHEMA_UNAVAILABLE", "IMAGES_MISSING", "BRAND_ID_MISSING",
            "LOGISTICS_MISSING", "REQUIRED_ATTRIBUTES_MISSING", "ATTRIBUTES_MISSING", "DESCRIPTION_MISSING",
        }
        for gap in readiness_gaps:
            if isinstance(gap, str) and gap in blocking_gaps:
                block(f"PREPARATION_GAP_{gap}")
            else:
                review("PREPARATION_READINESS_GAPS")

    eligibility = snapshot.get("gtinEligibility")
    if not isinstance(eligibility, dict) or eligibility.get("eligible") is not True:
        code = eligibility.get("code") if isinstance(eligibility, dict) else None
        block(code if isinstance(code, str) and code else "GTIN_NOT_ELIGIBLE")
    # Optional evidence remains attached to the revision for audit only. The
    # approved v1 eligibility rule is the 13-digit barcode plus pool/account/
    # ownership invariants, so evidence changes cannot gate QC.

    attempt_number = operation.get("attemptNumber")
    known_prior_attempts = snapshot.get("knownPriorAttempts", 0)
    if not _is_int(attempt_number) or attempt_number < 1:
        block("ATTEMPT_NUMBER_INVALID")
    elif not _is_int(known_prior_attempts) or known_prior_attempts < 0 or attempt_number <= known_prior_attempts:
        block("ATTEMPT_HISTORY_MISMATCH")
    elif attempt_number > 1:
        authorization = snapshot.get("retryAuthorization")
        if not isinstance(authorization, dict) or (
            authorization.get("decision") != "AUTHORIZED"
            or authorization.get("consumedByOperationId") != operation_id
            or authorization.get("expectedAttemptNumber") != attempt_number
            or authorization.get("historyReviewDecision") != "CONFIRMED_PRODUCTION"
        ):
            block("RESTART_RETRY_NOT_AUTHORIZED")
    elif snapshot.get("legacyHistoryExists") is True and not isinstance(snapshot.get("historyReview"), dict):
        block("LEGACY_HISTORY_UNREVIEWED")
    history_review = snapshot.get("historyReview")
    if isinstance(history_review, dict) and (
        history_review.get("decision") == "INSUFFICIENT"
        or history_review.get("consumedByOperationId") != operation_id
        or history_review.get("confirmedPriorAttemptCount", known_prior_attempts) < known_prior_attempts
    ):
        block("HISTORY_REVIEW_INVALID")

    original_price = price.get("originalPriceCents")
    otto_price = price.get("ottoStandardPriceCents")
    aftercool_price = price.get("aftercoolStartPriceCents")
    discount = price.get("discountCents")
    discounted_price = price.get("discountedPriceCents")
    target_price = price.get("targetPriceCents")
    source_type = source_snapshot.get("sourceType")
    uvp_price = price.get("uvpPriceCents")
    required_prices = (original_price, discount, discounted_price, target_price, uvp_price)
    if any(not _is_int(value) for value in required_prices):
        review("PRICE_SNAPSHOT_INCOMPLETE")
    else:
        if source_type == "OTTO_DIRECT" and (not _is_int(otto_price) or otto_price <= 0):
            review("OTTO_PRICE_SNAPSHOT_INCOMPLETE")
            expected = None
        elif source_type in {"AFTERBUY", "AFTERCOOL"} and not (
            (_is_int(otto_price) and otto_price > 0)
            or (_is_int(aftercool_price) and aftercool_price > 0)
        ):
            review("SOURCE_PRICE_SNAPSHOT_INCOMPLETE")
            expected = None
        elif source_type not in {"AFTERBUY", "AFTERCOOL", "OTTO_DIRECT"}:
            review("PRICE_SOURCE_TYPE_UNVERIFIED")
            expected = None
        else:
            try:
                expected = calculate_price_from_cents(
                    account,
                    otto_price_cents=otto_price,
                    source_type=source_type,
                    aftercool_price_cents=aftercool_price if source_type in {"AFTERBUY", "AFTERCOOL"} else None,
                )
            except PreparationError as error:
                if error.code == "SOURCE_TYPE_CONFLICT":
                    block("DIRECT_OTTO_HAS_AFTERCOOL_PRICE")
                elif error.code == "SOURCE_PRICE_UNAVAILABLE":
                    review("SOURCE_PRICE_SNAPSHOT_INCOMPLETE")
                else:
                    block(error.code)
                expected = None

        target_package = payload.get("ottoProductPayload")
        target_pricing = target_package.get("pricing") if isinstance(target_package, Mapping) else None
        target_standard = target_pricing.get("standardPrice") if isinstance(target_pricing, Mapping) else None
        target_msrp = target_pricing.get("msrp") if isinstance(target_pricing, Mapping) else None
        if expected is not None and (
            price.get("source") != expected["source"]
            or price.get("formula") != expected["formula"]
            or price.get("uvpFormula") != expected["uvpFormula"]
            or price.get("currency") != "EUR"
            or discount != expected["discountCents"]
            or original_price != expected["originalPriceCents"]
            or price.get("selectedSource") != expected["selectedSource"]
            or discounted_price != expected["discountedPriceCents"]
            or target_price != expected["targetPriceCents"]
            or price.get("targetFractionCents") != expected["targetFractionCents"]
            or uvp_price != expected["uvpPriceCents"]
            or payload.get("priceCents") != expected["targetPriceCents"]
            or not isinstance(target_standard, Mapping)
            or target_standard.get("currency") != "EUR"
            or _money_cents(target_standard.get("amount")) != expected["targetPriceCents"]
            or not isinstance(target_msrp, Mapping)
            or target_msrp.get("currency") != "EUR"
            or _money_cents(target_msrp.get("amount")) != expected["uvpPriceCents"]
        ):
            block("PRICE_POLICY_MISMATCH")

    references = source_reference_ids(source_snapshot.get("references"))
    source_title = source.get("originalTitle") or source_snapshot.get("originalTitle")
    title = content.get("title", payload.get("title"))
    if not isinstance(title, str) or not title.strip():
        review("TITLE_MISSING")
    else:
        if not isinstance(source_title, str) or not source_title.strip():
            review("SOURCE_TITLE_UNVERIFIED")
        try:
            validate_prepared_title(title)
        except PreparedTitleError as error:
            block(error.code)
        target_package = payload.get("ottoProductPayload")
        target_description = target_package.get("productDescription") if isinstance(target_package, Mapping) else None
        target_line = target_description.get("productLine") if isinstance(target_description, Mapping) else None
        if target_line != normalize_title_whitespace(title):
            block("TITLE_PAYLOAD_MISMATCH")
        if isinstance(source_title, str) and title.strip().casefold() == source_title.strip().casefold():
            block("TITLE_UNCHANGED")
        required_facts = source_snapshot.get("requiredTitleFacts")
        if not isinstance(required_facts, list):
            review("TITLE_FACTS_UNVERIFIED")
        elif any(not isinstance(fact, str) or fact.casefold() not in title.casefold() for fact in required_facts):
            block("TITLE_FACT_NOT_PRESERVED")
        allowed_terms = source_snapshot.get("verifiedTitleTerms")
        if not isinstance(allowed_terms, list):
            review("TITLE_TERMS_UNVERIFIED")
        else:
            tokens = {token.casefold() for token in re.findall(r"[\w]+", title, flags=re.UNICODE)}
            allowed = {token.casefold() for term in allowed_terms if isinstance(term, str) for token in re.findall(r"[\w]+", term, flags=re.UNICODE)}
            if not tokens.issubset(allowed) and not _matching_manual_confirmation(
                content, "title", title.strip(), references
            ):
                review("TITLE_HAS_UNVERIFIED_TERMS")

    target_package = payload.get("ottoProductPayload")
    if not isinstance(target_package, Mapping):
        target_package = {}
    target_description = target_package.get("productDescription")
    if not isinstance(target_description, Mapping):
        target_description = {}
        block("OTTO_PRODUCT_DESCRIPTION_MISSING")
    if not isinstance(target_description.get("brandId"), str) or not target_description.get("brandId", "").strip():
        block("OTTO_BRAND_ID_MISSING")
    if not isinstance(target_description.get("description"), str) or not target_description.get("description", "").strip():
        block("OTTO_DESCRIPTION_MISSING")
    target_bullets = target_description.get("bulletPoints")
    if not isinstance(target_bullets, list) or any(not isinstance(item, str) or not item.strip() for item in target_bullets):
        block("OTTO_BULLET_POINTS_INVALID")
    logistics = target_package.get("logistics")
    if not isinstance(logistics, Mapping) or not logistics:
        block("OTTO_LOGISTICS_MISSING")
    compliance = target_package.get("compliance")
    if not isinstance(compliance, Mapping) or not compliance:
        block("OTTO_COMPLIANCE_MISSING")
    else:
        try:
            from services.compliance_defaults import product_safety_compliance
            if dict(compliance) != product_safety_compliance(str(account)):
                block("OTTO_COMPLIANCE_ACCOUNT_MISMATCH")
        except ValueError:
            block("OTTO_COMPLIANCE_ACCOUNT_MISMATCH")

    category_schema = source_snapshot.get("categorySchema")
    if not isinstance(category_schema, dict) or category_schema.get("verified") is not True:
        review("CATEGORY_SCHEMA_UNAVAILABLE")
    elif not isinstance(content.get("category"), str) or not content.get("category", "").strip():
        review("CATEGORY_MISSING")
    elif (
        category_schema.get("source") != "OTTO_CATEGORY_SCHEMA"
        or category_schema.get("account") != account
        or category_schema.get("category") != content.get("category")
        or not isinstance(category_schema.get("attributes"), list)
    ):
        block("CATEGORY_SCHEMA_SCOPE_MISMATCH")
    else:
        raw_schema_attributes = category_schema["attributes"]
        schema_attributes = {
            item.get("name"): item for item in raw_schema_attributes
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        if len(schema_attributes) != len(raw_schema_attributes):
            block("CATEGORY_SCHEMA_INVALID")
        content_attributes = content.get("attributes")
        if not isinstance(content_attributes, list):
            review("ATTRIBUTES_MISSING")
            content_attributes = []
        found: dict[str, dict[str, Any]] = {}
        for attribute in content_attributes:
            if not isinstance(attribute, dict):
                block("ATTRIBUTE_FORMAT_INVALID")
                continue
            name, value = attribute.get("name"), attribute.get("value")
            schema_attribute = schema_attributes.get(name)
            if not isinstance(name, str) or not isinstance(value, str) or not schema_attribute:
                block("ATTRIBUTE_NOT_IN_CATEGORY_SCHEMA")
                continue
            if name in found:
                block("ATTRIBUTE_DUPLICATE")
                continue
            found[name] = attribute
            allowed_values = schema_attribute.get("allowedValues")
            if allowed_values is not None:
                if not isinstance(allowed_values, list):
                    block("ATTRIBUTE_ENUM_INVALID")
                elif len(allowed_values) > 0:
                    if (schema_attribute.get("multiValue") or ";" in value) and ";" in value:
                        parts = [p.strip() for p in value.split(";") if p.strip()]
                        if not parts or any(p not in allowed_values for p in parts):
                            block("ATTRIBUTE_ENUM_INVALID")
                    elif value not in allowed_values:
                        block("ATTRIBUTE_ENUM_INVALID")
            refs = attribute.get("sourceRefs")
            if not isinstance(refs, list) or not refs or any(ref not in references for ref in refs):
                review("ATTRIBUTE_SOURCE_UNVERIFIED")
            if attribute.get("origin") == "AI" and not _matching_manual_confirmation(content, f"attribute:{name}", value, references):
                review("AI_ATTRIBUTE_NEEDS_REVIEW")
        for name, schema_attribute in schema_attributes.items():
            if schema_attribute.get("required") is True or schema_attribute.get("legal") is True:
                if name not in found:
                    block("REQUIRED_LEGAL_ATTRIBUTE_MISSING")

        expected_package_attributes = [
            {"name": item["name"],
             "values": [part.strip() for part in str(item["value"]).split(";") if part.strip()],
             "additional": False}
            for item in content_attributes
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            and item["name"].strip().casefold() not in {"marke", "brandid", "brand id"}
        ]
        if target_description.get("category") != (category_schema.get("categoryId") or content.get("category")):
            block("OTTO_PACKAGE_CATEGORY_MISMATCH")
        if target_description.get("attributes") != expected_package_attributes:
            block("OTTO_PACKAGE_ATTRIBUTES_MISMATCH")

        expected_media = [
            {"type": "IMAGE", "location": item.get("url")}
            for item in content.get("images", []) if isinstance(item, dict)
        ] if isinstance(content.get("images"), list) else []
        if target_package.get("mediaAssets") != expected_media:
            block("OTTO_PACKAGE_MEDIA_MISMATCH")

    images = content.get("images")
    if not isinstance(images, list) or not images:
        review("IMAGES_MISSING")
    else:
        for image in images:
            if not isinstance(image, dict) or not isinstance(image.get("url"), str):
                block("IMAGE_FORMAT_INVALID")
                continue
            parsed = urlsplit(image["url"])
            if parsed.scheme != "https" or not parsed.hostname:
                block("IMAGE_URL_INVALID")
                continue
            image_refs = image.get("sourceRefs")
            if not isinstance(image_refs, list) or not image_refs or any(ref not in references for ref in image_refs):
                review("IMAGE_SOURCE_UNVERIFIED")

    target_fulfillment = fulfillment.get("target")
    if not isinstance(target_fulfillment, dict) or target_fulfillment.get("verified") is not True:
        review("TARGET_FULFILLMENT_UNVERIFIED")
    elif (
        target_fulfillment.get("account") != account
        or target_fulfillment.get("targetSku") != target_sku
        or not _is_int(target_fulfillment.get("stockQuantity"))
        or target_fulfillment.get("stockQuantity") < 0
        or not isinstance(target_fulfillment.get("shippingProfile"), str)
        or not target_fulfillment.get("shippingProfile", "").strip()
    ):
        block("TARGET_FULFILLMENT_INVALID")
    elif not isinstance(target_fulfillment.get("sourceRef"), str) or target_fulfillment.get("sourceRef") not in references:
        review("TARGET_FULFILLMENT_SOURCE_UNVERIFIED")

    ai_review = content.get("aiSuggestion")
    if isinstance(ai_review, dict) and ai_review.get("manualReviewRequired") is True:
        if not isinstance(title, str) or not _matching_manual_confirmation(content, "title", title.strip(), set(references)):
            review("AI_SUGGESTION_UNREVIEWED")

    if any(issue.outcome == "BLOCKED" for issue in issues):
        outcome = "BLOCKED"
    elif any(issue.outcome == "MANUAL_REQUIRED" for issue in issues):
        outcome = "MANUAL_REQUIRED"
    else:
        outcome = "PASSED_AUTO"
    return QCDecision(outcome, QC_POLICY_VERSION, tuple(issues))


def _fetch_json(row: Mapping[str, Any], key: str) -> Any:
    value = row[key]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _authorization_snapshot(conn: Connection, operation: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    from sqlalchemy import text

    account, operation_id = operation["account"], str(operation["id"])
    history = None
    retry = None
    if operation.get("history_review_id"):
        row = conn.execute(
            text("SELECT decision, confirmed_prior_attempt_count, consumed_operation_id FROM restart_authorizations "
                 "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' AND authorization_type='HISTORY_REVIEW'"),
            {"id": operation["history_review_id"], "account": account},
        ).mappings().first()
        if row:
            history = {
                "decision": row["decision"],
                "confirmedPriorAttemptCount": int(row["confirmed_prior_attempt_count"]),
                "consumedByOperationId": str(row["consumed_operation_id"]) if row["consumed_operation_id"] else None,
            }
    if operation.get("repeat_authorization_id"):
        row = conn.execute(
            text("SELECT decision, expected_attempt_number, consumed_operation_id FROM restart_authorizations "
                 "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' AND authorization_type='RESTART_RETRY'"),
            {"id": operation["repeat_authorization_id"], "account": account},
        ).mappings().first()
        if row:
            retry = {
                "decision": row["decision"],
                "expectedAttemptNumber": row["expected_attempt_number"],
                "consumedByOperationId": str(row["consumed_operation_id"]) if row["consumed_operation_id"] else None,
                "historyReviewDecision": history["decision"] if history else None,
                "confirmedPriorAttemptCount": history["confirmedPriorAttemptCount"] if history else None,
            }
    return history, retry


def record_operation_qc(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    actor: str,
    approved_evidence_types: Iterable[str],
) -> dict[str, Any]:
    """Record QC for the current revision and make READY reachable only after PASSED_AUTO."""
    from sqlalchemy import text

    from services.gtin_service import GtinEvidenceError, validate_gtin_eligibility
    from services.operation_service import _legacy_history_exists

    if account not in {"JV", "XL"}:
        raise QCError("INVALID_ACCOUNT", "Account must be JV or XL.")
    try:
        operation_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise QCError("INVALID_OPERATION_ID", "Operation ID must be a UUID.") from None
    row = conn.execute(
        text(
            "SELECT o.*, sp.original_title, sp.source_type AS source_type, sp.aftercool_ean AS source_aftercool_ean, "
            "ce.id AS episode_id, ce.observed_sku, ce.confirmed_ean, ce.classification, ce.episode_state "
            "FROM restart_operations o JOIN source_products sp ON sp.id=o.source_product_id "
            "AND sp.account=o.account AND sp.data_scope=o.data_scope "
            "JOIN candidate_episodes ce ON ce.id=o.candidate_episode_id AND ce.account=o.account "
            "AND ce.data_scope=o.data_scope AND ce.source_product_id=o.source_product_id "
            "WHERE o.id=:operation_id AND o.account=:account AND o.data_scope='PRODUCTION' "
            "FOR UPDATE OF o, sp, ce"
        ),
        {"operation_id": operation_id, "account": account},
    ).mappings().first()
    if row is None:
        raise QCError("OPERATION_NOT_FOUND", "Production operation was not found.")
    operation = dict(row)
    if int(operation["version"]) != expected_version:
        raise QCError("VERSION_CONFLICT", "Operation changed since it was read.", {"currentVersion": int(operation["version"])})
    if operation["status"] not in _EDITABLE_OPERATION_STATES or operation["submission_state"] != "NEVER_SUBMITTED":
        raise QCError("OPERATION_NOT_REVIEWABLE", "This operation can no longer be reviewed by QC.")
    revision_row = conn.execute(
        text("SELECT * FROM operation_revisions WHERE operation_id=:id AND account=:account "
             "AND data_scope='PRODUCTION' AND revision_number=:revision FOR UPDATE"),
        {"id": operation_id, "account": account, "revision": operation["current_revision_number"]},
    ).mappings().first()
    if revision_row is None:
        raise QCError("QC_REVISION_REQUIRED", "QC requires a current immutable operation revision.")
    revision = dict(revision_row)
    source_snapshot = _fetch_json(revision, "source_snapshot_json")
    content = _fetch_json(revision, "content_json")
    price_snapshot = _fetch_json(revision, "price_snapshot_json")
    fulfillment_snapshot = _fetch_json(revision, "fulfillment_snapshot_json")
    payload = _fetch_json(revision, "payload_json")
    saved_edit = conn.execute(text(
        "SELECT title,category,attributes_json FROM restart_product_edits WHERE account=:account "
        "AND data_scope='PRODUCTION' AND candidate_episode_id=:candidate_id"
    ), {"account": account, "candidate_id": operation["candidate_episode_id"]}).mappings().first()
    if saved_edit:
        from services.brand_guard import is_forbidden_warranty_attribute
        expected_attributes = {
            name: value for name, value in _json_object(saved_edit["attributes_json"]).items()
            if str(value or "").strip() and not is_forbidden_warranty_attribute(str(name))
        }
        revision_attributes = payload.get("attributes") if isinstance(payload, dict) else None
        revision_attributes = {
            item.get("name"): item.get("value") for item in revision_attributes
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        } if isinstance(revision_attributes, list) else {}
        if (
            not isinstance(payload, dict)
            or payload.get("title") != saved_edit["title"]
            or payload.get("category") != saved_edit["category"]
            or any(str(value or "").strip() != str(revision_attributes.get(name) or "").strip()
                   for name, value in expected_attributes.items())
        ):
            raise QCError("PRODUCT_EDITS_NOT_APPLIED", "The current revision does not contain the latest saved ProductDrawer edits; prepare a new revision before QC.")
    evidence_ids = _json_list(_fetch_json(revision, "gtin_evidence_ids_json"))
    evidence_manifest: list[dict[str, Any]] = []
    if evidence_ids:
        evidence_rows = conn.execute(
            text("SELECT id, artifact_sha256, validity_kind, expires_at, review_status "
                 "FROM gtin_evidence WHERE account=:account AND data_scope='PRODUCTION' AND id::text=ANY(CAST(:ids AS text[]))"),
            {"account": account, "ids": [str(item) for item in evidence_ids]},
        ).mappings().all()
        evidence_manifest = [
            {
                "id": str(item["id"]), "artifactSha256": item["artifact_sha256"].strip(),
                "validityKind": item["validity_kind"],
                "expiresAt": item["expires_at"].isoformat() if item["expires_at"] else None,
                "reviewStatus": item["review_status"],
            }
            for item in evidence_rows
        ]
    try:
        eligibility = validate_gtin_eligibility(
            conn,
            account=account,
            ean=str(operation["new_ean"] or ""),
            source_product_id=str(operation["source_product_id"]),
            approved_evidence_types=approved_evidence_types,
            operation_id=operation_id,
        ) if operation["new_ean"] else None
    except GtinEvidenceError as error:
        eligibility = None
        eligibility_error = error.code
    else:
        eligibility_error = None
    gtin_eligibility = (
        {"eligible": eligibility.eligible, "code": eligibility.code, "evidenceIds": list(eligibility.evidence_ids)}
        if eligibility is not None else {"eligible": False, "code": eligibility_error or "GTIN_NOT_ELIGIBLE", "evidenceIds": []}
    )
    source_row = {
        "id": str(operation["source_product_id"]), "account": operation["account"],
        "dataScope": operation["data_scope"], "originalTitle": operation["original_title"],
        "aftercoolEan": operation["source_aftercool_ean"], "sourceType": operation["source_type"],
    }
    episode = {
        "id": str(operation["episode_id"]), "account": operation["account"], "dataScope": "PRODUCTION",
        "sourceProductId": str(operation["source_product_id"]), "observedSku": operation["observed_sku"],
        "confirmedEan": operation["confirmed_ean"], "classification": operation["classification"],
        "episodeState": operation["episode_state"],
    }
    source_snapshot_value = _json_object(source_snapshot)
    legacy_exists = _legacy_history_exists(
        conn,
        account=account,
        source={"source_product_id": operation["source_product_id"], "aftercool_ean": operation["source_aftercool_ean"]},
    )
    history_review, retry_authorization = _authorization_snapshot(conn, operation)
    known_prior_attempts = int(conn.execute(
        text("SELECT COUNT(*) FROM restart_operations WHERE account=:account AND data_scope='PRODUCTION' "
             "AND source_product_id=:source_id AND attempt_number < :attempt"),
        {"account": account, "source_id": operation["source_product_id"], "attempt": operation["attempt_number"]},
    ).scalar_one())
    snapshot = {
        "operation": {
            "id": operation_id, "account": account, "dataScope": operation["data_scope"],
            "sourceProductId": str(operation["source_product_id"]), "candidateEpisodeId": str(operation["candidate_episode_id"]),
            "status": operation["status"], "submissionState": operation["submission_state"],
            "attemptNumber": operation["attempt_number"], "currentRevisionNumber": operation["current_revision_number"],
            "targetSku": operation["target_sku"], "newEan": operation["new_ean"],
        },
        "source": source_row,
        "episode": episode,
        "revision": {
            "id": str(revision["id"]), "revisionNumber": revision["revision_number"],
            "sourceSnapshot": source_snapshot, "content": content, "priceSnapshot": price_snapshot,
            "fulfillmentSnapshot": fulfillment_snapshot, "payload": payload,
            "payloadHash": revision["payload_hash"].strip(), "gtinEvidenceIds": evidence_ids,
        },
        "gtinEligibility": gtin_eligibility,
        "gtinEvidenceManifest": evidence_manifest,
        "legacyHistoryExists": legacy_exists,
        "knownPriorAttempts": known_prior_attempts,
        "historyReview": history_review,
        "retryAuthorization": retry_authorization,
    }
    decision = evaluate_qc(snapshot)
    result_json = {
        **decision.as_dict(),
        "revisionId": str(revision["id"]),
        "revisionNumber": int(revision["revision_number"]),
        "payloadHash": revision["payload_hash"].strip(),
        "sourceSnapshotHash": _canonical_hash(source_snapshot_value),
        "priceSnapshotHash": _canonical_hash(_json_object(price_snapshot)),
        "gtinEvidenceManifest": evidence_manifest,
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
    }
    qc_id = str(uuid4())
    conn.execute(
        text("INSERT INTO qc_results (id, operation_id, revision_id, account, data_scope, outcome, policy_version, "
             "payload_hash, result_json, created_by) VALUES (:id, :operation_id, :revision_id, :account, 'PRODUCTION', "
             ":outcome, :policy, :payload_hash, CAST(:result AS jsonb), :actor)"),
        {
            "id": qc_id, "operation_id": operation_id, "revision_id": revision["id"], "account": account,
            "outcome": decision.outcome, "policy": decision.policy_version,
            "payload_hash": revision["payload_hash"].strip(),
            "result": json.dumps(result_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            "actor": actor[:200],
        },
    )
    next_status = "READY" if decision.outcome == "PASSED_AUTO" else "NEEDS_REVIEW"
    conn.execute(
        text("UPDATE restart_operations SET status=:status, version=version+1, updated_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account AND version=:expected"),
        {"status": next_status, "id": operation_id, "account": account, "expected": expected_version},
    )
    event_id = str(uuid4())
    conn.execute(
        text("INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json, created_at) "
             "VALUES (:id, :account, 'PRODUCTION', 'RESTART_QC_COMPLETED', 'RESTART_OPERATION', :operation_id, "
             ":summary, CAST(:metadata AS jsonb), CURRENT_TIMESTAMP)"),
        {
            "id": event_id, "account": account, "operation_id": operation_id,
            "summary": f"Deterministic QC recorded: {decision.outcome}",
            "metadata": json.dumps({"actor": actor, "qcResultId": qc_id, "revisionId": str(revision["id"]),
                                    "policyVersion": decision.policy_version, "issueCodes": [issue.code for issue in decision.issues]},
                                   sort_keys=True, separators=(",", ":")),
        },
    )
    return {
        "id": qc_id, "operationId": operation_id, "account": account,
        "revisionId": str(revision["id"]), "revisionNumber": int(revision["revision_number"]),
        "payloadHash": revision["payload_hash"].strip(), "outcome": decision.outcome,
        "status": next_status, "version": expected_version + 1, "policyVersion": decision.policy_version,
        "issues": [{"code": issue.code, "outcome": issue.outcome} for issue in decision.issues],
    }


def append_manual_revision(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    title: str,
    category: str,
    attributes: list[dict[str, Any]],
    images: list[dict[str, Any]],
    confirmations: list[dict[str, str]],
    reason: str,
    actor: str,
) -> dict[str, Any]:
    """Append reviewed content while preserving immutable identity, price, fulfillment, and GTIN evidence."""
    from sqlalchemy import text

    from services.operation_service import OperationError, append_operation_revision

    try:
        operation_uuid = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise QCError("INVALID_OPERATION_ID", "Operation ID must be a UUID.") from None
    if account not in {"JV", "XL"} or not actor.strip():
        raise QCError("INVALID_REVIEW_ACTOR", "An account-scoped authenticated operator is required.")
    if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 2000:
        raise QCError("INVALID_REVIEW_REASON", "Manual review reason must contain 20–2000 characters.")
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise QCError("INVALID_TITLE", "Manual title must contain 1–500 characters.")
    if not isinstance(category, str) or not category.strip() or len(category) > 200:
        raise QCError("INVALID_CATEGORY", "Manual category must contain 1–200 characters.")
    if not isinstance(attributes, list) or len(attributes) > 200 or not isinstance(images, list) or len(images) > 100:
        raise QCError("MANUAL_CONTENT_INVALID", "Manual attributes or images exceed the supported limits.")
    if not isinstance(confirmations, list) or not confirmations:
        raise QCError("MANUAL_CONFIRMATION_REQUIRED", "At least one fact confirmation with a source reference is required.")

    operation = conn.execute(
        text("SELECT id, account, status, version, current_revision_number, submission_state "
             "FROM restart_operations WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": operation_uuid, "account": account},
    ).mappings().first()
    if operation is None:
        raise QCError("OPERATION_NOT_FOUND", "Production operation was not found.")
    if int(operation["version"]) != expected_version:
        raise QCError("VERSION_CONFLICT", "Operation changed since it was read.", {"currentVersion": int(operation["version"])})
    if operation["status"] not in {"DRAFT", "PREPARING", "NEEDS_REVIEW", "READY"} or operation["submission_state"] != "NEVER_SUBMITTED":
        raise QCError("OPERATION_NOT_EDITABLE", "Submitted or terminal operations cannot receive manual content edits.")
    old_revision = conn.execute(
        text("SELECT * FROM operation_revisions WHERE operation_id=:id AND account=:account AND data_scope='PRODUCTION' "
             "AND revision_number=:revision FOR UPDATE"),
        {"id": operation_uuid, "account": account, "revision": operation["current_revision_number"]},
    ).mappings().first()
    if old_revision is None:
        raise QCError("QC_REVISION_REQUIRED", "Manual review requires an existing immutable revision.")
    source_snapshot = _json_object(_fetch_json(old_revision, "source_snapshot_json"))
    old_content = _json_object(_fetch_json(old_revision, "content_json"))
    old_payload = _json_object(_fetch_json(old_revision, "payload_json"))
    allowed_refs = source_reference_ids(source_snapshot.get("references"))
    normalized_confirmations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in confirmations:
        if not isinstance(item, dict):
            raise QCError("MANUAL_CONFIRMATION_INVALID", "Each manual fact confirmation must be an object.")
        field, value, source_ref, basis = (item.get("field"), item.get("value"), item.get("sourceRef"), item.get("basis"))
        if (
            not isinstance(field, str) or not field.strip() or len(field) > 200
            or not isinstance(value, str) or not value.strip() or len(value) > 2000
            or not isinstance(source_ref, str) or source_ref not in allowed_refs
            or not isinstance(basis, str) or not 20 <= len(basis.strip()) <= 2000
        ):
            raise QCError("MANUAL_CONFIRMATION_INVALID", "A confirmation must bind a field and value to a source reference and review basis.")
        key = (field.strip(), value.strip(), source_ref)
        if key in seen:
            raise QCError("MANUAL_CONFIRMATION_DUPLICATE", "Duplicate manual fact confirmation.")
        seen.add(key)
        normalized_confirmations.append({
            "field": key[0], "value": key[1], "sourceRef": key[2], "basis": basis.strip(),
            "reviewedBy": actor[:200],
        })
    def is_confirmed(field: str, value: str, source_refs: set[str] | None = None) -> bool:
        return any(
            item["field"] == field and item["value"] == value
            and (source_refs is None or item["sourceRef"] in source_refs)
            for item in normalized_confirmations
        )

    if not is_confirmed("title", title.strip()) or not is_confirmed("category", category.strip()):
        raise QCError("MANUAL_CONFIRMATION_INCOMPLETE", "Title and category changes require explicit source-bound confirmations.")
    for attribute in attributes:
        if not isinstance(attribute, dict):
            raise QCError("MANUAL_CONTENT_INVALID", "Each manual attribute must be an object.")
        name, value, refs = attribute.get("name"), attribute.get("value"), attribute.get("sourceRefs")
        if not isinstance(name, str) or not isinstance(value, str) or not isinstance(refs, list):
            raise QCError("MANUAL_CONTENT_INVALID", "Manual attributes require name, value, and source references.")
        if any(ref not in allowed_refs for ref in refs) or not refs or not is_confirmed(f"attribute:{name}", value, set(refs)):
            raise QCError("MANUAL_CONFIRMATION_INCOMPLETE", "Each manual attribute requires a matching source-bound confirmation.")
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("url"), str):
            raise QCError("MANUAL_CONTENT_INVALID", "Each manual image requires a URL and source references.")
        image_refs = image.get("sourceRefs")
        if not isinstance(image_refs, list) or not image_refs or any(ref not in allowed_refs for ref in image_refs):
            raise QCError("MANUAL_CONTENT_INVALID", "Manual images must cite references in the immutable source snapshot.")
    reviewed_at = datetime.now(timezone.utc).isoformat()
    content = {
        **old_content,
        "title": title.strip(),
        "category": category.strip(),
        "attributes": attributes,
        "images": images,
        "manualReview": {
            "reviewedBy": actor[:200], "reviewedAt": reviewed_at, "reason": reason.strip(),
            "confirmations": normalized_confirmations,
        },
    }
    # Only editable listing content is overlaid. Account, source identity, price, GTIN,
    # target SKU, fulfillment, and source evidence remain copied from the prior revision.
    payload = {**old_payload, "title": title.strip(), "category": category.strip(), "attributes": attributes, "images": images}
    try:
        result = append_operation_revision(
            conn,
            account=account,
            operation_id=operation_uuid,
            expected_version=expected_version,
            source_snapshot=source_snapshot,
            content=content,
            price_snapshot=_json_object(_fetch_json(old_revision, "price_snapshot_json")),
            fulfillment_snapshot=_json_object(_fetch_json(old_revision, "fulfillment_snapshot_json")),
            gtin_evidence_ids=_json_list(_fetch_json(old_revision, "gtin_evidence_ids_json")),
            payload=payload,
            actor=actor,
        )
    except OperationError as error:
        raise QCError(error.code, str(error), error.details) from None
    conn.execute(
        text("UPDATE restart_operations SET status='NEEDS_REVIEW', updated_at=CURRENT_TIMESTAMP "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION' AND version=:version "
             "AND submission_state='NEVER_SUBMITTED'"),
        {"id": operation_uuid, "account": account, "version": expected_version + 1},
    )
    conn.execute(
        text("INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json, created_at) "
             "VALUES (:id, :account, 'PRODUCTION', 'RESTART_MANUAL_REVIEW_RECORDED', 'RESTART_OPERATION', :operation_id, "
             "'Manual content review created a new immutable revision', CAST(:metadata AS jsonb), CURRENT_TIMESTAMP)"),
        {
            "id": str(uuid4()), "account": account, "operation_id": operation_uuid,
            "metadata": json.dumps({"actor": actor, "revisionId": result["id"],
                                    "confirmationCount": len(normalized_confirmations),
                                    "reason": reason.strip()}, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        },
    )
    return {
        **result,
        "status": "NEEDS_REVIEW",
        "version": expected_version + 1,
        "manualReview": {"reviewedBy": actor[:200], "reviewedAt": reviewed_at, "confirmationCount": len(normalized_confirmations)},
    }

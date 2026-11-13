"""Verified, account-scoped source synchronization for selected catalog products."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.import_pipeline import _otto_product_snapshot
from services.otto_price_evidence import normalize_otto_product_variation, resolve_otto_standard_price
from services.operation_service import OperationError, append_operation_revision
from services.preparation_service import PreparationError, price_snapshot_from_sources


class ProductSyncError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


def _uuid(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ProductSyncError("INVALID_PRODUCT_ID", "Product ID must be a candidate episode UUID.") from None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


_SYNC_CONTEXT_SQL = """
    SELECT ce.id::text AS candidate_episode_id, ce.account, ce.data_scope,
           ce.observed_sku, ce.confirmed_ean, ce.classification, ce.episode_state,
           ce.period, ce.last_modified,
           sp.id::text AS source_product_id, sp.source_type, sp.aftercool_product_id,
           sp.aftercool_sku, sp.aftercool_ean, sp.factory_id, sp.factory_name,
           sp.original_title, sp.category AS stored_category, sp.image_url,
           sp.original_price_cents, sp.attributes_json, sp.updated_at AS source_updated_at,
           op.id::text AS operation_id, op.status AS operation_status,
           op.submission_state, op.version AS operation_version,
           op.current_revision_number,
           rev.source_snapshot_json AS revision_source_snapshot,
           rev.content_json AS revision_content,
           rev.price_snapshot_json AS revision_price_snapshot,
           rev.fulfillment_snapshot_json AS revision_fulfillment_snapshot,
           rev.gtin_evidence_ids_json AS revision_gtin_evidence_ids,
           rev.payload_json AS revision_payload
    FROM candidate_episodes ce
    JOIN source_products sp ON sp.id=ce.source_product_id
      AND sp.account=ce.account AND sp.data_scope=ce.data_scope
    LEFT JOIN LATERAL (
      SELECT o.* FROM restart_operations o
      WHERE o.account=ce.account AND o.data_scope='PRODUCTION'
        AND o.candidate_episode_id=ce.id
      ORDER BY o.created_at DESC, o.id DESC LIMIT 1
    ) op ON TRUE
    LEFT JOIN operation_revisions rev ON rev.operation_id=op.id
      AND rev.account=op.account AND rev.data_scope='PRODUCTION'
      AND rev.revision_number=op.current_revision_number
    WHERE ce.id=:candidate_id AND ce.account=:account
      AND ce.data_scope='PRODUCTION' AND sp.data_scope='PRODUCTION'
"""


def load_product_sync_context(
    conn: Connection,
    *,
    account: str,
    candidate_episode_id: str,
    lock: bool = False,
) -> dict[str, Any]:
    if account not in {"JV", "XL"}:
        raise ProductSyncError("INVALID_ACCOUNT", "Account must be JV or XL.")
    candidate_id = _uuid(candidate_episode_id)
    suffix = " FOR UPDATE OF ce, sp" if lock else ""
    row = conn.execute(
        text(_SYNC_CONTEXT_SQL + suffix),
        {"candidate_id": candidate_id, "account": account},
    ).mappings().first()
    if row is None:
        raise ProductSyncError("PRODUCT_NOT_FOUND", "Production candidate was not found in this account.")
    context = dict(row)
    if context["episode_state"] != "OPEN" or context["classification"] != "EXACT_CODE":
        raise ProductSyncError("CANDIDATE_UNVERIFIED", "Only an open, exactly classified production candidate can be synchronized.")
    if not context["observed_sku"] or not context["confirmed_ean"]:
        raise ProductSyncError("SOURCE_IDENTITY_UNVERIFIED", "The source SKU and confirmed EAN are required.")
    if context["source_type"] not in {"AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"}:
        raise ProductSyncError("SOURCE_TYPE_UNVERIFIED", "The source routing is not verified.")
    if context["aftercool_ean"] and context["aftercool_ean"] != context["confirmed_ean"]:
        raise ProductSyncError("SOURCE_EAN_MISMATCH", "The stored source EAN does not match the OTTO candidate.")
    if context["source_type"] in {"AFTERCOOL", "AFTERBUY"} and (
        not context["aftercool_product_id"] or not context["factory_id"]
    ):
        raise ProductSyncError("AFTERCOOL_IDENTITY_UNVERIFIED", "A confirmed Aftercool product and factory are required.")
    if context.get("operation_id"):
        if context.get("submission_state") != "NEVER_SUBMITTED" or context.get("operation_status") not in {
            "DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"
        }:
            raise ProductSyncError("OPERATION_NOT_EDITABLE", "A queued, submitted, or non-draft operation cannot be synchronized.")
    return context


def _category_value(product: Mapping[str, Any], normalized: Mapping[str, Any]) -> str:
    category = normalized.get("category")
    if isinstance(category, str) and category.strip():
        return category.strip()
    for container in (product, product.get("productDescription")):
        if not isinstance(container, Mapping):
            continue
        for key in ("categoryName", "categoryId", "category"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Mapping):
                for name_key in ("name", "categoryName", "categoryId", "id"):
                    nested = value.get(name_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
    raise ProductSyncError("OTTO_CATEGORY_UNVERIFIED", "OTTO did not return a usable product category.")


def _revision_attributes(attributes: list[dict[str, Any]], *, source_reference: str) -> list[dict[str, Any]]:
    """Convert confirmed OTTO values to the revision DTO while retaining raw values separately."""
    result: list[dict[str, Any]] = []
    for attribute in attributes:
        name = attribute.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_value = attribute.get("value")
        if raw_value is None:
            raw_value = attribute.get("values")
        if isinstance(raw_value, list):
            values: list[str] = []
            for item in raw_value:
                if isinstance(item, Mapping):
                    item = item.get("value", item.get("name"))
                if item is not None and not isinstance(item, (dict, list)):
                    rendered = str(item).strip()
                    if rendered:
                        values.append(rendered)
            rendered_value = "; ".join(values)
        elif isinstance(raw_value, Mapping):
            nested = raw_value.get("value", raw_value.get("name"))
            rendered_value = str(nested).strip() if nested is not None else ""
        elif raw_value is None or isinstance(raw_value, (dict, list)):
            rendered_value = ""
        else:
            rendered_value = str(raw_value).strip()
        if not rendered_value:
            continue
        result.append({
            **attribute,
            "name": name.strip(),
            "value": rendered_value,
            "sourceRefs": [source_reference],
            "origin": "OTTO",
        })
    return result


def build_product_sync_snapshot(
    context: Mapping[str, Any],
    *,
    account: str,
    otto_product: Mapping[str, Any],
    otto_prices: Mapping[str, Any] | None,
    aftercool: Mapping[str, Any] | None,
    captured_at: datetime,
) -> dict[str, Any]:
    """Validate source identity and create the full immutable pricing/content snapshot."""
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise ProductSyncError("INVALID_CAPTURE_TIME", "Source capture time must include a timezone.")
    if account not in {"JV", "XL"} or context.get("account") != account:
        raise ProductSyncError("ACCOUNT_MISMATCH", "The candidate context must belong to the selected account.")
    if context.get("data_scope") != "PRODUCTION":
        raise ProductSyncError("SOURCE_SCOPE_UNVERIFIED", "Only a production candidate can be synchronized.")
    expected_sku = str(context.get("observed_sku") or "")
    expected_ean = str(context.get("confirmed_ean") or "")
    normalized_product = normalize_otto_product_variation(
        otto_product, expected_sku=expected_sku, expected_ean=expected_ean,
    )
    if normalized_product is None:
        raise ProductSyncError("OTTO_SKU_MISMATCH", "OTTO product did not confirm the observed source SKU.")
    response_ean = normalized_product.get("ean")
    if response_ean and str(response_ean) != expected_ean:
        raise ProductSyncError("OTTO_EAN_MISMATCH", "OTTO returned a different source EAN for the confirmed SKU.")
    resolved_price = resolve_otto_standard_price(
        normalized_product, otto_prices, expected_sku=expected_sku, expected_ean=expected_ean,
    )
    normalized_prices = resolved_price.get("response")
    if not isinstance(normalized_prices, Mapping):
        # Preserve the confirmed SKU identity even when OTTO has no usable
        # standard price. A matching Aftercool price can still be used alone.
        normalized_prices = {"sku": expected_sku}

    source_type = str(context.get("source_type"))
    if source_type == "OTTO_DIRECT":
        if aftercool is not None:
            raise ProductSyncError("SOURCE_TYPE_CONFLICT", "Direct OTTO synchronization must not include Aftercool data.")
        aftercool_price = None
        aftercool_identity: dict[str, Any] | None = None
        aftercool_issue = None
    else:
        expected_product_id = str(context.get("aftercool_product_id"))
        returned_account = str(aftercool.get("account") or "").strip().upper() if isinstance(aftercool, Mapping) else ""
        returned_product_id = str(
            (aftercool.get("product_id") or aftercool.get("aftercool_product_id") or "")
            if isinstance(aftercool, Mapping) else ""
        )
        identity_confirmed = bool(
            isinstance(aftercool, Mapping)
            and aftercool.get("status") == "FOUND"
            and aftercool.get("found") is True
            and aftercool.get("verified") is True
            and aftercool.get("data_scope") == "PRODUCTION"
            and aftercool.get("source_type") == "AFTERCOOL"
            and returned_account == account
            and str(aftercool.get("ean") or "") == str(context.get("confirmed_ean"))
            and returned_product_id == expected_product_id
            and str(aftercool.get("factory_id") or "") == str(context.get("factory_id"))
        )
        # Only retain Aftercool identity after every account, EAN, product and
        # factory check passes. Its price is optional when OTTO has a usable one.
        aftercool_identity = ({
            "status": "FOUND",
            "found": True,
            "verified": True,
            "data_scope": "PRODUCTION",
            **{
                key: aftercool.get(key)
                for key in ("account", "source_type", "product_id", "aftercool_product_id", "ean", "factory_id", "factory_name", "name", "title", "matched_by")
                if key in aftercool
            },
        } if identity_confirmed else None)
        raw_aftercool_price = aftercool.get("price_cents") if identity_confirmed else None
        aftercool_price = raw_aftercool_price if type(raw_aftercool_price) is int and raw_aftercool_price > 0 else None
        if not identity_confirmed:
            aftercool_issue = (
                "AFTERCOOL_IDENTITY_MISMATCH" if isinstance(aftercool, Mapping) and aftercool.get("status") == "FOUND"
                else str(aftercool.get("reason_code") or aftercool.get("status") or "AFTERCOOL_IDENTITY_UNVERIFIED")
                if isinstance(aftercool, Mapping) else "AFTERCOOL_IDENTITY_UNVERIFIED"
            )
        elif aftercool_price is None:
            aftercool_issue = "AFTERCOOL_START_PRICE_UNVERIFIED"
        else:
            aftercool_issue = None

    normalized = _otto_product_snapshot(
        normalized_product, normalized_prices, expected_sku=expected_sku, expected_ean=expected_ean,
    )
    category = _category_value(normalized_product, normalized)
    description = normalized_product.get("productDescription")
    description = description if isinstance(description, Mapping) else {}
    raw_attributes = description.get("attributes", normalized_product.get("attributes"))
    if isinstance(raw_attributes, Mapping):
        attributes = [{"name": str(key), "value": value} for key, value in raw_attributes.items()]
    elif isinstance(raw_attributes, list):
        attributes = list(raw_attributes)
    else:
        raise ProductSyncError("OTTO_ATTRIBUTES_UNVERIFIED", "OTTO did not return a complete attributes collection.")
    if not all(isinstance(item, Mapping) for item in attributes):
        raise ProductSyncError("OTTO_ATTRIBUTES_INVALID", "OTTO returned malformed product attributes.")
    raw_attributes = [dict(item) for item in attributes]
    revision_attributes = _revision_attributes(
        raw_attributes, source_reference=f"OTTO:/v5/products/{context['observed_sku']}"
    )

    try:
        price = price_snapshot_from_sources(
            account,
            normalized_prices,
            source_type=source_type,
            aftercool_price_cents=aftercool_price,
        )
        price_review_reason = None
    except PreparationError as error:
        if error.code not in {"SOURCE_PRICE_UNAVAILABLE", "TARGET_PRICE_NON_POSITIVE"}:
            raise ProductSyncError(error.code, str(error), error.details) from None
        # Persist source evidence while keeping the target price unavailable
        # only when neither eligible source supplies a positive EUR price.
        amount_cents = resolved_price["evidence"].get("amountCents")
        if type(amount_cents) is not int or amount_cents <= 0:
            amount_cents = None
        price = {
            "currency": "EUR",
            "standardPriceAmount": resolved_price["evidence"].get("amount"),
            "ottoStandardPriceCents": amount_cents,
            "aftercoolStartPriceCents": aftercool_price,
            "targetPriceStatus": "PRICE_REVIEW_REQUIRED",
            "priceReviewReason": error.code,
        }
        price_review_reason = error.code

    return {
        "account": account,
        "candidateEpisodeId": str(context["candidate_episode_id"]),
        "sourceProductId": str(context["source_product_id"]),
        "sourceType": source_type,
        "sourceSku": str(context["observed_sku"]),
        "sourceEan": str(context["confirmed_ean"]),
        "category": category,
        "attributes": raw_attributes,
        "revisionAttributes": revision_attributes,
        "ottoProductData": dict(otto_product),
        "ottoPricesData": dict(normalized_prices) if isinstance(normalized_prices, Mapping) else {"sku": expected_sku},
        "ottoPriceEvidence": dict(resolved_price["evidence"]),
        "aftercoolIdentity": ({**aftercool_identity, "price_cents": aftercool_price}
                              if aftercool_identity is not None else None),
        "aftercoolIssue": price_review_reason,
        "priceSnapshot": {**price, "capturedAt": captured_at.astimezone(timezone.utc).isoformat(),
                          "sourceSku": str(context["observed_sku"])},
        "capturedAt": captured_at.astimezone(timezone.utc).isoformat(),
    }


def _context_fingerprint(context: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(context.get(key) for key in (
        "candidate_episode_id", "account", "source_product_id", "source_type", "observed_sku",
        "confirmed_ean", "classification", "episode_state", "aftercool_product_id", "aftercool_sku",
        "aftercool_ean", "factory_id", "factory_name", "original_title", "stored_category", "image_url",
        "original_price_cents", "attributes_json",
        "source_updated_at", "operation_id", "operation_status", "submission_state", "operation_version",
        "current_revision_number",
    ))


def apply_product_sync_snapshot(
    conn: Connection,
    *,
    preflight_context: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    expected_version: int | None,
    actor: str,
) -> dict[str, Any]:
    """Atomically store a source refresh or append a draft revision; no operation/EAN is created."""
    account = str(snapshot.get("account") or "")
    candidate_id = str(snapshot.get("candidateEpisodeId") or "")
    if not isinstance(actor, str) or not actor.strip():
        raise ProductSyncError("ACTOR_REQUIRED", "A trusted operator is required.")
    current = load_product_sync_context(conn, account=account, candidate_episode_id=candidate_id, lock=True)
    if _context_fingerprint(current) != _context_fingerprint(preflight_context):
        raise ProductSyncError("SOURCE_CHANGED_DURING_SYNC", "The candidate or operation changed while source data was being fetched.")
    operation_id = current.get("operation_id")
    if operation_id:
        if expected_version is None or type(expected_version) is not int or expected_version != current.get("operation_version"):
            raise ProductSyncError("VERSION_CONFLICT", "The editable operation version changed before synchronization.",
                                   {"expectedVersion": expected_version, "currentVersion": current.get("operation_version")})
        if current.get("operation_status") not in {"DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"} or current.get("submission_state") != "NEVER_SUBMITTED":
            raise ProductSyncError("OPERATION_NOT_EDITABLE", "Only a never-submitted draft can be synchronized.")
        queued = conn.execute(
            text("SELECT 1 FROM restart_items WHERE account=:account AND data_scope='PRODUCTION' "
                 "AND operation_id=:operation_id AND status NOT IN ('FAILED','CANCELLED','PUBLISHED') LIMIT 1"),
            {"account": account, "operation_id": operation_id},
        ).first()
        if queued:
            raise ProductSyncError("OPERATION_QUEUED", "A queued or submitted operation cannot be synchronized.")
    elif expected_version is not None:
        raise ProductSyncError("VERSION_CONFLICT", "No operation exists for the supplied operation version.")

    price_snapshot = _json_object(snapshot.get("priceSnapshot"))
    target_price_cents = price_snapshot.get("targetPriceCents")
    target_price_status = (
        "CALCULATED" if type(target_price_cents) is int and target_price_cents > 0
        else "PRICE_REVIEW_REQUIRED"
    )
    stored_data = _json_object(current.get("attributes_json"))
    aftercool_identity = snapshot.get("aftercoolIdentity")
    sync_snapshot = {
        **price_snapshot,
        "sourceType": snapshot["sourceType"],
        "sourceSku": snapshot["sourceSku"],
        "sourceEan": snapshot["sourceEan"],
        "capturedAt": snapshot["capturedAt"],
    }
    new_data = {
        **stored_data,
        "attributes": snapshot["attributes"],
        "ottoProductData": snapshot["ottoProductData"],
        "ottoPricesData": snapshot["ottoPricesData"],
        "ottoPriceEvidence": snapshot.get("ottoPriceEvidence", {}),
        "aftercoolLookup": aftercool_identity if isinstance(aftercool_identity, Mapping) else {},
        "syncSnapshot": sync_snapshot,
    }
    conn.execute(
        text("UPDATE source_products SET category=:category, attributes_json=CAST(:attributes AS jsonb), "
             "updated_at=CURRENT_TIMESTAMP WHERE id=:source_id AND account=:account AND data_scope='PRODUCTION'"),
        {"category": snapshot["category"], "attributes": json.dumps(new_data, ensure_ascii=False, separators=(",", ":")),
         "source_id": current["source_product_id"], "account": account},
    )

    revision_result = None
    if operation_id:
        old_source = _json_object(current.get("revision_source_snapshot"))
        old_content = _json_object(current.get("revision_content"))
        old_price = _json_object(current.get("revision_price_snapshot"))
        old_fulfillment = _json_object(current.get("revision_fulfillment_snapshot"))
        old_payload = _json_object(current.get("revision_payload"))
        captured = str(snapshot["capturedAt"])
        source_snapshot = {
            **old_source,
            "account": account,
            "sourceProductId": str(current["source_product_id"]),
            "sourceType": current["source_type"],
            "sourceSku": str(current["observed_sku"]),
            "sourceEan": str(current["confirmed_ean"]),
            "sourceRecordUpdatedAt": captured,
            "ottoProductResponse": snapshot["ottoProductData"],
            "aftercoolIdentity": aftercool_identity,
            "syncPriceSnapshot": price_snapshot,
            "references": list(old_source.get("references") or []) + [
                {"system": "OTTO", "method": "GET", "resource": f"/v5/products/{current['observed_sku']}"},
                {"system": "OTTO", "method": "GET", "resource": f"/v5/products/{current['observed_sku']}/prices"},
            ] + ([{"system": "Aftercool", "method": "GET", "resource": "/api/products", "productId": str(current["aftercool_product_id"])}] if aftercool_identity else []),
        }
        category_schema = source_snapshot.get("categorySchema")
        if isinstance(category_schema, Mapping) and category_schema.get("category") != snapshot["category"]:
            source_snapshot.pop("categorySchema", None)
        title = old_content.get("title")
        if not isinstance(title, str) or not title.strip():
            title = str(current.get("original_title") or "")
        content = {
            **old_content,
            "title": title,
            "category": snapshot["category"],
            "attributes": snapshot["revisionAttributes"],
            "ottoProductResponse": snapshot["ottoProductData"],
            "aftercoolIdentity": aftercool_identity,
        }
        # Category-specific AI suggestions and attribute confirmations belong
        # to the old source snapshot and cannot approve freshly synchronized data.
        content.pop("aiSuggestion", None)
        manual_review = content.get("manualReview")
        if isinstance(manual_review, Mapping):
            confirmations = manual_review.get("confirmations")
            if isinstance(confirmations, list):
                kept = [item for item in confirmations if isinstance(item, Mapping) and item.get("field") == "title"]
                if kept:
                    content["manualReview"] = {**manual_review, "confirmations": kept}
                else:
                    content.pop("manualReview", None)
        payload = {
            **old_payload,
            "account": account,
            "sourceProductId": str(current["source_product_id"]),
            "sourceSku": str(current["observed_sku"]),
            "sourceEan": str(current["confirmed_ean"]),
            "category": snapshot["category"],
            "attributes": snapshot["revisionAttributes"],
        }
        readiness_gaps = payload.get("readinessGaps")
        readiness_gaps = list(readiness_gaps) if isinstance(readiness_gaps, list) else []
        price_review_gaps = {
            "PRICE_REVIEW_REQUIRED", "AFTERCOOL_START_PRICE_UNVERIFIED", "TARGET_PRICE_NON_POSITIVE",
        }
        readiness_gaps = [
            gap for gap in readiness_gaps if isinstance(gap, str) and gap not in price_review_gaps
        ]
        if target_price_status == "CALCULATED":
            payload["priceCents"] = target_price_cents
        else:
            # Do not let an old target price survive a fresh source sync when
            # the current price policy cannot be completed from verified data.
            payload.pop("priceCents", None)
            readiness_gaps.append("PRICE_REVIEW_REQUIRED")
            reason = str(snapshot.get("aftercoolIssue") or price_snapshot.get("priceReviewReason") or "PRICE_REVIEW_REQUIRED")
            if reason in {"AFTERCOOL_START_PRICE_UNVERIFIED", "TARGET_PRICE_NON_POSITIVE"}:
                readiness_gaps.append(reason)
        payload["readinessGaps"] = readiness_gaps
        otto_payload = payload.get("ottoProductPayload")
        if isinstance(otto_payload, Mapping):
            payload.pop("ottoProductPayload", None)
            if "OTTO_PRODUCT_PAYLOAD_REQUIRES_REBUILD" not in readiness_gaps:
                readiness_gaps.append("OTTO_PRODUCT_PAYLOAD_REQUIRES_REBUILD")
        evidence_ids = _json_list(current.get("revision_gtin_evidence_ids"))
        try:
            revision_result = append_operation_revision(
                conn, account=account, operation_id=str(operation_id), expected_version=int(expected_version),
                source_snapshot=source_snapshot, content=content, price_snapshot=price_snapshot,
                fulfillment_snapshot=old_fulfillment, gtin_evidence_ids=evidence_ids,
                payload=payload, actor=actor,
            )
        except OperationError as error:
            raise ProductSyncError(error.code, str(error), error.details) from None
    else:
        conn.execute(
        text("INSERT INTO restart_events (id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json,created_at) "
             "VALUES (:event_id,:account,'PRODUCTION','PRODUCT_SOURCE_SYNCHRONIZED','SOURCE_PRODUCT',:source_id,:summary,CAST(:metadata AS jsonb),CURRENT_TIMESTAMP)"),
        {"event_id": str(uuid4()), "account": account, "source_id": str(current["source_product_id"]),
             "summary": "Selected OTTO source data synchronized; Restart price readiness recorded",
             "metadata": json.dumps({"actor": actor, "candidateEpisodeId": candidate_id,
                                     "capturedAt": snapshot["capturedAt"], "targetPriceCents": target_price_cents,
                                     "targetPriceStatus": target_price_status,
                                     "priceReviewReason": snapshot.get("aftercoolIssue")},
                                    ensure_ascii=False, separators=(",", ":"))},
        )
    return {
        "account": account,
        "candidateEpisodeId": candidate_id,
        "sourceProductId": str(current["source_product_id"]),
        "operationId": str(operation_id) if operation_id else None,
        "operationVersion": int(expected_version) + 1 if operation_id else None,
        "revision": revision_result,
        "category": snapshot["category"],
        "attributes": snapshot["attributes"],
        "priceSnapshot": price_snapshot,
        "targetPriceStatus": target_price_status,
        "priceReviewReason": snapshot.get("aftercoolIssue"),
        "syncedAt": snapshot["capturedAt"],
    }

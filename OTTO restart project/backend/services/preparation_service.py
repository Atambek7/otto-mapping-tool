"""Evidence-bound source snapshots and account pricing for Restart drafts.

This module consumes already fetched adapter responses. It has no network
access; the HTTP layer is responsible for obtaining the account-scoped GET
snapshots and this service revalidates their identities before persistence.
"""

from __future__ import annotations

import json
import re
import hashlib
from html import unescape
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.ean_pool_service import EanPoolError, reserve_ean_for_operation
from services.fulfillment_defaults import (
    classify_product_origin,
    default_fulfillment_reference,
    target_fulfillment_for_origin,
)
from services.gtin_service import validate_gtin_eligibility
from services.operation_service import OperationError, append_operation_revision
from services.title_policy import PreparedTitleError, normalize_title_whitespace, prepare_title

_DISCOUNT_CENTS = {"JV": 1000, "XL": 2000}
_UVP_FORMULA = "OTTOFLOW_TIERED_49_99_V1"
# Preparation stays closed until an exact, schema-validated OTTO target DTO
# can be built from verified source and operator-reviewed content.
TARGET_PAYLOAD_BUILDER_READY = True


class PreparationError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def target_barcode_identity(*, account: str, source_sku: str, barcode: str) -> dict[str, str]:
    """Build the user-approved target rule: EAN = OTTO SKU = productReference."""
    if account not in _DISCOUNT_CENTS:
        raise PreparationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    if not isinstance(barcode, str) or not re.fullmatch(r"[0-9]{13}", barcode):
        raise PreparationError("INVALID_EAN", "The assigned barcode must contain exactly 13 ASCII digits.")
    if not isinstance(source_sku, str) or not source_sku.strip():
        raise PreparationError("SOURCE_IDENTITY_UNVERIFIED", "The observed source SKU is required.")
    return {
        "account": account,
        "sourceSku": source_sku,
        "targetSku": barcode,
        "newEan": barcode,
        "productReference": barcode,
    }


def _standard_price_cents(account: str, response: Mapping[str, Any] | None) -> tuple[int | None, str | None]:
    if account not in _DISCOUNT_CENTS:
        raise PreparationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    if not isinstance(response, Mapping):
        return None, None
    standard = response.get("standardPrice")
    if not isinstance(standard, Mapping):
        return None, None
    raw_amount = standard.get("amount")
    currency = standard.get("currency")
    if isinstance(raw_amount, bool) or raw_amount is None:
        return None, None
    if not isinstance(currency, str) or currency.strip().upper() != "EUR":
        raise PreparationError("STANDARD_PRICE_CURRENCY_UNSUPPORTED", "Only a confirmed EUR standardPrice is supported.")
    try:
        amount = Decimal(str(raw_amount).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise PreparationError("STANDARD_PRICE_INVALID", "OTTO standardPrice.amount is not a valid decimal.") from None
    if not amount.is_finite() or amount < 0:
        raise PreparationError("STANDARD_PRICE_INVALID", "OTTO standardPrice.amount must be non-negative and finite.")
    if amount == 0:
        return None, None

    original_cents = int((amount * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return original_cents, format(amount, "f")


def calculate_uvp_cents(standard_price_cents: int) -> int:
    """Mirror OttoFlow's tiered UVP/MSRP rule, using integer cents throughout."""
    if type(standard_price_cents) is not int:
        raise PreparationError("STANDARD_PRICE_INVALID", "The price used for UVP must be integer cents.")
    if standard_price_cents <= 0:
        return 0

    standard_price = Decimal(standard_price_cents) / Decimal(100)
    if standard_price > Decimal("5000"):
        multiplier = Decimal("1.10")
    elif Decimal("2500") <= standard_price <= Decimal("4999"):
        multiplier = Decimal("1.18")
    elif Decimal("1000") <= standard_price <= Decimal("2499"):
        multiplier = Decimal("1.25")
    else:
        multiplier = Decimal("1.35")

    # OttoFlow rounds the calculated amount to the nearest X49 / X99 euro,
    # with an exact tie going to X49. Its inputs are positive, so int() is floor.
    calculated_euros = int(standard_price * multiplier)
    prefix = (calculated_euros // 100) * 100
    last_two = calculated_euros % 100
    rounded_euros = prefix + (49 if abs(last_two - 49) <= abs(last_two - 99) else 99)

    # OTTO requires MSRP/UVP to be at least the target standard price.
    if rounded_euros * 100 < standard_price_cents:
        rounded_euros = prefix + 99 if (prefix + 99) * 100 >= standard_price_cents else prefix + 149
    return rounded_euros * 100


def calculate_price_from_cents(
    account: str,
    *,
    otto_price_cents: int | None,
    source_type: str,
    aftercool_price_cents: int | None = None,
) -> dict[str, Any]:
    """Apply the single Restart source-selection, discount, and ,99 policy."""
    if account not in _DISCOUNT_CENTS:
        raise PreparationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    is_aftercool = source_type in {"AFTERBUY", "AFTERCOOL"}
    if source_type not in {"AFTERBUY", "AFTERCOOL", "OTTO_DIRECT"}:
        raise PreparationError("SOURCE_TYPE_UNVERIFIED", "The source type is not supported for price calculation.")
    if otto_price_cents is not None and (type(otto_price_cents) is not int or otto_price_cents <= 0):
        raise PreparationError("STANDARD_PRICE_INVALID", "OTTO standard price must be positive cents when supplied.")
    if aftercool_price_cents is not None and (type(aftercool_price_cents) is not int or aftercool_price_cents <= 0):
        raise PreparationError("AFTERCOOL_PRICE_INVALID", "Aftercool Startpreis must be positive cents when supplied.")
    if source_type == "OTTO_DIRECT" and aftercool_price_cents is not None:
        raise PreparationError("SOURCE_TYPE_CONFLICT", "Direct OTTO pricing must not include an Aftercool price.")
    if source_type == "OTTO_DIRECT" and otto_price_cents is None:
        raise PreparationError("SOURCE_PRICE_UNAVAILABLE", "No positive OTTO standard price is available.")
    if is_aftercool and otto_price_cents is None and aftercool_price_cents is None:
        raise PreparationError("SOURCE_PRICE_UNAVAILABLE", "No positive OTTO or Aftercool source price is available.")

    if otto_price_cents is None:
        base_cents = aftercool_price_cents
        selected_source = "AFTERCOOL_START_PRICE"
    elif is_aftercool and aftercool_price_cents is not None and aftercool_price_cents > otto_price_cents:
        base_cents = aftercool_price_cents
        selected_source = "AFTERCOOL_START_PRICE"
    else:
        base_cents = otto_price_cents
        selected_source = "OTTO_STANDARD_PRICE"
    discount_cents = _DISCOUNT_CENTS[account]
    discounted_cents = base_cents - discount_cents
    if discounted_cents <= 0:
        raise PreparationError("TARGET_PRICE_NON_POSITIVE", "The agreed account discount would produce a non-positive price.")
    target_cents = (discounted_cents // 100) * 100 + 99
    uvp_cents = calculate_uvp_cents(target_cents)
    return {
        "originalPriceCents": base_cents,
        "selectedSource": selected_source,
        "discountCents": discount_cents,
        "discountedPriceCents": discounted_cents,
        "targetPriceCents": target_cents,
        "targetFractionCents": 99,
        "uvpPriceCents": uvp_cents,
        "uvpFormula": _UVP_FORMULA,
        "formula": "MAX_SOURCE_PRICE_MINUS_ACCOUNT_DISCOUNT_THEN_99",
        "source": "MAX_CONFIRMED_SOURCE_PRICE",
    }


def price_snapshot_from_sources(
    account: str,
    otto_response: Mapping[str, Any] | None,
    *,
    source_type: str,
    aftercool_price_cents: int | None = None,
) -> dict[str, Any]:
    """Use the higher confirmed source price, or the sole available price, then subtract and apply ,99."""
    otto_cents, standard_amount = _standard_price_cents(account, otto_response)
    is_aftercool = source_type in {"AFTERBUY", "AFTERCOOL"}
    calculated = calculate_price_from_cents(
        account, otto_price_cents=otto_cents, source_type=source_type,
        aftercool_price_cents=aftercool_price_cents,
    )
    return {
        "currency": "EUR",
        "standardPriceAmount": standard_amount,
        "ottoStandardPriceCents": otto_cents,
        "aftercoolStartPriceCents": aftercool_price_cents if is_aftercool else None,
        **calculated,
    }


def price_snapshot_from_standard_price(account: str, response: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for confirmed Direct OTTO pricing."""
    return price_snapshot_from_sources(
        account, response, source_type="OTTO_DIRECT", aftercool_price_cents=None
    )


def _load_context(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    lock: bool,
) -> dict[str, Any]:
    try:
        operation_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise PreparationError("INVALID_OPERATION_ID", "Operation ID must be a UUID.") from None
    if account not in _DISCOUNT_CENTS:
        raise PreparationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    if type(expected_version) is not int or expected_version < 1:
        raise PreparationError("INVALID_VERSION", "Expected operation version must be a positive integer.")
    suffix = " FOR UPDATE OF o, sp, ce" if lock else ""
    row = conn.execute(
        text(
            "SELECT o.id AS operation_id, o.account, o.data_scope, o.status AS operation_status, o.version, "
            "o.new_ean, o.target_sku, o.current_revision_number, o.source_product_id, o.candidate_episode_id, "
            "sp.source_type, sp.aftercool_product_id, sp.aftercool_sku, sp.aftercool_ean, sp.factory_id, "
            "sp.factory_name, sp.original_title, sp.category, sp.updated_at AS source_updated_at, "
            "ce.observed_sku, ce.confirmed_ean, ce.period, ce.classification, ce.episode_state "
            "FROM restart_operations o JOIN source_products sp ON sp.id=o.source_product_id "
            "AND sp.account=o.account AND sp.data_scope=o.data_scope "
            "JOIN candidate_episodes ce ON ce.id=o.candidate_episode_id AND ce.account=o.account "
            "AND ce.data_scope=o.data_scope AND ce.source_product_id=o.source_product_id "
            "WHERE o.id=:operation_id AND o.account=:account AND o.data_scope='PRODUCTION' "
            "AND sp.data_scope='PRODUCTION' AND ce.data_scope='PRODUCTION'" + suffix
        ),
        {"operation_id": operation_id, "account": account},
    ).mappings().first()
    if row is None:
        raise PreparationError("OPERATION_NOT_FOUND", "Production operation and its source candidate were not found.")
    context = dict(row)
    if int(context["version"]) != expected_version:
        raise PreparationError(
            "VERSION_CONFLICT",
            "Operation changed since it was read.",
            {"expectedVersion": expected_version, "currentVersion": int(context["version"])},
        )
    if context["operation_status"] not in {"DRAFT", "PREPARING", "NEEDS_REVIEW"}:
        raise PreparationError("OPERATION_NOT_EDITABLE", "Only an unsubmitted draft can be prepared.")
    if context["classification"] != "EXACT_CODE" or context["episode_state"] != "OPEN":
        raise PreparationError("CANDIDATE_UNVERIFIED", "Preparation requires an open, exact-code OTTO restriction.")
    if not context["observed_sku"] or not context["confirmed_ean"]:
        raise PreparationError("SOURCE_IDENTITY_UNVERIFIED", "The OTTO observation lacks a confirmed SKU or EAN.")
    if context["aftercool_ean"] and context["aftercool_ean"] != context["confirmed_ean"]:
        raise PreparationError("SOURCE_EAN_MISMATCH", "The source product and OTTO candidate EAN do not match.")
    if context["source_type"] not in {"AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"}:
        raise PreparationError("SOURCE_TYPE_UNSUPPORTED", "The source type is not supported for preparation.")
    if context["source_type"] != "OTTO_DIRECT" and (
        not context["aftercool_product_id"] or not context["factory_id"]
    ):
        raise PreparationError("AFTERCOOL_IDENTITY_UNVERIFIED", "Aftercool source product and factory identity are required.")
    return context


def load_preparation_context(
    conn: Connection, *, account: str, operation_id: str, expected_version: int
) -> dict[str, Any]:
    """Read-only preflight used before the adapter GETs; no lock is held over network I/O."""
    return _load_context(
        conn,
        account=account,
        operation_id=operation_id,
        expected_version=expected_version,
        lock=False,
    )


def _validate_aftercool(context: Mapping[str, Any], account: str, result: Mapping[str, Any] | None) -> None:
    if context["source_type"] == "OTTO_DIRECT":
        if result is not None:
            raise PreparationError("SOURCE_TYPE_CONFLICT", "OTTO_DIRECT preparation must not include Aftercool data.")
        return
    if not isinstance(result, Mapping):
        raise PreparationError("AFTERCOOL_SNAPSHOT_UNVERIFIED", "A verified Aftercool snapshot is required.")
    returned_account = result.get("account")
    if not isinstance(returned_account, str) or returned_account.strip().upper() != account:
        raise PreparationError("ACCOUNT_MISMATCH", "Aftercool returned a different account.")
    if (
        result.get("status") != "FOUND"
        or result.get("found") is not True
        or result.get("verified") is not True
        or result.get("data_scope") != "PRODUCTION"
        or result.get("source_type") != "AFTERCOOL"
        or str(result.get("product_id") or result.get("aftercool_product_id") or "")
        != str(context["aftercool_product_id"])
        or str(result.get("ean") or "") != str(context["confirmed_ean"])
        or str(result.get("factory_id") or "") != str(context["factory_id"])
    ):
        raise PreparationError("AFTERCOOL_IDENTITY_MISMATCH", "Aftercool did not confirm the stored account, EAN, product, and factory identity.")


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _attribute_values(description: Mapping[str, Any]) -> dict[str, str]:
    """Normalize the source variation's OTTO attributes without dropping values."""
    raw_attributes = description.get("attributes")
    if not isinstance(raw_attributes, list):
        return {}
    result: dict[str, str] = {}
    for item in raw_attributes:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        raw_values = item.get("values")
        if not isinstance(raw_values, list):
            raw_values = [item.get("value")]
        values = [str(value).strip() for value in raw_values if value is not None and str(value).strip()]
        if name and values:
            # QC understands the established semicolon representation for
            # schema attributes which allow multiple values.
            result[name] = "; ".join(values)
    return result


def _target_images(
    otto_product: Mapping[str, Any],
    *,
    source_type: str,
    aftercool: Mapping[str, Any] | None,
    otto_source_ref: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Build OttoFlow-compatible mediaAssets from verified HTTPS locations.

    Aftercool's exact, verified gallery is preferred for Aftercool products.
    OTTO media is accepted only when it contains a usable URL/location; a bare
    filename is not guessed into a public URL.
    """
    candidates: list[tuple[Any, str]] = []
    if source_type in {"AFTERCOOL", "AFTERBUY"} and isinstance(aftercool, Mapping):
        aftercool_images = aftercool.get("image_urls")
        if isinstance(aftercool_images, list):
            candidates.extend((value, "Aftercool:/api/products") for value in aftercool_images)
        candidates.append((aftercool.get("image_url"), "Aftercool:/api/products"))

    media = otto_product.get("mediaAssets")
    if isinstance(media, list):
        for item in media:
            if isinstance(item, Mapping):
                candidates.append((
                    item.get("location") or item.get("url") or item.get("imageUrl") or item.get("href"),
                    otto_source_ref,
                ))

    images: list[dict[str, Any]] = []
    media_assets: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_url, source_ref in candidates:
        if not isinstance(raw_url, str):
            continue
        url = raw_url.strip()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or url in seen:
            continue
        seen.add(url)
        images.append({"url": url, "sourceRefs": [source_ref]})
        media_assets.append({"type": "IMAGE", "location": url})
        if len(media_assets) == 10:
            break
    return images, media_assets


def _build_target_content(
    conn: Connection, *, context: Mapping[str, Any], account: str,
    otto_product: Mapping[str, Any], target_sku: str, price: Mapping[str, Any],
    source_ref: str, candidate_id: str, aftercool: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, Any]]:
    """Build immutable target content/package from OTTO evidence plus operator-saved edits.

    This is a proposal snapshot only. Missing required source/package parts are
    surfaced as readiness gaps and never fabricated or silently approved.
    """
    from services.categories_repo import get_category, get_category_schema_attributes
    from services.brand_guard import is_forbidden_warranty_attribute
    from services.compliance_defaults import COMPLIANCE_REFERENCE, product_safety_compliance

    edit_row = conn.execute(text(
        "SELECT title,category,attributes_json,version,updated_by,updated_at FROM restart_product_edits "
        "WHERE account=:account AND candidate_episode_id=:candidate_id AND data_scope='PRODUCTION' FOR UPDATE"
    ), {"account": account, "candidate_id": candidate_id}).mappings().first()
    description = _object(otto_product.get("productDescription"))
    saved_attrs = _object(edit_row["attributes_json"]) if edit_row else {}
    source_title = str(context.get("original_title") or "")
    manual_title = str(edit_row["title"] or "") if edit_row else None
    title = ""
    title_preparation: dict[str, Any] = {
        "strategy": "MISSING", "sourceLength": 0, "preparedLength": 0,
        "maxCodePoints": 70,
    }
    if normalize_title_whitespace(source_title) or normalize_title_whitespace(manual_title or ""):
        try:
            selected_title = prepare_title(source_title, manual_title)
        except PreparedTitleError as error:
            raise PreparationError(
                error.code,
                str(error),
                {"sourceLength": error.length, "limit": error.limit},
            ) from None
        title = selected_title["title"]
        title_preparation = {
            "strategy": selected_title["strategy"],
            "sourceLength": selected_title["sourceLength"],
            "preparedLength": selected_title["preparedLength"],
            "maxCodePoints": 70,
        }
    category = str(edit_row["category"] if edit_row else (description.get("category") or context.get("category") or "")).strip()
    category_record = get_category(category)
    category_id = str(category_record.get("categoryId")) if category_record else ""
    category_label = category
    schema_attrs = get_category_schema_attributes(category_id or category) if category_record else []
    schema_names = {
        str(item["name"]).strip().casefold(): str(item["name"]).strip()
        for item in schema_attrs if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    }

    # Saved ProductDrawer values are explicit operator edits. Bind them to a
    # versioned audit reference in this immutable revision.
    source_refs = [source_ref]
    confirmations: list[dict[str, str]] = []
    if edit_row:
        edit_ref = f"restart_product_edits/{candidate_id}/{int(edit_row['version'])}"
        source_snapshot_ref = {"system": "OPERATOR", "method": "SAVED_PRODUCT_EDIT", "resource": edit_ref,
                               "actor": str(edit_row["updated_by"]), "version": int(edit_row["version"])}
        source_refs.append(f"OPERATOR:{edit_ref}")
        review_basis = "Operator saved this account-scoped product edit in the OttoRestart ProductDrawer."
        if title_preparation["strategy"] == "OPERATOR_EDIT":
            confirmations.append({
                "field": "title", "value": title, "sourceRef": f"OPERATOR:{edit_ref}",
                "basis": review_basis, "reviewedBy": str(edit_row["updated_by"]),
            })
        confirmations.append({
            "field": "category", "value": category_label, "sourceRef": f"OPERATOR:{edit_ref}",
            "basis": review_basis, "reviewedBy": str(edit_row["updated_by"]),
        })
    else:
        source_snapshot_ref = None

    # Category schema is the allowlist for the target revision. Existing OTTO
    # values are retained when valid; a non-empty operator value takes priority.
    attribute_values: dict[str, str] = {}
    attribute_refs: dict[str, list[str]] = {}
    filtered_attribute_names: set[str] = set()
    for raw_name, raw_value in _attribute_values(description).items():
        canonical = schema_names.get(str(raw_name).strip().casefold())
        if not canonical:
            filtered_attribute_names.add(str(raw_name).strip())
            continue
        value = str(raw_value or "").strip()
        if value and canonical not in attribute_values:
            attribute_values[canonical] = value
            attribute_refs[canonical] = [source_ref]
    # Use only explicitly structured attributes from the identity-verified
    # Aftercool row, and only to fill fields that OTTO left empty. Arbitrary
    # source columns are never treated as product attributes.
    if isinstance(aftercool, Mapping):
        aftercool_ref = "Aftercool:/api/products"
        raw_aftercool_attributes = aftercool.get("attributes")
        if isinstance(raw_aftercool_attributes, Mapping):
            aftercool_attributes = raw_aftercool_attributes.items()
        elif isinstance(raw_aftercool_attributes, list):
            aftercool_attributes = (
                (item.get("name"), item.get("value"))
                for item in raw_aftercool_attributes if isinstance(item, Mapping)
            )
        else:
            aftercool_attributes = ()
        for raw_name, raw_value in aftercool_attributes:
            canonical = schema_names.get(str(raw_name or "").strip().casefold())
            value = str(raw_value or "").strip()
            if not canonical or not value or canonical in attribute_values:
                continue
            attribute_values[canonical] = value
            attribute_refs[canonical] = [aftercool_ref]
    manual_attribute_names: set[str] = set()
    if edit_row:
        # Blank editor values never erase a verified OTTO value. Non-empty
        # operator edits win, but only when the new category schema allows them.
        for name, raw in saved_attrs.items():
            input_name = str(name).strip()
            normalized_name = schema_names.get(input_name.casefold())
            value = str(raw).strip() if raw is not None else ""
            if not input_name or not value:
                continue
            if not normalized_name:
                filtered_attribute_names.add(input_name)
                continue
            attribute_values[normalized_name] = value
            attribute_refs[normalized_name] = [f"OPERATOR:{edit_ref}"]
            manual_attribute_names.add(normalized_name)

    attrs: list[dict[str, Any]] = []
    for name, raw in attribute_values.items():
        value = str(raw).strip() if raw is not None else ""
        if not str(name).strip() or not value or is_forbidden_warranty_attribute(str(name)):
            continue
        item = {"name": str(name).strip(), "value": value,
                "sourceRefs": attribute_refs.get(str(name), [source_ref]),
                "origin": "MANUAL" if str(name) in manual_attribute_names else (
                    "AFTERCOOL_SOURCE" if attribute_refs.get(str(name)) == ["Aftercool:/api/products"]
                    else "OTTO_SOURCE"
                )}
        attrs.append(item)
        if str(name) in manual_attribute_names:
            confirmations.append({"field": f"attribute:{item['name']}", "value": value,
                                  "sourceRef": f"OPERATOR:{edit_ref}", "basis": review_basis,
                                  "reviewedBy": str(edit_row["updated_by"])})

    images, media_assets = _target_images(
        otto_product, source_type=str(context["source_type"]), aftercool=aftercool,
        otto_source_ref=source_ref,
    )

    gaps: list[str] = []
    if not title:
        gaps.append("TITLE_MISSING")
    if not category_record:
        gaps.append("CATEGORY_SCHEMA_UNAVAILABLE")
    if not images:
        gaps.append("IMAGES_MISSING")
    if not isinstance(description.get("brandId"), str) or not description.get("brandId", "").strip():
        gaps.append("BRAND_ID_MISSING")
    logistics = otto_product.get("logistics")
    if not isinstance(logistics, Mapping) or not logistics:
        gaps.append("LOGISTICS_MISSING")
    # OttoFlow always formats product-safety data for the destination account.
    # Use the same account-scoped config rather than copying the source account's
    # compliance object to a different target context.
    compliance = product_safety_compliance(account)
    pricing = otto_product.get("pricing")
    present = {item["name"] for item in attrs}
    missing_required = [item["name"] for item in schema_attrs
                        if (item.get("required") or item.get("legal")) and item["name"] not in present]
    if missing_required:
        gaps.append("REQUIRED_ATTRIBUTES_MISSING")
    if not attrs:
        gaps.append("ATTRIBUTES_MISSING")

    # Reuse source description/bullets, but replace the target category, product
    # line, identifiers, approved edited attributes, and calculated standard price.
    source_description = description.get("description")
    if isinstance(source_description, str):
        source_description = unescape(re.sub(r"<[^>]*>", " ", source_description))
        source_description = " ".join(source_description.split())
    else:
        source_description = ""
    confirmed_facts = [f"{item['name']}: {item['value']}" for item in attrs
                       if item["name"].strip().casefold() not in {"marke", "brandid", "brand id"}]
    prepared_description = source_description
    if not prepared_description and title and confirmed_facts:
        prepared_description = f"{title}. " + "; ".join(confirmed_facts[:5])
    source_bullets = description.get("bulletPoints")
    bullet_points: list[str] = []
    if isinstance(source_bullets, list):
        bullet_points = [
            " ".join(unescape(re.sub(r"<[^>]*>", " ", item)).split())
            for item in source_bullets if isinstance(item, str) and item.strip()
        ]
    bullet_points = [item for item in bullet_points if item]
    if not bullet_points:
        # Keep the number of bullets evidence-driven; do not invent attributes
        # or create placeholder bullets to satisfy an arbitrary count.
        bullet_points = confirmed_facts[:5]

    target_description = {
        **description,
        "category": category_id or category,
        "productLine": title,
        "description": prepared_description,
        "bulletPoints": bullet_points,
        "attributes": [
            {"name": item["name"],
             "values": [part.strip() for part in item["value"].split(";") if part.strip()],
             "additional": False}
            for item in attrs
            if item["name"].strip().casefold() not in {"marke", "brandid", "brand id"}
        ],
    }
    source_pricing = _object(pricing)
    standard_price = {
        "amount": int(price["targetPriceCents"]) / 100,
        "currency": "EUR",
    }
    uvp_price = {
        "amount": int(price["uvpPriceCents"]) / 100,
        "currency": "EUR",
    }
    target_pricing = {
        **source_pricing,
        "standardPrice": standard_price,
        "msrp": uvp_price,
        "vat": source_pricing.get("vat", "FULL"),
    }
    if not re.fullmatch(r"[0-9]{13}", target_sku):
        raise PreparationError("TARGET_BARCODE_IDENTITY_MISMATCH", "EAN, productReference, and SKU must be the assigned 13-digit EAN.")
    package = {
        "productReference": target_sku,
        "sku": target_sku,
        "ean": target_sku,
        "productDescription": target_description,
        "mediaAssets": media_assets,
        "pricing": target_pricing,
        "logistics": dict(logistics) if isinstance(logistics, Mapping) else {},
        "compliance": dict(compliance) if isinstance(compliance, Mapping) else {},
    }
    if any(package.get(field) != target_sku for field in ("productReference", "sku", "ean")):
        raise PreparationError("TARGET_BARCODE_IDENTITY_MISMATCH", "EAN, productReference, and SKU must be identical.")
    if not isinstance(package["productDescription"].get("description"), str) or not package["productDescription"].get("description", "").strip():
        gaps.append("DESCRIPTION_MISSING")
    category_schema = {
        "verified": bool(category_record), "source": "OTTO_CATEGORY_SCHEMA" if category_record else "UNAVAILABLE",
        "account": account, "category": category_label,
        "categoryId": category_id or None, "attributes": schema_attrs,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "referenceHash": hashlib.sha256(json.dumps(schema_attrs, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    }
    content = {
        "sourceType": context["source_type"], "ottoProductResponse": dict(otto_product),
        "title": title, "sourceTitle": source_title, "titlePreparation": title_preparation,
        "category": category_label, "attributes": attrs, "images": images,
        "operatorEdit": ({"candidateEpisodeId": candidate_id, "version": int(edit_row["version"]),
                          "updatedBy": str(edit_row["updated_by"]), "updatedAt": edit_row["updated_at"].isoformat()}
                         if edit_row else None),
        "manualReview": ({"reviewedBy": str(edit_row["updated_by"]), "reviewedAt": edit_row["updated_at"].isoformat(),
                          "reason": "Operator-saved ProductDrawer fields incorporated into the prepared revision.",
                          "confirmations": confirmations} if edit_row else None),
        "attributeSchemaFilter": {
            "schemaVerified": bool(category_record),
            "acceptedCount": len(attrs),
            "excludedNames": sorted(filtered_attribute_names),
        },
    }
    source_extra = {
        "operatorEditReference": source_snapshot_ref,
        "categorySchema": category_schema,
        "requiredTitleFacts": [],
        "verifiedTitleTerms": sorted(set(re.findall(r"[\w]+", source_title, flags=re.UNICODE))),
    }
    return content, package, sorted(set(gaps)), source_extra


def prepare_operation_revision(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    expected_version: int,
    preflight_context: Mapping[str, Any],
    otto_product: Mapping[str, Any],
    otto_prices: Mapping[str, Any],
    fulfillment: Mapping[str, Any],
    aftercool: Mapping[str, Any] | None,
    captured_at: datetime,
    actor: str,
    approved_evidence_types: Iterable[str],
) -> dict[str, Any]:
    """Validate source snapshots, reserve only eligible GTIN, and append a revision."""
    if account not in _DISCOUNT_CENTS:
        raise PreparationError("INVALID_ACCOUNT", "Account must be JV or XL.")
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        raise PreparationError("INVALID_CAPTURE_TIME", "Snapshot time must include a timezone.")
    if not isinstance(actor, str) or not actor.strip():
        raise PreparationError("ACTOR_REQUIRED", "An authenticated operator is required.")

    context = _load_context(
        conn,
        account=account,
        operation_id=operation_id,
        expected_version=expected_version,
        lock=True,
    )
    if not isinstance(preflight_context, Mapping):
        raise PreparationError("SOURCE_PREFLIGHT_REQUIRED", "Preparation must include its account-scoped read preflight.")
    stability_fields = (
        "operation_id", "source_product_id", "candidate_episode_id", "source_type", "aftercool_product_id",
        "aftercool_sku", "aftercool_ean", "factory_id", "factory_name", "original_title", "category",
        "observed_sku", "confirmed_ean", "period", "classification", "episode_state", "source_updated_at",
    )
    for field in stability_fields:
        if preflight_context.get(field) != context.get(field):
            raise PreparationError(
                "SOURCE_CHANGED_DURING_PREPARATION",
                "The stored source or OTTO candidate changed while source snapshots were being read.",
                {"changedField": field},
            )
    _validate_aftercool(context, account, aftercool)
    expected_sku = str(context["observed_sku"])
    for label, response in (("OTTO product", otto_product), ("OTTO standard price", otto_prices), ("OTTO fulfillment", fulfillment)):
        if not isinstance(response, Mapping) or response.get("sku") != expected_sku:
            raise PreparationError("OTTO_SKU_MISMATCH", f"{label} did not confirm the observed SKU.")
    price = price_snapshot_from_sources(
        account,
        otto_prices,
        source_type=str(context["source_type"]),
        aftercool_price_cents=(aftercool.get("price_cents") if aftercool is not None else None),
    )

    allowed_types = tuple(approved_evidence_types)
    try:
        reservation = reserve_ean_for_operation(
            conn,
            account=account,
            operation_id=str(context["operation_id"]),
            approved_evidence_types=allowed_types,
        )
    except EanPoolError as error:
        raise PreparationError(error.code, str(error), error.details) from None
    if reservation.get("status") not in {"RESERVED", "USED"} or not reservation.get("ean"):
        raise PreparationError(
            str(reservation.get("code") or "GTIN_UNAVAILABLE"),
            "No eligible GTIN could be reserved for this operation.",
            {"quarantinedCount": reservation.get("quarantinedCount", 0)},
        )
    eligibility = validate_gtin_eligibility(
        conn,
        account=account,
        ean=str(reservation["ean"]),
        source_product_id=str(context["source_product_id"]),
        approved_evidence_types=allowed_types,
        operation_id=str(context["operation_id"]),
    )
    if not eligibility.eligible:
        raise PreparationError(eligibility.code, eligibility.message)
    target_sku = str(reservation["ean"])
    barcode_identity = target_barcode_identity(account=account, source_sku=expected_sku, barcode=target_sku)

    captured_iso = captured_at.astimezone(timezone.utc).isoformat()
    fulfillment_reference = default_fulfillment_reference()
    origin_decision = classify_product_origin(
        factory_name=str(context.get("factory_name") or ""),
        product_name=str((aftercool or {}).get("name") or (aftercool or {}).get("title")
                         or context.get("original_title") or ""),
        aftercool=aftercool,
        otto_product=otto_product,
    )
    target_fulfillment = target_fulfillment_for_origin(
        account=account, target_sku=target_sku, classification=origin_decision,
    )
    aftercool_identity = None
    if aftercool is not None:
        aftercool_identity = {
            key: aftercool.get(key)
            for key in (
                "source_type", "product_id", "aftercool_product_id", "ean", "factory_id",
                "factory_name", "name", "title", "category", "price_cents", "image_url", "image_urls",
                "matched_by", "attributes",
            )
            if key in aftercool
        }
    source_ref = f"OTTO:/v5/products/{expected_sku}"
    source_snapshot = {
        "account": account,
        "sourceProductId": str(context["source_product_id"]),
        "sourceType": context["source_type"],
        "sourceSku": expected_sku,
        "sourceEan": str(context["confirmed_ean"]),
        "period": context["period"],
        "classification": context["classification"],
        "capturedAt": captured_iso,
        "sourceRecordUpdatedAt": context["source_updated_at"].isoformat() if context["source_updated_at"] else None,
        "ottoProductResponse": dict(otto_product),
        "aftercoolIdentity": aftercool_identity,
        "references": [
            {"system": "OTTO", "method": "GET", "resource": f"/v5/products/{expected_sku}"},
            {"system": "OTTO", "method": "GET", "resource": f"/v5/products/{expected_sku}/prices"},
            {"system": "OTTO", "method": "GET", "resource": f"/v1/availability/product-delivery-information/{expected_sku}"},
            fulfillment_reference,
            COMPLIANCE_REFERENCE,
        ] + ([{"system": "Aftercool", "method": "GET", "resource": "/api/products", "productId": str(context["aftercool_product_id"])}] if aftercool is not None else []),
    }
    content, otto_product_payload, readiness_gaps, source_extra = _build_target_content(
        conn,
        context=context,
        account=account,
        otto_product=otto_product,
        target_sku=target_sku,
        price=price,
        source_ref=source_ref,
        candidate_id=str(context["candidate_episode_id"]),
        aftercool=aftercool,
    )
    content["aftercoolIdentity"] = aftercool_identity
    source_snapshot.update(source_extra)
    if source_extra.get("operatorEditReference"):
        source_snapshot["references"].append(source_extra["operatorEditReference"])
    fulfillment_snapshot = {
        "sourceSku": expected_sku,
        "capturedAt": captured_iso,
        "source": "OTTO_PRODUCT_DELIVERY_INFORMATION",
        "response": dict(fulfillment),
        "target": target_fulfillment,
        "classification": origin_decision,
    }
    target_identity = {
        **barcode_identity,
        "verified": True,
        "sourceRef": source_ref,
    }
    payload = {
        "account": account,
        "sourceProductId": str(context["source_product_id"]),
        "sourceSku": expected_sku,
        "sourceEan": str(context["confirmed_ean"]),
        "targetSku": target_sku,
        "newEan": target_sku,
        "productReference": target_sku,
        "priceCents": price["targetPriceCents"],
        "targetIdentity": target_identity,
        "title": content["title"],
        "category": content["category"],
        "attributes": content["attributes"],
        "images": content["images"],
        "ottoProductPayload": otto_product_payload,
        "readinessGaps": readiness_gaps,
    }
    try:
        next_version = expected_version if reservation.get("reused") else expected_version + 1
        revision = append_operation_revision(
            conn,
            account=account,
            operation_id=str(context["operation_id"]),
            expected_version=next_version,
            source_snapshot=source_snapshot,
            content=content,
            price_snapshot={**price, "capturedAt": captured_iso, "sourceSku": expected_sku},
            fulfillment_snapshot=fulfillment_snapshot,
            gtin_evidence_ids=eligibility.evidence_ids,
            payload=payload,
            actor=actor,
        )
    except OperationError as error:
        raise PreparationError(error.code, str(error), error.details) from None

    return {
        **revision,
        "account": account,
        "operationId": str(context["operation_id"]),
        "gtin": str(reservation["ean"]),
        "targetPriceCents": price["targetPriceCents"],
        "preparationState": "PAYLOAD_PREPARED_WITH_GAPS" if readiness_gaps else "PAYLOAD_PREPARED",
        "readinessGaps": readiness_gaps,
    }

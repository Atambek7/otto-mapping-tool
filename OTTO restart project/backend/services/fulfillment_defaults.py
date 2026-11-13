"""User-approved default fulfillment copied from OttoFlow's local adapter source.

This module is Restart-owned and has no runtime dependency on OttoFlow.
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

DEFAULT_STOCK_QUANTITY = 20
DEFAULT_PROCESSING_TIME_DAYS = "37"
FULFILLMENT_CLASSIFIER_REFERENCE = "OTTOFLOW:RESTART_DEFAULTS:ORIGIN_CLASSIFIER_V1"
SHIPPING_PROFILES = {
    # Profile IDs copied from OttoFlow's read-only backend/services/otto_availability.py.
    "XL": {
        "EUROPE_4_8_WEEKS": "d045a64d-59e6-57fb-9f69-6370dc7c9d40",
        "CHINA_8_12_WEEKS": "027a1a46-cf60-56f4-917b-81661569f016",
    },
    "JV": {
        "EUROPE_4_8_WEEKS": "786c6468-3baf-52e0-88b5-13757eb7f873",
        "CHINA_8_12_WEEKS": "935a75b0-ac88-55a8-98df-8556306f1386",
    },
}
DEFAULT_SHIPPING_PROFILE_NAME = "EUROPE_4_8_WEEKS"
DEFAULT_SHIPPING_PROFILE_IDS = {
    account: profiles[DEFAULT_SHIPPING_PROFILE_NAME]
    for account, profiles in SHIPPING_PROFILES.items()
}

_CHINA_MARKERS = ("china", "chinesisch", "vr china", "volksrepublik china", "prc", "(cn)", "made in china")
_NON_CHINA_MARKERS = (
    "türkei", "turkey", "polen", "poland", "deutschland", "germany", "italien", "italy",
    "spanien", "spain", "frankreich", "france", "österreich", "austria", "europa", "europe",
    "made in eu", "made in germany", "made in turkey", "inegöl", "inegol", "istanbul", "kayseri", "bursa",
)
_ORIGIN_ATTRIBUTE_NAMES = {
    "ursprungsland", "herkunftsland", "herstellungsland", "produktionsland", "herstellerland",
}


def _as_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if item is not None).strip()
    return str(value).strip() if value is not None else ""


def _product_attributes(product: Mapping[str, Any]) -> dict[str, str]:
    description = product.get("productDescription")
    raw = description.get("attributes") if isinstance(description, Mapping) else None
    values: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            name = _as_text(item.get("name"))
            value = _as_text(item.get("values") or item.get("value"))
            if name and value and name not in values:
                values[name] = value
    elif isinstance(raw, Mapping):
        values = {str(name): _as_text(value) for name, value in raw.items() if _as_text(value)}
    return values


def classify_product_origin(
    *, factory_name: str = "", product_name: str = "", aftercool: Mapping[str, Any] | None = None,
    otto_product: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Copy OttoFlow's origin cascade while retaining the actual evidence used.

    No country is inferred from dimensions or product type. The existing EU
    profile remains the explicit fallback when no China marker is present.
    """
    aftercool = aftercool if isinstance(aftercool, Mapping) else {}
    otto_product = otto_product if isinstance(otto_product, Mapping) else {}
    attrs = _product_attributes(otto_product)
    origin = next((value for name, value in attrs.items()
                   if name.strip().casefold() in _ORIGIN_ATTRIBUTE_NAMES and value.strip()), "")
    if origin:
        lowered = origin.casefold()
        if any(marker in lowered for marker in _CHINA_MARKERS) or lowered.strip() in {"cn", "china", "prc"}:
            return {"region": "CHINA", "basis": "OTTO_ORIGIN_ATTRIBUTE", "evidence": origin[:160]}
        if any(marker in lowered for marker in _NON_CHINA_MARKERS):
            return {"region": "EUROPE", "basis": "OTTO_ORIGIN_ATTRIBUTE", "evidence": origin[:160]}

    short_description = _as_text(
        aftercool.get("kurzbeschreibung") or aftercool.get("short_description") or aftercool.get("shortDescription")
    )
    if short_description:
        lowered = short_description.casefold()
        if any(marker in lowered for marker in _NON_CHINA_MARKERS):
            return {"region": "EUROPE", "basis": "AFTERCOOL_SHORT_DESCRIPTION", "evidence": "NON_CHINA_MARKER"}
        if any(marker in lowered for marker in _CHINA_MARKERS) or "8-12 wochen" in lowered or "8 bis 12 wochen" in lowered:
            return {"region": "CHINA", "basis": "AFTERCOOL_SHORT_DESCRIPTION", "evidence": "CHINA_MARKER"}

    raw_media = otto_product.get("mediaAssets")
    image_values = list(raw_media) if isinstance(raw_media, (list, tuple)) else ([raw_media] if raw_media else [])
    extra_images = aftercool.get("image_urls")
    if isinstance(extra_images, (list, tuple)):
        image_values.extend(extra_images)
    image = aftercool.get("image_url")
    if image:
        image_values.append(image)
    for item in image_values:
        url = _as_text(item.get("location") if isinstance(item, Mapping) else item).casefold()
        if any(marker in url for marker in ("/china/", "/cn/", "_cn_", "chinese", "lieferzeit-8-12")):
            return {"region": "CHINA", "basis": "IMAGE_MARKER", "evidence": "CHINA_IMAGE_MARKER"}

    for source, value in (("FACTORY_NAME", factory_name), ("PRODUCT_NAME", product_name)):
        lowered = _as_text(value).casefold()
        if not lowered:
            continue
        if any(marker in lowered for marker in _NON_CHINA_MARKERS):
            return {"region": "EUROPE", "basis": source, "evidence": "NON_CHINA_MARKER"}
        if any(marker in lowered for marker in ("china", "chinesisch", "(cn)")):
            return {"region": "CHINA", "basis": source, "evidence": "CHINA_MARKER"}
    return {"region": "EUROPE", "basis": "DEFAULT_NO_CHINA_EVIDENCE", "evidence": ""}


def default_target_fulfillment(*, account: str, target_sku: str) -> dict[str, Any]:
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in SHIPPING_PROFILES:
        raise ValueError("Account must be JV or XL.")
    if not isinstance(target_sku, str) or not target_sku:
        raise ValueError("Target SKU is required for default fulfillment.")
    return target_fulfillment_for_origin(account=normalized_account, target_sku=target_sku)


def target_fulfillment_for_origin(
    *, account: str, target_sku: str, classification: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    normalized_account = str(account or "").strip().upper()
    if normalized_account not in SHIPPING_PROFILES:
        raise ValueError("Account must be JV or XL.")
    if not isinstance(target_sku, str) or not target_sku:
        raise ValueError("Target SKU is required for fulfillment.")
    decision = dict(classification or {"region": "EUROPE", "basis": "DEFAULT_NO_CHINA_EVIDENCE", "evidence": ""})
    region = "CHINA" if decision.get("region") == "CHINA" else "EUROPE"
    profile_name = "CHINA_8_12_WEEKS" if region == "CHINA" else "EUROPE_4_8_WEEKS"
    profile_id = str(UUID(SHIPPING_PROFILES[normalized_account][profile_name]))
    return {
        "account": normalized_account,
        "targetSku": target_sku,
        "stockQuantity": DEFAULT_STOCK_QUANTITY,
        "shippingProfile": profile_id,
        "shippingProfileName": profile_name,
        "processingTime": "56" if region == "CHINA" else "37",
        "originClassification": region,
        "classificationBasis": decision.get("basis"),
        "classificationEvidence": decision.get("evidence"),
        "verified": True,
        "source": "OTTOFLOW_ORIGIN_CLASSIFIER",
        "sourceRef": FULFILLMENT_CLASSIFIER_REFERENCE,
    }


def default_fulfillment_reference() -> dict[str, str]:
    return {
        "system": "OTTOFLOW",
        "method": "COPIED_ORIGIN_CLASSIFIER",
        "resource": "RESTART_DEFAULTS:ORIGIN_CLASSIFIER_V1",
    }

import logging
import os
import re
from typing import Any, List, Dict
from sqlalchemy.orm import Session

DEFAULT_STOCK_QUANTITY = int(os.getenv("DEFAULT_STOCK_QUANTITY", "20"))


def extract_stock_from_aftercool_item(item_or_row: Any) -> int | None:
    """
    Extracts numerical stock/quantity (Menge/Stock/Quantity) from an Aftercool product or row.
    Returns integer >= 0 or None if not determinable.
    """
    if not isinstance(item_or_row, dict):
        return None
    row = item_or_row.get("row") if isinstance(item_or_row.get("row"), dict) else {}
    raw_val = (
        item_or_row.get("Menge")
        or row.get("Menge")
        or item_or_row.get("stock")
        or item_or_row.get("quantity")
        or row.get("Stock")
        or row.get("Quantity")
    )
    if raw_val is not None:
        clean = str(raw_val).strip()
        if clean != "":
            try:
                val = int(clean)
                if val >= 0:
                    return val
            except (ValueError, TypeError):
                pass
    return None

OTTO_SHIPPING_PROFILES = {
    "XL": {
        "EUROPE_4_8_WEEKS": "d045a64d-59e6-57fb-9f69-6370dc7c9d40",
        "CHINA_8_12_WEEKS": "027a1a46-cf60-56f4-917b-81661569f016",
    },
    "JV": {
        "EUROPE_4_8_WEEKS": "786c6468-3baf-52e0-88b5-13757eb7f873",
        "CHINA_8_12_WEEKS": "935a75b0-ac88-55a8-98df-8556306f1386",
    }
}

# ---------------------------------------------------------------------------
# Country-of-origin keyword lists
# ---------------------------------------------------------------------------
_CHINA_KEYWORDS = [
    "china", "chinesisch", "vr china", "volksrepublik china", "prc",
    "(cn)", "made in china", "hergestellt in china", "herkunft: china",
    "herkunft china",
]
_CHINA_DELIVERY_KEYWORDS = [
    "8-12 wochen", "8 - 12 wochen", "8 bis 12 wochen", "10-12 wochen",
]

_NON_CHINA_COUNTRY_KEYWORDS = [
    "türkei", "turkey", "polen", "poland", "deutschland", "germany",
    "italien", "italy", "spanien", "spain", "europa",
    "made in eu", "made in germany", "made in türkei", "made in turkey",
]
# Turkish cities used as origin markers (e.g. INE = Inegöl)
_TURKISH_CITY_KEYWORDS = [
    "ine", "inegöl", "inegol", "istanbul", "kayseri", "bursa", "tr",
]
_NON_CHINA_DELIVERY_KEYWORDS = [
    "4-8 wochen", "4 - 8 wochen", "4 bis 8 wochen",
    "2-4 wochen", "sofort lieferbar",
]

# Countries and regions that are definitely NOT China (for Ursprungsland / Herkunftsland)
_NON_CHINA_COUNTRIES = {
    "türkei", "turkey", "deutschland", "germany", "polen", "poland",
    "italien", "italy", "spanien", "spain", "frankreich", "france",
    "österreich", "austria", "belgien", "belgium", "niederlande",
    "netherlands", "portugal", "griechenland", "greece", "rumänien",
    "romania", "bulgarien", "bulgaria", "tschechien", "czech republic",
    "ungarn", "hungary", "eu", "europa", "europe",
    "ine", "inegöl", "inegol", "bursa", "tr",
}


def is_chinese_product(
    factory_name: str = "",
    factory_id: str = "",
    image_urls: list[str] | None = None,
    product_name: str = "",
    kurzbeschreibung: str = "",
    attributes: dict[str, str] | None = None,
) -> bool:
    """
    Determines if a product originates from China using a cascading priority
    algorithm.  Returns True → China (8-12 weeks, ~56 days), False → Europe / Turkey (4-8 weeks, ~37 days).

    Priority order:
      1. kurzbeschreibung (short description from Afterbuy)
      2. Ursprungsland / Herkunftsland / Herstellungsland attribute from DB
      3. Image URL markers
      4. Factory / catalog name
      5. Product name
      Default → False (Europe / Germany / Turkey: 4-8 weeks, ~37 days)
    """
    # ------------------------------------------------------------------
    # Priority 1: kurzbeschreibung (Afterbuy short description)
    # ------------------------------------------------------------------
    kb_lower = (kurzbeschreibung or "").lower().strip()
    if kb_lower:
        # Check for explicit NON-China markers first (higher confidence)
        for kw in _NON_CHINA_COUNTRY_KEYWORDS:
            if kw in kb_lower:
                return False
        # Turkish city / country markers (e.g. "INE" = İnegöl, "TR")
        for city in _TURKISH_CITY_KEYWORDS:
            if re.search(r'\b' + re.escape(city) + r'\b', kb_lower):
                return False
        for kw in _NON_CHINA_DELIVERY_KEYWORDS:
            if kw in kb_lower:
                return False
        # Now check for China markers
        for kw in _CHINA_KEYWORDS:
            if kw in kb_lower:
                return True
        for kw in _CHINA_DELIVERY_KEYWORDS:
            if kw in kb_lower:
                return True

    # ------------------------------------------------------------------
    # Priority 2: Ursprungsland / Herkunftsland / Herstellungsland attribute from DB
    # ------------------------------------------------------------------
    if attributes:
        origin = (
            attributes.get("Ursprungsland")
            or attributes.get("ursprungsland")
            or attributes.get("Herkunftsland")
            or attributes.get("herkunftsland")
            or attributes.get("Herstellungsland")
            or attributes.get("herstellungsland")
            or attributes.get("Produktionsland")
            or attributes.get("produktionsland")
            or attributes.get("Herstellerland")
            or attributes.get("herstellerland")
            or ""
        )
        origin_lower = origin.lower().strip()
        if origin_lower:
            if "china" in origin_lower or origin_lower in ("cn", "prc"):
                return True
            for city in _TURKISH_CITY_KEYWORDS:
                if re.search(r'\b' + re.escape(city) + r'\b', origin_lower):
                    return False
            for c in _NON_CHINA_COUNTRY_KEYWORDS:
                if c in origin_lower:
                    return False
            if origin_lower in _NON_CHINA_COUNTRIES:
                return False

    # ------------------------------------------------------------------
    # Priority 3: Image URL markers
    # ------------------------------------------------------------------
    if image_urls:
        for url in image_urls:
            u_lower = str(url).lower()
            if any(k in u_lower for k in ["/china/", "/cn/", "_cn_", "chinese", "lieferzeit-8-12"]):
                return True

    # ------------------------------------------------------------------
    # Priority 4: Factory / catalog name
    # ------------------------------------------------------------------
    f_lower = (factory_name or "").lower().strip()
    if f_lower:
        for city in _TURKISH_CITY_KEYWORDS:
            if re.search(r'\b' + re.escape(city) + r'\b', f_lower):
                return False
        for c in _NON_CHINA_COUNTRY_KEYWORDS:
            if c in f_lower:
                return False
        if any(k in f_lower for k in ["china", "_cn", "/cn", "(cn)", " cn ", "chinesisch"]):
            return True

    # ------------------------------------------------------------------
    # Priority 5: Product name
    # ------------------------------------------------------------------
    p_lower = (product_name or "").lower().strip()
    if p_lower:
        for city in _TURKISH_CITY_KEYWORDS:
            if re.search(r'\b' + re.escape(city) + r'\b', p_lower):
                return False
        for c in _NON_CHINA_COUNTRY_KEYWORDS:
            if c in p_lower:
                return False
        if any(k in p_lower for k in ["china", "(cn)", "chinesisch"]):
            return True

    # Default to Europe / Germany / Turkey (4-8 weeks, 37 days)
    return False


def resolve_shipping_profile_id(account_name: str, is_china: bool) -> str:
    acc = str(account_name).upper()
    profiles = OTTO_SHIPPING_PROFILES.get(acc, OTTO_SHIPPING_PROFILES["JV"])
    return profiles["CHINA_8_12_WEEKS"] if is_china else profiles["EUROPE_4_8_WEEKS"]


def extract_unknown_skus(res: dict[str, Any], candidate_skus: list[str]) -> list[str]:
    """
    Analyzes OTTO Availability API response (quantities or delivery)
    and extracts SKUs that OTTO reports as unindexed / unknown.
    """
    status_code = res.get("status_code", 0)
    if status_code not in (400, 422):
        return []

    import json
    import re
    try:
        raw_text = json.dumps(res.get("response") or res.get("error") or "")
    except Exception:
        raw_text = str(res)
    text_lower = raw_text.lower()

    # Markers of unindexed / unknown SKU in Otto
    is_unknown_err = any(k in text_lower for k in [
        "sku is unknown",
        "is unknown",
        "unknown sku",
        "unknown for sku",
        "sku not found",
    ])
    if not is_unknown_err:
        return []

    found = []
    # 1. Direct match with candidates in response string
    for sku in candidate_skus:
        if str(sku) in raw_text or str(sku).lower() in text_lower:
            found.append(str(sku))

    # 2. Regex search for SKU tokens if direct match didn't find all
    if not found:
        tokens = re.findall(r'(?:sku[:\s=]+|\b)([0-9]{8,14})\b', raw_text, re.IGNORECASE)
        for t in tokens:
            if t in candidate_skus and t not in found:
                found.append(t)

    # 3. If marker is definitely present, but no specific SKU was extracted:
    # If candidate_skus has 1 item, it is definitely that item.
    # If candidate_skus has multiple, all are affected.
    if not found and is_unknown_err:
        found = list(candidate_skus)

    return found


def apply_otto_availability(
    client: Any,
    items: list[Any],
    account_name: str,
    db: Session,
    plan: Any = None,
    aftercool_product_map: dict[str, dict] | None = None,
    target_quantity_skus: list[str] | None = None,
    target_delivery_skus: list[str] | None = None,
    quantities_map: dict[str, int] | None = None,
) -> dict[str, Any]:
    """
    Updates available quantity (Stock from quantities_map/Aftercool or DEFAULT_STOCK_QUANTITY)
    and links the appropriate Shipping Profile (4-8 weeks vs 8-12 weeks) via Otto Availability API.

    Supports selective updates:
      - target_quantity_skus: if provided, only update stock for these SKUs.
        If empty list [], quantities update is skipped (already succeeded).
      - target_delivery_skus: if provided, only update shipping profile for these SKUs.
        If empty list [], delivery update is skipped (already succeeded).
      - quantities_map: optional mapping {ean: quantity}. If not provided or SKU missing,
        checks aftercool_product_map or falls back to DEFAULT_STOCK_QUANTITY.

    Returns a structured dictionary with per-operation results and unknown SKU breakdown:
      {
          "status": "success" | "failed",
          "success": bool,
          "unknown_skus": list[str],
          "quantity_failed_skus": list[str],
          "delivery_failed_skus": list[str],
          "quantities_res": dict,
          "delivery_res": dict,
          "error": str | None,
      }
    """
    if not items:
        return {
            "status": "empty",
            "success": True,
            "unknown_skus": [],
            "quantity_failed_skus": [],
            "delivery_failed_skus": [],
            "quantities_res": {},
            "delivery_res": {},
            "error": None,
        }

    all_eans = []
    for it in items:
        ean = getattr(it, "product_ean", None) or (it.get("product_ean") if isinstance(it, dict) else None)
        if ean:
            all_eans.append(str(ean).strip())

    if not all_eans:
        return {
            "status": "no_eans",
            "success": True,
            "unknown_skus": [],
            "quantity_failed_skus": [],
            "delivery_failed_skus": [],
            "quantities_res": {},
            "delivery_res": {},
            "error": None,
        }

    # ------------------------------------------------------------------
    # 1. Update Quantities (Dynamic Stock or DEFAULT_STOCK_QUANTITY)
    # ------------------------------------------------------------------
    qty_unknown_skus: list[str] = []
    qty_other_failed: list[str] = []
    qty_res: dict[str, Any] = {"status_code": 200, "skipped": True}

    if target_quantity_skus is not None:
        remaining_qty_skus = [ean for ean in all_eans if ean in target_quantity_skus]
    else:
        remaining_qty_skus = list(all_eans)

    if remaining_qty_skus:
        # Loop up to 3 times to isolate unknown SKUs if part of a batch was rejected
        for _ in range(3):
            if not remaining_qty_skus:
                break
            qty_payload = []
            for ean in remaining_qty_skus:
                qty = None
                if quantities_map and ean in quantities_map:
                    qty = quantities_map[ean]
                elif aftercool_product_map and ean in aftercool_product_map:
                    qty = extract_stock_from_aftercool_item(aftercool_product_map[ean])
                if qty is None or qty < 0:
                    qty = DEFAULT_STOCK_QUANTITY
                qty_payload.append({"sku": ean, "quantity": qty})
            qty_res = client.update_quantities(qty_payload)
            logging.info(
                f"[Otto Availability] Quantities update for {len(remaining_qty_skus)} items "
                f"({account_name}): status {qty_res.get('status_code')}"
            )

            status_code = qty_res.get("status_code", 0)
            if status_code in (200, 204):
                break

            unknowns = extract_unknown_skus(qty_res, remaining_qty_skus)
            if unknowns:
                qty_unknown_skus.extend(unknowns)
                remaining_qty_skus = [s for s in remaining_qty_skus if s not in qty_unknown_skus]
            else:
                qty_other_failed.extend(remaining_qty_skus)
                break

    # ------------------------------------------------------------------
    # 2. Assign Shipping Profiles
    # ------------------------------------------------------------------
    deliv_unknown_skus: list[str] = []
    deliv_other_failed: list[str] = []
    deliv_res: dict[str, Any] = {"status_code": 200, "skipped": True}

    if target_delivery_skus is not None:
        target_deliv_set = set(target_delivery_skus)
        delivery_eans = [ean for ean in all_eans if ean in target_deliv_set]
    else:
        delivery_eans = list(all_eans)

    if delivery_eans:
        import models
        acc_enum = models.AccountType(account_name) if hasattr(models.AccountType, account_name) else account_name
        mappings = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.product_ean.in_(delivery_eans),
                models.ProductMapping.account_name == acc_enum
            )
            .all()
        )
        mapping_by_ean = {m.product_ean: m for m in mappings}

        # Load Ursprungsland / Herkunftsland attributes
        origin_attrs_by_ean: dict[str, dict[str, str]] = {}
        try:
            origin_attributes = (
                db.query(models.ProductAttribute)
                .filter(
                    models.ProductAttribute.product_ean.in_(delivery_eans),
                    models.ProductAttribute.account_name == acc_enum,
                    models.ProductAttribute.attribute_name.in_([
                        "Ursprungsland", "ursprungsland",
                        "Herkunftsland", "herkunftsland",
                        "Herstellungsland", "herstellungsland",
                        "Produktionsland", "produktionsland",
                        "Herstellerland", "herstellerland",
                    ]),
                )
                .all()
            )
            for attr in origin_attributes:
                origin_attrs_by_ean.setdefault(attr.product_ean, {})[attr.attribute_name] = attr.attribute_value
        except Exception as e:
            logging.warning(f"[Otto Availability] Failed to load origin attributes: {e}")

        af_map = (aftercool_product_map or {}).copy()
        if not af_map and plan and getattr(plan, "factory_id", None):
            try:
                import json
                from routers.aftercool import redis_client
                if redis_client:
                    for k_candidate in (
                        f"aftercool_products_{plan.factory_id}_10000_1",
                        f"aftercool_products_{plan.factory_id}_1000_1",
                        f"aftercool_products_{plan.factory_id}_5000_1",
                    ):
                        cached_data = redis_client.get(k_candidate)
                        if cached_data:
                            raw_prods = json.loads(cached_data) if isinstance(cached_data, str) else cached_data
                            if isinstance(raw_prods, str):
                                raw_prods = json.loads(raw_prods)
                            for p in (raw_prods if isinstance(raw_prods, list) else []):
                                ean_p = str(p.get("ean") or p.get("EAN") or "").strip()
                                row_p = p.get("row") or {}
                                kurz_p = (
                                    row_p.get("Kurzbeschreibung")
                                    or row_p.get("kurzbeschreibung")
                                    or row_p.get("ShortDescription")
                                    or p.get("kurzbeschreibung")
                                    or ""
                                )
                                if ean_p and kurz_p:
                                    af_map[ean_p] = {"kurzbeschreibung": str(kurz_p).strip()}
                            if af_map:
                                break
            except Exception as cache_err:
                logging.debug(f"[Otto Availability] Redis cache lookup error: {cache_err}")

        remaining_deliv_payload = []
        for it in items:
            ean = getattr(it, "product_ean", None) or (it.get("product_ean") if isinstance(it, dict) else None)
            if not ean or str(ean).strip() not in delivery_eans:
                continue
            ean = str(ean).strip()
            m = mapping_by_ean.get(ean)
            img_urls = [m.image_url] if (m and m.image_url) else []
            prod_name = m.product_name if m else ""
            fact_name = getattr(plan, "factory_name", "") or ""
            fact_id = getattr(plan, "factory_id", "") or (m.factory_id if m else "")

            kurz = af_map.get(ean, {}).get("kurzbeschreibung", "")
            ean_attrs = origin_attrs_by_ean.get(ean, {})

            is_china = is_chinese_product(
                factory_name=fact_name,
                factory_id=fact_id,
                image_urls=img_urls,
                product_name=prod_name,
                kurzbeschreibung=kurz,
                attributes=ean_attrs,
            )
            profile_id = resolve_shipping_profile_id(account_name, is_china)
            processing_time = 56 if is_china else 37
            remaining_deliv_payload.append({
                "sku": ean,
                "shippingProfileId": profile_id,
                "processingTime": str(processing_time)
            })

        # Loop up to 3 times to isolate unknown SKUs if part of a batch was rejected
        for _ in range(3):
            if not remaining_deliv_payload:
                break
            deliv_res = client.update_product_delivery_information(remaining_deliv_payload)
            logging.info(
                f"[Otto Availability] Delivery info update for {len(remaining_deliv_payload)} items "
                f"({account_name}): status {deliv_res.get('status_code')}"
            )

            status_code = deliv_res.get("status_code", 0)
            if status_code in (200, 201, 202, 204):
                break

            unknowns = extract_unknown_skus(deliv_res, [p["sku"] for p in remaining_deliv_payload])
            if unknowns:
                deliv_unknown_skus.extend(unknowns)
                remaining_deliv_payload = [p for p in remaining_deliv_payload if p["sku"] not in deliv_unknown_skus]
            else:
                deliv_other_failed.extend([p["sku"] for p in remaining_deliv_payload])
                break

    # ------------------------------------------------------------------
    # 3. Aggregate Results
    # ------------------------------------------------------------------
    all_unknown_skus = sorted(list(set(qty_unknown_skus + deliv_unknown_skus)))
    all_qty_failed = sorted(list(set(qty_unknown_skus + qty_other_failed)))
    all_deliv_failed = sorted(list(set(deliv_unknown_skus + deliv_other_failed)))
    is_success = (len(all_qty_failed) == 0 and len(all_deliv_failed) == 0)

    error_msg = None
    if not is_success:
        parts = []
        if all_unknown_skus:
            parts.append(f"SKUs unknown in OTTO Availability: {all_unknown_skus}")
        if qty_other_failed:
            parts.append(f"Stock error: {qty_res.get('error') or qty_res.get('response')}")
        if deliv_other_failed:
            parts.append(f"Delivery error: {deliv_res.get('error') or deliv_res.get('response')}")
        error_msg = "; ".join(parts)

    return {
        "status": "success" if is_success else "failed",
        "success": is_success,
        "unknown_skus": all_unknown_skus,
        "quantity_failed_skus": all_qty_failed,
        "delivery_failed_skus": all_deliv_failed,
        "quantities_res": qty_res,
        "delivery_res": deliv_res,
        "error": error_msg,
    }

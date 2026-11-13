"""Pure, account-local normalization of already-fetched OTTO price snapshots."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping


def is_missing_otto_price_error(error: Any) -> bool:
    """Whether an exact-SKU price read failed because OTTO has no price record."""
    return (
        getattr(error, "code", None) == "MISSING_STANDARD_PRICE"
        or getattr(error, "status_code", None) == 404
    )


def _matches_identity(value: Mapping[str, Any], expected_sku: str, expected_ean: str | None) -> bool:
    if str(value.get("sku") or "") != expected_sku:
        return False
    returned_ean = value.get("ean")
    return not (expected_ean and returned_ean not in (None, "") and str(returned_ean) != expected_ean)


def normalize_otto_product_variation(
    payload: Any, *, expected_sku: str, expected_ean: str | None = None,
) -> dict[str, Any] | None:
    """Return an exact SKU/EAN product object, unwrapping a V5 list response if needed."""
    if not isinstance(payload, Mapping):
        return None
    variations = payload.get("productVariations")
    if isinstance(variations, list):
        matches = [item for item in variations if isinstance(item, Mapping)
                   and _matches_identity(item, expected_sku, expected_ean)]
        if len(matches) == 1:
            # Parent-level price data is not variant evidence. If the matched
            # SKU has no own standardPrice, leave it absent so the caller uses
            # the exact SKU's /prices endpoint rather than inheriting another
            # sibling's or aggregate product price.
            normalized = {
                key: value for key, value in payload.items()
                if key not in {"productVariations", "pricing", "standardPrice", "salePrice", "msrp", "prices"}
            }
            normalized.update(dict(matches[0]))
            normalized["_ottoOriginalProductResponse"] = dict(payload)
            return normalized
        if len(matches) > 1:
            return None
    if _matches_identity(payload, expected_sku, expected_ean):
        return dict(payload)
    return None


def _candidates(payload: Any, *, expected_sku: str, expected_ean: str | None, path: str,
                from_product: bool) -> tuple[list[tuple[Mapping[str, Any], str]], bool]:
    if not isinstance(payload, Mapping):
        return [], False
    found: list[tuple[Mapping[str, Any], str]] = []
    identity_mismatch = False
    has_variation_list = False

    def add(node: Any, node_path: str) -> None:
        nonlocal identity_mismatch
        if not isinstance(node, Mapping):
            return
        has_price_container = (
            isinstance(node.get("pricing"), Mapping)
            or isinstance(node.get("standardPrice"), Mapping)
        )
        if not has_price_container:
            return
        if not _matches_identity(node, expected_sku, expected_ean):
            identity_mismatch = True
            return
        pricing = node.get("pricing") if isinstance(node.get("pricing"), Mapping) else node
        standard = pricing.get("standardPrice")
        if isinstance(standard, Mapping):
            found.append((standard, f"{node_path}." + ("pricing." if pricing is not node else "") + "standardPrice"))
        else:
            found.append(({}, f"{node_path}." + ("pricing." if pricing is not node else "") + "standardPrice"))

    for list_key in ("productVariations", "variationPrices"):
        rows = payload.get(list_key)
        if isinstance(rows, list):
            has_variation_list = True
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping) or not _matches_identity(row, expected_sku, expected_ean):
                    continue
                nested_path = f"{path}.{list_key}[{index}]"
                prior_count = len(found)
                add(row, nested_path)
                if len(found) == prior_count:
                    # An exact variation with an incomplete pricing object
                    # blocks inheritance from any parent-level price.
                    found.append(({}, f"{nested_path}.pricing.standardPrice"))
    if found:
        return found, False
    if has_variation_list:
        return [], True
    add(payload, path)
    return found, identity_mismatch


def _evidence(standard: Mapping[str, Any], *, expected_sku: str, expected_ean: str | None,
              path: str, source: str) -> dict[str, Any]:
    raw_amount = standard.get("amount")
    currency = standard.get("currency")
    evidence: dict[str, Any] = {
        "source": "OTTO",
        "status": "MISSING_AMOUNT" if "amount" not in standard else "AMOUNT_NULL" if raw_amount is None else "INVALID_AMOUNT",
        "sku": expected_sku,
        "ean": expected_ean,
        "amount": None,
        "amountCents": None,
        "currency": currency if isinstance(currency, str) else None,
        "path": path,
        "snapshotSource": source,
    }
    if "amount" not in standard or raw_amount is None:
        return evidence
    if isinstance(raw_amount, bool):
        evidence["status"] = "INVALID_AMOUNT"
        return evidence
    try:
        amount = Decimal(str(raw_amount).strip())
    except (InvalidOperation, ValueError, AttributeError):
        evidence["status"] = "INVALID_AMOUNT"
        return evidence
    if not amount.is_finite() or amount < 0:
        evidence["status"] = "INVALID_AMOUNT"
        return evidence
    evidence["amount"] = format(amount, "f")
    if not isinstance(currency, str) or not currency.strip():
        evidence["status"] = "CURRENCY_MISSING"
        return evidence
    if currency.strip().upper() != "EUR":
        evidence["status"] = "UNSUPPORTED_CURRENCY"
        return evidence
    cents = int((amount * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    evidence["amountCents"] = cents
    evidence["currency"] = "EUR"
    evidence["status"] = "ZERO" if amount == 0 else "CONFIRMED" if cents > 0 else "AMOUNT_ROUNDS_TO_ZERO"
    return evidence


def _select(candidates: list[tuple[Mapping[str, Any], str]], *, expected_sku: str,
            expected_ean: str | None, source: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    if len(candidates) > 1:
        first = candidates[0][0]
        if any(dict(standard) != dict(first) for standard, _ in candidates[1:]):
            return {
                "source": "OTTO", "status": "AMBIGUOUS_VARIATION_PRICE", "sku": expected_sku,
                "ean": expected_ean, "amount": None, "amountCents": None, "currency": None,
                "path": candidates[0][1], "snapshotSource": source,
            }
    standard, path = candidates[0]
    return _evidence(standard, expected_sku=expected_sku, expected_ean=expected_ean, path=path, source=source)


def resolve_otto_standard_price(
    otto_product: Any,
    price_response: Any,
    *,
    expected_sku: str,
    expected_ean: str | None = None,
) -> dict[str, Any]:
    """Resolve standardPrice without confusing variant, SKU, currency, or MSRP.

    A matching `/prices` response has priority. A product response is used when
    the separate response has no usable standardPrice node. A matching but
    invalid or non-EUR `/prices` value remains an explicit error and is not
    hidden by another field. Numeric zero remains distinct from null/missing.
    """
    endpoint_candidates, endpoint_mismatch = _candidates(
        price_response, expected_sku=expected_sku, expected_ean=expected_ean,
        path="priceResponse", from_product=False,
    )
    endpoint = _select(endpoint_candidates, expected_sku=expected_sku,
                       expected_ean=expected_ean, source="GET /v5/products/{sku}/prices")
    if endpoint and endpoint.get("status") not in {"MISSING_AMOUNT", "AMOUNT_NULL", "CURRENCY_MISSING"}:
        canonical = None
        if endpoint.get("status") in {"CONFIRMED", "ZERO"}:
            # Preserve the exact endpoint body when available.
            canonical = dict(price_response) if isinstance(price_response, Mapping) else None
        return {"evidence": endpoint, "response": canonical}

    product_candidates, product_mismatch = _candidates(
        otto_product, expected_sku=expected_sku, expected_ean=expected_ean,
        path="productResponse", from_product=True,
    )
    product = _select(product_candidates, expected_sku=expected_sku,
                       expected_ean=expected_ean, source="GET /v5/products/{sku}")
    if product and product.get("status") in {"CONFIRMED", "ZERO"}:
        variants, _ = _candidates(otto_product, expected_sku=expected_sku,
                                  expected_ean=expected_ean, path="productResponse", from_product=True)
        standard, path = variants[0]
        canonical = {
            "sku": expected_sku,
            "standardPrice": dict(standard),
            "_ottoSourcePath": path,
            "_ottoSnapshotSource": "GET /v5/products/{sku}",
        }
        if endpoint:
            product["fallbackFrom"] = endpoint.get("status")
        return {"evidence": product, "response": canonical}

    selected = endpoint or product
    if selected:
        return {"evidence": selected, "response": None}
    status = "SKU_OR_EAN_MISMATCH" if endpoint_mismatch or product_mismatch else "MISSING"
    return {
        "evidence": {
            "source": "OTTO", "status": status, "sku": expected_sku, "ean": expected_ean,
            "amount": None, "amountCents": None, "currency": None, "path": None,
            "snapshotSource": None,
        },
        "response": None,
    }


def has_product_standard_price(otto_product: Any, *, expected_sku: str, expected_ean: str | None = None) -> bool:
    """Whether an exact-identity product response already contains a usable EUR amount."""
    result = resolve_otto_standard_price(
        otto_product, None, expected_sku=expected_sku, expected_ean=expected_ean
    )
    return result["evidence"].get("status") in {"CONFIRMED", "ZERO"}

"""Account-scoped production catalog reads over persisted candidate episodes."""

from __future__ import annotations

import math
import json
import threading
import time
from functools import lru_cache
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from schemas.discovery import ProductCatalogMetricsDTO, ProductListResponseDTO, ProductPageDTO, ProductReadDTO
from services.preparation_service import PreparationError, calculate_price_from_cents, price_snapshot_from_sources
from services.otto_price_evidence import resolve_otto_standard_price


_PRODUCT_FROM = """
    FROM candidate_episodes ce
    JOIN source_products sp
      ON sp.id = ce.source_product_id
     AND sp.account = ce.account
     AND sp.data_scope = ce.data_scope
    LEFT JOIN restart_operations op
      ON op.account = ce.account
     AND op.data_scope = 'PRODUCTION'
     AND op.candidate_episode_id = ce.id
"""

_PRODUCT_SELECT = """
    SELECT ce.id::text AS id,
           ce.id::text AS candidate_episode_id,
           sp.id::text AS source_product_id,
           ce.account,
           ce.data_scope,
           sp.source_type,
           sp.aftercool_product_id AS source_aftercool_product_id,
           sp.factory_id,
           sp.factory_name,
           ce.observed_sku AS sku,
           ce.confirmed_ean AS ean,
           ce.moin,
           ce.period,
           COALESCE(op.status, CASE WHEN ce.episode_state = 'OPEN' THEN ce.period ELSE 'CLOSED' END) AS status,
           ce.classification,
           ce.origin,
           ce.episode_state,
           op.id::text AS operation_id,
           op.status AS operation_status,
           op.version AS operation_version,
           op.current_revision_number,
           op.target_sku,
           op.new_ean,
           COALESCE(NULLIF(rev.content_json->>'title', ''), sp.original_title) AS title,
           sp.original_title AS source_title,
           edit.title AS draft_title,
           NULLIF(rev.content_json->>'title', '') AS prepared_title,
           CASE WHEN edit.version IS NULL THEN NULL ELSE jsonb_build_object(
               'title', edit.title, 'category', edit.category,
               'attributes', edit.attributes_json, 'version', edit.version
           ) END AS saved_edit,
           CASE
             WHEN (rev.price_snapshot_json->>'targetPriceCents') ~ '^-?[0-9]+$'
             THEN (rev.price_snapshot_json->>'targetPriceCents')::bigint
             WHEN (sp.attributes_json->'syncSnapshot'->>'targetPriceCents') ~ '^-?[0-9]+$'
             THEN (sp.attributes_json->'syncSnapshot'->>'targetPriceCents')::bigint
             ELSE sp.original_price_cents
           END AS price_cents,
           COALESCE(NULLIF(rev.content_json->>'category', ''), sp.category) AS category,
           COALESCE(
             CASE WHEN jsonb_typeof(rev.content_json->'images') = 'array'
                  THEN rev.content_json->'images'->0->>'url' END,
             sp.image_url
           ) AS image_url,
           CASE WHEN jsonb_typeof(rev.content_json->'attributes') = 'array'
                THEN rev.content_json->'attributes'
                WHEN jsonb_typeof(sp.attributes_json->'attributes') = 'array'
                THEN sp.attributes_json->'attributes'
                WHEN jsonb_typeof(sp.attributes_json->'ottoProductData'->'productDescription'->'attributes') = 'array'
                THEN sp.attributes_json->'ottoProductData'->'productDescription'->'attributes'
                ELSE '[]'::jsonb END AS attributes,
           CASE WHEN jsonb_typeof(sp.attributes_json) = 'object'
                THEN sp.attributes_json ELSE '{}'::jsonb END AS source_data,
           COALESCE(obs.errors_json, '[]'::jsonb) AS errors,
           COALESCE(obs.error_codes_json, '[]'::jsonb) AS error_codes,
           COALESCE(obs.error_titles_json, '[]'::jsonb) AS error_titles,
           ce.last_modified,
           ce.last_detected_at AS last_checked_at
"""

_PRODUCT_REMAINING_JOINS = """
    LEFT JOIN restart_product_edits edit
      ON edit.account = ce.account AND edit.candidate_episode_id = ce.id
     AND edit.data_scope = ce.data_scope
    LEFT JOIN operation_revisions rev
      ON rev.operation_id = op.id
     AND rev.account = ce.account
     AND rev.data_scope = 'PRODUCTION'
     AND rev.revision_number = op.current_revision_number
    LEFT JOIN otto_status_observations obs
      ON obs.id = ce.last_observation_id
     AND obs.account = ce.account
     AND obs.data_scope = ce.data_scope
"""

@lru_cache(maxsize=2048)
def _required_attributes_for_group(group_id: str) -> tuple[str, ...] | None:
    """Resolve HIGH fields once per shared OTTO taxonomy group."""
    from services.categories_repo import get_category_group, is_required_attribute

    group = get_category_group(group_id)
    if group is None:
        return None
    raw_attributes = group.get("attributes", {}).get("attributes", [])
    required = [
        str(attribute.get("name") or "").strip().lower()
        for attribute in raw_attributes
        if isinstance(attribute, Mapping) and is_required_attribute(attribute)
        and str(attribute.get("name") or "").strip()
    ]
    # OttoFlow treats Marke as required even where a taxonomy group omits it;
    # get_category_schema_attributes adds it as a universal schema field.
    if "marke" not in required:
        required.append("marke")
    return tuple(dict.fromkeys(required))


def _required_attributes_for_category(category: str) -> tuple[str, ...] | None:
    from services.categories_repo import get_category

    category_data = get_category(category)
    if category_data is None or not category_data.get("category_group_id"):
        return None
    return _required_attributes_for_group(str(category_data["category_group_id"]))


@lru_cache(maxsize=128)
def _card_schemas_for_categories(categories: tuple[str, ...]) -> str:
    schemas = {}
    for category in categories:
        required = _required_attributes_for_category(category)
        if required is not None:
            schemas[category] = required
    return json.dumps(schemas, ensure_ascii=False, separators=(",", ":"))


def _card_schemas(conn: Connection, *, account: str) -> str:
    """Build a small schema map for categories present in this account's catalog.

    Serializing the complete 14k-category taxonomy for every catalog request was
    several megabytes and made cold requests exceed the API timeout. The status
    SQL only needs definitions for categories currently stored in this account.
    """
    category_rows = conn.execute(text(
        "SELECT DISTINCT lower(btrim(category)) AS category FROM source_products "
        "WHERE account=:account AND data_scope='PRODUCTION' AND NULLIF(btrim(category), '') IS NOT NULL "
        "UNION "
        "SELECT DISTINCT lower(btrim(category)) AS category FROM restart_product_edits "
        "WHERE account=:account AND data_scope='PRODUCTION' AND NULLIF(btrim(category), '') IS NOT NULL "
        "UNION "
        "SELECT DISTINCT lower(btrim(content_json->>'category')) AS category FROM operation_revisions "
        "WHERE account=:account AND data_scope='PRODUCTION' AND NULLIF(btrim(content_json->>'category'), '') IS NOT NULL"
    ), {"account": account}).scalars().all()
    categories = tuple(sorted({str(value).strip().lower() for value in category_rows if str(value or "").strip()}))
    return _card_schemas_for_categories(categories)

_PRODUCT_STATUS_LATERAL_JOINS = """
    LEFT JOIN LATERAL (
      SELECT COALESCE(jsonb_object_agg(lower(btrim(a->>'name')), COALESCE(a->'value',a->'values','null'::jsonb))
             FILTER (WHERE NULLIF(a->>'name','') IS NOT NULL), '{}'::jsonb)
             || CASE WHEN jsonb_typeof(edit.attributes_json)='object' THEN COALESCE((SELECT jsonb_object_agg(lower(btrim(e.key)),e.value) FROM jsonb_each(edit.attributes_json) e),'{}'::jsonb) ELSE '{}'::jsonb END AS attrs
      FROM jsonb_array_elements(CASE
        WHEN jsonb_typeof(rev.content_json->'attributes')='array' THEN rev.content_json->'attributes'
        WHEN jsonb_typeof(sp.attributes_json->'attributes')='array' THEN sp.attributes_json->'attributes'
        WHEN jsonb_typeof(sp.attributes_json->'ottoProductData'->'productDescription'->'attributes')='array' THEN sp.attributes_json->'ottoProductData'->'productDescription'->'attributes'
        ELSE '[]'::jsonb END) a
    ) card_attrs ON TRUE
    LEFT JOIN LATERAL (
      SELECT CASE
        WHEN op.status IN ('AWAITING_ONLINE','PUBLISHED') THEN 'PUBLISHED'
        WHEN (SELECT i.status FROM restart_items i WHERE i.operation_id=op.id AND i.account=ce.account AND i.data_scope='PRODUCTION' ORDER BY i.id DESC LIMIT 1) IN ('FAILED','NEEDS_REVIEW') THEN 'ERROR'
        WHEN btrim(COALESCE(edit.category, NULLIF(rev.content_json->>'category',''), sp.category,''))='' THEN 'NO_CATEGORY'
        WHEN NOT EXISTS (SELECT 1 FROM jsonb_each(card_attrs.attrs) v WHERE v.value NOT IN ('null'::jsonb,'[]'::jsonb,'{}'::jsonb,'""'::jsonb) AND btrim(v.value #>> '{}') <> '') THEN 'NO_ATTRIBUTES'
        WHEN NOT (CAST(:card_schemas AS jsonb) ? lower(btrim(COALESCE(edit.category, NULLIF(rev.content_json->>'category',''), sp.category,'')))) THEN 'UNKNOWN'
        WHEN EXISTS (SELECT 1 FROM jsonb_array_elements_text(CAST(:card_schemas AS jsonb)->lower(btrim(COALESCE(edit.category, NULLIF(rev.content_json->>'category',''), sp.category,'')))) r(name)
          WHERE NOT (card_attrs.attrs ? r.name) OR card_attrs.attrs->r.name IN ('null'::jsonb,'[]'::jsonb,'{}'::jsonb,'""'::jsonb) OR btrim(card_attrs.attrs->>r.name)='') THEN 'ERROR'
        ELSE 'READY' END AS status
    ) card ON TRUE
"""
_PRODUCT_BASE_JOINS = _PRODUCT_REMAINING_JOINS
_PRODUCT_REMAINING_JOINS = _PRODUCT_BASE_JOINS + _PRODUCT_STATUS_LATERAL_JOINS
_METRICS_CACHE_TTL_SECONDS = 10.0
_METRICS_CACHE_LOCK = threading.Lock()
_METRICS_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def invalidate_product_catalog_metrics_cache(account: str | None = None) -> None:
    """Clear short-lived catalog metrics cache after mutations or completed imports."""
    normalized = str(account or "").strip().upper() if account is not None else None
    with _METRICS_CACHE_LOCK:
        if not normalized:
            _METRICS_CACHE.clear()
            return
        for key in [k for k in _METRICS_CACHE if k[0] == normalized]:
            _METRICS_CACHE.pop(key, None)


def _cached_metrics_get(key: tuple[str, str, str]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _METRICS_CACHE_LOCK:
        entry = _METRICS_CACHE.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if now >= expires_at:
            _METRICS_CACHE.pop(key, None)
            return None
        return {
            **payload,
            "statusCounts": dict(payload["statusCounts"]),
            "categories": list(payload["categories"]),
        }


def _cached_metrics_put(key: tuple[str, str, str], payload: dict[str, Any]) -> None:
    snapshot = {
        **payload,
        "statusCounts": dict(payload["statusCounts"]),
        "categories": list(payload["categories"]),
    }
    with _METRICS_CACHE_LOCK:
        if len(_METRICS_CACHE) > 256:
            _METRICS_CACHE.clear()
        _METRICS_CACHE[key] = (time.monotonic() + _METRICS_CACHE_TTL_SECONDS, snapshot)
_STATUS_SQL = "card.status"
_PRODUCT_SELECT = _PRODUCT_SELECT.replace("COALESCE(op.status, CASE WHEN ce.episode_state = 'OPEN' THEN ce.period ELSE 'CLOSED' END) AS status", "card.status AS status")
_TITLE_SQL = "CASE WHEN NULLIF(edit.title, '') IS NOT NULL AND btrim(edit.title) <> btrim(COALESCE(sp.original_title, '')) THEN edit.title ELSE COALESCE(NULLIF(rev.content_json->>'title', ''), sp.original_title) END"
_CATEGORY_SQL = "COALESCE(edit.category, NULLIF(rev.content_json->>'category', ''), sp.category, '')"
_SYNC = "sp.attributes_json->'syncSnapshot'"
_LOOKUP = "sp.attributes_json->'aftercoolLookup'"
_PRICES = "sp.attributes_json->'ottoPricesData'"
_SYNC_OTTO = f"CASE WHEN ({_SYNC}->>'ottoStandardPriceCents') ~ '^[0-9]{{1,12}}$' THEN ({_SYNC}->>'ottoStandardPriceCents')::bigint END"
_SYNC_AFTERCOOL = f"CASE WHEN ({_SYNC}->>'aftercoolStartPriceCents') ~ '^[0-9]{{1,12}}$' THEN ({_SYNC}->>'aftercoolStartPriceCents')::bigint END"
_RAW_OTTO = f"CASE WHEN ({_PRICES}->'standardPrice'->>'amount') ~ '^[0-9]{{1,10}}([.][0-9]{{1,6}})?$' THEN round(({_PRICES}->'standardPrice'->>'amount')::numeric * 100)::bigint END"
_MATCHED_RAW_OTTO = f"CASE WHEN {_PRICES}->>'sku'=ce.observed_sku AND upper({_PRICES}->'standardPrice'->>'currency')='EUR' THEN ({_RAW_OTTO}) END"
_RAW_AFTERCOOL = f"CASE WHEN ({_LOOKUP}->>'price_cents') ~ '^[0-9]{{1,12}}$' THEN ({_LOOKUP}->>'price_cents')::bigint END"
_VERIFIED_AFTERCOOL = f"({_LOOKUP}->>'status'='FOUND' AND {_LOOKUP}->>'verified'='true' AND {_LOOKUP}->>'data_scope'='PRODUCTION' AND {_LOOKUP}->>'account'=ce.account AND {_LOOKUP}->>'ean'=ce.confirmed_ean AND {_LOOKUP}->>'factory_id'=sp.factory_id AND {_LOOKUP}->>'aftercool_product_id'=sp.aftercool_product_id::text)"
_BASE_PRICE_SQL = f"""CASE
    WHEN {_SYNC}->>'sourceType'=sp.source_type AND {_SYNC}->>'sourceSku'=ce.observed_sku
         AND {_SYNC}->>'sourceEan'=ce.confirmed_ean AND sp.source_type='OTTO_DIRECT'
         AND ({_SYNC_OTTO}) > 0
      THEN ({_SYNC_OTTO})
    WHEN {_SYNC}->>'sourceType'=sp.source_type AND {_SYNC}->>'sourceSku'=ce.observed_sku
         AND {_SYNC}->>'sourceEan'=ce.confirmed_ean AND sp.source_type IN ('AFTERBUY','AFTERCOOL')
         AND (COALESCE(({_SYNC_OTTO}),0) > 0 OR COALESCE(({_SYNC_AFTERCOOL}),0) > 0)
      THEN GREATEST(COALESCE(({_SYNC_OTTO}),0), COALESCE(({_SYNC_AFTERCOOL}),0))
    WHEN sp.source_type='OTTO_DIRECT' AND ({_MATCHED_RAW_OTTO}) > 0
      THEN ({_MATCHED_RAW_OTTO})
    WHEN sp.source_type IN ('AFTERBUY','AFTERCOOL') AND {_VERIFIED_AFTERCOOL}
         AND (COALESCE(({_MATCHED_RAW_OTTO}),0) > 0 OR COALESCE(({_RAW_AFTERCOOL}),0) > 0)
      THEN GREATEST(COALESCE(({_MATCHED_RAW_OTTO}),0), COALESCE(({_RAW_AFTERCOOL}),0))
    ELSE NULL END"""
_PRICE_SQL = f"CASE WHEN ({_BASE_PRICE_SQL}) > CASE WHEN ce.account='JV' THEN 1000 ELSE 2000 END THEN ({_BASE_PRICE_SQL}) ELSE NULL END"


class ProductCatalogError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _product_dto(row: Any, *, include_source_data: bool = True) -> ProductReadDTO:
    """Compute a display-only price from account-scoped, identity-verified stored source evidence."""
    data = dict(row)
    source_product_id = data.pop("source_aftercool_product_id", None)
    source = data.get("source_data") if isinstance(data.get("source_data"), dict) else {}
    account, source_type, sku, ean = data["account"], data["source_type"], data["sku"], data["ean"]
    snapshot = source.get("syncSnapshot")
    raw_product = source.get("ottoProductData")
    prices = source.get("ottoPricesData")
    resolved = resolve_otto_standard_price(
        raw_product, prices, expected_sku=str(sku or ""), expected_ean=str(ean or "") or None,
    ) if sku else {"evidence": {"status": "MISSING", "amountCents": None}, "response": None}
    otto_evidence = dict(resolved["evidence"])
    if (isinstance(snapshot, dict) and snapshot.get("sourceType") == source_type
            and snapshot.get("sourceSku") == sku and snapshot.get("sourceEan") == ean
            and snapshot.get("currency") == "EUR"):
        snapshot_cents = snapshot.get("ottoStandardPriceCents")
        if type(snapshot_cents) is int and snapshot_cents >= 0:
            otto_evidence = {
                "source": "OTTO", "status": "ZERO" if snapshot_cents == 0 else "CONFIRMED",
                "sku": sku, "ean": ean, "amountCents": snapshot_cents,
                "amount": format(Decimal(snapshot_cents) / Decimal(100), "f"),
                "currency": "EUR", "path": "syncSnapshot.ottoStandardPriceCents",
                "snapshotSource": "PRODUCT_SYNC", "capturedAt": snapshot.get("capturedAt"),
            }
    lookup = source.get("aftercoolLookup")
    valid_lookup = False
    aftercool_evidence: dict[str, Any] = {"source": "AFTERCOOL", "status": "MISSING", "amountCents": None}
    if isinstance(lookup, dict):
        valid_lookup = (
            source_type in {"AFTERBUY", "AFTERCOOL"}
            and lookup.get("status") == "FOUND" and lookup.get("verified") is True
            and lookup.get("data_scope") == "PRODUCTION" and lookup.get("account") == account
            and str(lookup.get("ean") or "") == str(ean or "")
            and str(lookup.get("factory_id") or "") == str(data.get("factory_id") or "")
            and str(lookup.get("aftercool_product_id") or "") == str(source_product_id or "")
        )
        if valid_lookup:
            raw_aftercool_price = lookup.get("price_cents")
            aftercool_cents: int | None = None
            if type(raw_aftercool_price) is int and raw_aftercool_price >= 0:
                aftercool_cents = raw_aftercool_price
            elif isinstance(raw_aftercool_price, str) and raw_aftercool_price.isdigit():
                aftercool_cents = int(raw_aftercool_price)
            aftercool_evidence = {
                "source": "AFTERCOOL", "status": (
                    "MISSING" if aftercool_cents is None else "ZERO" if aftercool_cents == 0 else "CONFIRMED"
                ), "amountCents": aftercool_cents, "currency": "EUR",
                "path": "aftercoolLookup.price_cents", "sku": lookup.get("sku"),
                "ean": lookup.get("ean"), "snapshotSource": "AFTERCOOL_LOOKUP",
            }
    calculated = None
    captured_at = None
    if (isinstance(snapshot, dict) and snapshot.get("sourceType") == source_type
            and snapshot.get("sourceSku") == sku and snapshot.get("sourceEan") == ean):
        try:
            calculated = calculate_price_from_cents(
                account, otto_price_cents=snapshot.get("ottoStandardPriceCents"), source_type=source_type,
                aftercool_price_cents=snapshot.get("aftercoolStartPriceCents") if source_type != "OTTO_DIRECT" else None,
            )
            captured_at = snapshot.get("capturedAt")
        except PreparationError:
            pass
    if calculated is None:
        aftercool = aftercool_evidence.get("amountCents") if valid_lookup else None
        evidence_cents = otto_evidence.get("amountCents")
        has_positive_otto_price = (
            otto_evidence.get("status") == "CONFIRMED"
            and type(evidence_cents) is int and evidence_cents > 0
        )
        has_positive_aftercool_price = (
            source_type in {"AFTERBUY", "AFTERCOOL"}
            and valid_lookup and type(aftercool) is int and aftercool > 0
        )
        if has_positive_otto_price or has_positive_aftercool_price:
            source_price_response = resolved.get("response")
            if not isinstance(source_price_response, Mapping):
                source_price_response = {"sku": sku}
                if has_positive_otto_price:
                    source_price_response["standardPrice"] = {
                        "amount": otto_evidence.get("amount"), "currency": otto_evidence.get("currency"),
                    }
            try:
                calculated = price_snapshot_from_sources(
                    account, source_price_response, source_type=source_type,
                    aftercool_price_cents=aftercool if source_type != "OTTO_DIRECT" else None,
                )
                captured_at = otto_evidence.get("capturedAt") or data.get("last_checked_at")
            except PreparationError:
                pass
    data["price_preview"] = ({
        "baseCents": calculated["originalPriceCents"], "targetCents": calculated["targetPriceCents"],
        "uvpCents": calculated["uvpPriceCents"],
        "selectedSource": calculated["selectedSource"],
        "capturedAt": captured_at.isoformat() if hasattr(captured_at, "isoformat") else captured_at,
    } if calculated is not None else None)
    if calculated is not None:
        data["price_review_reason"] = None
    elif (isinstance(snapshot, dict) and snapshot.get("sourceType") == source_type
          and snapshot.get("sourceSku") == sku and snapshot.get("sourceEan") == ean):
        data["price_review_reason"] = snapshot.get("priceReviewReason")
    else:
        data["price_review_reason"] = None
    data["otto_source_price"] = otto_evidence
    data["aftercool_source_price"] = aftercool_evidence
    any_source_price = (
        (otto_evidence.get("status") == "CONFIRMED" and type(otto_evidence.get("amountCents")) is int and otto_evidence["amountCents"] > 0)
        or (aftercool_evidence.get("status") == "CONFIRMED" and type(aftercool_evidence.get("amountCents")) is int and aftercool_evidence["amountCents"] > 0)
    )
    has_invalid_evidence = otto_evidence.get("status") not in {"MISSING", "CONFIRMED", "ZERO"}
    data["target_price_status"] = (
        "CALCULATED" if calculated is not None else
        "PRICE_REVIEW_REQUIRED" if any_source_price or has_invalid_evidence else
        "PRICE_UNAVAILABLE"
    )
    if not include_source_data:
        # The list contract needs normalized price evidence, not the full raw OTTO
        # response repeated for every row. Detail reads retain the source snapshot.
        data["source_data"] = {}
    return ProductReadDTO.model_validate(data)


def _normalize_account(account: str) -> str:
    if account not in {"JV", "XL"}:
        raise ProductCatalogError("INVALID_ACCOUNT", "Account must be JV or XL.")
    return account


def _candidate_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ProductCatalogError("INVALID_PRODUCT_ID", "Product ID must identify a candidate episode.") from None


def _filters(
    *,
    account: str,
    period: str | None,
    status: str | None,
    source: str | None,
    factory_id: str | None,
    query: str | None,
    open_only: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    account = _normalize_account(account)
    if period is not None and period not in {"3M", "12M"}:
        raise ProductCatalogError("INVALID_PERIOD", "Period must be 3M or 12M.")
    if source is not None and source not in {"AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"}:
        raise ProductCatalogError("INVALID_SOURCE", "Source must be AFTERCOOL, AFTERBUY, or OTTO_DIRECT.")
    if status is not None and (not status.strip() or len(status) > 50):
        raise ProductCatalogError("INVALID_STATUS", "Status filter must contain 1–50 characters.")
    if factory_id is not None and (not factory_id.strip() or len(factory_id) > 200):
        raise ProductCatalogError("INVALID_FACTORY_ID", "Factory filter must contain 1–200 characters.")
    if query is not None and len(query) > 200:
        raise ProductCatalogError("INVALID_QUERY", "Search query must be at most 200 characters.")

    where = [
        "ce.account = :account",
        "ce.data_scope = 'PRODUCTION'",
        "sp.data_scope = 'PRODUCTION'",
    ]
    params: dict[str, Any] = {"account": account}
    if open_only:
        where.append("ce.episode_state = 'OPEN'")
    if period is not None:
        where.append("ce.period = :period")
        params["period"] = period
    if status is not None:
        where.append(f"{_STATUS_SQL} = :status")
        params["status"] = status.strip().upper()
    if source is not None:
        if source in {"AFTERBUY", "AFTERCOOL"}:
            where.append("sp.source_type IN ('AFTERCOOL', 'AFTERBUY')")
        else:
            where.append("sp.source_type = :source")
            params["source"] = source
    if factory_id is not None:
        where.append("sp.factory_id = :factory_id")
        params["factory_id"] = factory_id.strip()
    if query is not None and query.strip():
        where.append(
            "(ce.observed_sku ILIKE :query OR COALESCE(ce.confirmed_ean, '') ILIKE :query "
            "OR COALESCE(ce.moin, '') ILIKE :query OR sp.original_title ILIKE :query OR COALESCE(edit.title, '') ILIKE :query "
            "OR COALESCE(sp.factory_name, '') ILIKE :query OR COALESCE(sp.factory_id, '') ILIKE :query "
            "OR COALESCE(op.target_sku, '') ILIKE :query OR COALESCE(op.new_ean, '') ILIKE :query)"
        )
        params["query"] = f"%{query.strip()}%"
    return where, params


def list_products(
    conn: Connection,
    *,
    account: str,
    page: int = 1,
    page_size: int = 50,
    period: str | None = None,
    status: str | None = None,
    source: str | None = None,
    factory_id: str | None = None,
    query: str | None = None,
    category: str | None = None,
    title_over_70: bool = False,
    sort_by: str | None = None,
    sort_dir: str = "asc",
    include_metrics: bool = True,
    include_source_data: bool = False,
) -> ProductListResponseDTO:
    """Return linked, open production episodes with SQL-side filters and pagination."""
    if type(page) is not int or page < 1:
        raise ProductCatalogError("INVALID_PAGE", "Page must be a positive integer.")
    if type(page_size) is not int or not 1 <= page_size <= 200:
        raise ProductCatalogError("INVALID_PAGE_SIZE", "Page size must be between 1 and 200.")
    where, params = _filters(
        account=account, period=period, status=status, source=source,
        factory_id=factory_id, query=query,
    )
    params["card_schemas"] = _card_schemas(conn, account=account)
    if category:
        if len(category) > 200:
            raise ProductCatalogError("INVALID_CATEGORY", "Category filter exceeds 200 characters.")
        where.append(f"{_CATEGORY_SQL} = :category")
        params["category"] = "" if category == "__EMPTY__" else category
    if title_over_70:
        where.append(f"char_length({_TITLE_SQL}) > 70")
    sort_columns = {"name": _TITLE_SQL, "ean": "COALESCE(op.new_ean, ce.confirmed_ean, '')",
                    "price": _PRICE_SQL, "category": _CATEGORY_SQL, "status": _STATUS_SQL}
    if sort_by is not None and sort_by not in sort_columns:
        raise ProductCatalogError("INVALID_SORT", "Unsupported product sort field.")
    if sort_dir not in {"asc", "desc"}:
        raise ProductCatalogError("INVALID_SORT", "Sort direction must be asc or desc.")
    where_sql = " AND ".join(where)
    needs_status_eval = (status is not None) or (sort_by == "status")
    needs_full_count_joins = needs_status_eval or bool(query and query.strip()) or bool(category) or title_over_70

    if needs_status_eval:
        count_joins = _PRODUCT_FROM + _PRODUCT_REMAINING_JOINS
    elif needs_full_count_joins:
        count_joins = _PRODUCT_FROM + _PRODUCT_BASE_JOINS
    else:
        count_joins = (
            " FROM candidate_episodes ce "
            "JOIN source_products sp ON sp.id = ce.source_product_id "
            "AND sp.account = ce.account AND sp.data_scope = ce.data_scope "
        )

    total = int(conn.execute(
        text(f"SELECT COUNT(*) {count_joins} WHERE {where_sql}"), params
    ).scalar_one())

    order_clause = (
        f"ORDER BY {sort_columns[sort_by]} {sort_dir.upper()} NULLS LAST, ce.id ASC "
        if sort_by
        else "ORDER BY ce.last_detected_at DESC, ce.observed_sku ASC, ce.id ASC "
    )
    page_params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
    if needs_status_eval:
        rows = conn.execute(
            text(
                f"{_PRODUCT_SELECT} {_PRODUCT_FROM} {_PRODUCT_REMAINING_JOINS} "
                f"WHERE {where_sql} {order_clause} LIMIT :limit OFFSET :offset"
            ),
            page_params,
        ).mappings().all()
    else:
        # Defer expensive per-row LATERAL JSONB attribute inspection until AFTER
        # pagination narrows the working set down to at most `page_size` rows.
        if not needs_full_count_joins and sort_by is None:
            inner_from = (
                "FROM candidate_episodes ce "
                "JOIN source_products sp ON sp.id = ce.source_product_id "
                "AND sp.account = ce.account AND sp.data_scope = ce.data_scope"
            )
        else:
            inner_from = _PRODUCT_FROM + _PRODUCT_BASE_JOINS
        rows = conn.execute(
            text(
                f"WITH paged AS ("
                f"  SELECT ce.id AS paged_ce_id {inner_from} WHERE {where_sql} {order_clause} LIMIT :limit OFFSET :offset"
                f") "
                f"{_PRODUCT_SELECT} "
                f"FROM paged "
                f"JOIN candidate_episodes ce ON ce.id = paged.paged_ce_id "
                f"JOIN source_products sp ON sp.id = ce.source_product_id AND sp.account = ce.account AND sp.data_scope = ce.data_scope "
                "LEFT JOIN restart_operations op ON op.account = ce.account AND op.data_scope = 'PRODUCTION' "
                "AND op.candidate_episode_id = ce.id "
                f"{_PRODUCT_REMAINING_JOINS} "
                f"{order_clause}"
            ),
            page_params,
        ).mappings().all()
    items = [_product_dto(row, include_source_data=include_source_data) for row in rows]

    metrics = None
    if include_metrics:
        source_bucket = "AFTERBUY" if source in {"AFTERBUY", "AFTERCOOL"} else (source or "")
        cache_key = (account, source_bucket, factory_id.strip() if factory_id else "")
        metrics = _cached_metrics_get(cache_key)
        _METRICS_JOIN = (
            _PRODUCT_FROM
            + " LEFT JOIN restart_product_edits edit ON edit.account = ce.account AND edit.candidate_episode_id = ce.id AND edit.data_scope = ce.data_scope "
            + " LEFT JOIN operation_revisions rev ON rev.operation_id = op.id AND rev.account = ce.account AND rev.data_scope = 'PRODUCTION' AND rev.revision_number = op.current_revision_number "
            + _PRODUCT_STATUS_LATERAL_JOINS
        )
        if metrics is None:
            metrics_where, metrics_params = _filters(
                account=account, period=None, status=None, source=source, factory_id=factory_id, query=None,
            )
            metrics_params["card_schemas"] = params["card_schemas"]
            metrics_where_sql = " AND ".join(metrics_where)
            metrics_rows = conn.execute(
                text(
                    "SELECT ce.period, " + _STATUS_SQL + " AS status, " + _CATEGORY_SQL + " AS category, "
                    + "COUNT(*) AS item_count, "
                    + f"COUNT(*) FILTER (WHERE char_length({_TITLE_SQL}) > 70) AS over_70_count "
                    + _METRICS_JOIN + " WHERE " + metrics_where_sql
                    + " GROUP BY ce.period, " + _STATUS_SQL + ", " + _CATEGORY_SQL
                ),
                metrics_params,
            ).mappings().all()
            category_set: set[str] = set()
            metrics = {
                "scope": "CATALOG_OPEN_CANDIDATES" if source or factory_id else "ACCOUNT_OPEN_CANDIDATES",
                "total": 0, "count3m": 0, "count12m": 0, "statusCounts": {},
                "over70Count": 0, "categories": [],
            }
            for row in metrics_rows:
                count = int(row["item_count"])
                metrics["over70Count"] += int(row["over_70_count"])
                metrics["total"] += count
                metrics["count3m"] += count if row["period"] == "3M" else 0
                metrics["count12m"] += count if row["period"] == "12M" else 0
                state = str(row["status"])
                metrics["statusCounts"][state] = metrics["statusCounts"].get(state, 0) + count
                cat_val = str(row["category"] or "").strip()
                if cat_val:
                    category_set.add(cat_val)
            metrics["categories"] = sorted(category_set)
            _cached_metrics_put(cache_key, metrics)

    return ProductListResponseDTO(
        items=items,
        page=ProductPageDTO(
            page=page, pageSize=page_size, total=total,
            totalPages=math.ceil(total / page_size) if total else 0,
        ),
        metrics=ProductCatalogMetricsDTO(**metrics) if metrics is not None else None,
    )


def get_product(
    conn: Connection, *, account: str, candidate_episode_id: str
) -> ProductReadDTO:
    """Return a single production product/candidate episode; legacy rows are separate."""
    product_id = _candidate_id(candidate_episode_id)
    account = _normalize_account(account)
    row = conn.execute(
        text(
            f"{_PRODUCT_SELECT} {_PRODUCT_FROM} {_PRODUCT_REMAINING_JOINS} "
            "WHERE ce.account=:account AND ce.data_scope='PRODUCTION' AND sp.data_scope='PRODUCTION' "
            "AND ce.id=:id LIMIT 1"
        ),
        {"account": account, "id": product_id, "card_schemas": _card_schemas(conn, account=account)},
    ).mappings().first()
    if row is None:
        raise ProductCatalogError("PRODUCT_NOT_FOUND", "Production product was not found in this account.")
    return _product_dto(row)


"""OTTO Partner Connect portal media normalization, validation, and persistence."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.operation_service import _record_event

PORTAL_MEDIA_SYSTEM = "OTTO_PARTNER_CONNECT"
PORTAL_MEDIA_METHOD = "PORTAL_PRODUCT_ASSETS"
PORTAL_MEDIA_RESOURCE = "portal-asset"
PORTAL_MEDIA_SOURCE_REF = f"{PORTAL_MEDIA_SYSTEM}:{PORTAL_MEDIA_RESOURCE}"
PORTAL_MEDIA_HOSTS = frozenset({"i.otto.de"})
ACCOUNT_BRAND_IDS: dict[str, str] = {
    "JV": "UO4EGHSX",
    "XL": "6HMOZBOU",
}

_SAFE_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,127}$")
_SAFE_STATUSES = frozenset({"ASSET_FOUND", "FOUND", "NOT_FOUND", "NOT_FOUND_FIRST_PAGE", "NO_IMAGES", "NO_ASSET_ID", "ERROR"})
MAX_PORTAL_GALLERY_ITEMS = 10


class PortalMediaError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def canonical_portal_image_url(*, asset_id: str | None = None, url: str | None = None) -> tuple[str, str]:
    """Validate an OTTO CDN asset ID or i.otto.de URL and return (asset_id, canonical_https_url)."""
    raw_asset = str(asset_id or "").strip()
    if raw_asset:
        if not _SAFE_ASSET_ID.fullmatch(raw_asset):
            raise PortalMediaError("PORTAL_MEDIA_ASSET_ID_INVALID", "OTTO assetId has an unsupported format.")
        return raw_asset, f"https://i.otto.de/i/otto/{raw_asset}"

    raw_url = str(url or "").strip()
    if not raw_url:
        raise PortalMediaError("PORTAL_MEDIA_URL_REQUIRED", "An assetId or https://i.otto.de/i/otto/ URL is required.")
    parsed = urlsplit(raw_url)
    host = (parsed.hostname or "").strip().casefold()
    if (
        parsed.scheme != "https"
        or host not in PORTAL_MEDIA_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not parsed.path.startswith("/i/otto/")
    ):
        raise PortalMediaError("PORTAL_MEDIA_URL_INVALID", "Portal image URL must point to https://i.otto.de/i/otto/.")
    extracted = parsed.path[len("/i/otto/"):].strip("/")
    if not _SAFE_ASSET_ID.fullmatch(extracted):
        raise PortalMediaError("PORTAL_MEDIA_ASSET_ID_INVALID", "OTTO CDN path must contain a valid assetId.")
    return extracted, f"https://i.otto.de/i/otto/{extracted}"


def portal_gallery_urls_from_attributes(attributes_json: Any) -> list[dict[str, Any]]:
    """Read validated portalMediaGallery entries from source_products.attributes_json."""
    root = _json_object(attributes_json)
    raw = root.get("portalMediaGallery")
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        try:
            asset_id, canonical_url = canonical_portal_image_url(
                asset_id=item.get("assetId"),
                url=item.get("url"),
            )
        except PortalMediaError:
            continue
        if canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)
        pos = item.get("position")
        entries.append({
            "position": pos if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0 else index,
            "assetId": asset_id,
            "filename": str(item.get("filename") or item.get("fileName") or "").strip()[:260] or None,
            "url": canonical_url,
            "productUuid": str(item.get("productUuid") or "").strip()[:80] or None,
        })
        if len(entries) == MAX_PORTAL_GALLERY_ITEMS:
            break
    entries.sort(key=lambda entry: entry["position"])
    return entries


def _has_direct_otto_location(attributes_json: Any) -> bool:
    root = _json_object(attributes_json)
    product = root.get("ottoProductData")
    if not isinstance(product, Mapping):
        return False
    media = product.get("mediaAssets")
    if not isinstance(media, list):
        return False
    for asset in media:
        if not isinstance(asset, Mapping):
            continue
        candidate = asset.get("location") or asset.get("url") or asset.get("imageUrl") or asset.get("href")
        if isinstance(candidate, str) and candidate.strip().startswith("https://"):
            return True
    return False


def _media_filenames(attributes_json: Any) -> list[str]:
    root = _json_object(attributes_json)
    product = root.get("ottoProductData")
    if not isinstance(product, Mapping):
        return []
    media = product.get("mediaAssets")
    if not isinstance(media, list):
        return []
    names: list[str] = []
    for asset in media:
        if isinstance(asset, Mapping):
            fn = asset.get("filename") or asset.get("fileName")
            if isinstance(fn, str) and fn.strip():
                names.append(fn.strip()[:260])
    return names


def list_missing_portal_media(
    conn: Connection,
    *,
    account: str,
    include_resolved: bool = False,
    source_type: str | None = "OTTO_DIRECT",
    limit: int = 200,
) -> dict[str, Any]:
    """List account-scoped products missing usable image URLs so Partner Connect assets can be synced."""
    acc = str(account or "").strip().upper()
    if acc not in ACCOUNT_BRAND_IDS:
        raise PortalMediaError("INVALID_ACCOUNT", "Account must be JV or XL.")
    bounded_limit = max(1, min(int(limit), 500))

    conditions = [
        "sp.account = :account",
        "sp.data_scope = 'PRODUCTION'",
        "COALESCE(sp.image_url, '') = ''",
    ]
    params: dict[str, Any] = {"account": acc, "limit": bounded_limit}
    if source_type:
        normalized_source = str(source_type).strip().upper()
        if normalized_source not in {"AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"}:
            raise PortalMediaError("INVALID_SOURCE_TYPE", "Unsupported sourceType filter.")
        conditions.append("sp.source_type = :source_type")
        params["source_type"] = normalized_source
    if not include_resolved:
        conditions.append(
            "COALESCE(sp.attributes_json->'portalMediaLookup'->>'status', '') NOT IN ('NOT_FOUND', 'NO_IMAGES')"
        )

    rows = conn.execute(
        text(
            "SELECT sp.id::text AS source_product_id, sp.account, sp.source_type, sp.aftercool_sku, "
            "sp.aftercool_ean, sp.original_title, sp.attributes_json, "
            "ce.observed_sku, ce.confirmed_ean "
            "FROM source_products sp "
            "LEFT JOIN LATERAL ("
            "  SELECT c.observed_sku, c.confirmed_ean FROM candidate_episodes c "
            "  WHERE c.source_product_id = sp.id AND c.account = sp.account AND c.data_scope = 'PRODUCTION' "
            "  ORDER BY (c.episode_state = 'OPEN') DESC, c.updated_at DESC LIMIT 1"
            ") ce ON TRUE "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY sp.updated_at DESC, sp.id LIMIT :limit"
        ),
        params,
    ).mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        if _has_direct_otto_location(row["attributes_json"]):
            continue
        attrs = _json_object(row["attributes_json"])
        lookup = _json_object(attrs.get("portalMediaLookup"))
        gallery = portal_gallery_urls_from_attributes(attrs)
        sku = str(row["observed_sku"] or row["aftercool_sku"] or row["aftercool_ean"] or "").strip()
        ean = str(row["confirmed_ean"] or row["aftercool_ean"] or sku).strip()
        if not sku and not ean:
            continue
        items.append({
            "sourceProductId": row["source_product_id"],
            "account": row["account"],
            "sourceType": row["source_type"],
            "sku": sku or ean,
            "ean": ean or sku,
            "title": row["original_title"],
            "expectedBrandId": ACCOUNT_BRAND_IDS[acc],
            "filenames": _media_filenames(attrs),
            "lookupStatus": lookup.get("status"),
            "existingGalleryCount": len(gallery),
        })

    return {
        "account": acc,
        "expectedBrandId": ACCOUNT_BRAND_IDS[acc],
        "items": items,
        "total": len(items),
    }


def parse_portal_media_csv(csv_text: str) -> list[dict[str, Any]]:
    """Parse rows exported from the OTTO Partner Connect browser script into portal-assets items."""
    text_body = str(csv_text or "").lstrip("\ufeff").strip()
    if not text_body:
        raise PortalMediaError("PORTAL_MEDIA_CSV_EMPTY", "CSV content is empty.")
    reader = csv.DictReader(StringIO(text_body))
    if not reader.fieldnames or not any(f.strip().lower() in {"ean", "sku"} for f in reader.fieldnames):
        raise PortalMediaError("PORTAL_MEDIA_CSV_INVALID", "CSV must contain ean/sku and url/assetId columns.")
    grouped: dict[str, dict[str, Any]] = {}
    for row in reader:
        norm_row = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
        code = norm_row.get("sku") or norm_row.get("ean") or ""
        if not code:
            continue
        bucket = grouped.setdefault(code, {
            "sku": code,
            "ean": norm_row.get("ean") or code,
            "productUuid": norm_row.get("productuuid") or None,
            "brandId": norm_row.get("brandid") or None,
            "status": norm_row.get("status") or None,
            "assets": [],
        })
        asset_id = norm_row.get("assetid") or ""
        url = norm_row.get("url") or norm_row.get("originalurl") or ""
        if asset_id or url:
            pos_raw = norm_row.get("position") or ""
            try:
                position = max(0, int(pos_raw)) if pos_raw else len(bucket["assets"])
            except ValueError:
                position = len(bucket["assets"])
            bucket["assets"].append({
                "position": position,
                "assetId": asset_id or None,
                "filename": norm_row.get("filename") or None,
                "url": url or None,
            })
    return list(grouped.values())


def save_portal_media_assets(
    conn: Connection,
    *,
    account: str,
    items: Sequence[Mapping[str, Any]],
    actor: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Persist validated OTTO Partner Connect asset URLs onto account-scoped source_products."""
    acc = str(account or "").strip().upper()
    if acc not in ACCOUNT_BRAND_IDS:
        raise PortalMediaError("INVALID_ACCOUNT", "Account must be JV or XL.")
    if not isinstance(actor, str) or not actor.strip():
        raise PortalMediaError("ACTOR_REQUIRED", "An authenticated operator is required.", status_code=403)
    if not isinstance(items, Sequence) or not items:
        raise PortalMediaError("PORTAL_MEDIA_ITEMS_REQUIRED", "At least one item is required.")
    if len(items) > 500:
        raise PortalMediaError("PORTAL_MEDIA_BATCH_TOO_LARGE", "At most 500 items may be saved per request.")

    expected_brand = ACCOUNT_BRAND_IDS[acc]
    now_iso = datetime.now(timezone.utc).isoformat()
    actor_clean = actor.strip()[:200]
    updated_count = 0
    saved_assets_count = 0
    results: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, Mapping):
            continue
        sku = str(item.get("sku") or item.get("ean") or "").strip()
        ean = str(item.get("ean") or item.get("sku") or "").strip()
        if not sku and not ean:
            results.append({"sku": "", "status": "INVALID_IDENTIFIER"})
            continue

        row = conn.execute(
            text(
                "SELECT sp.id, sp.image_url, sp.attributes_json "
                "FROM source_products sp "
                "WHERE sp.account = :account AND sp.data_scope = 'PRODUCTION' "
                "  AND (sp.aftercool_sku IN (:sku, :ean) "
                "       OR sp.aftercool_ean IN (:sku, :ean) "
                "       OR EXISTS ("
                "         SELECT 1 FROM candidate_episodes ce "
                "         WHERE ce.source_product_id = sp.id AND ce.account = sp.account "
                "           AND ce.data_scope = 'PRODUCTION' "
                "           AND (ce.observed_sku IN (:sku, :ean) OR ce.confirmed_ean IN (:sku, :ean))"
                "       )) "
                "ORDER BY sp.updated_at DESC, sp.id LIMIT 1 FOR UPDATE OF sp"
            ),
            {"account": acc, "sku": sku or ean, "ean": ean or sku},
        ).mappings().first()
        if row is None:
            results.append({"sku": sku or ean, "status": "NOT_MATCHED_IN_ACCOUNT"})
            continue

        supplied_brand = str(item.get("brandId") or "").strip()
        if supplied_brand and supplied_brand != expected_brand:
            raise PortalMediaError(
                "BRAND_ACCOUNT_MISMATCH",
                f"Portal brandId '{supplied_brand}' does not belong to account {acc} ({expected_brand}).",
                status_code=409,
            )

        product_uuid = str(item.get("productUuid") or "").strip()[:80] or None
        raw_assets = item.get("assets") if isinstance(item.get("assets"), Sequence) else []
        attrs = _json_object(row["attributes_json"])

        parsed_gallery: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        indexed_assets: list[tuple[int, int, Mapping[str, Any]]] = []
        for idx, asset in enumerate(raw_assets):
            if not isinstance(asset, Mapping):
                continue
            pos = asset.get("position")
            sort_pos = pos if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0 else idx
            indexed_assets.append((sort_pos, idx, asset))
        indexed_assets.sort(key=lambda triple: (triple[0], triple[1]))

        for pos, _idx, asset in indexed_assets:
            asset_id, canonical_url = canonical_portal_image_url(
                asset_id=asset.get("assetId"),
                url=asset.get("url") or asset.get("originalUrl"),
            )
            if canonical_url in seen_urls:
                continue
            seen_urls.add(canonical_url)
            parsed_gallery.append({
                "position": pos,
                "assetId": asset_id,
                "filename": str(asset.get("filename") or asset.get("fileName") or "").strip()[:260] or None,
                "url": canonical_url,
                "productUuid": product_uuid,
                "capturedAt": now_iso,
                "capturedBy": actor_clean,
            })
            if len(parsed_gallery) == MAX_PORTAL_GALLERY_ITEMS:
                break

        source_id = str(row["id"])
        if parsed_gallery:
            attrs["portalMediaGallery"] = parsed_gallery
            attrs["portalMediaLookup"] = {
                "status": "FOUND",
                "count": len(parsed_gallery),
                "productUuid": product_uuid,
                "brandId": supplied_brand or expected_brand,
                "checkedAt": now_iso,
                "checkedBy": actor_clean,
            }
            next_hero = parsed_gallery[0]["url"] if (overwrite or not str(row["image_url"] or "").strip()) else row["image_url"]
            conn.execute(
                text(
                    "UPDATE source_products SET image_url = :image_url, attributes_json = CAST(:attrs AS jsonb), "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"
                ),
                {
                    "image_url": next_hero,
                    "attrs": json.dumps(attrs, ensure_ascii=False, separators=(",", ":")),
                    "id": source_id,
                    "account": acc,
                },
            )
            _record_event(
                conn,
                account=acc,
                actor=actor_clean,
                event_type="PORTAL_MEDIA_IMPORTED",
                entity_type="SOURCE_PRODUCT",
                entity_id=source_id,
                summary=f"Imported {len(parsed_gallery)} OTTO Partner Connect image URL(s) for {sku or ean}",
                metadata={
                    "sku": sku or ean,
                    "ean": ean or sku,
                    "productUuid": product_uuid,
                    "count": len(parsed_gallery),
                    "heroUrl": next_hero,
                },
            )
            updated_count += 1
            saved_assets_count += len(parsed_gallery)
            results.append({
                "sourceProductId": source_id,
                "sku": sku or ean,
                "status": "SAVED",
                "assetCount": len(parsed_gallery),
                "heroUrl": next_hero,
            })
        else:
            raw_status = str(item.get("status") or "NO_IMAGES").strip().upper()
            lookup_status = raw_status if raw_status in _SAFE_STATUSES else "ERROR"
            attrs["portalMediaLookup"] = {
                "status": lookup_status,
                "count": 0,
                "productUuid": product_uuid,
                "error": str(item.get("error") or "").strip()[:300] or None,
                "checkedAt": now_iso,
                "checkedBy": actor_clean,
            }
            conn.execute(
                text(
                    "UPDATE source_products SET attributes_json = CAST(:attrs AS jsonb), "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"
                ),
                {
                    "attrs": json.dumps(attrs, ensure_ascii=False, separators=(",", ":")),
                    "id": source_id,
                    "account": acc,
                },
            )
            results.append({
                "sourceProductId": source_id,
                "sku": sku or ean,
                "status": lookup_status,
                "assetCount": 0,
            })

    return {
        "account": acc,
        "updatedCount": updated_count,
        "savedAssetsCount": saved_assets_count,
        "results": results,
    }

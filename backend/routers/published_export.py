import os
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import text
import httpx
import redis

from database import get_db
import models
import auth
from services.otto_formatter import format_otto_product, calculate_msrp, PRODUCT_SAFETY_COMPLIANCE
from services.brand_guard import sanitize_brand_attributes, is_forbidden_brand_image_url
from services.executors.otto_api import extract_images_from_aftercool_row
from services.otto_availability import OTTO_SHIPPING_PROFILES, is_chinese_product
from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["export"])

REDIS_URL = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://redis:6379/0"))
try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
except Exception:
    redis_client = None

EXPECTED_API_KEY = os.getenv("EXTERNAL_SERVICE_API_KEY", "omt_live_ext_key_2026")


async def verify_external_auth(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(None, description="Optional: API key in query param"),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """
    Allows authentication via:
    1. X-API-Key header (recommended for external service)
    2. ?api_key=<key> query parameter
    3. Authorization: Bearer <token> (JWT token)
    """
    key_candidate = (x_api_key or api_key or "").strip(" .\t\r\n")
    if key_candidate and key_candidate == EXPECTED_API_KEY.strip():
        return {"auth_type": "api_key", "client": "external_service"}

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            user = await auth.get_current_user(token=token, db=db)
            if user:
                return {"auth_type": "jwt", "user": user.username}
        except Exception:
            pass

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Valid 'X-API-Key' header, '?api_key=' param, or 'Authorization: Bearer <token>' is required."
    )


async def fetch_aftercool_batch(
    missing_by_factory: Dict[tuple, List[models.ProductMapping]],
    db: Optional[Session] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Fetches missing product details from Aftercool API in batches grouped by (factory_id, account).
    Falls back to /api/products/by-ean for any EANs missing from factory batches.
    Returns: {ean: {"row": row_dict, "p": product_dict, "images": list_of_imgs, "desc": str, "cis": str, "kurz": str}}
    """
    result = {}
    session_cookie = await get_aftercool_session()

    async with httpx.AsyncClient(cookies={"session": session_cookie}, timeout=35.0) as client:
        async def safe_get(url: str, params: dict):
            nonlocal session_cookie
            try:
                res = await client.get(url, params=params)
                if res.status_code in (401, 403):
                    session_cookie = await get_aftercool_session(force_refresh=True)
                    client.cookies.set("session", session_cookie)
                    res = await client.get(url, params=params, cookies={"session": session_cookie})
                return res
            except Exception as ex:
                logger.warning(f"Error fetching from Aftercool {url}: {ex}")
                return None

        from services.aftercool_service import resolve_aftercool_account

        # 1. Batch fetch by factory
        for (fid, acc_str), mappings in missing_by_factory.items():
            if not fid:
                continue
            eans_needed = {m.product_ean for m in mappings}
            aftercool_acc = resolve_aftercool_account(str(fid).strip(), acc_str, db=db)
            offset = 0
            limit = 500
            while True:
                params = {
                    "factory_id": str(fid).strip(),
                    "account": aftercool_acc,
                    "dataset": "lister",
                    "include_row": 1,
                    "limit": limit,
                    "offset": offset,
                }
                res = await safe_get(f"{AFTERCOOL_BASE_URL}/api/products", params)
                if not res or res.status_code != 200:
                    break
                data = res.json()
                items_list = data.get("items", data) if isinstance(data, dict) else data
                if not isinstance(items_list, list) or not items_list:
                    break

                for p in items_list:
                    row = p.get("row", {})
                    ean = str(p.get("ean") or row.get("EAN") or "").strip()
                    if ean in eans_needed:
                        pic_urls = extract_images_from_aftercool_row(row, p, account=acc_str)
                        desc = str(row.get("Artikelbeschreibung") or row.get("Beschreibung") or row.get("Description") or p.get("description") or "").strip()
                        cis = str(row.get("CustomItemSpecifics") or p.get("CustomItemSpecifics") or row.get("custom_item_specifics") or "").strip()
                        kurz = str(row.get("Kurzbeschreibung") or row.get("kurzbeschreibung") or row.get("ShortDescription") or row.get("short_description") or p.get("kurzbeschreibung") or "").strip()
                        result[ean] = {
                            "images": pic_urls,
                            "description": desc,
                            "custom_item_specifics": cis,
                            "kurzbeschreibung": kurz,
                            "row": row,
                            "p": p
                        }

                offset += len(items_list)
                total = data.get("total") if isinstance(data, dict) else None
                if total is not None and offset >= total:
                    break
                if len(items_list) < limit:
                    break

        # 2. Fallback by-ean for any still missing
        all_eans = [m.product_ean for mappings in missing_by_factory.values() for m in mappings]
        missing_eans = [e for e in all_eans if e not in result]
        for m_ean in missing_eans:
            m_map = next((m for maps in missing_by_factory.values() for m in maps if m.product_ean == m_ean), None)
            m_fid = m_map.factory_id if m_map else None
            m_acc_str = next((acc for (fid, acc), maps in missing_by_factory.items() if any(m.product_ean == m_ean for m in maps)), "JV")
            m_aftercool_acc = (m_map.source_account if (m_map and m_map.source_account) else resolve_aftercool_account(m_fid, m_acc_str, db=db))
            try:
                res_s = await safe_get(
                    f"{AFTERCOOL_BASE_URL}/api/products/by-ean",
                    params={"account": m_aftercool_acc, "ean": m_ean, "dataset": "lister", "limit": 1}
                )
                if res_s and res_s.status_code == 200:
                    s_data = res_s.json()
                    s_items = s_data.get("items", []) if isinstance(s_data, dict) else s_data
                    if s_items:
                        p_s = s_items[0]
                        row_s = p_s.get("row", {})
                        pic_urls_s = extract_images_from_aftercool_row(row_s, p_s, account=m_acc_str)
                        desc_s = str(row_s.get("Artikelbeschreibung") or row_s.get("Beschreibung") or row_s.get("Description") or p_s.get("description") or "").strip()
                        cis_s = str(row_s.get("CustomItemSpecifics") or p_s.get("CustomItemSpecifics") or row_s.get("custom_item_specifics") or "").strip()
                        kurz_s = str(row_s.get("Kurzbeschreibung") or row_s.get("kurzbeschreibung") or row_s.get("ShortDescription") or row_s.get("short_description") or p_s.get("kurzbeschreibung") or "").strip()
                        result[m_ean] = {
                            "images": pic_urls_s,
                            "description": desc_s,
                            "custom_item_specifics": cis_s,
                            "kurzbeschreibung": kurz_s,
                            "row": row_s,
                            "p": p_s
                        }
            except Exception as ex:
                logger.debug(f"Fallback by-ean fetch error for EAN {m_ean}: {ex}")

    return result


async def build_published_products_response(
    account: Optional[str] = None,
    factory_id: Optional[str] = None,
    since: Optional[datetime] = None,
    ean: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    include_raw_payload: bool = True,
    db: Session = None,
) -> Dict[str, Any]:
    # 1. Base Query on ProductMapping
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.status == "Published",
        models.ProductMapping.published_at.isnot(None)
    )

    if account:
        acc_filter = account.strip().upper()
        query = query.filter(models.ProductMapping.account_name == acc_filter)

    if factory_id:
        query = query.filter(models.ProductMapping.factory_id == str(factory_id).strip())

    if since:
        query = query.filter(models.ProductMapping.published_at >= since)

    if ean:
        query = query.filter(models.ProductMapping.product_ean == str(ean).strip())

    total = query.count()
    mappings = query.order_by(
        models.ProductMapping.published_at.asc(),
        models.ProductMapping.id.asc()
    ).offset(offset).limit(limit).all()

    if not mappings:
        return {
            "total": total,
            "count": 0,
            "offset": offset,
            "items": []
        }

    eans = [m.product_ean for m in mappings]

    # 2. Bulk fetch attributes
    attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(eans)
    ).all()
    attrs_by_ean_acc = {}
    for a in attrs:
        acc_key = a.account_name.value if hasattr(a.account_name, "value") else str(a.account_name or "JV")
        attrs_by_ean_acc.setdefault((a.product_ean, acc_key), {})[a.attribute_name] = a.attribute_value

    # 3. Bulk fetch categories
    cat_ids = {m.category_id for m in mappings if m.category_id}
    cats = db.query(models.CategoryCache).filter(models.CategoryCache.id.in_(cat_ids)).all() if cat_ids else []
    cat_map = {c.id: {"name": c.name, "path": c.path} for c in cats}

    # 4. Bulk fetch latest published queue item for task_href
    queue_items = db.query(models.PublishQueueItem).filter(
        models.PublishQueueItem.product_ean.in_(eans),
        models.PublishQueueItem.status == models.QueueItemStatus.PUBLISHED
    ).order_by(models.PublishQueueItem.id.desc()).all()
    queue_by_ean_acc = {}
    for q in queue_items:
        q_acc = q.account_name.value if hasattr(q.account_name, "value") else str(q.account_name or "JV")
        if (q.product_ean, q_acc) not in queue_by_ean_acc:
            queue_by_ean_acc[(q.product_ean, q_acc)] = q

    # 5. Check Redis Cache for already enriched product objects
    enriched_items = {}
    missing_mappings_by_factory = {}

    for m in mappings:
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or "JV")
        cache_key = f"omt:published_item:{m_acc}:{m.product_ean}"
        cached_val = None
        if redis_client:
            try:
                cached_val = redis_client.get(cache_key)
            except Exception:
                pass

        if cached_val:
            try:
                enriched_items[(m.product_ean, m_acc)] = json.loads(cached_val)
            except Exception:
                pass

        if (m.product_ean, m_acc) not in enriched_items:
            missing_mappings_by_factory.setdefault((m.factory_id, m_acc), []).append(m)

    # 6. Fetch from Aftercool for uncached items
    af_data_map = {}
    if missing_mappings_by_factory:
        try:
            af_data_map = await fetch_aftercool_batch(missing_mappings_by_factory, db=db)
        except Exception as e:
            logger.warning(f"Error fetching Aftercool details for export: {e}")

    # 7. Assemble full product objects
    items_response = []
    for m in mappings:
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or "JV")
        is_xl = m_acc.upper() == "XL"

        # If already cached, use cached version (stripping raw payload if not requested)
        if (m.product_ean, m_acc) in enriched_items:
            cached_item = dict(enriched_items[(m.product_ean, m_acc)])
            if not include_raw_payload and "otto_v5_payload" in cached_item:
                cached_item.pop("otto_v5_payload", None)
            items_response.append(cached_item)
            continue

        # Attributes & Category
        raw_attrs = attrs_by_ean_acc.get((m.product_ean, m_acc), {}).copy()
        safe_attrs = sanitize_brand_attributes(m_acc, raw_attrs)
        cat_info = cat_map.get(m.category_id, {"name": m.category_id or "Standard", "path": None})
        cat_name = cat_info["name"]

        # Parse price safely
        prod_price = "0.00"
        for p_val in (m.price, m.price_af):
            if not p_val:
                continue
            clean_p = str(p_val).replace(",", ".").replace("\xa0", "").strip()
            try:
                if float(clean_p) > 0:
                    prod_price = clean_p
                    break
            except ValueError:
                pass

        # Aftercool info
        af_prod = af_data_map.get(m.product_ean, {})
        af_images = af_prod.get("images", [])

        # Clean images according to account isolation
        all_gallery_images = []
        if is_xl:
            clean_imgs = [img for img in af_images if not is_forbidden_brand_image_url("XL", img)]
            main_candidate = m.image_url if (m.image_url and not is_forbidden_brand_image_url("XL", m.image_url)) else (clean_imgs[0] if clean_imgs else "")
            if main_candidate:
                all_gallery_images.append(main_candidate)
            for extra in clean_imgs:
                if extra and extra not in all_gallery_images and not is_forbidden_brand_image_url("XL", extra):
                    all_gallery_images.append(extra)
        else:
            main_candidate = m.image_url or (af_images[0] if af_images else "")
            if main_candidate:
                all_gallery_images.append(main_candidate)
            for extra in af_images:
                if extra and extra not in all_gallery_images:
                    all_gallery_images.append(extra)

        all_gallery_images = all_gallery_images[:10]
        main_img = all_gallery_images[0] if all_gallery_images else (m.image_url or "")

        full_desc = (
            af_prod.get("description")
            or raw_attrs.get("Beschreibung")
            or raw_attrs.get("description")
            or m.product_name
            or f"Product {m.product_ean}"
        )

        aftercool_info = {
            "name": m.product_name or f"Product {m.product_ean}",
            "description": full_desc,
            "price": prod_price,
            "images": all_gallery_images,
            "custom_item_specifics": af_prod.get("custom_item_specifics", ""),
            "kurzbeschreibung": af_prod.get("kurzbeschreibung", ""),
        }

        mapping_dict = {
            "attributes": safe_attrs,
            "category_id": m.category_id,
            "category": cat_name,
            "product_name": m.product_name,
            "price": prod_price,
            "account_name": m_acc,
        }

        # Generate standard OTTO v5 payload
        otto_payload = None
        try:
            otto_payload = format_otto_product(
                m.product_ean,
                aftercool_info,
                mapping_dict,
                cat_name,
                account_name=m_acc
            )
        except Exception as e:
            logger.debug(f"format_otto_product exception for {m.product_ean}: {e}")

        # Extract structured data from generated payload or fallbacks
        otto_desc_data = (otto_payload or {}).get("productDescription", {})
        bullet_points = otto_desc_data.get("bulletPoints", [])
        final_description = otto_desc_data.get("description", full_desc)
        safe_product_line = otto_desc_data.get("productLine", (m.product_name or "")[:70])
        brand_id = otto_desc_data.get("brandId") or safe_attrs.get("brandId") or safe_attrs.get("Marke") or ("UO4EGHSX" if m_acc == "JV" else "6HMOZBOU")

        pricing_block = (otto_payload or {}).get("pricing", {})
        std_price_val = float(prod_price) if prod_price else 0.0
        msrp_val = calculate_msrp(std_price_val)
        try:
            if "standardPrice" in pricing_block:
                std_price_val = float(pricing_block["standardPrice"].get("amount", std_price_val))
            if "msrp" in pricing_block:
                msrp_val = float(pricing_block["msrp"].get("amount", msrp_val))
        except Exception:
            pass

        # Logistics and availability
        q_item = queue_by_ean_acc.get((m.product_ean, m_acc))
        is_china = is_chinese_product(
            factory_id=m.factory_id or "",
            image_urls=all_gallery_images,
            product_name=m.product_name or "",
            kurzbeschreibung=af_prod.get("kurzbeschreibung", "")
        )
        shipping_profiles_acc = OTTO_SHIPPING_PROFILES.get(m_acc, OTTO_SHIPPING_PROFILES["JV"])
        profile_id = shipping_profiles_acc["CHINA_8_12_WEEKS"] if is_china else shipping_profiles_acc["EUROPE_4_8_WEEKS"]

        gpsr_compliance = PRODUCT_SAFETY_COMPLIANCE.get(m_acc, PRODUCT_SAFETY_COMPLIANCE["JV"])

        # Format media assets array
        media_assets = (otto_payload or {}).get("mediaAssets") or [
            {"type": "IMAGE", "location": img_url, "slot": idx + 1}
            for idx, img_url in enumerate(all_gallery_images)
        ]

        item_data = {
            "identification": {
                "ean": m.product_ean,
                "sku": m.product_ean,
                "product_reference": m.product_ean,
                "account": m_acc,
                "factory_id": m.factory_id,
                "status": m.status,
                "published_at": m.published_at.isoformat() if m.published_at else None,
                "last_task_href": q_item.otto_task_href if q_item else None,
            },
            "titles": {
                "current_name": m.product_name,
                "product_line_otto": safe_product_line,
                "has_ai_title": bool(m.has_ai_title),
            },
            "category": {
                "id": m.category_id,
                "name": cat_name,
                "path": cat_info.get("path"),
            },
            "pricing": {
                "price_otto": std_price_val,
                "price_aftercool": float(str(m.price_af).replace(",", ".").replace("\xa0", "").strip()) if m.price_af else None,
                "msrp_uvp": msrp_val,
                "currency": "EUR",
            },
            "content": {
                "description": final_description,
                "short_description": af_prod.get("kurzbeschreibung") or None,
                "bullet_points": bullet_points,
            },
            "media": {
                "main_image": main_img,
                "gallery_images": all_gallery_images,
                "media_assets": media_assets,
            },
            "attributes": safe_attrs,
            "brand_and_regulatory": {
                "brand_id": brand_id,
                "weee_reg_nr": safe_attrs.get("WEEE-Reg.-Nr. DE") or safe_attrs.get("WEEE-Reg.-Nr.") or ("46974041" if m_acc == "JV" else "80120806"),
                "elektrog_type": safe_attrs.get("Geräteart laut ElektroG") or "Produkt fällt nicht unter das ElektroG.",
            },
            "logistics_and_availability": {
                "delivery_type": (otto_payload or {}).get("logistics", {}).get("deliveryType", "PARCEL"),
                "delivery_time_days": 3,
                "shipping_profile_id": profile_id,
                "stock_quantity": 20,
            },
            "compliance_gpsr": gpsr_compliance,
            "otto_v5_payload": otto_payload,
        }

        # Cache in Redis for 24h (86400s)
        if redis_client:
            try:
                cache_key = f"omt:published_item:{m_acc}:{m.product_ean}"
                redis_client.set(cache_key, json.dumps(item_data), ex=86400)
            except Exception as ex:
                logger.debug(f"Redis cache write error for {m.product_ean}: {ex}")

        # If user didn't request raw payload, exclude it from response
        output_item = dict(item_data)
        if not include_raw_payload and "otto_v5_payload" in output_item:
            output_item.pop("otto_v5_payload", None)

        items_response.append(output_item)

    return {
        "total": total,
        "count": len(items_response),
        "offset": offset,
        "items": items_response
    }


# Standard path: /export/published-products
@router.get("/export/published-products")
@router.get("/published-products", include_in_schema=False)
async def get_published_products_export(
    account: Optional[str] = Query(None, description="Account filter: 'JV' or 'XL'"),
    factory_id: Optional[str] = Query(None, description="Factory ID filter (e.g. '504220')"),
    since: Optional[datetime] = Query(None, description="Incremental sync filter: published_at >= since (ISO format)"),
    ean: Optional[str] = Query(None, description="Single product filter by EAN"),
    limit: int = Query(50, ge=1, le=500, description="Page limit (default: 50, max: 500)"),
    offset: int = Query(0, ge=0, description="Page offset"),
    include_raw_payload: bool = Query(True, description="Whether to include full raw otto_v5_payload"),
    auth_data: dict = Depends(verify_external_auth),
    db: Session = Depends(get_db),
):
    """
    Returns ALL product data used during OTTO Market publication for Published products.
    Designed for existing external service ingestion.
    """
    return await build_published_products_response(
        account=account,
        factory_id=factory_id,
        since=since,
        ean=ean,
        limit=limit,
        offset=offset,
        include_raw_payload=include_raw_payload,
        db=db,
    )


# Alias path: /api/published-products (for external service convenience)
@router.get("/api/published-products")
async def get_published_products_api_alias(
    account: Optional[str] = Query(None, description="Account filter: 'JV' or 'XL'"),
    factory_id: Optional[str] = Query(None, description="Factory ID filter (e.g. '504220')"),
    since: Optional[datetime] = Query(None, description="Incremental sync filter: published_at >= since (ISO format)"),
    ean: Optional[str] = Query(None, description="Single product filter by EAN"),
    limit: int = Query(50, ge=1, le=500, description="Page limit (default: 50, max: 500)"),
    offset: int = Query(0, ge=0, description="Page offset"),
    include_raw_payload: bool = Query(True, description="Whether to include full raw otto_v5_payload"),
    auth_data: dict = Depends(verify_external_auth),
    db: Session = Depends(get_db),
):
    """
    Alias route for /export/published-products.
    """
    return await build_published_products_response(
        account=account,
        factory_id=factory_id,
        since=since,
        ean=ean,
        limit=limit,
        offset=offset,
        include_raw_payload=include_raw_payload,
        db=db,
    )


def _load_factory_names_map() -> Dict[str, str]:
    """
    Returns a dict mapping factory_id -> factory_name from Redis cache.
    """
    if not redis_client:
        return {}
    try:
        raw = redis_client.get("aftercool_factories_cache") or redis_client.get("aftercool_factories_last_good")
        if not raw:
            return {}
        data = json.loads(raw)
        result = {}
        items = []
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                items = data["items"]
            else:
                for v in data.values():
                    if isinstance(v, list):
                        items.extend(v)
        elif isinstance(data, list):
            items = data
        for f in items:
            if isinstance(f, dict):
                fid = str(f.get("id") or "").strip()
                name = str(f.get("name") or "").strip()
                if fid and name:
                    result[fid] = name
        return result
    except Exception as ex:
        logger.warning(f"Error loading factory names map: {ex}")
        return {}


async def build_drip_factories_response(
    include_existing: bool = True,
    only_paired: bool = False,
    factory_name: Optional[str] = None,
    factory_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, List[Dict[str, Optional[str]]]]:
    """
    Builds the mapping of Drip Feed factories to paired JV & XL published EANs.
    """
    cache_key = f"omt:drip_factories_cache:{include_existing}:{only_paired}:{factory_name or ''}:{factory_id or ''}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    from collections import defaultdict
    from services.aftercool_twin import _normalize_name, _normalize_title
    from services.queue_reconciliation import load_observations

    # 1. Load factory names cache
    fac_names_cache = _load_factory_names_map()

    # 2. Query PublishPlan rows for OTTO_API executor type
    plans_query = db.query(models.PublishPlan).filter(
        models.PublishPlan.executor_type == models.ExecutorType.OTTO_API
    )

    clean_target_fid = str(factory_id).strip() if factory_id else None
    target_norm_name = _normalize_name(factory_name) if factory_name else None

    # If factory_id is given, check if we can resolve its twin factory name
    twin_norm_from_fid = None
    if clean_target_fid:
        if clean_target_fid in fac_names_cache:
            twin_norm_from_fid = _normalize_name(fac_names_cache[clean_target_fid])
        else:
            plan_match = plans_query.filter(models.PublishPlan.factory_id == clean_target_fid).first()
            if plan_match and plan_match.factory_name and str(plan_match.factory_name).strip() != clean_target_fid:
                twin_norm_from_fid = _normalize_name(str(plan_match.factory_name).strip())

    plans = plans_query.all()
    if not plans:
        return {}

    plan_map = {}
    display_names = {}
    plan_norm_names = {}

    for p in plans:
        fid = str(p.factory_id).strip()
        fname = fac_names_cache.get(fid)
        if not fname and p.factory_name and str(p.factory_name).strip() != fid:
            fname = str(p.factory_name).strip()
        if not fname:
            fname = f"Factory_{fid}"

        norm = _normalize_name(fname)

        # Apply factory_name filter if requested
        if target_norm_name and target_norm_name != norm and target_norm_name not in norm:
            continue

        # Apply factory_id filter if requested
        if clean_target_fid:
            if twin_norm_from_fid:
                if norm != twin_norm_from_fid:
                    continue
            else:
                if fid != clean_target_fid:
                    continue

        plan_map[p.id] = p
        plan_norm_names[p.id] = norm
        if norm not in display_names or display_names[norm].startswith("Factory_"):
            display_names[norm] = fname

    if not plan_map:
        return {}

    # 3. Query queue items for matched plans
    queue_items = db.query(models.PublishQueueItem).filter(
        models.PublishQueueItem.plan_id.in_(list(plan_map.keys()))
    ).all()

    # 4. Optional load_observations for existing items in OTTO
    obs_map = {}
    if include_existing:
        jv_items = [i for i in queue_items if (i.account_name.value if hasattr(i.account_name, "value") else str(i.account_name)) == "JV"]
        xl_items = [i for i in queue_items if (i.account_name.value if hasattr(i.account_name, "value") else str(i.account_name)) == "XL"]
        obs_map.update(load_observations("JV", jv_items))
        obs_map.update(load_observations("XL", xl_items))

    # 5. Group published EANs by factory norm and account
    fac_eans = defaultdict(lambda: {"JV": set(), "XL": set()})

    for it in queue_items:
        acc = it.account_name.value if hasattr(it.account_name, "value") else str(it.account_name)
        norm = plan_norm_names.get(it.plan_id)
        if not norm:
            continue
        is_published = (it.status == models.QueueItemStatus.PUBLISHED)
        is_existing = (include_existing and it.id in obs_map and obs_map[it.id].get("exists") is True)
        if is_published or is_existing:
            fac_eans[norm][acc].add(it.product_ean)

    all_needed_eans = list(set().union(*(v["JV"] | v["XL"] for v in fac_eans.values())))

    # 6. Bulk load ProductMapping metadata (image_url, product_name) for pairing
    mappings = {}
    for i in range(0, len(all_needed_eans), 2000):
        chunk = all_needed_eans[i:i+2000]
        rows = db.query(
            models.ProductMapping.product_ean,
            models.ProductMapping.account_name,
            models.ProductMapping.image_url,
            models.ProductMapping.product_name
        ).filter(models.ProductMapping.product_ean.in_(chunk)).all()
        for row in rows:
            acc_str = row.account_name.value if hasattr(row.account_name, "value") else str(row.account_name)
            mappings[(acc_str, row.product_ean)] = row

    # 7. Pair JV & XL EANs
    result = {}
    for norm, acc_dict in sorted(fac_eans.items(), key=lambda x: display_names.get(x[0], x[0])):
        fname = display_names.get(norm, norm)
        jv_set = set(acc_dict["JV"])
        xl_set = set(acc_dict["XL"])
        pairs = []

        # Step 1: Identical EAN
        for e in sorted(jv_set & xl_set):
            pairs.append({"jv": e, "xl": e})
            jv_set.remove(e)
            xl_set.remove(e)

        # Step 2: Image URL match
        xl_by_img = {}
        for e in sorted(xl_set):
            m = mappings.get(("XL", e))
            if m and m.image_url and m.image_url.strip():
                xl_by_img.setdefault(m.image_url.strip(), []).append(e)

        for e in sorted(list(jv_set)):
            m = mappings.get(("JV", e))
            if m and m.image_url and m.image_url.strip() in xl_by_img and xl_by_img[m.image_url.strip()]:
                xe = xl_by_img[m.image_url.strip()].pop(0)
                pairs.append({"jv": e, "xl": xe})
                jv_set.remove(e)
                xl_set.remove(xe)

        # Step 3: Normalized title match
        xl_by_title = {}
        for e in sorted(xl_set):
            m = mappings.get(("XL", e))
            if m and m.product_name and m.product_name.strip():
                xl_by_title.setdefault(_normalize_title(m.product_name), []).append(e)

        for e in sorted(list(jv_set)):
            m = mappings.get(("JV", e))
            if m and m.product_name and m.product_name.strip():
                nt = _normalize_title(m.product_name)
                if nt in xl_by_title and xl_by_title[nt]:
                    xe = xl_by_title[nt].pop(0)
                    pairs.append({"jv": e, "xl": xe})
                    jv_set.remove(e)
                    xl_set.remove(xe)

        # Step 4: Unmatched items (if not only_paired)
        if not only_paired:
            for e in sorted(jv_set):
                pairs.append({"jv": e, "xl": None})
            for e in sorted(xl_set):
                pairs.append({"jv": None, "xl": e})

        if pairs or not only_paired:
            result[fname] = pairs

    if redis_client:
        try:
            redis_client.set(cache_key, json.dumps(result), ex=180)
        except Exception:
            pass

    return result


# Standard path: /export/drip-factories
@router.get("/export/drip-factories")
@router.get("/drip-factories", include_in_schema=False)
async def get_drip_factories_export(
    include_existing: bool = Query(True, description="Include items confirmed in OTTO via observations"),
    only_paired: bool = Query(False, description="Return only pairs where both JV and XL are present"),
    factory_name: Optional[str] = Query(None, description="Filter by factory name"),
    factory_id: Optional[str] = Query(None, description="Filter by factory ID (JV or XL)"),
    auth_data: dict = Depends(verify_external_auth),
    db: Session = Depends(get_db),
):
    """
    Returns Drip Feed factories and their published EANs mapped as JV & XL pairs.
    """
    return await build_drip_factories_response(
        include_existing=include_existing,
        only_paired=only_paired,
        factory_name=factory_name,
        factory_id=factory_id,
        db=db,
    )


# Alias path: /api/drip-factories (for external service convenience)
@router.get("/api/drip-factories")
async def get_drip_factories_api_alias(
    include_existing: bool = Query(True, description="Include items confirmed in OTTO via observations"),
    only_paired: bool = Query(False, description="Return only pairs where both JV and XL are present"),
    factory_name: Optional[str] = Query(None, description="Filter by factory name"),
    factory_id: Optional[str] = Query(None, description="Filter by factory ID (JV or XL)"),
    auth_data: dict = Depends(verify_external_auth),
    db: Session = Depends(get_db),
):
    """
    Alias route for /export/drip-factories.
    """
    return await build_drip_factories_response(
        include_existing=include_existing,
        only_paired=only_paired,
        factory_name=factory_name,
        factory_id=factory_id,
        db=db,
    )

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

        # 1. Batch fetch by factory
        for (fid, acc_str), mappings in missing_by_factory.items():
            if not fid:
                continue
            eans_needed = {m.product_ean for m in mappings}
            offset = 0
            limit = 500
            while True:
                params = {
                    "factory_id": str(fid).strip(),
                    "account": acc_str,
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
            acc_str = next((acc for (fid, acc), maps in missing_by_factory.items() if any(m.product_ean == m_ean for m in maps)), "JV")
            try:
                res_s = await safe_get(
                    f"{AFTERCOOL_BASE_URL}/api/products/by-ean",
                    params={"account": acc_str, "ean": m_ean, "dataset": "lister", "limit": 1}
                )
                if res_s and res_s.status_code == 200:
                    s_data = res_s.json()
                    s_items = s_data.get("items", []) if isinstance(s_data, dict) else s_data
                    if s_items:
                        p_s = s_items[0]
                        row_s = p_s.get("row", {})
                        pic_urls_s = extract_images_from_aftercool_row(row_s, p_s, account=acc_str)
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
            af_data_map = await fetch_aftercool_batch(missing_mappings_by_factory)
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

import os
import logging
import datetime
from typing import Optional, Dict, Any, List
from collections import defaultdict
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, String

import auth
from database import get_db
import models

# Import from dedicated service layers
from services.aftercool_service import (
    AFTERCOOL_BASE_URL,
    AFTERCOOL_LOGIN_URL,
    redis_client,
    get_aftercool_session,
    request_aftercool,
    get_cached_json,
    set_cached_json,
    sync_products_to_db,
    force_sync_factory_metadata,
    parse_aftercool_product,
    iter_products,
    invalidate_products_cache,
    resolve_aftercool_account,
)
from services.brand_guard import (
    prepare_attributes_for_transfer,
    sanitize_brand_attributes,
    sanitize_brand_text,
    filter_brand_images,
)
from services.constant_attributes import apply_default_attributes
from services.validator import validate_product

logger = logging.getLogger("aftercool_router")

router = APIRouter(prefix="/aftercool", tags=["aftercool"])


@router.get("/factories")
async def get_factories(
    refresh: bool = False,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieves the list of factories from Aftercool API with 120s Redis caching.
    Merges cross-account transferred factories so they appear seamlessly in the target account (e.g. XL).
    """
    cache_key = "aftercool_factories_cache"
    if not refresh:
        cached_data = await get_cached_json(cache_key)
        if cached_data:
            return cached_data

    params = {"refresh": "1"} if refresh else {"refresh": "", "fast": ""}
    data = None
    try:
        res = await request_aftercool("GET", "/api/factories", params=params, timeout=15.0)
        if res.status_code == 200:
            data = res.json()
            await set_cached_json("aftercool_factories_last_good", data, ex=86400 * 7)
        else:
            logger.warning("Failed to fetch factories from Aftercool: HTTP %s", res.status_code)
    except Exception as e:
        logger.warning("Error fetching factories from Aftercool: %s", e)

    if not data:
        data = await get_cached_json("aftercool_factories_last_good")
    if not data:
        data = await get_cached_json(cache_key)

    if not data:
        raise HTTPException(status_code=503, detail="Aftercool temporarily unavailable")
    if data:
        # Merge transferred factories from DB
        try:
            transferred = db.query(
                models.ProductMapping.factory_id,
                models.ProductMapping.source_account,
                models.ProductMapping.account_name,
                func.count(models.ProductMapping.id).label("cnt")
            ).filter(
                models.ProductMapping.source_account.isnot(None),
                cast(models.ProductMapping.account_name, String) != models.ProductMapping.source_account
            ).group_by(
                models.ProductMapping.factory_id,
                models.ProductMapping.source_account,
                models.ProductMapping.account_name
            ).all()

            items_list = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            original_native_keys = {(str(f.get("id")), str(f.get("account")).upper()) for f in items_list if isinstance(f, dict)}

            # Guarantee that no native Aftercool factory is marked as transferred
            for f in items_list:
                if isinstance(f, dict) and (str(f.get("id")), str(f.get("account")).upper()) in original_native_keys:
                    f["is_transferred"] = False
                    f.pop("transferred_from", None)

            if transferred:
                for row in transferred:
                    fid_str = str(row.factory_id)
                    tgt_acc = (row.account_name.value if hasattr(row.account_name, "value") else str(row.account_name)).upper()
                    if tgt_acc.startswith("ACCOUNTTYPE."):
                        tgt_acc = tgt_acc.replace("ACCOUNTTYPE.", "")
                    src_acc = (row.source_account.value if hasattr(row.source_account, "value") else str(row.source_account)).upper()
                    if src_acc.startswith("ACCOUNTTYPE."):
                        src_acc = src_acc.replace("ACCOUNTTYPE.", "")

                    # 1. If this factory is already native to target account, NEVER mark it as transferred
                    if (fid_str, tgt_acc) in original_native_keys:
                        continue

                    # 2. If this ID belongs to target account native range (e.g. 48xxxx for XL), skip
                    if tgt_acc == "XL" and fid_str.startswith("48"):
                        continue
                    if tgt_acc == "JV" and fid_str.startswith("50"):
                        continue

                    # 3. Otherwise, this is a cloned factory from another account
                    src_item = next(
                        (f for f in items_list if isinstance(f, dict) and str(f.get("id")) == fid_str and str(f.get("account")).upper() == src_acc),
                        None
                    )
                    cloned = dict(src_item) if src_item else {
                        "id": fid_str,
                        "name": f"Factory {fid_str}",
                        "kind": "lister",
                        "status": "ok",
                        "updated": None,
                    }
                    cloned["account"] = tgt_acc
                    cloned["items_count"] = row.cnt
                    cloned["is_transferred"] = True
                    cloned["transferred_from"] = src_acc
                    items_list.append(cloned)
                    original_native_keys.add((fid_str, tgt_acc))

            if isinstance(data, dict):
                data["items"] = items_list
            else:
                data = items_list
        except Exception as e:
            logger.warning("Error merging transferred factories in get_factories: %s", e)

        # Cache for 120s (2 minutes) so newly created factories appear quickly
        await set_cached_json(cache_key, data, ex=120)

    return data


@router.get("/twin-factory")
async def get_twin_factory(
    factory_id: str,
    source_account: str = "JV",
    target_account: str = "XL",
    current_user: models.User = Depends(auth.get_current_user)
):
    """
    Finds the twin factory in target_account matching source_factory_id.
    """
    from services.aftercool_twin import resolve_target_factory_id

    src_acc = str(source_account).upper().strip()
    tgt_acc = str(target_account).upper().strip()
    fid = str(factory_id).strip()

    target_fid = await resolve_target_factory_id(
        source_factory_id=fid,
        source_account=src_acc,
        target_account=tgt_acc
    )
    return {
        "source_factory_id": fid,
        "source_account": src_acc,
        "target_account": tgt_acc,
        "target_factory_id": target_fid
    }


@router.get("/products")
async def get_products(
    factory_id: Optional[str] = None,
    account: str = "",
    dataset: str = "",
    q: str = "",
    include_row: int = 0,
    limit: int = 100,
    offset: int = 0,
    refresh: bool = False,
    sync_db: int = 1,
    dedup: int = 0,
    background_tasks: BackgroundTasks = None,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetches a page of products from Aftercool with Redis caching and optional background DB sync.
    Supports cross-account routing for transferred factories.
    """
    fid_str = str(factory_id).strip() if factory_id else ""
    acc_str = str(account or "").upper().strip()
    aftercool_acc = resolve_aftercool_account(fid_str, acc_str, db) if (fid_str and acc_str) else acc_str
    src_account = aftercool_acc if aftercool_acc != acc_str else None

    cache_key = f"aftercool:products:{acc_str}:{fid_str}:{dataset}:{q}:{limit}:{offset}:{include_row}:{dedup}"

    if not refresh:
        cached_data = await get_cached_json(cache_key)
        if cached_data:
            if background_tasks and include_row == 1 and sync_db == 1 and fid_str:
                background_tasks.add_task(sync_products_to_db, fid_str, acc_str, cached_data, 500, src_account)
            return cached_data

    params = {
        "account": aftercool_acc,
        "dataset": dataset,
        "q": q,
        "include_row": include_row,
        "limit": min(max(1, limit), 500),
        "offset": max(0, offset)
    }
    if fid_str:
        params["factory_id"] = fid_str

    res = await request_aftercool("GET", "/api/products", params=params, timeout=30.0)
    if res.status_code != 200:
        logger.error("Error fetching products from Aftercool: HTTP %s (%s)", res.status_code, res.text[:200])
        raise HTTPException(status_code=res.status_code, detail="Error fetching products from Aftercool")

    data = res.json()

    if dedup == 1 and isinstance(data, dict) and "items" in data:
        seen_eans = set()
        deduped = []
        for item in data["items"]:
            ean = str(item.get("ean") or (item.get("row") or {}).get("EAN") or "").strip()
            if not ean or ean in ("—", "0", "-"):
                deduped.append(item)
            elif ean not in seen_eans:
                seen_eans.add(ean)
                deduped.append(item)
        data["items"] = deduped
        # Retain original total if available, otherwise fallback
        if "total" not in data or data["total"] is None:
            data["total"] = len(deduped)

    if data:
        # Cache for 30 minutes (1800s)
        await set_cached_json(cache_key, data, ex=1800)

    if background_tasks and include_row == 1 and sync_db == 1 and fid_str:
        background_tasks.add_task(sync_products_to_db, fid_str, acc_str, data, 500, src_account)

    return data


@router.post("/sync-metadata/{factory_id}")
async def force_sync_metadata(
    factory_id: str,
    account: str = "JV",
    dataset: str = "lister",
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Force-sync product_name, price, image_url for all products of a factory from Aftercool into our DB.
    Protects manually set user prices (has_manual_price).
    """
    fid = str(factory_id).strip()
    acc = str(account or "JV").upper().strip()

    result = await force_sync_factory_metadata(factory_id=fid, account=acc, dataset=dataset)
    if result["status"] == "partial_error" and result["synced"] == 0 and result["error"]:
        raise HTTPException(status_code=502, detail=result["error"])

    return result


class TransferFactoryRequest(BaseModel):
    factory_ids: List[str]
    source_account: str = "JV"
    target_account: str = "XL"
    copy_attributes: bool = True
    copy_categories: bool = True
    skip_existing: bool = False


@router.post("/factories/transfer-to-xl")
async def transfer_factories_to_xl(
    req: TransferFactoryRequest,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    """
    Clones all products of specified factory_ids from source_account (e.g. JV) to target_account (e.g. XL).
    Key features:
      - Swaps brand attributes cleanly to target account defaults (Marke, WEEE, BattVO, Materialhinweis)
      - Fills default attributes from default_attributes.json for XL
      - Filters out foreign brand logo images (JVMÖBEL logo stripped for XL)
      - Sanitizes product titles for brand safety
      - Protects user manual prices (has_manual_price)
      - Tracks origin using source_account column on ProductMapping
      - Validates mappings into Ready / Need Attributes status
      - Invalidates relevant Redis caches
    """
    src_acc = str(req.source_account or "JV").upper().strip()
    tgt_acc = str(req.target_account or "XL").upper().strip()

    if src_acc == tgt_acc:
        raise HTTPException(status_code=400, detail="Source and target accounts must be different.")
    if not req.factory_ids:
        raise HTTPException(status_code=400, detail="No factory IDs provided.")

    now = datetime.datetime.utcnow()
    total_transferred = 0
    total_ready = 0
    total_need_attributes = 0
    total_not_mapped = 0

    for fid in req.factory_ids:
        clean_fid = str(fid).strip()
        if not clean_fid:
            continue

        # 1. Fetch all products of this factory from Aftercool using source_account
        factory_products = []
        try:
            async for page in iter_products(factory_id=clean_fid, account=src_acc, dataset="lister", page_size=500, dedup=True):
                factory_products.extend(page)
        except Exception as e:
            logger.error("Failed to load products for factory %s from Aftercool: %s", clean_fid, e)
            continue

        if not factory_products:
            logger.info("Factory %s has 0 products in Aftercool account %s", clean_fid, src_acc)
            continue

        eans = [p.ean for p in factory_products if p.ean and p.ean not in ("—", "0", "None", "-")]
        if not eans:
            continue

        # 2. Filter foreign brand logos from images
        all_imgs = [p.image_url for p in factory_products if p.image_url]
        clean_imgs = await filter_brand_images(tgt_acc, all_imgs)
        valid_img_set = set(clean_imgs)

        # 3. Fetch existing target mappings in ONE query
        existing_targets = {
            m.product_ean: m
            for m in db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean.in_(eans),
                models.ProductMapping.account_name == tgt_acc
            ).all()
        }

        # 4. Fetch source mappings to inherit category_id
        src_mappings = {
            m.product_ean: m
            for m in db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean.in_(eans),
                models.ProductMapping.account_name == src_acc
            ).all()
        }

        # 5. Fetch source attributes
        src_attrs_rows = db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(eans),
            models.ProductAttribute.account_name == src_acc
        ).all()
        attrs_by_ean = defaultdict(dict)
        for a in src_attrs_rows:
            attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

        # Delete existing target attributes if copy_attributes is enabled
        if req.copy_attributes:
            db.query(models.ProductAttribute).filter(
                models.ProductAttribute.product_ean.in_(eans),
                models.ProductAttribute.account_name == tgt_acc
            ).delete(synchronize_session=False)

        new_attributes_list = []

        for prod in factory_products:
            if not prod.ean or prod.ean in ("—", "0", "None", "-"):
                continue

            tgt_m = existing_targets.get(prod.ean)
            if tgt_m and req.skip_existing:
                continue

            src_m = src_mappings.get(prod.ean)
            target_cat_id = None
            if req.copy_categories and src_m and src_m.category_id:
                target_cat_id = src_m.category_id
            elif tgt_m and tgt_m.category_id:
                target_cat_id = tgt_m.category_id

            clean_title = sanitize_brand_text(tgt_acc, prod.name) if prod.name else f"Product {prod.ean}"
            clean_image = prod.image_url if (prod.image_url and prod.image_url in valid_img_set) else None

            # Prepare attributes:
            src_attr_dict = attrs_by_ean.get(prod.ean, {})

            # A. Brand Guard: replace brand tokens, remove warranty, guarantee account defaults
            transformed = prepare_attributes_for_transfer(
                source_account=src_acc,
                target_account=tgt_acc,
                source_attributes=src_attr_dict
            )

            # B. Apply default attributes from default_attributes.json for target_account (XL)
            transformed = apply_default_attributes(
                transformed,
                account=tgt_acc,
                category_id=target_cat_id,
                overwrite_empty_only=True
            )

            # C. Final sanitization pass
            transformed = sanitize_brand_attributes(tgt_acc, transformed)

            if req.copy_attributes and transformed:
                for k, v in transformed.items():
                    if v and str(v).strip():
                        new_attributes_list.append(
                            models.ProductAttribute(
                                product_ean=prod.ean,
                                account_name=tgt_acc,
                                attribute_name=k,
                                attribute_value=str(v).strip()
                            )
                        )

            # UPSERT ProductMapping
            if not tgt_m:
                tgt_m = models.ProductMapping(
                    product_ean=prod.ean,
                    factory_id=clean_fid,
                    account_name=tgt_acc,
                    source_account=src_acc,
                    product_name=clean_title,
                    price=str(prod.price) if prod.price is not None else None,
                    has_manual_price=False,
                    price_af=str(prod.price_af) if prod.price_af is not None else None,
                    image_url=clean_image,
                    category_id=target_cat_id,
                    status="Not Mapped",
                    last_synced_at=now
                )
                db.add(tgt_m)
                existing_targets[prod.ean] = tgt_m
            else:
                tgt_m.factory_id = clean_fid
                tgt_m.source_account = src_acc
                tgt_m.product_name = clean_title or tgt_m.product_name
                # Protect manual user price
                if not getattr(tgt_m, "has_manual_price", False) and prod.price is not None:
                    tgt_m.price = str(prod.price)
                if prod.price_af is not None:
                    tgt_m.price_af = str(prod.price_af)
                if clean_image:
                    tgt_m.image_url = clean_image
                if target_cat_id:
                    tgt_m.category_id = target_cat_id
                tgt_m.last_synced_at = now

            # Validate in target account
            validation_errors = validate_product(prod.ean, tgt_m.category_id, transformed)
            if not validation_errors:
                tgt_m.status = "Ready"
                tgt_m.missing_attributes = []
                tgt_m.last_error = None
                total_ready += 1
            elif tgt_m.category_id:
                tgt_m.status = "Error"
                tgt_m.missing_attributes = validation_errors
                tgt_m.last_error = "; ".join(validation_errors)
                total_need_attributes += 1
            else:
                tgt_m.status = "Not Mapped"
                tgt_m.missing_attributes = ["Category is not selected"]
                tgt_m.last_error = None
                total_not_mapped += 1

            total_transferred += 1

        if new_attributes_list:
            db.add_all(new_attributes_list)

        db.commit()

        # Invalidate cache for this factory
        await invalidate_products_cache(account=tgt_acc, factory_id=clean_fid)
        if redis_client:
            try:
                redis_client.delete(f"authoritative_eans:{tgt_acc}:{clean_fid}")
                redis_client.delete(f"authoritative_eans:{src_acc}:{clean_fid}")
            except Exception:
                pass

    # Invalidate factories list cache so transferred factories show up immediately
    if redis_client:
        try:
            redis_client.delete("aftercool_factories_cache")
        except Exception:
            pass

    return {
        "status": "success",
        "transferred_count": total_transferred,
        "ready_count": total_ready,
        "need_attributes_count": total_need_attributes,
        "not_mapped_count": total_not_mapped,
        "factory_ids": req.factory_ids,
        "source_account": src_acc,
        "target_account": tgt_acc
    }

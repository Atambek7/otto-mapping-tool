from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from database import get_db
import models
import json
from services.otto_formatter import calculate_msrp

router = APIRouter(
    prefix="/export",
    tags=["export"],
)

@router.get("/factories")
async def get_export_factories(account: str = None, db: Session = Depends(get_db)):
    """Returns a list of factory_ids that have at least one product ready for export"""
    query = db.query(models.ProductMapping.factory_id).filter(
        models.ProductMapping.status == "Ready"
    )
    if account:
        query = query.filter(models.ProductMapping.account_name == account)
    factories = query.distinct().all()
    
    # Extract from tuples
    return [f[0] for f in factories if f[0]]

@router.get("/preview")
async def get_export_preview(factory_id: str = None, account: str = None, db: Session = Depends(get_db)):
    query_all = db.query(models.ProductMapping)
    if factory_id:
        query_all = query_all.filter(models.ProductMapping.factory_id == factory_id)
    if account:
        query_all = query_all.filter(models.ProductMapping.account_name == account)
        
    mappings = query_all.all()
    eans = [m.product_ean for m in mappings]
    
    if not eans:
        return {"ready_count": 0, "error_count": 0, "factory_id": factory_id}
        
    attr_query = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans))
    if account:
        attr_query = attr_query.filter(models.ProductAttribute.account_name == account)
    db_attrs = attr_query.all()

    attr_by_ean = {}
    for a in db_attrs:
        acc = a.account_name or "JV"
        if (a.product_ean, acc) not in attr_by_ean:
            attr_by_ean[(a.product_ean, acc)] = {}
        attr_by_ean[(a.product_ean, acc)][a.attribute_name] = a.attribute_value
        
    from services.validator import validate_product
    from services.brand_guard import validate_brand_safety
    ready_count = 0
    error_count = 0
    
    for m in mappings:
        ean = m.product_ean
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or account or "JV")
        attr_map = attr_by_ean.get((ean, m_acc)) or attr_by_ean.get((ean, "JV"), {})
        errors = validate_product(ean, m.category_id, attr_map)
        brand_errors = validate_brand_safety(m_acc, attr_map)
        if brand_errors:
            errors.extend(brand_errors)
        
        if errors:
            error_count += 1
            if m.status != "Published":
                m.status = "Error"
        else:
            ready_count += 1
            if m.status != "Ready" and m.status != "Published":
                m.status = "Ready"
                
    db.commit()
    
    return {
        "ready_count": ready_count,
        "error_count": error_count,
        "factory_id": factory_id
    }

@router.get("/download")
async def download_export(factory_id: str, format: str = "json", account: str = None, db: Session = Depends(get_db)):
    # Instead of just taking "Ready" status from DB, we re-verify them!
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id
    )
    if account:
        query = query.filter(models.ProductMapping.account_name == account)
    
    mappings = query.all()
    
    # Pre-fetch all attributes for these products
    eans = [m.product_ean for m in mappings]
    if not eans:
        return Response(content="[]", media_type="application/json")
        
    target_acc = account or (mappings[0].account_name.value if hasattr(mappings[0].account_name, "value") else str(mappings[0].account_name)) if mappings else "JV"
    attr_query = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans))
    if account:
        attr_query = attr_query.filter(models.ProductAttribute.account_name == account)
    attrs = attr_query.all()
    
    # Group attributes by (EAN, account)
    attr_by_ean = {}
    for a in attrs:
        acc = a.account_name or "JV"
        if (a.product_ean, acc) not in attr_by_ean:
            attr_by_ean[(a.product_ean, acc)] = {}
        attr_by_ean[(a.product_ean, acc)][a.attribute_name] = a.attribute_value
        
    from services.validator import validate_product
    from services.brand_guard import sanitize_brand_attributes, validate_brand_safety
        
    result = []
    for m in mappings:
        ean = m.product_ean
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or target_acc)
        attr_map = attr_by_ean.get((ean, m_acc)) or attr_by_ean.get((ean, "JV"), {})
        # Brand Guard sanitize and check
        attr_map = sanitize_brand_attributes(m_acc, attr_map)
        brand_errors = validate_brand_safety(m_acc, attr_map)
        errors = validate_product(ean, m.category_id, attr_map)
        if brand_errors:
            errors.extend(brand_errors)
        
        # Only export if it is TRULY valid
        if not errors:
            result.append({
                "EAN": m.product_ean,
                "CategoryId": m.category_id,
                "Attributes": attr_map
            })
            if m.status != "Ready" and m.status != "Published":
                m.status = "Ready"
        else:
            if m.status != "Published":
                m.status = "Error"
                
    db.commit()
        
    if format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
        safe_factory_id = factory_id.replace(" ", "_")
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={safe_factory_id}.json"}
        )
        
    if format == "csv":
        from routers.csv_workflow import fetch_product_info_for_export, get_category_schema_attrs
        from routers.mapping import get_category_by_id
        import io
        import zipfile
        import pandas as pd
        from fastapi.responses import StreamingResponse
        
        product_info = await fetch_product_info_for_export(factory_id)
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            # 1. Categories.csv
            cat_data = []
            for item in result:
                ean = item["EAN"]
                info = product_info.get(ean, {})
                cat = get_category_by_id(item["CategoryId"])
                cat_name = cat.get("name", "") if cat else ""
                
                cat_data.append({
                    "EAN": f'="{ean}"',
                    "Product_Name": info.get("name", ""),
                    "Price": info.get("price", ""),
                    "Category_Name": cat_name,
                    "Category_ID": item["CategoryId"]
                })
            
            if cat_data:
                df_cat = pd.DataFrame(cat_data)
                csv_str = df_cat.to_csv(index=False, sep=";")
                csv_bytes = "\ufeff".encode("utf-8") + csv_str.encode("utf-8")
                zip_file.writestr(f"Categories_{factory_id}.csv", csv_bytes)
                
            # 2. Attributes by category
            by_category = {}
            for item in result:
                cat_id = item["CategoryId"]
                if cat_id not in by_category:
                    by_category[cat_id] = []
                by_category[cat_id].append(item)
                
            for cat_id, items in by_category.items():
                cat = get_category_by_id(cat_id)
                cat_name = cat.get("name", "UnknownCategory").replace("/", "_").replace(" ", "") if cat else cat_id
                req_attrs = get_category_schema_attrs(cat_id)
                
                attr_data = []
                for item in items:
                    ean = item["EAN"]
                    info = product_info.get(ean, {})
                    row = {
                        "EAN": f'="{ean}"',
                        "Product_Name": info.get("name", ""),
                        "Price": info.get("price", "")
                    }
                    for attr_name in req_attrs:
                        row[attr_name] = item["Attributes"].get(attr_name, "")
                    attr_data.append(row)
                    
                df_attr = pd.DataFrame(attr_data)
                csv_str = df_attr.to_csv(index=False, sep=";")
                csv_bytes = "\ufeff".encode("utf-8") + csv_str.encode("utf-8")
                zip_file.writestr(f"Attributes_{cat_name}_{factory_id}.csv", csv_bytes)
                
        zip_buffer.seek(0)
        return StreamingResponse(
            iter([zip_buffer.getvalue()]), 
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=export_{factory_id}.zip"}
        )
        
    if format == "db":
        from services.roma_db import build_roma_sqlite_db
        from routers.csv_workflow import fetch_product_info_for_export
        from routers.mapping import get_category_by_id
        import os
        
        product_info = await fetch_product_info_for_export(factory_id)
        
        items = []
        for item in result:
            ean = item["EAN"]
            info = product_info.get(ean, {})
            cat = get_category_by_id(item["CategoryId"])
            cat_name = cat.get("name", "") if cat else ""
            
            items.append({
                "ean": ean,
                "old_prod_ref": ean,
                "sku": info.get("sku", ean),
                "collection": factory_id,
                "category_id": item["CategoryId"],
                "category_name": cat_name,
                "price_otto": info.get("price", ""),
                "price_af": info.get("startpreis", ""),
                "title": info.get("name", ""),
                "pic_main": info.get("img", "")
            })
            
        target_account = account or "JV"
        path = await build_roma_sqlite_db(items, account=target_account, factory_name=factory_id)
        
        try:
            with open(path, "rb") as f:
                db_bytes = f.read()
                
            return Response(
                content=db_bytes,
                media_type="application/x-sqlite3",
                headers={"Content-Disposition": f"attachment; filename=NEW_BATCH_{target_account}_{factory_id}.db"}
            )
        finally:
            if os.path.exists(path):
                os.remove(path)
            if os.path.exists(path):
                os.remove(path)
    
    return {"error": "Format not supported"}

@router.get("/otto_json/{factory_id}")
async def export_otto_json(factory_id: str, account: str = None, db: Session = Depends(get_db)):
    """
    Generates a proper Otto Market Products V5 JSON for all products of the factory.
    Pulls categories, attributes, prices and images from the database.
    """
    from routers.mapping import get_category_by_id
    
    # Fetch only Ready products for this factory
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id,
        models.ProductMapping.status == "Ready"
    )
    if account:
        query = query.filter(models.ProductMapping.account_name == account)
    mappings = query.all()
    
    if not mappings:
        return Response(content="[]", media_type="application/json")
    # Bulk-fetch ALL attributes for all EANs in one query
    all_eans = [m.product_ean for m in mappings]
    attr_q = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(all_eans)
    )
    if account:
        attr_q = attr_q.filter(models.ProductAttribute.account_name == account)
    all_attrs = attr_q.all()
    
    # Group by (EAN, account)
    attr_by_ean = {}
    for a in all_attrs:
        acc = a.account_name or "JV"
        if (a.product_ean, acc) not in attr_by_ean:
            attr_by_ean[(a.product_ean, acc)] = {}
        attr_by_ean[(a.product_ean, acc)][a.attribute_name] = a.attribute_value
    
    from services.brand_guard import sanitize_brand_attributes

    result = []
    for m in mappings:
        ean = m.product_ean
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or account or "JV")
        attr_map = attr_by_ean.get((ean, m_acc), {})
        attr_map = sanitize_brand_attributes(m_acc, attr_map)
        
        # Get category name
        cat = get_category_by_id(m.category_id) if m.category_id else None
        cat_name = cat.get("name", m.category_id or "") if cat else (m.category_id or "")
        
        # Parse price safely
        raw_price = m.price or "0"
        try:
            price = float(str(raw_price).replace(",", ".").replace("\xa0", "").replace(" ", ""))
        except (ValueError, AttributeError):
            price = 0.0
        
        # Build image list
        images = []
        if m.image_url:
            # image_url may be a comma or pipe separated list
            for url in m.image_url.replace("|", ",").split(","):
                url = url.strip()
                if url:
                    images.append({"url": url, "type": "IMAGE"})
        
        # Format attributes: Otto expects { "AttrName": ["Value"] } strictly matching category schema
        from services.categories_repo import get_allowed_attribute_names
        cat_for_allowed = m.category_id or cat_name
        allowed_attrs = get_allowed_attribute_names(cat_for_allowed) if cat_for_allowed else set()
        allowed_lookup = {str(a).strip().lower(): str(a).strip() for a in allowed_attrs} if allowed_attrs else {}

        formatted_attrs = {}
        for key, value in attr_map.items():
            if value is not None and str(value).strip():
                key_str = str(key).strip()
                if allowed_lookup:
                    canonical_key = allowed_lookup.get(key_str.lower())
                    if not canonical_key:
                        continue
                    key_str = canonical_key
                formatted_attrs[key_str] = [str(value).strip()]
        
        result.append({
            "sku": ean,
            "ean": ean,
            "productReference": ean,
            "category": cat_name,
            "pricing": {
                "standardPrice": {
                    "amount": price,
                    "currency": "EUR"
                },
                "msrp": {
                    "amount": calculate_msrp(price),
                    "currency": "EUR"
                }
            },
            "productDescription": {
                "title": m.product_name or "",
                "description": m.product_name or ""
            },
            "media": {
                "images": images
            },
            "attributes": formatted_attrs,
            "delivery": {
                "type": "PARCEL",
                "deliveryTime": 3
            }
        })
    
    content = json.dumps(result, ensure_ascii=False, indent=2)
    safe_factory_id = factory_id.replace(" ", "_")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={safe_factory_id}.json"}
    )

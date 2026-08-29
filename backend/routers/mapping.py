from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from database import get_db
import models
import auth
from pydantic import BaseModel
import asyncio
from database import SessionLocal
from services.automapper import auto_map_product, predict_category, extract_attributes, format_product_info
from services.okb_sync import sync_product_from_okb
import openai

router = APIRouter(
    prefix="/mapping",
    tags=["mapping"],
)

# ====== Pydantic schemas ======

class CategoryMappingBase(BaseModel):
    product_ean: str
    factory_id: str
    account: str = "JV"
    category_id: str

class CategoryMappingBulk(BaseModel):
    product_eans: List[str]
    factory_id: str
    account: str = "JV"
    category_id: str

class BulkDeleteRequest(BaseModel):
    product_eans: List[str]

class AttributeMappingBase(BaseModel):
    product_ean: str
    attributes: Dict[str, str]

class AttributeMappingBulk(BaseModel):
    product_eans: List[str]
    attributes: Dict[str, str]

# ====== Routes ======

class BulkExportRequest(BaseModel):
    factory_ids: List[str]

@router.post("/bulk/export")
async def bulk_export(format: str, req: BulkExportRequest, db: Session = Depends(get_db)):
    from fastapi.responses import StreamingResponse
    import json
    import pandas as pd
    import io
    import sqlite3
    import tempfile
    import os

    if not req.factory_ids:
        raise HTTPException(status_code=400, detail="No factory IDs provided")
    
    mappings = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id.in_(req.factory_ids)).all()
    
    if format == "json":
        data = [m.__dict__ for m in mappings]
        for d in data:
            d.pop("_sa_instance_state", None)
        json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
        response = StreamingResponse(iter([json_bytes]), media_type="application/json")
        response.headers["Content-Disposition"] = "attachment; filename=bulk_export.json"
        return response

    elif format == "csv":
        data = []
        for m in mappings:
            data.append({
                "EAN": f'="{m.product_ean}"',
                "Factory": m.factory_id,
                "Category_ID": m.category_id or "",
                "Status": m.status,
                "Errors": m.validation_errors or ""
            })
        df = pd.DataFrame(data)
        stream = io.StringIO()
        df.to_csv(stream, index=False, sep=";")
        csv_bytes = "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")
        response = StreamingResponse(iter([csv_bytes]), media_type="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = "attachment; filename=bulk_export.csv"
        return response

    elif format == "db":
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE mappings (
            product_ean TEXT,
            factory_id TEXT,
            category_id TEXT,
            status TEXT,
            validation_errors TEXT
        )''')
        for m in mappings:
            cur.execute("INSERT INTO mappings VALUES (?, ?, ?, ?, ?)", 
                        (m.product_ean, m.factory_id, m.category_id, m.status, m.validation_errors))
        conn.commit()
        conn.close()
        
        def iterfile():
            with open(path, mode="rb") as file_like:
                yield from file_like
            os.remove(path)
            
        return StreamingResponse(iterfile(), media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename=bulk_export.sqlite"})
        
    raise HTTPException(status_code=400, detail="Invalid format")

class AutoMapProductModel(BaseModel):
    ean: str
    title: str
    description: str = ""
    specifics: str
    gallery_url: str
    picture_urls: str

class AutoMapBulkRequest(BaseModel):
    factory_id: str
    account: str = "JV"
    products: List[AutoMapProductModel]
    only_required: bool = False
    use_vision: bool = False
    target: str = "all"

@router.post("/auto-map")
async def auto_map_bulk(req: AutoMapBulkRequest, current_user: models.User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not current_user.openai_api_key:
        raise HTTPException(
            status_code=400, 
            detail="OPENAI_API_KEY_MISSING",
            headers={"x-error-code": "OPENAI_API_KEY_MISSING"}
        )
    custom_api_key = current_user.openai_api_key
    
    # 1. Pre-fetch all necessary data to avoid N+1 and concurrent DB locks
    eans = [p.ean for p in req.products]
    
    existing_mappings = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean.in_(eans)).all()
    mapping_dict = {m.product_ean: m for m in existing_mappings}
    
    existing_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all()
    attrs_val_dict = {ean: {} for ean in eans}
    attrs_orm_dict = {ean: {} for ean in eans}
    for a in existing_attrs:
        attrs_val_dict[a.product_ean][a.attribute_name] = a.attribute_value
        attrs_orm_dict[a.product_ean][a.attribute_name] = a

    sem = asyncio.Semaphore(10)
    
    async def process(p: AutoMapProductModel):
        async with sem:
            m = mapping_dict.get(p.ean)
            cat_id = m.category_id if m else None
            p_attrs = attrs_val_dict.get(p.ean, {})
            
            return await auto_map_product(
                p.ean, p.title, p.description, p.specifics, p.gallery_url, p.picture_urls, 
                req.factory_id, 
                existing_category_id=cat_id, 
                existing_attrs_dict=p_attrs, 
                only_required=req.only_required, 
                use_vision=req.use_vision, 
                target=req.target, 
                custom_api_key=custom_api_key
            )
                
    # 2. Run all AI calls concurrently (no DB access inside!)
    try:
        results = await asyncio.gather(*(process(p) for p in req.products))
    except openai.OpenAIError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
            headers={"x-error-code": "OPENAI_API_ERROR"}
        )
    
    # 3. Apply all updates to the DB in one single batch
    for res in results:
        if res.get("status") in ["success", "partial"] and "db_updates" in res:
            ean = res["ean"]
            updates = res["db_updates"]
            
            # Update mapping
            db_mapping = mapping_dict.get(ean)
            if not db_mapping:
                db_mapping = models.ProductMapping(product_ean=ean, factory_id=req.factory_id, account_name=req.account)
                db.add(db_mapping)
                mapping_dict[ean] = db_mapping
                
            db_mapping.category_id = updates.get("category_id")
            db_mapping.status = updates.get("mapping_status")
            
            # Insert or update attributes
            for k, v in updates.get("attributes_to_save", {}).items():
                db_attr = attrs_orm_dict.get(ean, {}).get(k)
                if db_attr:
                    db_attr.attribute_value = v
                else:
                    new_attr = models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=v)
                    db.add(new_attr)
                    attrs_orm_dict.setdefault(ean, {})[k] = new_attr

    db.commit()
    
    # Remove db_updates from results before sending to frontend
    for res in results:
        res.pop("db_updates", None)
        
    return {"status": "success", "results": results}

class SyncOkbBulkRequest(BaseModel):
    product_eans: List[str]
    factory_id: str
    account: str = "JV"

@router.post("/sync-okb-bulk")
async def sync_okb_bulk(req: SyncOkbBulkRequest, db: Session = Depends(get_db)):
    sem = asyncio.Semaphore(5)
    
    async def process(ean: str):
        async with sem:
            result = await sync_product_from_okb(ean, req.factory_id, req.account)
            await asyncio.sleep(0.3)
            return result
                
    results = await asyncio.gather(*(process(ean) for ean in req.product_eans))
    
    # Pre-fetch existing mappings
    eans = [r["ean"] for r in results if r.get("status") == "success"]
    existing_mappings = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean.in_(eans)).all()
    mapping_dict = {m.product_ean: m for m in existing_mappings}
    
    # Fetch existing attributes to avoid duplicates
    existing_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all()
    attrs_orm_dict = {ean: {} for ean in eans}
    for a in existing_attrs:
        attrs_orm_dict[a.product_ean][a.attribute_name] = a

    from services.validator import validate_product

    for res in results:
        if res.get("status") == "success":
            ean = res["ean"]
            cat_id = res["category_id"]
            attributes = res["attributes"]
            
            # Update mapping
            db_mapping = mapping_dict.get(ean)
            if not db_mapping:
                db_mapping = models.ProductMapping(
                    product_ean=ean, factory_id=req.factory_id, account_name=req.account
                )
                db.add(db_mapping)
                mapping_dict[ean] = db_mapping
                
            db_mapping.category_id = cat_id
            
            # Upsert attributes
            for k, v in attributes.items():
                if not v.strip():
                    continue
                db_attr = attrs_orm_dict.get(ean, {}).get(k)
                if db_attr:
                    db_attr.attribute_value = v
                else:
                    new_attr = models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=v)
                    db.add(new_attr)
                    attrs_orm_dict.setdefault(ean, {})[k] = new_attr
                    
            # Delete old attributes not in new payload? Let's keep it simple and just overwrite.
            # (OKB sync normally replaces all, but for now we just upsert).
            
            errors = validate_product(ean, cat_id, attributes)
            db_mapping.status = "Error" if errors else "Ready"
            res["errors"] = errors
            res["attributes_count"] = len(attributes)
            
    db.commit()
    
    return {"status": "success", "results": results}


@router.post("/predict-category")
async def api_predict_category(req: AutoMapProductModel, current_user: models.User = Depends(auth.get_current_user)):
    if not current_user.openai_api_key:
        raise HTTPException(
            status_code=400, 
            detail="OPENAI_API_KEY_MISSING",
            headers={"x-error-code": "OPENAI_API_KEY_MISSING"}
        )
    product_info = format_product_info(req.title, req.description, req.specifics, req.gallery_url, req.picture_urls)
    try:
        category_id = await predict_category(product_info, req.title, custom_api_key=current_user.openai_api_key)
    except openai.OpenAIError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
            headers={"x-error-code": "OPENAI_API_ERROR"}
        )
    if not category_id:
        raise HTTPException(status_code=400, detail="Could not predict category")
    return {"status": "success", "category_id": category_id}

class ExtractAttributesRequest(AutoMapProductModel):
    category_id: str
    use_vision: bool = False

@router.post("/extract-attributes")
async def api_extract_attributes(req: ExtractAttributesRequest, current_user: models.User = Depends(auth.get_current_user)):
    if not current_user.openai_api_key:
        raise HTTPException(
            status_code=400, 
            detail="OPENAI_API_KEY_MISSING",
            headers={"x-error-code": "OPENAI_API_KEY_MISSING"}
        )
    product_info = format_product_info(req.title, req.description, req.specifics, req.gallery_url, req.picture_urls)
    try:
        attributes = await extract_attributes(product_info, req.category_id, use_vision=req.use_vision, gallery_url=req.gallery_url, custom_api_key=current_user.openai_api_key)
    except openai.OpenAIError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
            headers={"x-error-code": "OPENAI_API_ERROR"}
        )
    if attributes is None:
        raise HTTPException(status_code=400, detail="Could not extract attributes")
    return {"status": "success", "attributes": attributes}

@router.post("/category")
async def assign_category(mapping: CategoryMappingBase, db: Session = Depends(get_db)):
    db_mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == mapping.product_ean).first()
    
    if db_mapping:
        db_mapping.category_id = mapping.category_id
        db_mapping.status = "Need Attributes"
    else:
        db_mapping = models.ProductMapping(
            product_ean=mapping.product_ean,
            factory_id=mapping.factory_id,
            account_name=mapping.account,
            category_id=mapping.category_id,
            status="Need Attributes"
        )
        db.add(db_mapping)
    
    db.commit()
    return {"status": "success"}

@router.post("/category/bulk")
async def assign_category_bulk(mapping: CategoryMappingBulk, db: Session = Depends(get_db)):
    for ean in mapping.product_eans:
        db_mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == ean).first()
        if db_mapping:
            db_mapping.category_id = mapping.category_id
            db_mapping.status = "Need Attributes"
        else:
            db_mapping = models.ProductMapping(
                product_ean=ean,
                factory_id=mapping.factory_id,
                account_name=mapping.account,
                category_id=mapping.category_id,
                status="Need Attributes"
            )
            db.add(db_mapping)
    db.commit()
    return {"status": "success", "count": len(mapping.product_eans)}

@router.post("/bulk/delete")
async def delete_mapping_bulk(req: BulkDeleteRequest, db: Session = Depends(get_db)):
    if not req.product_eans:
        return {"status": "success", "deleted": 0}
        
    # Delete attributes
    db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(req.product_eans)
    ).delete(synchronize_session=False)
    
    # Delete mappings
    deleted_count = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(req.product_eans)
    ).delete(synchronize_session=False)
    
    db.commit()
    return {"status": "success", "deleted": deleted_count}

from services.categories_repo import get_category_by_id, get_categories, get_groups

@router.get("/categories")
async def search_categories(q: str = ""):
    q_lower = q.lower()
    results = []
    for c in get_categories():
        name = c.get("name", "")
        group = c.get("category_group", "")
        if q_lower in name.lower() or q_lower in group.lower():
            results.append({
                "id": str(c.get("categoryId")),
                "name": name,
                "group": group,
                "group_id": str(c.get("category_group_id"))
            })
            if len(results) >= 50:
                break
    return results

@router.get("/categories/{category_id}")
async def get_category(category_id: str):
    cat = get_category_by_id(category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return {
        "id": str(cat.get("categoryId")),
        "name": cat.get("name", ""),
        "group": cat.get("category_group", ""),
        "group_id": str(cat.get("category_group_id"))
    }

@router.get("/categories/{category_id}/attributes")
async def get_category_attributes(category_id: str):
    cat = get_category_by_id(category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    
    group_id = str(cat.get("category_group_id"))
    group_data = get_groups().get(group_id)
    if not group_data:
        return []
    
    # Map OKB attributes to our frontend schema format
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    schema = []
    for a in raw_attrs:
        is_req = a.get("relevance") == "HIGH"
        # Force Brand to be required based on typical marketplace rules, or rely on 'HIGH'
        # Often 'Marke' is universally required
        if a.get("name") == "Marke":
            is_req = True
            
        attr_type = "text"
        if a.get("type") == "FLOAT" or a.get("type") == "INTEGER":
            attr_type = "number"
        elif a.get("allowedValues"):
            attr_type = "select"
            
        schema.append({
            "id": str(a.get("attributeId")),
            "name": a.get("name"),
            "type": attr_type,
            "required": is_req,
            "options": [v if isinstance(v, str) else v.get("value", str(v)) for v in a.get("allowedValues", [])] if a.get("allowedValues") else None
        })
    return schema

@router.get("/product/{product_ean}")
async def get_product_mapping(product_ean: str, db: Session = Depends(get_db)):
    mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == product_ean).first()
    if not mapping:
        return {"category_id": None, "category_name": None, "attributes": {}, "status": "Unmapped"}
    
    attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == product_ean).all()
    attr_dict = {a.attribute_name: a.attribute_value for a in attrs}
    
    cat = get_category_by_id(mapping.category_id)
    cat_name = cat.get("name", mapping.category_id) if cat else mapping.category_id
    
    return {
        "category_id": mapping.category_id,
        "category_name": cat_name,
        "attributes": attr_dict,
        "status": mapping.status
    }

@router.get("/factory/{factory_id}")
async def get_factory_mappings(factory_id: str, db: Session = Depends(get_db)):
    mappings = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == factory_id).all()
    result = {}
    for m in mappings:
        # Get category name
        cat = get_category_by_id(m.category_id)
        cat_name = cat.get("name", m.category_id) if cat else m.category_id
        result[m.product_ean] = {
            "category_id": m.category_id,
            "category_name": cat_name,
            "status": m.status
        }
    return result

@router.post("/attributes")
async def save_attributes(mapping: AttributeMappingBase, db: Session = Depends(get_db)):
    # Clear old attributes
    db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == mapping.product_ean).delete()
    
    # Save new
    for k, v in mapping.attributes.items():
        if v.strip(): # Only save non-empty
            db.add(models.ProductAttribute(product_ean=mapping.product_ean, attribute_name=k, attribute_value=v))
        
    db_mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == mapping.product_ean).first()
    if not db_mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
        
    # --- Validation Logic ---
    from services.validator import validate_product
    errors = validate_product(mapping.product_ean, db_mapping.category_id, mapping.attributes)
    
    if errors:
        db_mapping.status = "Error"
    else:
        db_mapping.status = "Ready"
        
    db.commit()
    return {"status": "success", "errors": errors}

@router.post("/attributes/bulk")
async def save_attributes_bulk(mapping: AttributeMappingBulk, db: Session = Depends(get_db)):
    from services.validator import validate_product
    
    keys = list(mapping.attributes.keys())
    
    for ean in mapping.product_eans:
        # Delete old matching attributes for this EAN for the keys being updated
        if keys:
            db.query(models.ProductAttribute).filter(
                models.ProductAttribute.product_ean == ean,
                models.ProductAttribute.attribute_name.in_(keys)
            ).delete(synchronize_session=False)
            
        # Save new
        for k, v in mapping.attributes.items():
            if v.strip(): # Only save non-empty
                db.add(models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=v))
                
        # Re-validate
        db_mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == ean).first()
        if db_mapping:
            # get all attributes
            all_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == ean).all()
            attr_dict = {a.attribute_name: a.attribute_value for a in all_attrs}
            errors = validate_product(ean, db_mapping.category_id, attr_dict)
            if errors:
                db_mapping.status = "Error"
            else:
                db_mapping.status = "Ready"

    db.commit()
    return {"status": "success"}

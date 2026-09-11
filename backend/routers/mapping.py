import os
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response, FileResponse
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from database import get_db
import models
import auth
from pydantic import BaseModel
import asyncio
from database import SessionLocal
from services.automapper import auto_map_product, predict_category, extract_attributes, format_product_info
from services.okb_sync import sync_product_from_okb
from services.constant_attributes import get_defaults_for_account

def ensure_default_attributes(db: Session, ean: str, account: str):
    defaults = get_defaults_for_account(account)
    existing = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == ean).all()
    existing_map = {a.attribute_name: a for a in existing}
    for k, v in defaults.items():
        if k not in existing_map:
            db.add(models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=v))
        elif not str(existing_map[k].attribute_value).strip():
            existing_map[k].attribute_value = v
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
        from services.roma_db import build_roma_sqlite_db
        items = []
        for m in mappings:
            items.append({
                "ean": m.product_ean,
                "old_prod_ref": m.product_ean,
                "sku": m.product_ean,
                "collection": m.factory_id,
                "category_id": m.category_id,
                "price_otto": m.price,
                "price_af": m.price_af or m.price,
                "title": m.product_name,
                "pic_main": m.image_url
            })
        target_account = mappings[0].account_name if mappings else "JV"
        path = await build_roma_sqlite_db(items, account=target_account)
        try:
            with open(path, "rb") as f:
                content = f.read()
        finally:
            if os.path.exists(path):
                os.remove(path)
        return Response(
            content=content, 
            media_type="application/x-sqlite3", 
            headers={"Content-Disposition": f"attachment; filename=NEW_BATCH_{target_account}.db"}
        )
        
    raise HTTPException(status_code=400, detail="Invalid format")

class ExportDbRequest(BaseModel):
    product_eans: List[str] = []

@router.api_route("/export-db/{factory_id}", methods=["GET", "POST"])
async def export_factory_db(
    factory_id: str, 
    account: str = "JV", 
    factory_name: str = "",
    req: ExportDbRequest = None,
    db: Session = Depends(get_db)
):
    from services.roma_db import build_roma_sqlite_db
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id,
        models.ProductMapping.account_name == account
    )
    if req and req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))

    mappings = query.all()
    if not mappings:
        from routers.aftercool import force_sync_metadata
        try:
            await force_sync_metadata(factory_id=factory_id, account=account, dataset="lister", db=db)
            query = db.query(models.ProductMapping).filter(
                models.ProductMapping.factory_id == factory_id,
                models.ProductMapping.account_name == account
            )
            if req and req.product_eans:
                query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
            mappings = query.all()
        except Exception as e:
            print(f"Auto-sync metadata for export-db failed: {e}")

    target_name = factory_name or factory_id
    items = []
    for m in mappings:
        items.append({
            "ean": m.product_ean,
            "product": m.product_name,
            "title": m.product_name,
            "price_otto": m.price,
            "price_af": m.price_af,
            "price": m.price or m.price_af,
            "collection": target_name,
            "category_id": m.category_id,
            "pic_main": m.image_url
        })
    path = await build_roma_sqlite_db(items, account=account, factory_name=target_name)
    try:
        with open(path, "rb") as f:
            content = f.read()
    finally:
        if os.path.exists(path):
            os.remove(path)
    safe_name = "".join(c for c in target_name if c.isalnum() or c in ("_", "-"))
    return Response(
        content=content, 
        media_type="application/x-sqlite3", 
        headers={"Content-Disposition": f"attachment; filename=NEW_BATCH_{account}_{safe_name}.db"}
    )

@router.post("/export-db/start/{factory_id}")
async def start_export_factory_db(
    factory_id: str,
    account: str = "JV",
    factory_name: str = "",
    req: ExportDbRequest = None,
    db: Session = Depends(get_db)
):
    from tasks import export_factory_db_task
    eans = req.product_eans if req else []
    try:
        task = export_factory_db_task.delay(
            factory_id=str(factory_id),
            account=str(account),
            factory_name=str(factory_name or factory_id),
            product_eans=eans if eans else None
        )
        return {
            "status": "queued",
            "task_id": task.id,
            "message": "Export task started in background"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start export task: {e}")

@router.get("/export-db/status/{task_id}")
async def get_export_db_status(task_id: str):
    from celery.result import AsyncResult
    task_result = AsyncResult(task_id)
    response = {
        "task_id": task_id,
        "status": task_result.status,
        "result": task_result.result if task_result.ready() else None
    }
    if task_result.status == "PROGRESS":
        response["progress"] = task_result.info
    elif task_result.status == "FAILURE":
        response["error"] = str(task_result.info)
    return response

@router.get("/export-db/download/{task_id}")
async def download_export_db(task_id: str):
    from celery.result import AsyncResult
    clean_id = "".join(c for c in task_id if c.isalnum() or c in ("-", "_"))
    file_path = os.path.join(os.path.dirname(__file__), "..", "static", "exports", f"{clean_id}.db")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export file not found or expired")

    filename = "NEW_BATCH_export.db"
    tr = AsyncResult(task_id)
    if tr.ready() and isinstance(tr.result, dict) and tr.result.get("filename"):
        filename = tr.result["filename"]

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/x-sqlite3"
    )

@router.post("/db/import")
async def import_db(
    file: UploadFile = File(...),
    factory_id: str = Form(None),
    account: str = Form("JV"),
    db: Session = Depends(get_db)
):
    import shutil
    import tempfile
    import sqlite3
    from services.roma_db import import_roma_sqlite_db
    fd, temp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Check whether this DB is Roma's NEW_BATCH or an attributes DB
        conn = sqlite3.connect(temp_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='NEW_BATCH'")
        has_new_batch = cur.fetchone() is not None
        conn.close()

        if has_new_batch:
            result = import_roma_sqlite_db(temp_path, factory_id=factory_id, account=account, db=db)
            return result
        else:
            from routers.csv_workflow import process_attribute_import_from_db
            result = process_attribute_import_from_db(temp_path, factory_id=factory_id, db=db)
            return result
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

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
    products_data: Optional[List[Dict[str, Any]]] = None

@router.post("/sync-okb-bulk")
async def sync_okb_bulk(req: SyncOkbBulkRequest, db: Session = Depends(get_db)):
    row_dict = {}
    if req.products_data:
        for pd in req.products_data:
            if isinstance(pd, dict) and pd.get("ean"):
                row_dict[str(pd["ean"])] = pd.get("row")

    from services.okb_sync import sync_product_data
    sem = asyncio.Semaphore(5)
    
    async def process(ean: str):
        async with sem:
            row = row_dict.get(str(ean))
            result = await sync_product_data(ean, req.factory_id, req.account, row=row)
            await asyncio.sleep(0.1)
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
            cat_id = res.get("category_id")
            attributes = res.get("attributes", {})
            
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
                if not str(v).strip():
                    continue
                db_attr = attrs_orm_dict.get(ean, {}).get(k)
                if db_attr:
                    db_attr.attribute_value = str(v).strip()
                else:
                    new_attr = models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=str(v).strip())
                    db.add(new_attr)
                    attrs_orm_dict.setdefault(ean, {})[k] = new_attr
                    
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
    
    ensure_default_attributes(db, mapping.product_ean, mapping.account)
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
        ensure_default_attributes(db, ean, mapping.account)
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
async def get_factory_mappings(factory_id: str, account: str = None, db: Session = Depends(get_db)):
    query = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == factory_id)
    if account:
        query = query.filter(models.ProductMapping.account_name == account)
    mappings = query.all()
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

    if not mapping.product_eans:
        return {"status": "success"}

    keys = [k for k, v in mapping.attributes.items() if v and v.strip()]
    if not keys:
        return {"status": "success"}

    # 1. Bulk delete old matching attributes in a single query
    db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(mapping.product_eans),
        models.ProductAttribute.attribute_name.in_(keys)
    ).delete(synchronize_session=False)

    # 2. Bulk add new attributes
    new_attrs = [
        models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=mapping.attributes[k].strip())
        for ean in mapping.product_eans
        for k in keys
    ]
    if new_attrs:
        db.add_all(new_attrs)
    db.flush()

    # 3. Bulk re-validate all affected mappings
    mappings_to_validate = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(mapping.product_eans)
    ).all()
    all_db_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(mapping.product_eans)
    ).all()

    attrs_by_ean = {}
    for a in all_db_attrs:
        if a.product_ean not in attrs_by_ean:
            attrs_by_ean[a.product_ean] = {}
        attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

    for m in mappings_to_validate:
        attr_dict = attrs_by_ean.get(m.product_ean, {})
        errors = validate_product(m.product_ean, m.category_id, attr_dict)
        m.status = "Error" if errors else "Ready"

    db.commit()
    return {"status": "success"}


class MassEditSchemaRequest(BaseModel):
    product_eans: List[str]

@router.post("/mass-edit/schema")
async def get_mass_edit_schema(req: MassEditSchemaRequest, db: Session = Depends(get_db)):
    """
    Returns the intersection of attribute schemas across all categories of selected products,
    along with each product's current attribute values for those shared attributes.
    """
    if not req.product_eans:
        return {"shared_attrs": [], "current_values": {}, "categories": []}

    mappings_list = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(req.product_eans)
    ).all()

    if not mappings_list:
        return {"shared_attrs": [], "current_values": {}, "categories": [], "unmapped_eans": req.product_eans}

    cat_to_eans: dict = {}
    ean_to_cat: dict = {}
    for m in mappings_list:
        if not m.category_id:
            continue
        cid = str(m.category_id)
        cat_to_eans.setdefault(cid, []).append(m.product_ean)
        ean_to_cat[m.product_ean] = cid

    if not cat_to_eans:
        return {"shared_attrs": [], "current_values": {}, "categories": [], "unmapped_eans": req.product_eans}

    from services.categories_repo import get_category_by_id, get_groups

    def load_schema_for_cat(cat_id: str) -> list:
        cat = get_category_by_id(cat_id)
        if not cat:
            return []
        group_id = str(cat.get("category_group_id", ""))
        group_data = get_groups().get(group_id)
        if not group_data:
            return []
        raw_attrs = group_data.get("attributes", {}).get("attributes", [])
        result = []
        for a in raw_attrs:
            is_req = a.get("relevance") == "HIGH" or a.get("name") == "Marke"
            attr_type = "text"
            if a.get("type") in ("FLOAT", "INTEGER"):
                attr_type = "number"
            elif a.get("allowedValues"):
                attr_type = "select"
            result.append({
                "id": str(a.get("attributeId", "")),
                "name": a.get("name", ""),
                "type": attr_type,
                "required": is_req,
                "options": [
                    v if isinstance(v, str) else v.get("value", str(v))
                    for v in a.get("allowedValues", [])
                ] if a.get("allowedValues") else None
            })
        return result

    schemas_by_cat: dict = {}
    for cid in cat_to_eans.keys():
        schemas_by_cat[cid] = load_schema_for_cat(cid)

    all_cat_ids = list(cat_to_eans.keys())

    if len(all_cat_ids) == 1:
        shared_names = {a["name"] for a in schemas_by_cat[all_cat_ids[0]]}
    else:
        sets = [set(a["name"] for a in schemas_by_cat[cid]) for cid in all_cat_ids]
        shared_names = sets[0].intersection(*sets[1:])

    first_schema = schemas_by_cat[all_cat_ids[0]]
    required_in_any = set()
    for schema in schemas_by_cat.values():
        for a in schema:
            if a["required"]:
                required_in_any.add(a["name"])

    shared_attrs = []
    for a in first_schema:
        if a["name"] in shared_names:
            shared_attrs.append({
                **a,
                "required": a["name"] in required_in_any,
                "from_multiple_cats": len(all_cat_ids) > 1
            })

    db_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(req.product_eans)
    ).all()

    current_values: dict = {}
    for a in db_attrs:
        if a.attribute_name in shared_names:
            current_values.setdefault(a.product_ean, {})[a.attribute_name] = a.attribute_value

    cat_infos = []
    for cid, eans in cat_to_eans.items():
        cat = get_category_by_id(cid)
        cat_name = cat.get("name", cid) if cat else cid
        cat_infos.append({"id": cid, "name": cat_name, "ean_count": len(eans)})

    unmapped_eans = [e for e in req.product_eans if e not in ean_to_cat]

    return {
        "shared_attrs": shared_attrs,
        "current_values": current_values,
        "categories": cat_infos,
        "unmapped_eans": unmapped_eans,
        "total_eans": len(req.product_eans),
        "mapped_eans": len([e for e in req.product_eans if e in ean_to_cat])
    }

import os
import datetime
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Response
from fastapi.responses import StreamingResponse, Response, FileResponse
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from collections import defaultdict
from database import get_db
import models
import auth
from pydantic import BaseModel
import asyncio
from database import SessionLocal
from services.automapper import auto_map_product, predict_category, extract_attributes, format_product_info
from services.okb_sync import sync_product_from_okb
from services.constant_attributes import (
    get_defaults_for_account,
    get_presets_payload,
    get_attributes_for_apply,
    apply_default_attributes
)
from services.brand_guard import validate_brand_safety, sanitize_brand_attributes, prepare_attributes_for_transfer, is_forbidden_warranty_attribute

from services.categories_repo import get_allowed_attribute_names, get_category_by_id

def is_same_category(cat1, cat2):
    if not cat1 or not cat2:
        return False
    if str(cat1).strip() == str(cat2).strip():
        return True
    c1 = get_category_by_id(cat1)
    c2 = get_category_by_id(cat2)
    id1 = str(c1.get("categoryId")) if c1 else str(cat1)
    id2 = str(c2.get("categoryId")) if c2 else str(cat2)
    return id1 == id2

def ensure_default_attributes(db: Session, ean: str, account: str, category_id: str = None, attrs_map: dict = None):
    defaults = get_defaults_for_account(account)
    if not category_id:
        pm = db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean == ean,
            models.ProductMapping.account_name == account
        ).first()
        if pm:
            category_id = pm.category_id

    allowed = get_allowed_attribute_names(category_id) if category_id else None

    if attrs_map is not None:
        existing_map = {k.strip().lower(): v for k, v in attrs_map.items()}
    else:
        existing = db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean == ean,
            models.ProductAttribute.account_name == account
        ).all()
        existing_map = {a.attribute_name.strip().lower(): a for a in existing}

    for k, v in defaults.items():
        if is_forbidden_warranty_attribute(k):
            continue
        if allowed is not None and k not in allowed:
            continue
        k_lower = k.strip().lower()
        if k_lower not in existing_map:
            new_attr = models.ProductAttribute(product_ean=ean, account_name=account, attribute_name=k, attribute_value=v)
            db.add(new_attr)
            if attrs_map is not None:
                attrs_map[k] = new_attr
            existing_map[k_lower] = new_attr
        elif not str(existing_map[k_lower].attribute_value).strip():
            existing_map[k_lower].attribute_value = v
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
    account: str = "JV"

class AttributeMappingBase(BaseModel):
    product_ean: str
    account: str = "JV"
    attributes: Dict[str, str]
    ai_attributes: Optional[List[str]] = None

class AttributeMappingBulk(BaseModel):
    product_eans: List[str]
    account: str = "JV"
    attributes: Dict[str, str]
    ai_attributes: Optional[List[str]] = None

class ApplyDefaultsRequest(BaseModel):
    product_eans: List[str]
    account: str = "JV"
    target_group: Optional[str] = "Erwachsene ; Jugendliche"
    design: Optional[str] = None
    overwrite_existing: bool = False

class TransferAccountRequest(BaseModel):
    source_account: models.AccountType = models.AccountType.JV
    target_account: models.AccountType = models.AccountType.XL
    factory_id: Optional[str] = None
    target_factory_id: Optional[str] = None
    product_eans: Optional[List[str]] = None
    only_ready: bool = False
    copy_categories: bool = True
    copy_attributes: bool = True

# ====== Routes ======

class BulkExportRequest(BaseModel):
    factory_ids: List[str]
    account: str = "JV"

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
    
    target_account = req.account or "JV"
    mappings = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id.in_(req.factory_ids),
        models.ProductMapping.account_name == target_account
    ).all()
    
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
                "Errors": getattr(m, "validation_errors", "") or ""
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
            result = process_attribute_import_from_db(temp_path, factory_id=factory_id, db=db, account=account)
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
    category_id: Optional[str] = None

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
    eans = [p.ean.strip() for p in req.products if p.ean and p.ean.strip()]
    
    existing_mappings = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(eans),
        models.ProductMapping.account_name == req.account
    ).all()
    mapping_dict = {m.product_ean: m for m in existing_mappings}
    
    existing_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(eans),
        models.ProductAttribute.account_name == req.account
    ).all()
    attrs_val_dict = {ean: {} for ean in eans}
    attrs_orm_dict = {ean: {} for ean in eans}
    for a in existing_attrs:
        attrs_val_dict[a.product_ean][a.attribute_name] = a.attribute_value
        attrs_orm_dict[a.product_ean][a.attribute_name] = a

    sem = asyncio.Semaphore(10)
    
    async def process(p: AutoMapProductModel):
        async with sem:
            m = mapping_dict.get(p.ean)
            cat_id = p.category_id or (m.category_id if m else None)
            if cat_id:
                cat_obj = get_category_by_id(cat_id)
                if cat_obj and cat_obj.get("categoryId"):
                    cat_id = str(cat_obj["categoryId"])
            p_attrs = attrs_val_dict.get(p.ean, {})
            
            try:
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
            except Exception as e:
                import traceback
                traceback.print_exc()
                return {
                    "status": "error",
                    "ean": p.ean,
                    "message": str(e),
                    "db_updates": {}
                }
                
    # 2. Run all AI calls concurrently (no DB access inside!)
    try:
        results = await asyncio.gather(*(process(p) for p in req.products))
    except openai.OpenAIError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
            headers={"x-error-code": "OPENAI_API_ERROR"}
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
            headers={"x-error-code": "AI_MAPPING_ERROR"}
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
            elif req.factory_id and not db_mapping.factory_id:
                db_mapping.factory_id = req.factory_id
                
            new_cat_id = updates.get("category_id")
            if new_cat_id and db_mapping.category_id and not is_same_category(db_mapping.category_id, new_cat_id):
                # Category changed during auto-map: clear attributes of the old category and ai_attributes
                db.query(models.ProductAttribute).filter(
                    models.ProductAttribute.product_ean == ean,
                    models.ProductAttribute.account_name == req.account
                ).delete(synchronize_session=False)
                db_mapping.ai_attributes = []
                attrs_orm_dict[ean] = {}
            if new_cat_id:
                db_mapping.category_id = new_cat_id
            if updates.get("mapping_status"):
                db_mapping.status = updates.get("mapping_status")
            
            # Ensure standard account defaults are populated for the category
            if new_cat_id:
                ensure_default_attributes(db, ean, req.account, new_cat_id, attrs_map=attrs_orm_dict.setdefault(ean, {}))

            # Insert or update attributes
            for k, v in updates.get("attributes_to_save", {}).items():
                if is_forbidden_warranty_attribute(k):
                    continue
                k_clean = str(k).strip()
                v_clean = str(v).strip()
                if not v_clean or v_clean.lower() == "null":
                    continue
                db_attr = attrs_orm_dict.get(ean, {}).get(k_clean)
                if not db_attr:
                    k_lower = k_clean.lower()
                    for ex_k, ex_obj in list(attrs_orm_dict.get(ean, {}).items()):
                        if ex_k.strip().lower() == k_lower:
                            db_attr = ex_obj
                            break
                if db_attr:
                    db_attr.attribute_value = v_clean
                else:
                    new_attr = models.ProductAttribute(product_ean=ean, account_name=req.account, attribute_name=k_clean, attribute_value=v_clean)
                    db.add(new_attr)
                    attrs_orm_dict.setdefault(ean, {})[k_clean] = new_attr

            # Re-validate with defaults included to ensure accurate Ready/Error status
            if new_cat_id:
                from services.validator import validate_product
                all_current = {
                    a.attribute_name: a.attribute_value 
                    for a in db.query(models.ProductAttribute).filter(
                        models.ProductAttribute.product_ean == ean,
                        models.ProductAttribute.account_name == req.account
                    ).all()
                }
                for k, v in updates.get("attributes_to_save", {}).items():
                    if not is_forbidden_warranty_attribute(k) and v and str(v).strip():
                        all_current[k] = str(v).strip()
                val_errs = validate_product(ean, new_cat_id, all_current)
                db_mapping.status = "Error" if val_errs else "Ready"
                from services.validator import get_missing_required_attributes
                db_mapping.missing_attributes = get_missing_required_attributes(new_cat_id, all_current)
                res["errors"] = val_errs

            # Track AI attributes
            ai_keys = [k for k, v in updates.get("attributes_to_save", {}).items() if not is_forbidden_warranty_attribute(k) and v and str(v).strip()]
            if ai_keys:
                existing_ai = set(db_mapping.ai_attributes or [])
                existing_ai.update(ai_keys)
                db_mapping.ai_attributes = list(existing_ai)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Database commit error: {str(e)}",
            headers={"x-error-code": "AI_MAPPING_DB_ERROR"}
        )
    
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

    acc = req.account or "JV"
    if acc == "JV":
        xl_m = db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(req.product_eans),
            models.ProductMapping.account_name == "XL"
        ).first()
        if xl_m:
            acc = "XL"

    from services.okb_sync import sync_product_data
    sem = asyncio.Semaphore(5)
    
    async def process(ean: str):
        async with sem:
            row = row_dict.get(str(ean))
            result = await sync_product_data(ean, req.factory_id, acc, row=row)
            await asyncio.sleep(0.1)
            return result
                
    results = await asyncio.gather(*(process(ean) for ean in req.product_eans))
    
    # Pre-fetch existing mappings
    eans = [r["ean"] for r in results if r.get("status") == "success"]
    existing_mappings = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(eans),
        models.ProductMapping.account_name == acc
    ).all()
    mapping_dict = {m.product_ean: m for m in existing_mappings}
    
    # Fetch existing attributes to avoid duplicates
    existing_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(eans),
        models.ProductAttribute.account_name == acc
    ).all()
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
                    product_ean=ean, factory_id=req.factory_id, account_name=acc
                )
                db.add(db_mapping)
                mapping_dict[ean] = db_mapping
                
            if cat_id:
                db_mapping.category_id = cat_id

            from services.okb_sync import clean_dimension_value, DIMENSION_ATTR_NAMES
            
            # Upsert attributes
            for k, v in attributes.items():
                if not str(v).strip() or is_forbidden_warranty_attribute(k):
                    continue
                v_clean = str(v).strip()
                if k in DIMENSION_ATTR_NAMES or any(d in k.lower() for d in ("breite", "höhe", "tiefe", "länge")):
                    v_clean = clean_dimension_value(v_clean)

                db_attr = attrs_orm_dict.get(ean, {}).get(k)
                if db_attr:
                    db_attr.attribute_value = v_clean
                else:
                    new_attr = models.ProductAttribute(product_ean=ean, account_name=acc, attribute_name=k, attribute_value=v_clean)
                    db.add(new_attr)
                    attrs_orm_dict.setdefault(ean, {})[k] = new_attr

            if db_mapping.ai_attributes and attributes:
                synced_keys = set(attributes.keys())
                db_mapping.ai_attributes = [a for a in db_mapping.ai_attributes if a not in synced_keys]
                    
            errors = validate_product(ean, db_mapping.category_id, attributes)
            # Status protection: do not downgrade Published status
            if (db_mapping.status or "").lower() != "published":
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
        attributes = await extract_attributes(
            product_info, 
            req.category_id, 
            use_vision=req.use_vision, 
            gallery_url=req.gallery_url, 
            picture_urls=req.picture_urls,
            custom_api_key=current_user.openai_api_key
        )
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
    acc = mapping.account or "JV"
    clean_ean = mapping.product_ean.strip()
    db_mapping = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean == clean_ean,
        models.ProductMapping.account_name == acc
    ).first()
    
    if db_mapping:
        if db_mapping.category_id != mapping.category_id:
            # Delete old attributes for the old category
            db.query(models.ProductAttribute).filter(
                models.ProductAttribute.product_ean == clean_ean,
                models.ProductAttribute.account_name == acc
            ).delete(synchronize_session=False)
            db_mapping.ai_attributes = []
            db_mapping.status = "Need Attributes"
            
        db_mapping.category_id = mapping.category_id
        if mapping.factory_id:
            db_mapping.factory_id = mapping.factory_id
    else:
        db_mapping = models.ProductMapping(
            product_ean=clean_ean,
            factory_id=mapping.factory_id,
            account_name=acc,
            category_id=mapping.category_id,
            status="Need Attributes"
        )
        db.add(db_mapping)
    
    ensure_default_attributes(db, clean_ean, acc, category_id=mapping.category_id)
    db.commit()
    return {"status": "success", "account": acc}

@router.post("/category/bulk")
async def assign_category_bulk(mapping: CategoryMappingBulk, db: Session = Depends(get_db)):
    clean_eans = [e.strip() for e in mapping.product_eans if e and e.strip()]
    for ean in clean_eans:
        db_mapping = db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean == ean,
            models.ProductMapping.account_name == mapping.account
        ).first()
        if db_mapping:
            if db_mapping.category_id != mapping.category_id:
                # Delete old attributes
                db.query(models.ProductAttribute).filter(
                    models.ProductAttribute.product_ean == ean,
                    models.ProductAttribute.account_name == mapping.account
                ).delete(synchronize_session=False)
                db_mapping.ai_attributes = []
                db_mapping.status = "Need Attributes"
                
            db_mapping.category_id = mapping.category_id
            if mapping.factory_id:
                db_mapping.factory_id = mapping.factory_id
        else:
            db_mapping = models.ProductMapping(
                product_ean=ean,
                factory_id=mapping.factory_id,
                account_name=mapping.account,
                category_id=mapping.category_id,
                status="Need Attributes"
            )
            db.add(db_mapping)
        ensure_default_attributes(db, ean, mapping.account, category_id=mapping.category_id)
    db.commit()
    return {"status": "success", "count": len(clean_eans)}

@router.post("/bulk/delete")
async def delete_mapping_bulk(req: BulkDeleteRequest, db: Session = Depends(get_db)):
    clean_eans = [e.strip() for e in req.product_eans if e and e.strip()]
    if not clean_eans:
        return {"status": "success", "deleted": 0}
        
    acc = req.account or "JV"
    attr_q = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(clean_eans),
        models.ProductAttribute.account_name == acc
    )
    map_q = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(clean_eans),
        models.ProductMapping.account_name == acc
    )

    attr_q.delete(synchronize_session=False)
    deleted_count = map_q.delete(synchronize_session=False)
    
    db.commit()
    return {"status": "success", "deleted": deleted_count, "account": acc}

from services.categories_repo import get_category_by_id, get_categories, get_groups

@router.get("/categories")
async def search_categories(q: str = "", sort: str = "asc", letter: str = None, limit: int = 100):
    q_lower = q.lower().strip()
    letter_upper = letter.upper().strip() if letter else None
    results = []
    
    cats = get_categories(sorted_alphabetical=True)
    if sort == "desc":
        cats = list(reversed(cats))
        
    for c in cats:
        name = c.get("name", "")
        group = c.get("category_group", "")
        
        if letter_upper:
            first_char = name[0].upper() if name else ""
            if letter_upper == "#":
                if first_char.isalpha():
                    continue
            elif first_char != letter_upper:
                continue
                
        if q_lower and (q_lower not in name.lower() and q_lower not in group.lower()):
            continue
            
        results.append({
            "id": str(c.get("categoryId")),
            "name": name,
            "group": group,
            "group_id": str(c.get("category_group_id"))
        })
        if len(results) >= limit:
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
async def get_category_attributes(category_id: str, response: Response):
    response.headers["Cache-Control"] = "public, max-age=86400"
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
        attr_name = a.get("name")
        if is_forbidden_warranty_attribute(attr_name):
            continue
        is_req = a.get("relevance") == "HIGH"
        # Force Brand to be required based on typical marketplace rules, or rely on 'HIGH'
        # Often 'Marke' is universally required
        if attr_name == "Marke":
            is_req = True
        elif attr_name == "Ausführung":
            is_req = False
            
        options = [v if isinstance(v, str) else v.get("value", str(v)) for v in a.get("allowedValues", [])] if a.get("allowedValues") else None
        if attr_name in {"Holzart", "Holzart Bettgestell"} and not options:
            options = ["Buche", "Kiefer"]

        attr_type = "text"
        if a.get("type") == "FLOAT" or a.get("type") == "INTEGER":
            attr_type = "number"
        elif options:
            attr_type = "select"
            
        schema.append({
            "id": str(a.get("attributeId")),
            "name": a.get("name"),
            "type": attr_type,
            "unit": a.get("unit"),
            "required": is_req,
            "options": options
        })
    for i in range(1, 6):
        schema.append({
            "id": f"bullet_point_{i}",
            "name": f"Bullet Point {i}",
            "type": "text",
            "required": False,
            "options": None
        })
    return schema

@router.get("/product/{product_ean}")
async def get_product_mapping(product_ean: str, account: str = None, db: Session = Depends(get_db)):
    clean_ean = product_ean.strip()
    query = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == clean_ean)
    mapping = None
    if account:
        mapping = query.filter(models.ProductMapping.account_name == account).first()
    else:
        mapping = query.first()

    if not mapping:
        return {"category_id": None, "category_name": None, "attributes": {}, "ai_attributes": [], "status": "Unmapped", "account": account or "JV"}

    actual_account = mapping.account_name.value if hasattr(mapping.account_name, "value") else str(mapping.account_name)
    attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean == clean_ean,
        models.ProductAttribute.account_name == mapping.account_name
    ).all()
    attr_dict = {a.attribute_name: a.attribute_value for a in attrs}

    cat = get_category_by_id(mapping.category_id)
    cat_name = cat.get("name", mapping.category_id) if cat else mapping.category_id

    def _clean_price_val(v):
        if not v:
            return None
        c = str(v).replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "").replace(",", ".").strip()
        try:
            f = float(c)
            if f > 0.0001:
                return str(int(f)) if f.is_integer() else f"{f:.2f}"
        except (ValueError, TypeError):
            pass
        return None

    clean_p = _clean_price_val(mapping.price)
    clean_p_af = _clean_price_val(mapping.price_af)
    clean_p_name = mapping.product_name if (mapping.product_name and mapping.product_name.strip() not in ("Без названия", "None", "null")) else None

    return {
        "category_id": mapping.category_id,
        "category_name": cat_name,
        "attributes": attr_dict,
        "ai_attributes": mapping.ai_attributes or [],
        "missing_attributes": mapping.missing_attributes or [],
        "last_error": mapping.last_error,
        "status": mapping.status,
        "account": actual_account,
        "product_name": clean_p_name,
        "price": clean_p or clean_p_af,
        "price_af": clean_p_af
    }

@router.get("/factory/{factory_id}")
async def get_factory_mappings(factory_id: str, account: str = None, db: Session = Depends(get_db)):
    query = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == factory_id)
    if account:
        query = query.filter(models.ProductMapping.account_name == account.upper())
    mappings = query.all()
    result = {}
    
    def _clean_price_val(v):
        if not v:
            return None
        c = str(v).replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "").replace(",", ".").strip()
        try:
            f = float(c)
            if f > 0.0001:
                return str(int(f)) if f.is_integer() else f"{f:.2f}"
        except (ValueError, TypeError):
            pass
        return None

    for m in mappings:
        # Get category name
        cat = get_category_by_id(m.category_id)
        cat_name = cat.get("name", m.category_id) if cat else m.category_id
        clean_p = _clean_price_val(m.price)
        clean_p_af = _clean_price_val(m.price_af)
        clean_p_name = m.product_name if (m.product_name and m.product_name.strip() not in ("Без названия", "None", "null")) else None
        has_ai = bool(m.has_ai_title or (clean_p_name and len(clean_p_name) <= 70 and (account or "JV").upper() == "XL"))
        result[m.product_ean] = {
            "category_id": m.category_id,
            "category_name": cat_name,
            "status": m.status,
            "published_at": m.published_at.isoformat() if m.published_at else None,
            "product_name": clean_p_name,
            "price": clean_p or clean_p_af,
            "price_af": clean_p_af,
            "missing_attributes": m.missing_attributes or [],
            "last_error": m.last_error,
            "has_ai_title": has_ai
        }
    return result

@router.get("/factory/{factory_id}/processing-status")
async def get_factory_processing_status(
    factory_id: str,
    account: Optional[str] = None,
    since: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Lightweight incremental status polling endpoint.
    Returns only mappings modified since `since` (ISO datetime string).
    If `since` is omitted or invalid, returns all mappings for the factory with only status-relevant fields.
    """
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    server_time = now_dt.isoformat()

    query = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == factory_id)
    if account:
        query = query.filter(models.ProductMapping.account_name == account.upper())

    if since:
        try:
            cleaned_since = since.strip().replace("Z", "+00:00")
            since_dt = datetime.datetime.fromisoformat(cleaned_since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=datetime.timezone.utc)
            query = query.filter(models.ProductMapping.updated_at >= since_dt)
        except Exception as e:
            logger.warning(f"Could not parse 'since' datetime parameter '{since}': {e}")

    mappings = query.with_entities(
        models.ProductMapping.product_ean,
        models.ProductMapping.category_id,
        models.ProductMapping.status,
        models.ProductMapping.published_at,
        models.ProductMapping.last_error,
        models.ProductMapping.missing_attributes
    ).all()

    changed = []
    for row in mappings:
        ean, cat_id, st, pub_at, last_err, missing_attrs = row
        cat = get_category_by_id(cat_id) if cat_id else None
        cat_name = cat.get("name", cat_id) if cat else cat_id
        changed.append({
            "ean": ean,
            "category_id": cat_id,
            "category_name": cat_name,
            "status": st,
            "published_at": pub_at.isoformat() if pub_at else None,
            "last_error": last_err,
            "missing_attributes": missing_attrs or []
        })

    return {
        "server_time": server_time,
        "changed": changed
    }

class ProductDetailsUpdate(BaseModel):
    product_ean: str
    factory_id: Optional[str] = None
    account: Optional[str] = "JV"
    product_name: Optional[str] = None
    price: Optional[str] = None

@router.post("/product-details")
async def update_product_details(req: ProductDetailsUpdate, db: Session = Depends(get_db)):
    clean_ean = req.product_ean.strip()
    if not clean_ean:
        raise HTTPException(status_code=400, detail="product_ean is required")
    acc = (req.account or "JV").upper()

    db_mapping = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean == clean_ean,
        models.ProductMapping.account_name == acc
    ).first()

    if not db_mapping:
        db_mapping = models.ProductMapping(
            product_ean=clean_ean,
            account_name=acc,
            factory_id=req.factory_id,
            status="Not Mapped"
        )
        db.add(db_mapping)
    elif req.factory_id and not db_mapping.factory_id:
        db_mapping.factory_id = req.factory_id

    updated_fields = []
    if req.product_name is not None:
        clean_name = req.product_name.strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="Название товара не может быть пустым")
        db_mapping.product_name = clean_name
        updated_fields.append("product_name")

    if req.price is not None:
        raw_price = str(req.price).replace("€", "").replace("EUR", "").replace("eur", "").strip()
        raw_price = raw_price.replace(",", ".")
        try:
            num_price = float(raw_price)
            if num_price <= 0:
                raise ValueError("Price must be greater than 0")
            clean_price_str = f"{int(num_price)}" if num_price.is_integer() else f"{num_price:.2f}"
            db_mapping.price = clean_price_str
            updated_fields.append("price")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Некорректный формат цены. Укажите число больше 0 (например, 1299 или 1299.99)")

    db_mapping.last_synced_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(db_mapping)

    return {
        "status": "success",
        "product_ean": clean_ean,
        "account": acc,
        "product_name": db_mapping.product_name,
        "price": db_mapping.price,
        "updated_fields": updated_fields
    }

@router.post("/attributes")
async def save_attributes(mapping: AttributeMappingBase, db: Session = Depends(get_db)):
    acc = mapping.account or "JV"
    clean_ean = mapping.product_ean.strip()
    
    # Ensure product mapping exists for the requested account
    db_mapping = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean == clean_ean,
        models.ProductMapping.account_name == acc
    ).first()
    if not db_mapping:
        db_mapping = models.ProductMapping(
            product_ean=clean_ean,
            account_name=acc,
            status="Need Attributes"
        )
        db.add(db_mapping)

    from services.okb_sync import clean_dimension_value, DIMENSION_ATTR_NAMES
    allowed_lookup = None
    if db_mapping and db_mapping.category_id:
        from services.categories_repo import get_allowed_attribute_names
        cat_allowed = get_allowed_attribute_names(db_mapping.category_id)
        if cat_allowed:
            allowed_lookup = {str(a).strip().lower(): str(a).strip() for a in cat_allowed}

    clean_attributes = {}
    for k, v in mapping.attributes.items():
        if not str(v).strip() or is_forbidden_warranty_attribute(k):
            continue
        k_clean = str(k).strip()
        v_clean = str(v).strip()

        # Whitelist filtering: if category schema is known, skip attributes that don't belong
        if allowed_lookup:
            canonical_k = allowed_lookup.get(k_clean.lower())
            if not canonical_k:
                continue
            k_clean = canonical_k

        if k_clean in DIMENSION_ATTR_NAMES or any(d in k_clean.lower() for d in ("breite", "höhe", "tiefe", "länge")):
            v_clean = clean_dimension_value(v_clean)
        clean_attributes[k_clean] = v_clean

    # 1. Brand Guard Safety Check
    brand_errors = validate_brand_safety(acc, clean_attributes)
    if brand_errors:
        raise HTTPException(status_code=400, detail=" ; ".join(brand_errors))

    # 2. Clear old attributes for this specific account
    db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean == clean_ean,
        models.ProductAttribute.account_name == acc
    ).delete()
    
    # 3. Save new attributes with account_name
    for k, v in clean_attributes.items():
        if v and v.strip():
            db.add(models.ProductAttribute(
                product_ean=clean_ean,
                account_name=acc,
                attribute_name=k,
                attribute_value=v.strip()
            ))
        
    if not db_mapping:
        raise HTTPException(status_code=404, detail=f"Mapping for product {clean_ean} ({acc}) not found")
        
    # 4. Re-validate Product
    from services.validator import validate_product, get_missing_required_attributes
    errors = validate_product(clean_ean, db_mapping.category_id, clean_attributes)
    db_mapping.missing_attributes = get_missing_required_attributes(db_mapping.category_id, clean_attributes)
    
    if errors:
        db_mapping.status = "Error"
    else:
        db_mapping.status = "Ready"
        db_mapping.last_error = None

    if mapping.ai_attributes is not None:
        db_mapping.ai_attributes = [
            a for a in mapping.ai_attributes
            if a in clean_attributes and str(clean_attributes[a]).strip()
        ]
        
    db.commit()
    return {"status": "success", "errors": errors, "account": acc}

@router.post("/attributes/bulk")
async def save_attributes_bulk(mapping: AttributeMappingBulk, db: Session = Depends(get_db)):
    from services.validator import validate_product

    acc = mapping.account or "JV"
    clean_eans = [e.strip() for e in mapping.product_eans if e and e.strip()]

    clean_attributes = {k: v for k, v in mapping.attributes.items() if not is_forbidden_warranty_attribute(k)}

    # 1. Brand Guard Safety Check
    brand_errors = validate_brand_safety(acc, clean_attributes)
    if brand_errors:
        raise HTTPException(status_code=400, detail=" ; ".join(brand_errors))

    if not clean_eans:
        return {"status": "success"}

    keys = [k for k, v in clean_attributes.items() if v and v.strip()]
    if not keys:
        return {"status": "success"}

    # 2. Bulk delete old matching attributes strictly for this account
    db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(clean_eans),
        models.ProductAttribute.account_name == acc,
        models.ProductAttribute.attribute_name.in_(keys)
    ).delete(synchronize_session=False)

    # 3. Bulk add new attributes with account_name, respecting each product's category schema
    mappings_for_bulk = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(clean_eans),
        models.ProductMapping.account_name == acc
    ).all()
    cat_by_ean = {m.product_ean: m.category_id for m in mappings_for_bulk}

    from services.categories_repo import get_allowed_attribute_names
    allowed_by_cat = {}

    new_attrs = []
    for ean in clean_eans:
        prod_cat = cat_by_ean.get(ean)
        prod_allowed = None
        if prod_cat:
            if prod_cat not in allowed_by_cat:
                cat_allowed = get_allowed_attribute_names(prod_cat)
                allowed_by_cat[prod_cat] = {str(a).strip().lower(): str(a).strip() for a in cat_allowed} if cat_allowed else None
            prod_allowed = allowed_by_cat[prod_cat]

        for k in keys:
            k_clean = k
            if prod_allowed:
                canonical_k = prod_allowed.get(k.lower().strip())
                if not canonical_k:
                    # Not allowed in this product's category schema -> skip
                    continue
                k_clean = canonical_k
            new_attrs.append(
                models.ProductAttribute(
                    product_ean=ean,
                    account_name=acc,
                    attribute_name=k_clean,
                    attribute_value=clean_attributes[k].strip()
                )
            )
    if new_attrs:
        db.add_all(new_attrs)
    db.flush()

    # 4. Bulk re-validate all affected mappings for this account
    mappings_to_validate = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(mapping.product_eans),
        models.ProductMapping.account_name == acc
    ).all()
    all_db_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(mapping.product_eans),
        models.ProductAttribute.account_name == acc
    ).all()

    attrs_by_ean = {}
    for a in all_db_attrs:
        if a.product_ean not in attrs_by_ean:
            attrs_by_ean[a.product_ean] = {}
        attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

    keys_to_remove = set(clean_attributes.keys()) - set(mapping.ai_attributes or [])
    for m in mappings_to_validate:
        existing_ai = set(m.ai_attributes or [])
        if mapping.ai_attributes:
            existing_ai.update(mapping.ai_attributes)
        if keys_to_remove:
            existing_ai.difference_update(keys_to_remove)
        m.ai_attributes = list(existing_ai)

        attr_dict = attrs_by_ean.get(m.product_ean, {})
        errors = validate_product(m.product_ean, m.category_id, attr_dict)
        from services.validator import get_missing_required_attributes
        m.missing_attributes = get_missing_required_attributes(m.category_id, attr_dict)
        if errors:
            m.status = "Error"
        else:
            m.status = "Ready"
            m.last_error = None

    db.commit()
    return {"status": "success"}

@router.get("/attribute-presets")
async def get_attribute_presets(account: str = "JV"):
    """
    Returns default attribute presets according to the account (JV/XL),
    including universal constants, target group options, and sofa presets.
    """
    return get_presets_payload(account)

@router.post("/apply-defaults")
async def apply_defaults_bulk(req: ApplyDefaultsRequest, db: Session = Depends(get_db)):
    """
    Applies standard default constants (green), account branding (blue JV/XL),
    and optional target group/design options to selected products in bulk,
    strictly scoped to req.account, then re-validates mapping status.
    """
    from services.validator import validate_product

    if not req.product_eans:
        return {"status": "success", "total": 0, "updated": 0, "ready": 0, "errors": 0}

    defaults_to_apply = get_attributes_for_apply(
        account=req.account,
        target_group=req.target_group,
        design=req.design
    )

    if not defaults_to_apply:
        return {"status": "success", "total": len(req.product_eans), "updated": 0, "ready": 0, "errors": 0}

    # Fetch existing mappings to know the category for each EAN and avoid schema mismatch
    mappings = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(req.product_eans),
        models.ProductMapping.account_name == req.account
    ).all()
    mapping_by_ean = {m.product_ean: m for m in mappings}

    # Fetch existing attributes for all requested eans and this account
    existing_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(req.product_eans),
        models.ProductAttribute.account_name == req.account
    ).all()

    # Organize existing attributes by EAN and lowercased attribute name
    attrs_by_ean: Dict[str, Dict[str, models.ProductAttribute]] = {}
    for a in existing_attrs:
        attrs_by_ean.setdefault(a.product_ean, {})[a.attribute_name.strip().lower()] = a

    new_attrs = []
    updated_count = 0
    for ean in req.product_eans:
        ean_map = attrs_by_ean.setdefault(ean, {})
        cat_id = mapping_by_ean.get(ean).category_id if mapping_by_ean.get(ean) else None
        allowed = get_allowed_attribute_names(cat_id) if cat_id else None

        for k, v in defaults_to_apply.items():
            if is_forbidden_warranty_attribute(k):
                continue
            # Whitelisting: only apply default if allowed in this category schema
            if allowed is not None and k not in allowed:
                continue

            k_lower = k.strip().lower()
            if k_lower in ean_map:
                existing_obj = ean_map[k_lower]
                if req.overwrite_existing or not str(existing_obj.attribute_value or "").strip():
                    if existing_obj.attribute_value != str(v):
                        existing_obj.attribute_value = str(v)
                        updated_count += 1
            else:
                new_obj = models.ProductAttribute(
                    product_ean=ean,
                    account_name=req.account,
                    attribute_name=k,
                    attribute_value=str(v)
                )
                new_attrs.append(new_obj)
                ean_map[k_lower] = new_obj
                updated_count += 1

    if new_attrs:
        db.add_all(new_attrs)
    db.flush()

    ready_count = 0
    error_count = 0

    applied_keys = set(defaults_to_apply.keys())
    for m in mappings:
        if m.ai_attributes:
            m.ai_attributes = [a for a in m.ai_attributes if a not in applied_keys]

        if not m.category_id:
            continue
        # Status protection: never downgrade a Published product to Ready or Error
        if (m.status or "").lower() == "published":
            ready_count += 1
            continue

        prod_attrs = {
            a_obj.attribute_name: a_obj.attribute_value
            for a_obj in attrs_by_ean.get(m.product_ean, {}).values()
        }
        errors = validate_product(m.product_ean, m.category_id, prod_attrs)
        if errors:
            m.status = "Error"
            error_count += 1
        else:
            m.status = "Ready"
            ready_count += 1

    db.commit()

    return {
        "status": "success",
        "total": len(req.product_eans),
        "updated": updated_count,
        "ready": ready_count,
        "errors": error_count,
        "applied_attributes_count": len(defaults_to_apply)
    }

@router.post("/transfer-to-account")
async def transfer_to_account(req: TransferAccountRequest, db: Session = Depends(get_db)):
    """
    Transfers category mapping and attributes from source_account (e.g. JV) to target_account (e.g. XL).
    All product characteristics (dimensions, colors, materials, design, etc.) are kept.
    All company-identifying brand attributes (Marke, WEEE, Materialhinweis, BattVO, etc.)
    are cleanly replaced with the target account's canonical credentials.
    """
    from services.brand_guard import prepare_attributes_for_transfer
    from services.validator import validate_product
    from services.categories_repo import get_allowed_attribute_names
    from services.aftercool_twin import resolve_twin_product

    src_acc_str = req.source_account.value if hasattr(req.source_account, "value") else str(req.source_account)
    tgt_acc_str = req.target_account.value if hasattr(req.target_account, "value") else str(req.target_account)

    if src_acc_str == tgt_acc_str:
        raise HTTPException(status_code=400, detail="Source and target accounts must be different.")

    # 1. Query source mappings
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.account_name == req.source_account
    )
    if req.factory_id:
        query = query.filter(models.ProductMapping.factory_id == req.factory_id)
    if req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
    if req.only_ready:
        query = query.filter(models.ProductMapping.status == "Ready")

    source_mappings = query.all()
    resolved_target_factory_id = req.target_factory_id
    source_fid = req.factory_id or (source_mappings[0].factory_id if source_mappings else None)
    if not resolved_target_factory_id and source_fid:
        from services.aftercool_twin import resolve_target_factory_id
        resolved_target_factory_id = await resolve_target_factory_id(source_fid, src_acc_str, tgt_acc_str)

    if not source_mappings:
        return {
            "status": "success",
            "transferred_count": 0,
            "ready_count": 0,
            "error_count": 0,
            "message": "No matching source mappings found.",
            "target_factory_id": resolved_target_factory_id
        }

    if not resolved_target_factory_id:
        raise HTTPException(
            status_code=409,
            detail=f"Target factory could not be resolved in {tgt_acc_str} for source factory {source_fid}. Manual mapping required."
        )

    src_eans = [m.product_ean for m in source_mappings]

    # 2. Fetch all attributes from source account in one query
    src_attrs_all = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(src_eans),
        models.ProductAttribute.account_name == req.source_account
    ).all()
    attrs_by_ean = defaultdict(dict)
    for a in src_attrs_all:
        attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

    # 3. Resolve target factory twin products map once (not per-item)
    from services.aftercool_twin import get_factory_product_map, _normalize_title
    target_name_map = {}
    if resolved_target_factory_id:
        try:
            target_name_map = await get_factory_product_map(resolved_target_factory_id, tgt_acc_str)
        except Exception as e:
            logger.warning(f"Could not load target factory product map: {e}")

    # Pre-resolve twin target EANs in memory
    src_ean_to_name = {}
    if source_fid and any(not src_m.product_name for src_m in source_mappings):
        try:
            from services.aftercool_twin import get_factory_product_map
            src_map = await get_factory_product_map(source_fid, src_acc_str)
            src_ean_to_name = {e: n for n, e in src_map.items()}
        except Exception as e:
            logger.warning(f"Could not load source factory product map: {e}")

    target_info_by_src_ean = {}
    all_target_eans = set()
    unresolved_twins = []
    for src_m in source_mappings:
        src_ean = src_m.product_ean
        prod_name = src_m.product_name or src_ean_to_name.get(src_ean)
        tgt_fid = resolved_target_factory_id
        tgt_ean = None
        tgt_pname = prod_name
        if prod_name and target_name_map:
            norm_name = _normalize_title(prod_name)
            if matched_ean := target_name_map.get(norm_name):
                tgt_ean = matched_ean
        
        if not tgt_ean:
            unresolved_twins.append(src_ean)
        else:
            all_target_eans.add(tgt_ean)
        target_info_by_src_ean[src_ean] = (tgt_fid, tgt_ean, tgt_pname)

    if req.product_eans and len(req.product_eans) == 1 and unresolved_twins:
        raise HTTPException(
            status_code=409,
            detail=f"Twin product not found in {tgt_acc_str} for EAN {unresolved_twins[0]}. Manual mapping required."
        )

    # 4. Bulk clean old attributes in target account in ONE query (not N queries)
    if req.copy_attributes and all_target_eans:
        db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(list(all_target_eans)),
            models.ProductAttribute.account_name == req.target_account
        ).delete(synchronize_session=False)

    # 5. Bulk fetch all existing target mappings in ONE query (not N queries)
    existing_tgt_mappings = {}
    if all_target_eans:
        existing_rows = db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(list(all_target_eans)),
            models.ProductMapping.account_name == req.target_account
        ).all()
        for r in existing_rows:
            existing_tgt_mappings[r.product_ean] = r

    target_attrs_dict = {}
    transferred_count = 0
    ready_count = 0
    error_count = 0

    def _is_pos_price(v):
        if not v:
            return False
        clean = str(v).replace("€", "").replace("EUR", "").replace("eur", "").replace(" ", "").replace(",", ".").strip()
        try:
            return float(clean) > 0.0001
        except (ValueError, TypeError):
            return False

    for src_m in source_mappings:
        src_ean = src_m.product_ean
        src_attr_dict = attrs_by_ean.get(src_ean, {})
        tgt_fid, tgt_ean, tgt_pname = target_info_by_src_ean[src_ean]
        if not tgt_ean:
            continue

        transformed = prepare_attributes_for_transfer(
            source_account=src_acc_str,
            target_account=tgt_acc_str,
            source_attributes=src_attr_dict
        )

        final_tgt_fid = req.target_factory_id or tgt_fid
        if not final_tgt_fid:
            continue
        target_cat_id = src_m.category_id if req.copy_categories else None

        # Schema whitelisting for target category
        if target_cat_id:
            allowed = get_allowed_attribute_names(target_cat_id)
            if allowed is not None:
                transformed = {k: v for k, v in transformed.items() if k in allowed}

        if req.copy_attributes:
            for k, v in transformed.items():
                if v and str(v).strip():
                    target_attrs_dict[(tgt_ean, k)] = {
                        "product_ean": tgt_ean,
                        "account_name": req.target_account,
                        "attribute_name": k,
                        "attribute_value": str(v).strip()
                    }

        tgt_m = existing_tgt_mappings.get(tgt_ean)

        final_src_price = src_m.price if _is_pos_price(src_m.price) else (src_m.price_af if _is_pos_price(src_m.price_af) else None)
        final_src_price_af = src_m.price_af if _is_pos_price(src_m.price_af) else None

        if not tgt_m:
            tgt_m = models.ProductMapping(
                product_ean=tgt_ean,
                factory_id=final_tgt_fid,
                account_name=req.target_account,
                category_id=target_cat_id,
                product_name=tgt_pname or src_m.product_name,
                has_ai_title=False,
                price=final_src_price,
                price_af=final_src_price_af,
                image_url=src_m.image_url,
                last_synced_at=src_m.last_synced_at
            )
            db.add(tgt_m)
            existing_tgt_mappings[tgt_ean] = tgt_m
        else:
            if req.copy_categories:
                tgt_m.category_id = target_cat_id
            tgt_m.factory_id = final_tgt_fid
            if tgt_pname or src_m.product_name:
                tgt_m.product_name = tgt_pname or src_m.product_name
                tgt_m.has_ai_title = False
            if _is_pos_price(src_m.price):
                tgt_m.price = src_m.price
            elif _is_pos_price(src_m.price_af) and not _is_pos_price(tgt_m.price):
                tgt_m.price = src_m.price_af
            if _is_pos_price(src_m.price_af):
                tgt_m.price_af = src_m.price_af
            if src_m.image_url:
                tgt_m.image_url = src_m.image_url

        if req.copy_attributes:
            tgt_m.ai_attributes = [a for a in (src_m.ai_attributes or []) if a in transformed]

        # Validate in target account
        errors = validate_product(tgt_ean, tgt_m.category_id, transformed)
        if errors:
            tgt_m.status = "Error"
            error_count += 1
        else:
            tgt_m.status = "Ready"
            ready_count += 1

        transferred_count += 1

    new_target_attrs = list(target_attrs_dict.values())
    if new_target_attrs:
        db.bulk_insert_mappings(models.ProductAttribute, new_target_attrs)

    db.commit()

    return {
        "status": "success",
        "transferred_count": transferred_count,
        "transferred_mappings": transferred_count,
        "transferred_attributes": len(new_target_attrs),
        "ready_count": ready_count,
        "error_count": error_count,
        "unresolved_twins_count": len(unresolved_twins),
        "unresolved_twins": unresolved_twins,
        "source_account": src_acc_str,
        "target_account": tgt_acc_str,
        "target_factory_id": resolved_target_factory_id
    }

class MassEditSchemaRequest(BaseModel):
    product_eans: List[str]
    account: str = "JV"

@router.post("/mass-edit/schema")
async def get_mass_edit_schema(req: MassEditSchemaRequest, db: Session = Depends(get_db)):
    """
    Returns the intersection of attribute schemas across all categories of selected products,
    along with each product's current attribute values for those shared attributes,
    strictly scoped to req.account.
    """
    if not req.product_eans:
        return {"shared_attrs": [], "current_values": {}, "categories": []}

    mappings_list = db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(req.product_eans),
        models.ProductMapping.account_name == req.account
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
            if is_forbidden_warranty_attribute(a.get("name")):
                continue
            is_req = a.get("relevance") == "HIGH" or a.get("name") == "Marke"
            if a.get("name") == "Ausführung":
                is_req = False
            options = [
                v if isinstance(v, str) else v.get("value", str(v))
                for v in a.get("allowedValues", [])
            ] if a.get("allowedValues") else None
            if a.get("name") in {"Holzart", "Holzart Bettgestell"} and not options:
                options = ["Buche", "Kiefer"]

            attr_type = "text"
            if a.get("type") in ("FLOAT", "INTEGER"):
                attr_type = "number"
            elif options:
                attr_type = "select"
            result.append({
                "id": str(a.get("attributeId", "")),
                "name": a.get("name", ""),
                "type": attr_type,
                "required": is_req,
                "options": options
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
        models.ProductAttribute.product_ean.in_(req.product_eans),
        models.ProductAttribute.account_name == req.account
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


class GenerateAiTitlesRequest(BaseModel):
    factory_id: str
    account: str
    eans: Optional[List[str]] = None
    mode: Optional[str] = None  # "over_70_only" or "all"
    force: Optional[bool] = False  # If True, bypass protection and regenerate even if already has AI title


def _truncate_title_safely(title: str, max_len: int = 70) -> str:
    import re
    title = (title or "").strip().strip('"').strip("'").strip("`").strip()
    title = re.sub(r"\s+", " ", title)
    if len(title) <= max_len:
        return title
    truncated = title[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip(" ,;:-.")


@router.post("/generate-ai-titles")
async def generate_ai_titles(
    req: GenerateAiTitlesRequest,
    current_user: models.User = Depends(auth.get_current_user),
    db: Session = Depends(get_db)
):
    target_account = str(req.account or "").strip().upper()
    if target_account not in ("JV", "XL"):
        raise HTTPException(status_code=400, detail="Account must be JV or XL")

    factory_id_clean = str(req.factory_id or "").strip()
    if not factory_id_clean:
        raise HTTPException(status_code=400, detail="factory_id is required")

    # 1. Resolve OpenAI API key
    custom_api_key = current_user.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not custom_api_key:
        user_with_key = db.query(models.User).filter(
            models.User.openai_api_key.isnot(None),
            models.User.openai_api_key != ""
        ).first()
        if user_with_key:
            custom_api_key = user_with_key.openai_api_key
        else:
            raise HTTPException(
                status_code=400,
                detail="OPENAI_API_KEY_MISSING",
                headers={"x-error-code": "OPENAI_API_KEY_MISSING"}
            )

    from services.automapper import get_openai_client
    from services.categories_service import resolve_category_names

    client = get_openai_client(custom_api_key)

    # 2. Query target products with strict account isolation
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id_clean,
        models.ProductMapping.account_name == target_account
    )

    clean_eans = [e.strip() for e in (req.eans or []) if e and e.strip()]
    if clean_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(clean_eans))

    mappings = query.all()

    # 3. Apply account-specific filtering and token protection:
    # JV: only items with current title length > 70
    # XL: by default protects products that ALREADY have an AI title (or compliant <= 70 title),
    #     unless force=True is explicitly set to regenerate.
    skipped_count = 0
    is_over_70_only = (target_account == "JV") or (req.mode == "over_70_only")
    if is_over_70_only:
        mappings = [m for m in mappings if len(m.product_name or "") > 70]
    elif target_account == "XL" and not req.force:
        total_before = len(mappings)
        # Skip products that already have a valid AI-generated or <=70 title
        mappings = [
            m for m in mappings 
            if not (m.has_ai_title is True or (m.product_name and 0 < len(m.product_name.strip()) <= 70))
        ]
        skipped_count = total_before - len(mappings)

    if not mappings:
        msg = (
            f"Защита от повторной траты токенов: все {skipped_count} товаров уже имеют готовое ИИ-название (до 70 симв.) и были пропущены."
            if skipped_count > 0
            else "Нет товаров, требующих обновления названия"
        )
        return {
            "status": "success",
            "total": 0,
            "skipped_count": skipped_count,
            "updated": {},
            "errors": {},
            "message": msg
        }

    # 4. Resolve categories batch in 1 query
    cat_ids = {m.category_id for m in mappings if m.category_id}
    cat_names = resolve_category_names(cat_ids, db)

    # 5. Concurrently generate titles with Semaphore(15)
    sem = asyncio.Semaphore(15)
    updated_map: Dict[str, str] = {}
    errors_map: Dict[str, str] = {}

    SYSTEM_PROMPT = (
        "Du bist ein professioneller E-Commerce-Texter für Otto. "
        "Erstelle prägnante, natürliche Produkttitel auf Deutsch bis maximal 70 Zeichen."
    )

    async def generate_one(pm: models.ProductMapping):
        ean = pm.product_ean
        current_title = (pm.product_name or "").strip()
        cat_name = cat_names.get(str(pm.category_id), "") or "Möbel"

        prompt = (
            f"Titel: {current_title}\n"
            f"Kategorie: {cat_name}\n\n"
            "Regeln:\n"
            "1. Natürlicher Produkttitel auf Deutsch bis maximal 70 Zeichen.\n"
            "2. Beginne NICHT mit der Kategorie.\n"
            "3. Kein Keyword-Stuffing: keine Wortwiederholungen, keine sinnlose Aneinanderreihung von Suchbegriffen.\n"
            "4. Verwende nur wesentliche Produkteigenschaften.\n"
            "5. Keine erfundenen Daten, keine Werbefüllwörter (wie Premium, Top, Hochwertig).\n"
            "6. Antworte NUR mit dem fertigen Titel, ohne Anführungszeichen, ohne Erklärungen."
        )

        async with sem:
            for attempt in range(2):
                try:
                    resp = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=40,
                        temperature=0.3
                    )
                    content = (resp.choices[0].message.content or "").strip()
                    cleaned = _truncate_title_safely(content, max_len=70)

                    if cleaned and len(cleaned) >= 3 and not cleaned.lower().startswith("entschuldigung"):
                        updated_map[ean] = cleaned
                        return
                    else:
                        errors_map[ean] = "Model returned empty or invalid title"
                except Exception as ex:
                    if attempt == 1:
                        errors_map[ean] = str(ex)
                    await asyncio.sleep(0.5)

    await asyncio.gather(*(generate_one(m) for m in mappings))

    # 6. Persist updates to DB in a single commit
    if updated_map:
        now_utc = datetime.datetime.utcnow()
        for pm in mappings:
            if pm.product_ean in updated_map:
                pm.product_name = updated_map[pm.product_ean]
                pm.has_ai_title = True
                pm.last_synced_at = now_utc
        db.commit()

    return {
        "status": "success",
        "total": len(mappings),
        "skipped_count": skipped_count,
        "updated": updated_map,
        "errors": errors_map
    }


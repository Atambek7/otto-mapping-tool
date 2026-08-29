from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
import models
from pydantic import BaseModel
from typing import List
import io
import zipfile
import pandas as pd
from fastapi.responses import StreamingResponse

router = APIRouter(
    prefix="/validation",
    tags=["validation"],
)

@router.get("/products")
async def get_invalid_products(factory_id: str = None, db: Session = Depends(get_db)):
    # We fetch mappings for the factory and re-validate on the fly
    # BUT we exclude "Not Mapped" items so that completely untouched products don't clutter the validation tab.
    query = db.query(models.ProductMapping).filter(models.ProductMapping.status != "Not Mapped")
    if factory_id:
        query = query.filter(models.ProductMapping.factory_id == factory_id)
        
    mappings = query.all()
    
    # Pre-fetch all attributes
    eans = [m.product_ean for m in mappings]
    if not eans:
        return []
        
    db_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all()
    attr_by_ean = {}
    for a in db_attrs:
        if a.product_ean not in attr_by_ean:
            attr_by_ean[a.product_ean] = {}
        attr_by_ean[a.product_ean][a.attribute_name] = a.attribute_value
    # Fetch category names using in-memory JSON cache
    cat_ids = list(set([m.category_id for m in mappings if m.category_id]))
    
    from services.validator import validate_product, get_category_by_id
    
    cat_name_by_id = {}
    for cid in cat_ids:
        cat_info = get_category_by_id(cid)
        if cat_info:
            cat_name_by_id[cid] = cat_info.get("name", cid)
    
    result = []
    for m in mappings:
        ean = m.product_ean
        attr_map = attr_by_ean.get(ean, {})
        errors = validate_product(ean, m.category_id, attr_map)
        
        # If there are errors, we return it to the frontend Validation page
        if errors:
            cat_name = cat_name_by_id.get(m.category_id, m.category_id)
            result.append({
                "product_ean": m.product_ean,
                "factory_id": m.factory_id,
                "category_id": m.category_id,
                "category_name": cat_name,
                "status": m.status,
                "errors": errors
            })
            # Optionally, we can sync the DB status if it was wrongly marked as "Ready"
            if m.status == "Ready":
                m.status = "Error"
        else:
            # If valid, but DB says Error, auto-correct it
            if m.status != "Ready":
                m.status = "Ready"
                
    db.commit() # Save any auto-corrected statuses
    return result

from pydantic import BaseModel
from typing import List

class ApproveRequest(BaseModel):
    product_eans: List[str]

@router.post("/approve")
async def force_approve_products(req: ApproveRequest, db: Session = Depends(get_db)):
    db.query(models.ProductMapping).filter(
        models.ProductMapping.product_ean.in_(req.product_eans)
    ).update({"status": "Ready"}, synchronize_session=False)
    db.commit()
    return {"status": "success"}

class ValidationExportRequest(BaseModel):
    factory_ids: List[str]

@router.post("/export/csv")
async def export_invalid_csv(req: ValidationExportRequest, db: Session = Depends(get_db)):
    if not req.factory_ids:
        return {"error": "No factory IDs provided"}
        
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id.in_(req.factory_ids)
    )
    mappings = query.all()
    
    eans = [m.product_ean for m in mappings]
    if not eans:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            zip_file.writestr("empty.txt", b"No products found")
        zip_buffer.seek(0)
        return StreamingResponse(iter([zip_buffer.getvalue()]), media_type="application/zip", headers={"Content-Disposition": "attachment; filename=validation_export.zip"})
        
    db_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all()
    attr_by_ean = {}
    for a in db_attrs:
        if a.product_ean not in attr_by_ean:
            attr_by_ean[a.product_ean] = {}
        attr_by_ean[a.product_ean][a.attribute_name] = a.attribute_value
        
    from services.validator import validate_product, get_category_by_id
    from routers.csv_workflow import fetch_product_info_for_export, get_category_schema_attrs
    
    import asyncio
    # We need product info for each unique factory
    info_by_ean = {}
    unique_fids = list(set([m.factory_id for m in mappings]))
    
    # Fetch all factory info concurrently to make it lightning fast
    f_infos = await asyncio.gather(*[fetch_product_info_for_export(fid) for fid in unique_fids])
    for f_info in f_infos:
        info_by_ean.update(f_info)
        
    result_by_cat = {}
    
    for m in mappings:
        ean = m.product_ean
        attr_map = attr_by_ean.get(ean, {})
        errors = validate_product(ean, m.category_id, attr_map)
        
        if errors and m.category_id:
            if m.category_id not in result_by_cat:
                result_by_cat[m.category_id] = []
            
            result_by_cat[m.category_id].append({
                "EAN": ean,
                "Attributes": attr_map
            })
            
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for cat_id, items in result_by_cat.items():
            cat = get_category_by_id(cat_id)
            cat_name = cat.get("name", "UnknownCategory").replace("/", "_").replace(" ", "") if cat else cat_id
            req_attrs = get_category_schema_attrs(cat_id)
            
            attr_data = []
            for item in items:
                ean = item["EAN"]
                info = info_by_ean.get(ean, {})
                row = {
                    "EAN": f'="{ean}"',
                    "Product_Name": info.get("name", ""),
                    "Price": info.get("price", "")
                }
                for attr_dict in req_attrs:
                    attr_name = attr_dict["name"]
                    row[attr_name] = item["Attributes"].get(attr_name, "")
                attr_data.append(row)
                
            df_attr = pd.DataFrame(attr_data)
            csv_str = df_attr.to_csv(index=False, sep=";")
            csv_bytes = "\ufeff".encode("utf-8") + csv_str.encode("utf-8")
            zip_file.writestr(f"Invalid_Attributes_{cat_name}.csv", csv_bytes)
            
        if not result_by_cat:
            zip_file.writestr("empty.txt", b"No invalid products found with categories")
            
    zip_buffer.seek(0)
    return StreamingResponse(
        iter([zip_buffer.getvalue()]), 
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=validation_export.zip"}
    )

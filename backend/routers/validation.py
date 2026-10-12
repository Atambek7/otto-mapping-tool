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

@router.get("/summary")
async def get_validation_summary(account: str = None, db: Session = Depends(get_db)):
    """
    Fast aggregated validation summary grouped by factory_id using composite index.
    """
    from sqlalchemy import func, case
    acc_name = account or "JV"

    rows = db.query(
        models.ProductMapping.factory_id,
        func.count(models.ProductMapping.id).label("total_affected"),
        func.count(case((models.ProductMapping.status == "Error", 1))).label("errors_count"),
        func.count(case((models.ProductMapping.status == "Need Attributes", 1))).label("need_attrs_count")
    ).filter(
        models.ProductMapping.account_name == acc_name,
        models.ProductMapping.status.in_(["Error", "Need Attributes"])
    ).group_by(models.ProductMapping.factory_id).all()

    return [
        {
            "factory_id": r.factory_id,
            "count": r.total_affected,
            "errors": r.errors_count,
            "need_attributes": r.need_attrs_count
        }
        for r in rows
    ]

@router.get("/products")
async def get_invalid_products(factory_id: str = None, account: str = None, limit: int = 500, db: Session = Depends(get_db)):
    # Only fetch mappings that are in Error or Need Attributes state — skip Ready/Not Mapped entirely.
    # This is much faster than fetching all products and re-validating each one.
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.status.in_(["Error", "Need Attributes"])
    )
    if factory_id:
        query = query.filter(models.ProductMapping.factory_id == factory_id)
    if account:
        query = query.filter(models.ProductMapping.account_name == account)

    mappings = query.limit(limit).all()

    # Pre-fetch all attributes in one batch query
    eans = [m.product_ean for m in mappings]
    if not eans:
        return []

    attr_q = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans))
    if account:
        attr_q = attr_q.filter(models.ProductAttribute.account_name == account)
    db_attrs = attr_q.all()
    attr_by_ean: dict = {}
    for a in db_attrs:
        acc = a.account_name or "JV"
        if (a.product_ean, acc) not in attr_by_ean:
            attr_by_ean[(a.product_ean, acc)] = {}
        attr_by_ean[(a.product_ean, acc)][a.attribute_name] = a.attribute_value

    # Fetch category names using in-memory JSON cache
    cat_ids = list(set([m.category_id for m in mappings if m.category_id]))

    from services.validator import validate_product, get_category_by_id
    from services.brand_guard import validate_brand_safety

    cat_name_by_id = {}
    for cid in cat_ids:
        cat_info = get_category_by_id(cid)
        if cat_info:
            cat_name_by_id[cid] = cat_info.get("name", cid)

    result = []
    status_updates = []
    for m in mappings:
        ean = m.product_ean
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or account or "JV")
        attr_map = attr_by_ean.get((ean, m_acc)) or attr_by_ean.get((ean, "JV"), {})
        errors = validate_product(ean, m.category_id, attr_map)
        brand_errors = validate_brand_safety(m_acc, attr_map)
        if brand_errors:
            errors.extend(brand_errors)

        if errors:
            cat_name = cat_name_by_id.get(m.category_id, m.category_id)
            img = m.image_url or ""
            if "|" in img:
                img = img.split("|")[0].strip()
            elif "," in img:
                img = img.split(",")[0].strip()

            result.append({
                "product_ean": m.product_ean,
                "factory_id": m.factory_id,
                "category_id": m.category_id,
                "category_name": cat_name,
                "status": m.status,
                "product_name": m.product_name or "",
                "image_url": img,
                "price": m.price or "",
                "errors": errors
            })
        else:
            # Product is now valid — auto-correct DB status to Ready
            m.status = "Ready"
            status_updates.append(m)

    # Only commit if there were auto-corrections
    if status_updates:
        db.commit()
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

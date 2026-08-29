from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from database import get_db
import models
import json

router = APIRouter(
    prefix="/export",
    tags=["export"],
)

@router.get("/factories")
async def get_export_factories(db: Session = Depends(get_db)):
    """Returns a list of factory_ids that have at least one product ready for export"""
    factories = db.query(models.ProductMapping.factory_id).filter(
        models.ProductMapping.status == "Ready"
    ).distinct().all()
    
    # Extract from tuples
    return [f[0] for f in factories if f[0]]

@router.get("/preview")
async def get_export_preview(factory_id: str = None, db: Session = Depends(get_db)):
    query_all = db.query(models.ProductMapping)
    if factory_id:
        query_all = query_all.filter(models.ProductMapping.factory_id == factory_id)
        
    mappings = query_all.all()
    eans = [m.product_ean for m in mappings]
    
    if not eans:
        return {"ready_count": 0, "error_count": 0, "factory_id": factory_id}
        
    db_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all()
    attr_by_ean = {}
    for a in db_attrs:
        if a.product_ean not in attr_by_ean:
            attr_by_ean[a.product_ean] = {}
        attr_by_ean[a.product_ean][a.attribute_name] = a.attribute_value
        
    from services.validator import validate_product
    ready_count = 0
    error_count = 0
    
    for m in mappings:
        ean = m.product_ean
        attr_map = attr_by_ean.get(ean, {})
        errors = validate_product(ean, m.category_id, attr_map)
        
        if errors:
            error_count += 1
            if m.status == "Ready":
                m.status = "Error"
        else:
            ready_count += 1
            if m.status != "Ready":
                m.status = "Ready"
                
    db.commit()
    
    return {
        "ready_count": ready_count,
        "error_count": error_count,
        "factory_id": factory_id
    }

@router.get("/download")
async def download_export(factory_id: str, format: str = "json", db: Session = Depends(get_db)):
    # Instead of just taking "Ready" status from DB, we re-verify them!
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id
    )
    
    mappings = query.all()
    
    # Pre-fetch all attributes for these products
    eans = [m.product_ean for m in mappings]
    if not eans:
        return Response(content="[]", media_type="application/json")
        
    attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all()
    
    # Group attributes by EAN
    attr_by_ean = {}
    for a in attrs:
        if a.product_ean not in attr_by_ean:
            attr_by_ean[a.product_ean] = {}
        attr_by_ean[a.product_ean][a.attribute_name] = a.attribute_value
        
    from services.validator import validate_product
        
    result = []
    for m in mappings:
        ean = m.product_ean
        attr_map = attr_by_ean.get(ean, {})
        errors = validate_product(ean, m.category_id, attr_map)
        
        # Only export if it is TRULY valid
        if not errors:
            result.append({
                "EAN": m.product_ean,
                "CategoryId": m.category_id,
                "Attributes": attr_map
            })
            if m.status != "Ready":
                m.status = "Ready"
        else:
            if m.status == "Ready":
                m.status = "Error"
                
    db.commit()
        
    if format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=export_{factory_id}.json"}
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
        import sqlite3
        import tempfile
        import os
        
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        try:
            os.close(fd)
            conn = sqlite3.connect(path)
            c = conn.cursor()
            c.execute("CREATE TABLE products (ean TEXT PRIMARY KEY, category_id TEXT)")
            c.execute("CREATE TABLE attributes (ean TEXT, name TEXT, value TEXT)")
            
            for item in result:
                c.execute("INSERT INTO products (ean, category_id) VALUES (?, ?)", (item["EAN"], item["CategoryId"]))
                for k, v in item["Attributes"].items():
                    c.execute("INSERT INTO attributes (ean, name, value) VALUES (?, ?, ?)", (item["EAN"], k, v))
            
            conn.commit()
            conn.close()
            
            with open(path, "rb") as f:
                db_bytes = f.read()
                
            return Response(
                content=db_bytes,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename=export_{factory_id}.sqlite"}
            )
        finally:
            if os.path.exists(path):
                os.remove(path)
    
    return {"error": "Format not supported"}

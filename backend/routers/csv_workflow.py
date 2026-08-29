from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse
import pandas as pd
import io
import zipfile
import csv
from typing import List

from database import get_db
import models
from services.validator import validate_product
from services.categories_repo import get_category_by_id, get_groups
from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL
import httpx
import time
import re

# No longer needed, removed cache

router = APIRouter(
    prefix="/csv",
    tags=["csv_workflow"],
)

from pydantic import BaseModel
class ExportRequest(BaseModel):
    product_eans: List[str] = []


# --- Helpers ---
def get_category_schema_attrs(category_id: str):
    cat = get_category_by_id(category_id)
    if not cat: return []
    group_id = str(cat.get("category_group_id"))
    group_data = get_groups().get(group_id)
    if not group_data: return []
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])
    schema = []
    for a in raw_attrs:
        raw_rel = a.get("relevance", "OPTIONAL")
        if a.get("name") == "Marke":
            raw_rel = "HIGH"
        schema.append({"name": a.get("name"), "relevance": raw_rel})
    return schema

# Removed fetch_product_info_for_export

# --- Routes ---

@router.get("/export/categories/{factory_id}")
@router.post("/export/categories/{factory_id}")
async def export_categories(factory_id: str, req: ExportRequest = None, db: Session = Depends(get_db)):
    query = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == factory_id)
    if req and req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
    mappings = query.all()
    
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(["EAN", "Product_Name", "Price", "Category_Name"])
    
    for m in mappings:
        cat = get_category_by_id(m.category_id) if m.category_id else None
        cat_name = cat.get("name", "") if cat else ""
        
        writer.writerow([
            f'="{m.product_ean}"',
            m.product_name or "",
            m.price or "",
            cat_name
        ])
        
    csv_bytes = "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")
    
    response = StreamingResponse(iter([csv_bytes]), media_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f"attachment; filename=Categories_{factory_id}_{len(mappings)}.csv"
    return response

@router.get("/export/attributes/{factory_id}")
@router.post("/export/attributes/{factory_id}")
async def export_attributes(factory_id: str, req: ExportRequest = None, db: Session = Depends(get_db)):
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id,
        models.ProductMapping.category_id != None,
        models.ProductMapping.category_id != ""
    )
    if req and req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
    mappings = query.all()
    
    if not mappings:
        raise HTTPException(status_code=400, detail="No mapped products found to export attributes for.")
    
    # Pre-fetch ALL attributes for all EANs in one query (fix N+1)
    all_eans = [m.product_ean for m in mappings]
    all_db_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(all_eans)
    ).all()
    attrs_by_ean: dict = {}
    for a in all_db_attrs:
        if a.product_ean not in attrs_by_ean:
            attrs_by_ean[a.product_ean] = {}
        attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

    by_category: dict = {}
    for m in mappings:
        if m.category_id not in by_category:
            by_category[m.category_id] = []
        by_category[m.category_id].append(m)
        
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for cat_id, prods in by_category.items():
            cat = get_category_by_id(cat_id)
            cat_name = cat.get("name", "UnknownCategory").replace("/", "_").replace(" ", "") if cat else cat_id
            req_attrs = get_category_schema_attrs(cat_id)
            
            # 1. Group attributes by relevance
            high_attrs = [a for a in req_attrs if a["relevance"] == "HIGH"]
            mid_attrs = [a for a in req_attrs if a["relevance"] == "RECOMMENDED"]
            low_attrs = [a for a in req_attrs if a["relevance"] not in ["HIGH", "RECOMMENDED"]]
            
            # 2. Check which attributes are filled across all products
            filled_attrs = set()
            for p in prods:
                attr_dict = attrs_by_ean.get(p.product_ean, {})
                for attr_name, val in attr_dict.items():
                    if val and str(val).strip():
                        filled_attrs.add(attr_name)
            
            # 3. Create sorted list of attribute names (Filled first, then Empty for each group)
            def sort_group(group, keep_empty=True):
                filled = [a["name"] for a in group if a["name"] in filled_attrs]
                empty = [a["name"] for a in group if a["name"] not in filled_attrs]
                if keep_empty:
                    return filled + empty
                return filled
                
            sorted_attr_names = sort_group(high_attrs, keep_empty=True) + sort_group(mid_attrs, keep_empty=False) + sort_group(low_attrs, keep_empty=False)
            
            # Map of attr name to display name
            attr_to_display = {}
            for a in req_attrs:
                rel_map = {"HIGH": "High", "RECOMMENDED": "Middle"}
                rel_label = rel_map.get(a["relevance"], "Low")
                attr_to_display[a["name"]] = f"{a['name']} ({rel_label})"
            
            stream = io.StringIO()
            writer = csv.writer(stream, delimiter=";")
            header = ["EAN", "Product_Name"] + [attr_to_display[n] for n in sorted_attr_names]
            writer.writerow(header)
            
            for m in prods:
                row = [
                    f'="{m.product_ean}"',
                    m.product_name or ""
                ]
                ean_attrs = attrs_by_ean.get(m.product_ean, {})
                for attr_name in sorted_attr_names:
                    row.append(ean_attrs.get(attr_name, ""))
                writer.writerow(row)
                
            csv_bytes = "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")
            file_name = f"{cat_name}_{factory_id}_{len(prods)}.csv"
            zip_file.writestr(file_name, csv_bytes)
    zip_buffer.seek(0)
    response = StreamingResponse(iter([zip_buffer.getvalue()]), media_type="application/zip")
    response.headers["Content-Disposition"] = f"attachment; filename=Attributes_{factory_id}.zip"
    return response

@router.post("/import/{factory_id}")
async def import_csv(factory_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    
    dataframes = []
    if file.filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as thezip:
            for zipinfo in thezip.infolist():
                if zipinfo.filename.endswith(".csv"):
                    with thezip.open(zipinfo) as thefile:
                        # Try UTF-8 with BOM first, fallback to latin-1
                        raw = thefile.read()
                        try:
                            df = pd.read_csv(io.BytesIO(raw), dtype=str, sep=";", encoding="utf-8-sig")
                        except Exception:
                            df = pd.read_csv(io.BytesIO(raw), dtype=str, sep=";", encoding="latin-1")
                        dataframes.append(df)
    elif file.filename.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(content), dtype=str, sep=";", encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(io.BytesIO(content), dtype=str, sep=";", encoding="latin-1")
        dataframes.append(df)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload .csv or .zip")
    
    updated_categories = 0
    updated_attributes = 0
    skipped_eans = 0

    # Pre-load all existing mappings for this factory in one query
    existing_mappings = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id
    ).all()
    mappings_by_ean = {m.product_ean: m for m in existing_mappings}
    valid_eans = set(mappings_by_ean.keys())

    # Collect all EANs from all dataframes for bulk attr pre-fetch
    all_eans_in_file: set = set()
    for df in dataframes:
        if "EAN" not in df.columns:
            continue
        for raw_ean in df["EAN"].dropna():
            ean = str(raw_ean).strip().replace('="', '').replace('"', '').replace('=', '')
            if ean and ean != "nan":
                all_eans_in_file.add(ean)

    # Collect new/updated objects — bulk commit at end
    new_mappings: list = []
    new_attrs: list = []
    eans_to_revalidate: set = set()

    for df in dataframes:
        if "EAN" not in df.columns:
            continue
        is_category = "Category_Name" in df.columns

        for _, row in df.iterrows():
            raw_ean = str(row["EAN"]).strip()
            ean = raw_ean.replace('="', '').replace('"', '').replace('=', '')
            if not ean or ean == "nan":
                continue
            if valid_eans and ean not in valid_eans:
                skipped_eans += 1
                continue

            db_mapping = mappings_by_ean.get(ean)
            if not db_mapping:
                db_mapping = models.ProductMapping(product_ean=ean, factory_id=factory_id, status="Unmapped")
                new_mappings.append(db_mapping)
                mappings_by_ean[ean] = db_mapping

            if is_category:
                raw_cat_name = str(row.get("Category_Name", "")).strip()
                if raw_cat_name and raw_cat_name != "nan":
                    # Validate and convert to ID by Name
                    from services.categories_repo import get_category_by_id
                    cat = get_category_by_id(raw_cat_name)
                    if cat:
                        new_cat_id = str(cat.get("categoryId"))
                        if db_mapping.category_id != new_cat_id:
                            db_mapping.category_id = new_cat_id
                            db_mapping.status = "Need Attributes"
                            eans_to_revalidate.add(ean)
                            updated_categories += 1
                else:
                    # If cell is cleared, we unmap the category
                    if db_mapping.category_id:
                        db_mapping.category_id = None
                        db_mapping.status = "Not Mapped"
                        eans_to_revalidate.add(ean)
                        updated_categories += 1
            else:
                attrs_to_save = {}
                for col in df.columns:
                    col_str = str(col).strip()
                    if col_str in ("EAN", "Product_Name", "Price", "Image URL"):
                        continue
                    # Strip out (High), (Middle), (Low) suffixes added during export
                    clean_attr_name = re.sub(r'\s*\([^)]*\)$', '', col_str).strip()
                    if not clean_attr_name:
                        continue
                    val = str(row.get(col, "")).strip()
                    if val and val != "nan":
                        attrs_to_save[clean_attr_name] = val

                if attrs_to_save:
                    # Delete old values for these keys
                    db.query(models.ProductAttribute).filter(
                        models.ProductAttribute.product_ean == ean,
                        models.ProductAttribute.attribute_name.in_(list(attrs_to_save.keys()))
                    ).delete(synchronize_session=False)
                    for k, v in attrs_to_save.items():
                        new_attrs.append(models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=v))
                    updated_attributes += 1
                    eans_to_revalidate.add(ean)

    # Bulk insert — single commit
    if new_mappings:
        db.add_all(new_mappings)
    if new_attrs:
        db.add_all(new_attrs)
    db.commit()

    # Revalidate and update statuses in bulk
    if eans_to_revalidate:
        all_attrs_q = db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(eans_to_revalidate)
        ).all()
        attrs_by_ean: dict = {}
        for a in all_attrs_q:
            if a.product_ean not in attrs_by_ean:
                attrs_by_ean[a.product_ean] = {}
            attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

        for ean in eans_to_revalidate:
            db_mapping = mappings_by_ean.get(ean)
            if not db_mapping or not db_mapping.category_id:
                continue
            attr_dict = attrs_by_ean.get(ean, {})
            errors = validate_product(ean, db_mapping.category_id, attr_dict)
            db_mapping.status = "Error" if errors else "Ready"

        db.commit()
    
    return {
        "status": "success",
        "updated_categories": updated_categories,
        "updated_attributes": updated_attributes,
        "skipped_foreign_eans": skipped_eans
    }

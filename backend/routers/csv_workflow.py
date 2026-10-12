from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse, FileResponse
import pandas as pd
import io
import zipfile
import csv
import os
import sqlite3
import tempfile
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
    account: str = None


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
    for i in range(1, 6):
        schema.append({"name": f"Bullet Point {i}", "relevance": "OPTIONAL"})
    return schema

async def fetch_product_info_for_export(factory_id: str) -> dict:
    from database import SessionLocal
    _db = SessionLocal()
    try:
        mappings = _db.query(
            models.ProductMapping.product_ean,
            models.ProductMapping.product_name,
            models.ProductMapping.price,
            models.ProductMapping.price_af,
            models.ProductMapping.image_url
        ).filter(models.ProductMapping.factory_id == str(factory_id)).all()
        return {
            m[0]: {
                "name": m[1] or "",
                "price": m[2] or "",
                "startpreis": m[3] or "",
                "img": m[4] or "",
                "sku": m[0]
            }
            for m in mappings
        }
    finally:
        _db.close()

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
    writer.writerow(["pic_main", "EAN", "Product_Name", "Price", "Category_Name"])
    
    for m in mappings:
        cat = get_category_by_id(m.category_id) if m.category_id else None
        cat_name = cat.get("name", "") if cat else ""
        
        writer.writerow([
            m.image_url or "",
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
async def export_attributes(factory_id: str, req: ExportRequest = None, account: str = None, db: Session = Depends(get_db)):
    target_acc = (req.account if req and req.account else None) or account
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.factory_id == factory_id,
        models.ProductMapping.category_id != None,
        models.ProductMapping.category_id != ""
    )
    if target_acc:
        query = query.filter(models.ProductMapping.account_name == target_acc)
    if req and req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
    mappings = query.all()
    
    if not mappings:
        raise HTTPException(status_code=400, detail="No mapped products found to export attributes for.")
    
    # Pre-fetch ALL attributes for all EANs in one query (fix N+1)
    all_eans = [m.product_ean for m in mappings]
    attr_q = db.query(
        models.ProductAttribute.product_ean,
        models.ProductAttribute.attribute_name,
        models.ProductAttribute.attribute_value
    ).filter(
        models.ProductAttribute.product_ean.in_(all_eans)
    )
    if target_acc:
        attr_q = attr_q.filter(models.ProductAttribute.account_name == target_acc)
    all_db_attrs = attr_q.all()
    
    from services.brand_guard import sanitize_brand_attributes
    attrs_by_ean: dict = {}
    for p_ean, a_name, a_val in all_db_attrs:
        acc = target_acc or "JV"
        if (p_ean, acc) not in attrs_by_ean:
            attrs_by_ean[(p_ean, acc)] = {}
        attrs_by_ean[(p_ean, acc)][a_name] = a_val

    for m in mappings:
        m_acc = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name or target_acc or "JV")
        m_attrs = attrs_by_ean.get((m.product_ean, m_acc)) or attrs_by_ean.get((m.product_ean, "JV"), {})
        attrs_by_ean[(m.product_ean, m_acc)] = sanitize_brand_attributes(m_acc, m_attrs)

    from services.image_processor import fetch_images_batch
    all_img_urls = [m.image_url for m in mappings if m.image_url]
    img_bytes_map = await fetch_images_batch(all_img_urls, max_concurrency=60)

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
            header = ["pic_main", "EAN", "Product_Name"] + [attr_to_display[n] for n in sorted_attr_names]
            writer.writerow(header)
            
            for m in prods:
                row = [
                    m.image_url or "",
                    f'="{m.product_ean}"',
                    m.product_name or ""
                ]
                ean_attrs = attrs_by_ean.get(m.product_ean, {})
                for attr_name in sorted_attr_names:
                    row.append(ean_attrs.get(attr_name, ""))
                writer.writerow(row)
                
            csv_bytes = "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")
            file_name = f"1.Excel .csv/{cat_name}_{factory_id}_{len(prods)}.csv"
            zip_file.writestr(file_name, csv_bytes)

            # 2. Database .db per category
            fd, temp_cat_db = tempfile.mkstemp(suffix=".sqlite")
            os.close(fd)
            try:
                cat_conn = sqlite3.connect(temp_cat_db)
                cat_cur = cat_conn.cursor()
                col_names = ["pic_main", "EAN", "Product_Name"] + [attr_to_display[n] for n in sorted_attr_names]
                col_defs = ['"pic_main" BLOB', '"EAN" TEXT', '"Product_Name" TEXT'] + [f'"{attr_to_display[n]}" TEXT' for n in sorted_attr_names]
                cat_cur.execute(f'CREATE TABLE attributes ({", ".join(col_defs)})')

                placeholders = ", ".join(["?"] * len(col_names))
                insert_sql = f'INSERT INTO attributes VALUES ({placeholders})'

                db_rows = []
                for m in prods:
                    ean_attrs = attrs_by_ean.get(m.product_ean, {})
                    raw_bytes = img_bytes_map.get(m.image_url)
                    pic_blob = sqlite3.Binary(raw_bytes) if raw_bytes else None
                    row_data = [pic_blob, str(m.product_ean), str(m.product_name or "")]
                    for attr_name in sorted_attr_names:
                        row_data.append(str(ean_attrs.get(attr_name, "") or ""))
                    db_rows.append(row_data)

                cat_cur.executemany(insert_sql, db_rows)
                cat_conn.commit()
                cat_conn.close()

                with open(temp_cat_db, "rb") as f:
                    db_bytes = f.read()
                zip_file.writestr(f"2. Database .db/{cat_name}_{factory_id}_{len(prods)}.db", db_bytes)
            finally:
                if os.path.exists(temp_cat_db):
                    os.remove(temp_cat_db)

    zip_buffer.seek(0)
    response = StreamingResponse(iter([zip_buffer.getvalue()]), media_type="application/zip")
    response.headers["Content-Disposition"] = f"attachment; filename=Export_{factory_id}.zip"
    return response

@router.post("/export/attributes/start/{factory_id}")
async def start_export_attributes_zip(
    factory_id: str,
    factory_name: str = "",
    account: str = "JV",
    req: ExportRequest = None,
    db: Session = Depends(get_db)
):
    from tasks import export_factory_attributes_zip_task
    eans = req.product_eans if req else []
    try:
        task = export_factory_attributes_zip_task.delay(
            factory_id=str(factory_id),
            factory_name=str(factory_name or factory_id),
            account=str(account) if account else None,
            product_eans=eans if eans else None
        )
        return {
            "status": "queued",
            "task_id": task.id,
            "message": "Attributes ZIP export task started"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start export task: {e}")

@router.get("/export/attributes/status/{task_id}")
async def get_export_attributes_status(task_id: str):
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

@router.get("/export/attributes/download/{task_id}")
async def download_export_attributes_zip(task_id: str):
    from celery.result import AsyncResult
    clean_id = "".join(c for c in task_id if c.isalnum() or c in ("-", "_"))
    file_path = os.path.join(os.path.dirname(__file__), "..", "static", "exports", f"{clean_id}.zip")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export ZIP file not found or expired")

    filename = "Attributes_export.zip"
    tr = AsyncResult(task_id)
    if tr.ready() and isinstance(tr.result, dict) and tr.result.get("filename"):
        filename = tr.result["filename"]

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/zip"
    )

def process_attribute_dataframes(dataframes: List[pd.DataFrame], factory_id: str, db: Session, account: str = None) -> dict:
    updated_categories = 0
    updated_attributes = 0
    skipped_eans = 0

    query_m = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == factory_id)
    if account:
        query_m = query_m.filter(models.ProductMapping.account_name == account)
    existing_mappings = query_m.all() if factory_id else []
    mappings_by_ean = {m.product_ean: m for m in existing_mappings}
    valid_eans = set(mappings_by_ean.keys())

    if not valid_eans and factory_id:
        try:
            from services.factory_scope import get_authoritative_factory_eans
            import asyncio
            auth = asyncio.run(get_authoritative_factory_eans(factory_id, account=account))
            if auth:
                valid_eans = set(auth)
        except Exception:
            pass

    all_eans_in_file: set = set()
    for df in dataframes:
        ean_col = next((c for c in df.columns if str(c).strip().upper() == "EAN"), None)
        if not ean_col:
            continue
        for raw_ean in df[ean_col].dropna():
            ean = str(raw_ean).strip().replace('="', '').replace('"', '').replace('=', '')
            if ean and ean != "nan":
                all_eans_in_file.add(ean)

    new_mappings: list = []
    new_attrs: list = []
    eans_to_revalidate: set = set()

    for df in dataframes:
        ean_col = next((c for c in df.columns if str(c).strip().upper() == "EAN"), None)
        if not ean_col:
            continue
        cat_name_col = next((c for c in df.columns if str(c).strip().upper() == "CATEGORY_NAME"), None)
        is_category = cat_name_col is not None

        for _, row in df.iterrows():
            raw_ean = str(row[ean_col]).strip()
            ean = raw_ean.replace('="', '').replace('"', '').replace('=', '')
            if not ean or ean == "nan":
                continue
            if valid_eans and ean not in valid_eans:
                skipped_eans += 1
                continue

            db_mapping = mappings_by_ean.get(ean)
            if not db_mapping:
                target_acc = account or "JV"
                db_mapping = models.ProductMapping(product_ean=ean, factory_id=factory_id, account_name=target_acc, status="Unmapped")
                new_mappings.append(db_mapping)
                mappings_by_ean[ean] = db_mapping

            pic_main_col = next((c for c in df.columns if str(c).strip().lower() in ("pic_main", "image_url", "image url")), None)
            if pic_main_col:
                new_img = row.get(pic_main_col)
                if isinstance(new_img, str):
                    new_img_str = new_img.strip()
                    if new_img_str and new_img_str != "nan" and new_img_str.startswith("http") and new_img_str != str(db_mapping.image_url or "").strip():
                        db_mapping.image_url = new_img_str

            if is_category and cat_name_col:
                raw_cat_name = str(row.get(cat_name_col, "")).strip()
                if raw_cat_name and raw_cat_name != "nan":
                    cat = get_category_by_id(raw_cat_name)
                    if cat:
                        new_cat_id = str(cat.get("categoryId"))
                        if db_mapping.category_id != new_cat_id:
                            # Delete old attributes for the old category
                            target_acc = account or (db_mapping.account_name.value if hasattr(db_mapping.account_name, "value") else str(db_mapping.account_name or "JV"))
                            db.query(models.ProductAttribute).filter(
                                models.ProductAttribute.product_ean == ean,
                                models.ProductAttribute.account_name == target_acc
                            ).delete(synchronize_session=False)
                            db_mapping.ai_attributes = []
                            db_mapping.category_id = new_cat_id
                            db_mapping.status = "Need Attributes"
                            eans_to_revalidate.add(ean)
                            updated_categories += 1
                else:
                    if db_mapping.category_id:
                        db_mapping.category_id = None
                        db_mapping.status = "Not Mapped"
                        eans_to_revalidate.add(ean)
                        updated_categories += 1
            else:
                attrs_to_save = {}
                for col in df.columns:
                    col_str = str(col).strip()
                    if col_str.upper() in ("PIC_MAIN", "EAN", "PRODUCT_NAME", "PRICE", "IMAGE URL", "IMAGE_URL"):
                        continue
                    clean_attr_name = re.sub(r'\s*\([^)]*\)$', '', col_str).strip()
                    if not clean_attr_name:
                        continue
                    val = str(row.get(col, "")).strip()
                    if val and val != "nan":
                        attrs_to_save[clean_attr_name] = val

                if attrs_to_save:
                    target_acc = account or (db_mapping.account_name.value if hasattr(db_mapping.account_name, "value") else str(db_mapping.account_name or "JV"))
                    from services.brand_guard import sanitize_brand_attributes
                    attrs_to_save = sanitize_brand_attributes(target_acc, attrs_to_save)
                    db.query(models.ProductAttribute).filter(
                        models.ProductAttribute.product_ean == ean,
                        models.ProductAttribute.account_name == target_acc,
                        models.ProductAttribute.attribute_name.in_(list(attrs_to_save.keys()))
                    ).delete(synchronize_session=False)
                    for k, v in attrs_to_save.items():
                        new_attrs.append(models.ProductAttribute(product_ean=ean, account_name=target_acc, attribute_name=k, attribute_value=v))
                    if db_mapping.ai_attributes:
                        imported_keys = set(attrs_to_save.keys())
                        db_mapping.ai_attributes = [a for a in db_mapping.ai_attributes if a not in imported_keys]
                    updated_attributes += 1
                    eans_to_revalidate.add(ean)

    if new_mappings:
        db.add_all(new_mappings)
    if new_attrs:
        db.add_all(new_attrs)
    db.commit()

    if eans_to_revalidate:
        attr_q = db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(eans_to_revalidate)
        )
        if account:
            attr_q = attr_q.filter(models.ProductAttribute.account_name == account)
        all_attrs_q = attr_q.all()
        attrs_by_ean: dict = {}
        for a in all_attrs_q:
            acc = a.account_name or "JV"
            if (a.product_ean, acc) not in attrs_by_ean:
                attrs_by_ean[(a.product_ean, acc)] = {}
            attrs_by_ean[(a.product_ean, acc)][a.attribute_name] = a.attribute_value

        for ean in eans_to_revalidate:
            db_mapping = mappings_by_ean.get(ean)
            if not db_mapping or not db_mapping.category_id:
                continue
            m_acc = db_mapping.account_name.value if hasattr(db_mapping.account_name, "value") else str(db_mapping.account_name or account or "JV")
            attr_dict = attrs_by_ean.get((ean, m_acc)) or attrs_by_ean.get((ean, "JV"), {})
            errors = validate_product(ean, db_mapping.category_id, attr_dict)
            db_mapping.status = "Error" if errors else "Ready"

        db.commit()

    return {
        "status": "success",
        "updated_categories": updated_categories,
        "updated_attributes": updated_attributes,
        "skipped_foreign_eans": skipped_eans,
        "imported_count": updated_attributes or updated_categories,
        "matched_categories": updated_categories
    }

def process_attribute_import_from_db(db_path: str, factory_id: str, db: Session, account: str = None) -> dict:
    sqlite_conn = sqlite3.connect(db_path)
    cur = sqlite_conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attributes'")
    tbl = cur.fetchone()
    table_name = "attributes" if tbl else None
    if not table_name:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tbls = cur.fetchall()
        table_name = tbls[0][0] if tbls else None

    if not table_name:
        sqlite_conn.close()
        return {"error": "No tables found in SQLite database"}

    df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', sqlite_conn)
    sqlite_conn.close()

    if not factory_id:
        ean_col = next((c for c in df.columns if str(c).strip().upper() == "EAN"), None)
        if ean_col:
            first_eans = [str(x).strip() for x in df[ean_col].dropna().head(10)]
            for fean in first_eans:
                m_q = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == fean)
                if account:
                    m_q = m_q.filter(models.ProductMapping.account_name == account)
                m = m_q.first()
                if m and m.factory_id:
                    factory_id = m.factory_id
                    break

    return process_attribute_dataframes([df], factory_id=factory_id, db=db, account=account)

@router.post("/import/{factory_id}")
async def import_csv(factory_id: str, file: UploadFile = File(...), account: str = None, db: Session = Depends(get_db)):
    content = await file.read()
    
    dataframes = []
    if file.filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as thezip:
            csv_infos = [z for z in thezip.infolist() if z.filename.endswith(".csv") and not z.filename.startswith("__MACOSX")]
            db_infos = [z for z in thezip.infolist() if (z.filename.endswith(".db") or z.filename.endswith(".sqlite")) and not z.filename.startswith("__MACOSX")]

            target_infos = []
            if csv_infos and db_infos:
                max_csv_time = max(z.date_time for z in csv_infos)
                max_db_time = max(z.date_time for z in db_infos)
                target_infos = db_infos if max_db_time > max_csv_time else csv_infos
            elif db_infos:
                target_infos = db_infos
            else:
                target_infos = csv_infos

            for info in target_infos:
                if info.filename.endswith(".csv"):
                    with thezip.open(info) as thefile:
                        raw = thefile.read()
                        try:
                            df = pd.read_csv(io.BytesIO(raw), dtype=str, sep=";", encoding="utf-8-sig")
                        except Exception:
                            df = pd.read_csv(io.BytesIO(raw), dtype=str, sep=";", encoding="latin-1")
                        dataframes.append(df)
                elif info.filename.endswith(".db") or info.filename.endswith(".sqlite"):
                    db_bytes = thezip.read(info)
                    fd, temp_db = tempfile.mkstemp(suffix=".sqlite")
                    os.close(fd)
                    try:
                        with open(temp_db, "wb") as f:
                            f.write(db_bytes)
                        sqlite_conn = sqlite3.connect(temp_db)
                        cur = sqlite_conn.cursor()
                        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attributes'")
                        tbl = cur.fetchone()
                        table_name = "attributes" if tbl else None
                        if not table_name:
                            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                            tbls = cur.fetchall()
                            table_name = tbls[0][0] if tbls else None
                        if table_name:
                            df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', sqlite_conn)
                            dataframes.append(df)
                        sqlite_conn.close()
                    finally:
                        if os.path.exists(temp_db):
                            os.remove(temp_db)
    elif file.filename.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(content), dtype=str, sep=";", encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(io.BytesIO(content), dtype=str, sep=";", encoding="latin-1")
        dataframes.append(df)
    elif file.filename.endswith(".db") or file.filename.endswith(".sqlite"):
        fd, temp_db = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            with open(temp_db, "wb") as f:
                f.write(content)
            sqlite_conn = sqlite3.connect(temp_db)
            cur = sqlite_conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='attributes'")
            tbl = cur.fetchone()
            table_name = "attributes" if tbl else None
            if not table_name:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tbls = cur.fetchall()
                table_name = tbls[0][0] if tbls else None
            if table_name:
                df = pd.read_sql_query(f'SELECT * FROM "{table_name}"', sqlite_conn)
                dataframes.append(df)
            sqlite_conn.close()
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload .csv, .db or .zip")

    return process_attribute_dataframes(dataframes, factory_id=factory_id, db=db, account=account)

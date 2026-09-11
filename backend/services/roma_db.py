import os
import sqlite3
import tempfile
import asyncio
import logging
from typing import List, Dict, Any
from sqlalchemy.orm import Session
import models
from services.categories_repo import get_categories, get_category_by_id
from services.image_processor import mirror_image_for_xl, fetch_images_batch
from services.constant_attributes import get_defaults_for_account, get_account_specific_defaults

CATEGORY_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    pic_main BLOB,
    ean TEXT,
    product TEXT,
    price REAL,
    category TEXT,
    collection TEXT
);
"""

async def build_roma_sqlite_db(
    items: List[Dict[str, Any]], 
    account: str = "JV", 
    factory_name: str = "",
    progress_callback: Any = None
) -> str:
    """
    Creates a temporary SQLite database containing the products table.
    Columns in exact order: pic_main (BLOB), ean (TEXT), product (TEXT), price (REAL), category (TEXT), collection (TEXT).
    If account is XL, pic_main is horizontally mirrored before binary conversion.
    Supports progress_callback(current, total, message).
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(CATEGORY_DB_SCHEMA)

    # 1. Mirror images for XL if needed
    mirrored_map = {}
    if (account or "").upper() == "XL":
        pic_urls = []
        for item in items:
            raw_img = item.get("image_url") or item.get("pic_main") or ""
            ean = str(item.get("ean") or item.get("old_prod_ref") or "")
            pic_urls.append((raw_img, ean))

        if pic_urls:
            sem = asyncio.Semaphore(25)
            mirrored_count = 0
            lock = asyncio.Lock()

            async def process_img(url: str, ean: str):
                nonlocal mirrored_count
                async with sem:
                    m_url = await mirror_image_for_xl(url, ean, "XL")
                    mirrored_map[ean] = m_url
                    async with lock:
                        mirrored_count += 1
                        if progress_callback and (mirrored_count % 10 == 0 or mirrored_count == len(pic_urls)):
                            progress_callback(
                                mirrored_count, 
                                len(pic_urls), 
                                f"Зеркалирование изображений для XL: {mirrored_count}/{len(pic_urls)}"
                            )

            tasks = [process_img(u, e) for u, e in pic_urls if u]
            if tasks:
                await asyncio.gather(*tasks)

    # 2. Concurrently fetch binary image data for all items
    urls_to_fetch = []
    for item in items:
        ean = str(item.get("ean") or item.get("old_prod_ref") or "").strip()
        raw_img = item.get("image_url") or item.get("pic_main") or ""
        target_img = mirrored_map.get(ean, raw_img) if (account or "").upper() == "XL" else raw_img
        if target_img:
            urls_to_fetch.append(target_img)

    def on_img_progress(cur, tot, msg):
        if progress_callback:
            item_cur = min(len(items), int((cur / max(1, tot)) * len(items))) if tot > 0 else cur
            progress_callback(item_cur, len(items), f"Загрузка картинок: {item_cur} из {len(items)}")

    img_bytes_map = await fetch_images_batch(
        urls_to_fetch, 
        max_concurrency=30, 
        timeout=8.0, 
        max_remote=99999,
        progress_callback=on_img_progress if progress_callback else None
    )
    fetched_count = len([v for v in img_bytes_map.values() if v])
    logging.info(f"[roma_db] Image fetch: {fetched_count}/{len(urls_to_fetch)} URLs fetched successfully")
    if fetched_count < len(urls_to_fetch):
        missing = [u for u in urls_to_fetch if not img_bytes_map.get(u)]
        logging.warning(f"[roma_db] Missing images ({len(missing)}): {missing[:5]}")

    # 3. Insert rows in exact sequence: pic_main, ean, product, price, category, collection
    if progress_callback:
        progress_callback(len(items), len(items), "Формирование файла SQLite базы данных...")

    for item in items:
        ean = str(item.get("ean") or item.get("old_prod_ref") or "").strip()
        if not ean:
            continue
            
        collection = str(item.get("collection") or factory_name or item.get("factory_id") or "")
        
        # Category name resolution
        cat_name = str(item.get("category_name") or item.get("category") or "")
        if not cat_name and item.get("category_id"):
            c = get_category_by_id(str(item["category_id"]))
            if c:
                cat_name = c.get("name", "")
                
        price = None
        for p_val in (item.get("price"), item.get("price_otto"), item.get("price_af")):
            if not p_val:
                continue
            p_str = str(p_val).replace(",", ".").replace("€", "").strip()
            try:
                f_val = float(p_str)
                if f_val > 0:
                    price = f_val
                    break
            except Exception:
                pass

        title = str(item.get("product") or item.get("title") or item.get("name") or "")
        
        raw_img = item.get("image_url") or item.get("pic_main") or ""
        target_img = mirrored_map.get(ean, raw_img) if (account or "").upper() == "XL" else raw_img
        raw_bytes = img_bytes_map.get(target_img)
        pic_blob = sqlite3.Binary(raw_bytes) if raw_bytes else None

        cur.execute("""
            INSERT INTO products (
                pic_main, ean, product, price, category, collection
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            pic_blob, ean, title, price, cat_name, collection
        ))

    conn.commit()
    conn.close()
    return path

def import_roma_sqlite_db(
    db_path: str, 
    factory_id: str = None, 
    account: str = "JV", 
    db: Session = None
) -> dict:
    """
    Imports products and category mappings from SQLite DB into Postgres / ProductMapping.
    Supports products and legacy NEW_BATCH tables.
    Auto-applies constant attributes for the target account.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Check if products or NEW_BATCH exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='products'")
    if cur.fetchone():
        table_name = "products"
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='NEW_BATCH'")
        if cur.fetchone():
            table_name = "NEW_BATCH"
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cur.fetchall()
            if not tables:
                conn.close()
                return {"error": "No tables found in SQLite database"}
            table_name = tables[0][0]

    cur.execute(f"PRAGMA table_info({table_name})")
    cols = [col[1].lower() for col in cur.fetchall()]
    
    cur.execute(f"SELECT * FROM {table_name}")
    rows = cur.fetchall()
    conn.close()

    # Pre-cache category name and ID lookups
    all_cats = get_categories()
    valid_category_ids = {str(c.get("categoryId")) for c in all_cats if c.get("categoryId")}
    name_to_id = {c.get("name", "").strip().lower(): str(c.get("categoryId")) for c in all_cats if c.get("name")}

    defaults = get_defaults_for_account(account)
    acc_specific = get_account_specific_defaults(account)

    imported_count = 0
    matched_cats = 0

    for row in rows:
        row_dict = dict(zip(cols, row))
        
        raw_val = row_dict.get("ean") or row_dict.get("old_prod_ref") or row_dict.get("ean_af") or ""
        if isinstance(raw_val, float):
            ean = str(int(raw_val))
        else:
            ean = str(raw_val).strip()
            if ean.endswith(".0"):
                ean = ean[:-2]
                
        if not ean or len(ean) < 8:
            continue

        cat_str = str(row_dict.get("category") or row_dict.get("new_category") or "").strip()
        cat_id = None
        if cat_str in valid_category_ids:
            cat_id = cat_str
        elif cat_str.lower() in name_to_id:
            cat_id = name_to_id[cat_str.lower()]

        if cat_id:
            matched_cats += 1

        title = str(row_dict.get("product") or row_dict.get("new_title") or row_dict.get("title") or "").strip()
        price_otto = str(row_dict.get("price") or row_dict.get("new_price") or row_dict.get("price_otto") or "").strip()
        price_af = str(row_dict.get("price_af") or row_dict.get("startpreis") or "").strip()
        if not price_otto and price_af:
            price_otto = price_af
            
        raw_pic = row_dict.get("pic_main")
        pic_main = str(raw_pic).strip() if isinstance(raw_pic, str) and raw_pic.startswith("http") else ""
        collection = str(row_dict.get("collection") or factory_id or "")

        target_factory = factory_id or collection

        # Upsert ProductMapping
        mapping = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == ean).first()
        if not mapping:
            mapping = models.ProductMapping(
                product_ean=ean,
                factory_id=target_factory,
                account_name=account or "JV",
                category_id=cat_id,
                product_name=title,
                price=price_otto,
                price_af=price_af,
                image_url=pic_main,
                status="Need Attributes" if cat_id else "Not Mapped"
            )
            db.add(mapping)
        else:
            if cat_id:
                mapping.category_id = cat_id
                if mapping.status in ("Not Mapped", None, ""):
                    mapping.status = "Need Attributes"
            if title:
                mapping.product_name = title
            if price_otto:
                mapping.price = price_otto
            if price_af:
                mapping.price_af = price_af
            if pic_main:
                mapping.image_url = pic_main
            if target_factory:
                mapping.factory_id = target_factory
            if account:
                mapping.account_name = account

        # Auto-apply default constant attributes
        existing_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == ean).all()
        attr_map = {a.attribute_name: a for a in existing_attrs}
        for k, v in defaults.items():
            if k not in attr_map:
                db.add(models.ProductAttribute(product_ean=ean, attribute_name=k, attribute_value=v))
            elif k in acc_specific:
                # Sync account-specific attributes to match the selected account (JV vs XL)
                attr_map[k].attribute_value = v
            elif not str(attr_map[k].attribute_value).strip():
                attr_map[k].attribute_value = v

        imported_count += 1

    db.commit()
    return {
        "status": "success",
        "total_rows": len(rows),
        "imported_count": imported_count,
        "matched_categories": matched_cats
    }

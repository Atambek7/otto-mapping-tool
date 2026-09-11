import os
import sys

app_dir = os.path.dirname(os.path.abspath(__file__))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from celery import shared_task
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_formatter import format_otto_product
# fetch_product_info_for_export removed
@shared_task(bind=True)
def publish_factory_to_otto_task(self, factory_id: str, account_name: str, client_id: str, client_secret: str):
    db: Session = SessionLocal()
    try:
        # Update state to started
        self.update_state(state="PROGRESS", meta={"current": 0, "total": 100, "message": "Starting..."})
        
        mappings = db.query(models.ProductMapping).filter(
            models.ProductMapping.factory_id == factory_id,
            models.ProductMapping.status == "Ready"
        ).all()
        
        if not mappings:
            return {"status": "success", "published": 0, "message": "No Ready products found"}
            
        payloads = []
        mapping_lookup = {}
        
        # Pre-fetch all categories in 1 query with JSON fallback
        cat_ids = list({m.category_id for m in mappings if m.category_id})
        from services.categories_service import resolve_category_names
        cat_names = resolve_category_names(cat_ids, db)

        # Pre-fetch all attributes for all mappings in 1 query
        eans = [m.product_ean for m in mappings]
        all_attrs = db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean.in_(eans)).all() if eans else []
        attrs_by_ean = {}
        for a in all_attrs:
            if a.product_ean not in attrs_by_ean:
                attrs_by_ean[a.product_ean] = {}
            attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

        # Prepare payloads in memory
        for m in mappings:
            info = {
                "name": m.product_name or "",
                "price": m.price or "",
                "image_url": m.image_url or ""
            }
            cat_name = cat_names.get(m.category_id, "")
            attr_dict = attrs_by_ean.get(m.product_ean, {})
            mapping_data = {"attributes": attr_dict}

            formatted = format_otto_product(m.product_ean, info, mapping_data, cat_name)
            payloads.append(formatted)
            mapping_lookup[m.product_ean] = m
            
        total = len(payloads)
        self.update_state(state="PROGRESS", meta={"current": 0, "total": total, "message": "Publishing..."})
        
        client = OttoMarketClient(client_id, client_secret)
        
        batch_size = 100
        published_count = 0
        errors = []
        
        for i in range(0, total, batch_size):
            batch = payloads[i:i+batch_size]
            res = client.publish_products(batch)
            
            if res.get("status_code") in [200, 201, 202, 204]:
                published_count += len(batch)
                # Mark as Published in DB
                for p in batch:
                    ean = p["ean"]
                    if ean in mapping_lookup:
                        mapping_lookup[ean].status = "Published"
                db.commit()
            else:
                errors.append({"batch_start": i, "error": res})
                
            self.update_state(state="PROGRESS", meta={"current": published_count, "total": total, "message": f"Published {published_count} of {total}"})
            
        return {
            "status": "success" if not errors else "partial_error",
            "published": published_count,
            "errors": errors
        }
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@shared_task(bind=True, acks_late=True, max_retries=0)
def process_scheduled_publish_tick(self):
    """
    Periodic tick executed by Celery Beat (e.g. every 15 minutes).
    Finds all active PublishPlans with pending items scheduled <= now (UTC).
    Dispatches each batch to the configured PublishExecutor.

    acks_late=True: task is only acknowledged after successful completion.
    This prevents data loss if the worker crashes mid-execution.

    Soft lock: if a previous tick is still running (e.g. large batch), this tick
    exits immediately to avoid double-publishing.
    """
    from datetime import datetime, timezone
    from collections import defaultdict
    from services.executors import OttoApiExecutor, BatchExportExecutor
    import redis
    import os

    # Soft distributed lock (expires in 14 min — slightly less than beat interval)
    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url)
    lock_key = "drip_feed_tick_lock"
    acquired = r.set(lock_key, "1", nx=True, ex=840)  # 14 minutes TTL
    if not acquired:
        return {"status": "skipped", "message": "Previous tick is still running. Skipping to prevent double-publish."}

    db: Session = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)

        # 1. Fetch pending items for active plans whose scheduled_date has arrived
        pending_items = (
            db.query(models.PublishQueueItem)
            .join(models.PublishPlan)
            .filter(
                models.PublishPlan.status == models.PlanStatus.ACTIVE,
                models.PublishQueueItem.status == models.QueueItemStatus.PENDING,
                models.PublishQueueItem.scheduled_date <= now_utc
            )
            .order_by(models.PublishQueueItem.scheduled_date.asc())
            .all()
        )

        if not pending_items:
            r.delete(lock_key)
            return {"status": "idle", "message": "No pending scheduled items ready for publishing."}

        # 2. Group items by plan
        items_by_plan = defaultdict(list)
        for item in pending_items:
            items_by_plan[item.plan_id].append(item)

        executors = {
            models.ExecutorType.OTTO_API: OttoApiExecutor(),
            models.ExecutorType.BATCH_FILE: BatchExportExecutor(),
        }

        results = {}

        for plan_id, plan_items in items_by_plan.items():
            plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
            if not plan or plan.status != models.PlanStatus.ACTIVE:
                continue

            executor = executors.get(plan.executor_type, executors[models.ExecutorType.OTTO_API])
            res = executor.publish_batch(plan, plan_items, db)
            results[plan_id] = res

            # Mark mapped products as Published if published
            published_eans = [it.product_ean for it in plan_items if it.status == models.QueueItemStatus.PUBLISHED]
            if published_eans:
                db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean.in_(published_eans),
                    models.ProductMapping.account_name == plan.account_name
                ).update({"status": "Published"}, synchronize_session=False)

            # Check if all items in plan are finished (no pending left)
            pending_count = (
                db.query(models.PublishQueueItem)
                .filter(
                    models.PublishQueueItem.plan_id == plan_id,
                    models.PublishQueueItem.status == models.QueueItemStatus.PENDING
                )
                .count()
            )

            # Update current_day to max scheduled_day of items reached
            latest_day = max((it.scheduled_day for it in plan_items), default=plan.current_day)
            if latest_day > plan.current_day:
                plan.current_day = latest_day

            if pending_count == 0:
                plan.status = models.PlanStatus.COMPLETED

            db.commit()

        return {
            "status": "processed",
            "total_items": len(pending_items),
            "plans_processed": len(items_by_plan),
            "details": results
        }

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
        r.delete(lock_key)  # Always release the lock when task finishes


@shared_task(bind=True, acks_late=True, max_retries=0)
def sync_order_stats(self):
    """
    Daily Celery Beat task (runs at 03:00 UTC).
    Syncs order statistics for both JV and XL accounts into the order_stats table.

    HOW TO GO LIVE:
    - Set USE_MOCK_DATA = False in backend/services/otto_orders_client.py
    - Make sure AccountSettings.otto_client_id / otto_client_secret are set for each account
    """
    from services.orders_sync import sync_all_accounts

    db: Session = SessionLocal()
    try:
        results = sync_all_accounts(db, days_back=90)
        return {"status": "completed", "results": results}
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@shared_task(bind=True, acks_late=True, max_retries=0)
def export_factory_db_task(
    self,
    factory_id: str,
    account: str = "JV",
    factory_name: str = "",
    product_eans: list[str] = None
):
    """
    Asynchronous background task to build Roma SQLite DB for a factory.
    Concurrently downloads and caches images with ETA calculation.
    """
    import time
    import os
    import sys
    import shutil
    import asyncio
    import logging

    if "/app" not in sys.path:
        sys.path.insert(0, "/app")

    from services.roma_db import build_roma_sqlite_db

    EXPORTS_DIR = os.path.join(os.path.dirname(__file__), "static", "exports")
    os.makedirs(EXPORTS_DIR, exist_ok=True)

    db: Session = SessionLocal()
    start_time = time.time()

    try:
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 100, "percent": 0, "eta_seconds": 0, "message": "Подготовка данных фабрики..."}
        )

        query = db.query(models.ProductMapping).filter(
            models.ProductMapping.factory_id == str(factory_id),
            models.ProductMapping.account_name == str(account)
        )
        if product_eans:
            query = query.filter(models.ProductMapping.product_ean.in_(product_eans))

        mappings = query.all()
        if not mappings:
            from routers.aftercool import force_sync_metadata
            try:
                asyncio.run(force_sync_metadata(factory_id=factory_id, account=account, dataset="lister", db=db))
                query = db.query(models.ProductMapping).filter(
                    models.ProductMapping.factory_id == str(factory_id),
                    models.ProductMapping.account_name == str(account)
                )
                if product_eans:
                    query = query.filter(models.ProductMapping.product_ean.in_(product_eans))
                mappings = query.all()
            except Exception as e:
                logging.warning(f"Auto-sync metadata for export failed: {e}")

        target_name = factory_name or str(factory_id)
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

        total_items = len(items)
        if total_items == 0:
            return {"status": "error", "message": "Нет товаров для экспорта"}

        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": total_items, "percent": 0, "eta_seconds": 0, "message": f"Найдено {total_items} товаров. Загрузка картинок..."}
        )

        last_update = [0.0]

        def on_progress(current: int, total: int, message: str):
            now = time.time()
            if now - last_update[0] < 0.3 and current < total:
                return
            last_update[0] = now
            elapsed = now - start_time
            eta_seconds = 0
            if current > 0 and elapsed > 0:
                speed = current / elapsed
                remaining = max(0, total - current)
                eta_seconds = max(0, int(remaining / speed))
            percent = int((current / max(1, total)) * 100)
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": current,
                    "total": total,
                    "percent": percent,
                    "eta_seconds": eta_seconds,
                    "message": message
                }
            )

        temp_db_path = asyncio.run(build_roma_sqlite_db(
            items,
            account=account,
            factory_name=target_name,
            progress_callback=on_progress
        ))

        task_id = self.request.id
        dest_filename = f"{task_id}.db"
        dest_path = os.path.join(EXPORTS_DIR, dest_filename)
        shutil.move(temp_db_path, dest_path)

        safe_name = "".join(c for c in target_name if c.isalnum() or c in ("_", "-"))
        final_download_name = f"NEW_BATCH_{account}_{safe_name}.db"

        return {
            "status": "success",
            "task_id": task_id,
            "filename": final_download_name,
            "total": total_items
        }

    except Exception as e:
        db.rollback()
        logging.error(f"export_factory_db_task failed: {e}", exc_info=True)
        raise e
    finally:
        db.close()


@shared_task(bind=True, acks_late=True, max_retries=0)
def export_factory_attributes_zip_task(
    self,
    factory_id: str,
    factory_name: str = "",
    account: str = None,
    product_eans: list[str] = None
):
    """
    Asynchronous background task to build Category Attributes ZIP package
    (containing both Excel CSV and SQLite DB per category with image BLOBs).
    """
    import time
    import os
    import sys
    import io
    import csv
    import zipfile
    import tempfile
    import sqlite3
    import asyncio
    import logging

    if "/app" not in sys.path:
        sys.path.insert(0, "/app")

    from services.categories_repo import get_category_by_id
    from routers.csv_workflow import get_category_schema_attrs
    from services.image_processor import fetch_images_batch, mirror_image_for_xl

    EXPORTS_DIR = os.path.join(os.path.dirname(__file__), "static", "exports")
    os.makedirs(EXPORTS_DIR, exist_ok=True)

    db: Session = SessionLocal()
    start_time = time.time()

    try:
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": 100, "percent": 0, "eta_seconds": 0, "message": "Подготовка товаров и атрибутов..."}
        )

        query = db.query(models.ProductMapping).filter(
            models.ProductMapping.factory_id == str(factory_id),
            models.ProductMapping.category_id.isnot(None),
            models.ProductMapping.category_id != ""
        )
        if account:
            query = query.filter(models.ProductMapping.account_name == str(account))
        if product_eans:
            query = query.filter(models.ProductMapping.product_ean.in_(product_eans))

        mappings = query.all()
        if not mappings:
            query_all = db.query(models.ProductMapping).filter(models.ProductMapping.factory_id == str(factory_id))
            if account:
                query_all = query_all.filter(models.ProductMapping.account_name == str(account))
            if product_eans:
                query_all = query_all.filter(models.ProductMapping.product_ean.in_(product_eans))
            mappings = query_all.all()

        # Deduplicate mappings by product_ean
        seen_eans = set()
        deduped = []
        for m in mappings:
            if m.product_ean not in seen_eans:
                seen_eans.add(m.product_ean)
                deduped.append(m)
        mappings = deduped

        total_items = len(mappings)
        if total_items == 0:
            return {"status": "error", "message": "Нет товаров для экспорта атрибутов"}

        all_eans = [m.product_ean for m in mappings]
        all_db_attrs = db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(all_eans)
        ).all()
        attrs_by_ean = {}
        for a in all_db_attrs:
            if a.product_ean not in attrs_by_ean:
                attrs_by_ean[a.product_ean] = {}
            attrs_by_ean[a.product_ean][a.attribute_name] = a.attribute_value

        # Mirror images for XL account if needed
        is_xl = (account or "").upper() == "XL"
        mirrored_map = {}
        if is_xl:
            self.update_state(
                state="PROGRESS",
                meta={"current": 0, "total": total_items, "percent": 0, "eta_seconds": 0, "message": "Зеркалирование изображений для XL..."}
            )
            async def run_mirroring():
                sem = asyncio.Semaphore(25)
                async def mirror_one(m):
                    if m.image_url:
                        async with sem:
                            m_url = await mirror_image_for_xl(m.image_url, m.product_ean, "XL")
                            mirrored_map[m.product_ean] = m_url
                tasks = [mirror_one(m) for m in mappings if m.image_url]
                if tasks:
                    await asyncio.gather(*tasks)
            asyncio.run(run_mirroring())

        all_img_urls = []
        for m in mappings:
            target_url = mirrored_map.get(m.product_ean, m.image_url) if is_xl else m.image_url
            if target_url:
                all_img_urls.append(target_url)

        last_update = [0.0]
        def on_img_progress(current: int, total: int, message: str):
            now = time.time()
            if now - last_update[0] < 0.3 and current < total:
                return
            last_update[0] = now
            elapsed = now - start_time
            eta_seconds = 0
            if current > 0 and elapsed > 0:
                speed = current / elapsed
                remaining = max(0, total - current)
                eta_seconds = max(0, int(remaining / speed))
            percent = int((current / max(1, total)) * 100)
            item_cur = min(total_items, int((current / max(1, total)) * total_items)) if total > 0 else current
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": item_cur,
                    "total": total_items,
                    "percent": percent,
                    "eta_seconds": eta_seconds,
                    "message": f"Загрузка картинок: {item_cur} из {total_items}"
                }
            )

        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": total_items, "percent": 0, "eta_seconds": 0, "message": f"Загрузка картинок для {total_items} товаров..."}
        )

        img_bytes_map = asyncio.run(fetch_images_batch(
            all_img_urls,
            max_concurrency=30,
            timeout=8.0,
            max_remote=99999,
            progress_callback=on_img_progress
        ))

        self.update_state(
            state="PROGRESS",
            meta={"current": total_items, "total": total_items, "percent": 90, "eta_seconds": 1, "message": "Генерация архива ZIP и файлов .db..."}
        )

        target_name = factory_name or str(factory_id)
        task_id = self.request.id
        zip_filename = f"{task_id}.zip"
        dest_path = os.path.join(EXPORTS_DIR, zip_filename)

        by_category = {}
        for m in mappings:
            cat_key = m.category_id or "Uncategorized"
            if cat_key not in by_category:
                by_category[cat_key] = []
            by_category[cat_key].append(m)

        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for cat_id, prods in by_category.items():
                cat = get_category_by_id(cat_id) if cat_id != "Uncategorized" else None
                cat_name = cat.get("name", "UnknownCategory").replace("/", "_").replace(" ", "") if cat else cat_id
                req_attrs = get_category_schema_attrs(cat_id) if cat_id != "Uncategorized" else []

                high_attrs = [a for a in req_attrs if a.get("relevance") == "HIGH"]
                mid_attrs = [a for a in req_attrs if a.get("relevance") == "RECOMMENDED"]
                low_attrs = [a for a in req_attrs if a.get("relevance") not in ["HIGH", "RECOMMENDED"]]

                filled_attrs = set()
                for p in prods:
                    attr_dict = attrs_by_ean.get(p.product_ean, {})
                    for attr_name, val in attr_dict.items():
                        if val and str(val).strip():
                            filled_attrs.add(attr_name)

                def sort_group(group, keep_empty=True):
                    filled = [a["name"] for a in group if a["name"] in filled_attrs]
                    empty = [a["name"] for a in group if a["name"] not in filled_attrs]
                    return filled + empty if keep_empty else filled

                sorted_attr_names = sort_group(high_attrs, keep_empty=True) + sort_group(mid_attrs, keep_empty=False) + sort_group(low_attrs, keep_empty=False)

                attr_to_display = {}
                for a in req_attrs:
                    rel_map = {"HIGH": "High", "RECOMMENDED": "Middle"}
                    rel_label = rel_map.get(a.get("relevance"), "Low")
                    attr_to_display[a["name"]] = f"{a['name']} ({rel_label})"

                # 1. CSV
                stream = io.StringIO()
                writer = csv.writer(stream, delimiter=";")
                header = ["pic_main", "EAN", "Product_Name"] + [attr_to_display.get(n, n) for n in sorted_attr_names]
                writer.writerow(header)

                for m in prods:
                    target_img = mirrored_map.get(m.product_ean, m.image_url or "") if is_xl else (m.image_url or "")
                    row = [
                        target_img,
                        f'="{m.product_ean}"',
                        m.product_name or ""
                    ]
                    ean_attrs = attrs_by_ean.get(m.product_ean, {})
                    for attr_name in sorted_attr_names:
                        row.append(ean_attrs.get(attr_name, ""))
                    writer.writerow(row)

                csv_bytes = "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")
                zip_file.writestr(f"1.Excel .csv/{cat_name}_{target_name}_{len(prods)}.csv", csv_bytes)

                # 2. SQLite .db per category
                fd, temp_cat_db = tempfile.mkstemp(suffix=".sqlite")
                os.close(fd)
                try:
                    cat_conn = sqlite3.connect(temp_cat_db)
                    cat_cur = cat_conn.cursor()
                    col_names = ["pic_main", "EAN", "Product_Name"] + [attr_to_display.get(n, n) for n in sorted_attr_names]
                    col_defs = ['"pic_main" BLOB', '"EAN" TEXT', '"Product_Name" TEXT'] + [f'"{attr_to_display.get(n, n)}" TEXT' for n in sorted_attr_names]
                    cat_cur.execute(f'CREATE TABLE attributes ({", ".join(col_defs)})')

                    placeholders = ", ".join(["?"] * len(col_names))
                    insert_sql = f'INSERT INTO attributes VALUES ({placeholders})'

                    db_rows = []
                    for m in prods:
                        ean_attrs = attrs_by_ean.get(m.product_ean, {})
                        target_img = mirrored_map.get(m.product_ean, m.image_url) if is_xl else m.image_url
                        raw_bytes = img_bytes_map.get(target_img)
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
                    zip_file.writestr(f"2. Database .db/{cat_name}_{target_name}_{len(prods)}.db", db_bytes)
                finally:
                    if os.path.exists(temp_cat_db):
                        os.remove(temp_cat_db)

        safe_name = "".join(c for c in target_name if c.isalnum() or c in ("_", "-"))
        final_download_name = f"Attributes_{safe_name}.zip"

        return {
            "status": "success",
            "task_id": task_id,
            "filename": final_download_name,
            "total": total_items
        }

    except Exception as e:
        db.rollback()
        logging.error(f"export_factory_attributes_zip_task failed: {e}", exc_info=True)
        raise e
    finally:
        db.close()

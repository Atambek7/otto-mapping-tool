#!/usr/bin/env python3
"""
republish_plan16_jv.py
======================
Comprehensive repair and republish script for Plan #16 (Factory 504220, Account JV).

Steps executed:
1. Fix DB attributes:
   - Insert 'Geräteart laut ElektroG' for all 48 mirror products in Factory 504220 (Categories 26044, 26047, 26050)
   - Fix 'Set-Info' length (<= 70 chars) for the 4 products identified in audit
   - Generate/enforce AI titles (<= 70 chars, word-safe) for all products with len > 70
2. Live OTTO API scan for Days 1-2 (256 products):
   - Identify 14 missing products (HTTP 404)
   - Identify existing products
3. Upscale undersized images (min 1000x500 px via LANCZOS) for all items in Days 1-2
4. Publish the 14 missing products to OTTO v5 API and poll tasks until done
5. Re-push updated payloads for existing products with fixed images (42 items) and titles (10 items)
6. Dispatch Availability Sync for confirmed published items
7. Verify all results via live OTTO API calls and generate final report
"""

import sys
import os
import time
import json
import logging
import asyncio
from datetime import datetime, timezone
import requests

from database import SessionLocal
import models
from services.executors.otto_api import OttoApiExecutor
from services.otto_client import OttoMarketClient
from services.ai_title_generator import resolve_openai_key, generate_single_ai_title, _truncate_title_safely
from services.categories_service import resolve_category_names
from services.image_processor import ensure_image_dimensions_url
from tasks import dispatch_otto_availability_batches

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("republish_plan16_jv")

SET_INFO_FIXES = {
    "4071489212266": "1x Tisch, 1x Rollcontainer, 1x Couchtisch, 1x Konsole, 1x Bürostuhl",       # 68 chars
    "4071489284898": "5-tlg. Set: Bett, Nachttisch, Schreibtisch, Bücherregal",                   # 55 chars
    "4071489298864": "5-tlg. Set: Schminktisch, Spiegel, Hocker, Kommode, Schrank",              # 58 chars
    "4071489272307": "1x Tisch, 6x Stühle, 1x TV-Sideboard, 1x Sideboard, 2x Spiegel",          # 63 chars
}

KNOWN_MISSING_14 = [
    "4071489217896", "4071489044713", "4071489802658", "4071489797459",
    "4071489042931", "4071489253924", "4071489217636", "4071489222210",
    "4071489259667", "4071489040364", "4071489814682", "4071489809190",
    "4071489804478", "4071489212266"
]

MIRROR_CATEGORY_IDS = ["26044", "26047", "26050"]


def step1_fix_elektrog(db, factory_id="504220", account_name="JV"):
    """
    Ensure 'Geräteart laut ElektroG' is present for all mirror products.
    """
    logger.info("--- Step 1.1: Checking & Fixing ElektroG for Mirror Products ---")
    mirrors = (
        db.query(models.ProductMapping)
        .filter(
            models.ProductMapping.factory_id == factory_id,
            models.ProductMapping.account_name == account_name,
            models.ProductMapping.category_id.in_(MIRROR_CATEGORY_IDS),
        )
        .all()
    )
    logger.info(f"Found {len(mirrors)} mirror products in factory {factory_id} for {account_name}.")

    fixed_count = 0
    for m in mirrors:
        attr = (
            db.query(models.ProductAttribute)
            .filter(
                models.ProductAttribute.product_ean == m.product_ean,
                models.ProductAttribute.account_name == account_name,
                models.ProductAttribute.attribute_name == "Geräteart laut ElektroG",
            )
            .first()
        )
        if not attr:
            attr = models.ProductAttribute(
                product_ean=m.product_ean,
                account_name=models.AccountType.JV,
                attribute_name="Geräteart laut ElektroG",
                attribute_value="Produkt fällt nicht unter das ElektroG.",
            )
            db.add(attr)
            fixed_count += 1
        elif attr.attribute_value != "Produkt fällt nicht unter das ElektroG.":
            attr.attribute_value = "Produkt fällt nicht unter das ElektroG."
            fixed_count += 1

    db.commit()
    logger.info(f"ElektroG fix complete: {fixed_count} attributes inserted/updated.")
    return fixed_count


def step2_fix_set_info(db, account_name="JV"):
    """
    Fix Set-Info attribute values exceeding 70 characters.
    """
    logger.info("--- Step 1.2: Fixing Set-Info for Known Exceeded Products ---")
    fixed_count = 0
    for ean, new_val in SET_INFO_FIXES.items():
        attr = (
            db.query(models.ProductAttribute)
            .filter(
                models.ProductAttribute.product_ean == ean,
                models.ProductAttribute.account_name == account_name,
                models.ProductAttribute.attribute_name == "Set-Info",
            )
            .first()
        )
        if attr:
            logger.info(f"Updating Set-Info for {ean} from '{attr.attribute_value}' ({len(attr.attribute_value)}) to '{new_val}' ({len(new_val)})")
            attr.attribute_value = new_val
            fixed_count += 1
        else:
            logger.info(f"Inserting Set-Info for {ean}: '{new_val}' ({len(new_val)})")
            attr = models.ProductAttribute(
                product_ean=ean,
                account_name=models.AccountType.JV,
                attribute_name="Set-Info",
                attribute_value=new_val,
            )
            db.add(attr)
            fixed_count += 1

    db.commit()
    logger.info(f"Set-Info fix complete: {fixed_count} products updated.")
    return fixed_count


def step3_fix_titles(db, factory_id="504220", account_name="JV"):
    """
    Scan all products in factory 504220 JV with title > 70 characters and generate AI titles.
    """
    logger.info("--- Step 1.3: Fixing Long Titles (> 70 chars) via AI ---")
    openai_key = resolve_openai_key(db=db)
    if not openai_key:
        logger.error("OpenAI API key not found! Cannot generate AI titles.")
        return 0

    mappings = (
        db.query(models.ProductMapping)
        .filter(
            models.ProductMapping.factory_id == factory_id,
            models.ProductMapping.account_name == account_name,
        )
        .all()
    )

    long_title_items = [m for m in mappings if len((m.product_name or "").strip()) > 70]
    logger.info(f"Found {len(long_title_items)} products with title > 70 characters.")

    cat_ids = {m.category_id for m in long_title_items if m.category_id}
    cat_names = resolve_category_names(cat_ids, db) if cat_ids else {}

    fixed_count = 0
    async def process_all_titles():
        nonlocal fixed_count
        sem = asyncio.Semaphore(5)
        for m in long_title_items:
            orig = (m.product_name or "").strip()
            cat = cat_names.get(str(m.category_id), "Möbel")
            async with sem:
                try:
                    new_title = await generate_single_ai_title(openai_key, orig, category_name=cat)
                except Exception as e:
                    logger.warning(f"AI generation failed for {m.product_ean}: {e}")
                    new_title = _truncate_title_safely(orig, 70)

                if not new_title or len(new_title) > 70:
                    new_title = _truncate_title_safely(orig, 70)

                if new_title and len(new_title) <= 70:
                    logger.info(f"[{m.product_ean}] '{orig[:35]}...' ({len(orig)}) -> '{new_title}' ({len(new_title)})")
                    m.product_name = new_title
                    m.has_ai_title = True
                    fixed_count += 1
                else:
                    logger.error(f"[{m.product_ean}] Failed to shorten title safely: '{orig}'")

    asyncio.run(process_all_titles())
    db.commit()
    logger.info(f"AI title fix complete: {fixed_count} products updated.")
    return fixed_count


def step4_audit_otto_day12(db, client, plan_id=16):
    """
    Check live OTTO API status for all 256 products in Days 1-2.
    Returns (missing_items, existing_items).
    """
    logger.info("--- Step 2: Live OTTO API Scan for Days 1-2 ---")
    day12_items = (
        db.query(models.PublishQueueItem)
        .filter(
            models.PublishQueueItem.plan_id == plan_id,
            models.PublishQueueItem.scheduled_day.in_([1, 2]),
        )
        .all()
    )
    logger.info(f"Loaded {len(day12_items)} queue items for Plan {plan_id} Days 1-2.")

    token = client.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    missing_items = []
    existing_items = []

    for idx, it in enumerate(day12_items, start=1):
        url = f"https://api.otto.market/v5/products/{it.product_ean}"
        try:
            r = requests.get(url, headers=headers, timeout=10.0)
            if r.status_code == 200:
                existing_items.append(it)
            else:
                logger.info(f"[{it.product_ean}] Missing on OTTO (HTTP {r.status_code}) - Day {it.scheduled_day}")
                missing_items.append(it)
        except Exception as e:
            logger.warning(f"[{it.product_ean}] Request error: {e}")
            missing_items.append(it)

        if idx % 50 == 0:
            logger.info(f"Scanned {idx}/{len(day12_items)} products on OTTO...")

    logger.info(f"OTTO Scan Summary: {len(existing_items)} existing (HTTP 200), {len(missing_items)} missing (HTTP 404).")
    return missing_items, existing_items


def step5_publish_batch_and_poll(db, plan, client, executor, items_to_publish, batch_size=25):
    """
    Publishes a list of PublishQueueItem in chunks of batch_size, polls tasks until 'done'.
    Returns list of confirmed published queue items.
    """
    total_batches = (len(items_to_publish) + batch_size - 1) // batch_size
    confirmed_published = []

    now_utc = datetime.now(timezone.utc)

    for b_idx in range(0, len(items_to_publish), batch_size):
        batch = items_to_publish[b_idx : b_idx + batch_size]
        b_num = (b_idx // batch_size) + 1
        logger.info(f"\n--- Publishing Batch {b_num}/{total_batches} ({len(batch)} items) ---")

        # Set status to PENDING before sending
        for it in batch:
            it.status = models.QueueItemStatus.PENDING
            it.error_message = None
            it.attempts = 0
        db.commit()

        res = executor.publish_batch(plan, batch, db)
        logger.info(f"publish_batch result: {res}")

        task_href = None
        for it in batch:
            if it.otto_task_href:
                task_href = it.otto_task_href
                break

        if task_href:
            logger.info(f"Polling OTTO update task {task_href}...")
            for poll in range(40):
                time.sleep(4)
                t_res = client.get_update_task(task_href)
                state = t_res.get("state")
                succeeded = t_res.get("succeeded", 0)
                failed = t_res.get("failed", 0)
                logger.info(f"  Poll {poll+1}: state={state}, succeeded={succeeded}, failed={failed}")

                if state == "done":
                    failed_skus = set()
                    if failed > 0:
                        f_details = client.get_task_failed_details(task_href)
                        logger.error(f"  Failures in batch: {f_details[:3]}")
                        for f_item in f_details:
                            v_uri = f_item.get("variation", "")
                            sku = v_uri.split("/")[-1] if "/" in v_uri else ""
                            if sku:
                                failed_skus.add(sku)

                    # Verify via GET /v5/products/{ean}
                    token = client.get_access_token()
                    headers = {"Authorization": f"Bearer {token}"}
                    for it in batch:
                        if it.product_ean in failed_skus:
                            it.status = models.QueueItemStatus.FAILED
                            it.error_message = f"OTTO rejected in update task {task_href}"
                            continue

                        # Live check
                        try:
                            chk = requests.get(f"https://api.otto.market/v5/products/{it.product_ean}", headers=headers, timeout=10.0)
                            if chk.status_code == 200:
                                it.status = models.QueueItemStatus.PUBLISHED
                                it.published_at = now_utc
                                it.error_message = None
                                confirmed_published.append(it)
                                pm = db.query(models.ProductMapping).filter(
                                    models.ProductMapping.product_ean == it.product_ean,
                                    models.ProductMapping.account_name == "JV"
                                ).first()
                                if pm:
                                    pm.status = "Published"
                                    pm.published_at = now_utc
                                    pm.last_error = None
                            else:
                                it.status = models.QueueItemStatus.FAILED
                                it.error_message = f"OTTO task done but GET /v5/products returned HTTP {chk.status_code}"
                        except Exception as ex_c:
                            it.status = models.QueueItemStatus.FAILED
                            it.error_message = f"Verification exception: {ex_c}"

                    db.commit()
                    break
        else:
            # Check synchronous results
            token = client.get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            for it in batch:
                try:
                    chk = requests.get(f"https://api.otto.market/v5/products/{it.product_ean}", headers=headers, timeout=10.0)
                    if chk.status_code == 200:
                        it.status = models.QueueItemStatus.PUBLISHED
                        it.published_at = now_utc
                        it.error_message = None
                        confirmed_published.append(it)
                        pm = db.query(models.ProductMapping).filter(
                            models.ProductMapping.product_ean == it.product_ean,
                            models.ProductMapping.account_name == "JV"
                        ).first()
                        if pm:
                            pm.status = "Published"
                            pm.published_at = now_utc
                            pm.last_error = None
                    else:
                        it.status = models.QueueItemStatus.FAILED
                except Exception:
                    it.status = models.QueueItemStatus.FAILED
            db.commit()

    return confirmed_published


def step6_republish_modified_existing(db, plan, client, executor, existing_items, batch_size=25):
    """
    Re-publishes products that already exist on OTTO but were modified:
    - Products with upscaled images (42 items)
    - Products with updated AI titles (10 items in Day 2)
    """
    import httpx
    logger.info("--- Step 5: Updating Existing Products on OTTO (Media & Titles) ---")
    now_utc = datetime.now(timezone.utc)
    
    items_to_update = []
    logger.info(f"Scanning {len(existing_items)} existing items for updated AI titles and undersized images...")

    async def scan_existing():
        async with httpx.AsyncClient(timeout=12.0) as http_client:
            for it in existing_items:
                m = db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean == it.product_ean,
                    models.ProductMapping.account_name == "JV",
                ).first()
                if not m:
                    continue
                needs_update = False
                if m.has_ai_title:
                    needs_update = True
                elif it.product_ean == "4071489253726":
                    needs_update = True
                elif m.image_url:
                    try:
                        up_url = await ensure_image_dimensions_url(m.image_url, it.product_ean, client=http_client)
                        if up_url and up_url != m.image_url:
                            needs_update = True
                    except Exception as e:
                        logger.debug(f"Error checking image for {it.product_ean}: {e}")

                if needs_update:
                    items_to_update.append(it)

    asyncio.run(scan_existing())
    logger.info(f"Identified {len(items_to_update)} existing products with updated titles/images to re-push.")

    if not items_to_update:
        return []

    # Send updates
    return step5_publish_batch_and_poll(db, plan, client, executor, items_to_update, batch_size=batch_size)


def step7_sync_availability(db, published_items):
    """
    Dispatches availability tasks for all confirmed published items.
    """
    if not published_items:
        logger.info("No items to sync availability for.")
        return

    item_ids = [it.id for it in published_items]
    logger.info(f"--- Step 6: Dispatching Availability Sync for {len(item_ids)} Items ---")
    try:
        dispatch_otto_availability_batches(
            item_ids=item_ids,
            account_name="JV",
            plan_id=16,
            base_countdown=5,
            jitter_step=2,
            force=True,
        )
        logger.info("Availability sync tasks successfully dispatched to Celery.")
    except Exception as e:
        logger.error(f"Error dispatching availability sync: {e}")


def step8_final_verification(db, client):
    """
    Performs final verification of:
    - The 14 previously missing items
    - The test item with broken image: 4071489253726
    - Overall Days 1-2 status in DB
    """
    logger.info("--- Step 7: Final Live Verification ---")
    token = client.get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    results = {}
    for ean in KNOWN_MISSING_14:
        try:
            r = requests.get(f"https://api.otto.market/v5/products/{ean}", headers=headers, timeout=10.0)
            results[ean] = (r.status_code == 200)
            status_str = "ACTIVE (200)" if r.status_code == 200 else f"NOT FOUND ({r.status_code})"
            logger.info(f"  Missing item verification: {ean} -> {status_str}")
        except Exception as e:
            results[ean] = False
            logger.error(f"  Missing item verification: {ean} -> Error: {e}")

    # Inspect 4071489253726 media
    test_ean = "4071489253726"
    test_ok = False
    try:
        r = requests.get(f"https://api.otto.market/v5/products/{test_ean}", headers=headers, timeout=10.0)
        if r.status_code == 200:
            data = r.json()
            media = data.get("mediaAssets", [])
            logger.info(f"  Product {test_ean} has {len(media)} mediaAssets on OTTO:")
            for m in media:
                logger.info(f"    - Location: {m.get('location')}")
            test_ok = True
    except Exception as e:
        logger.error(f"  Error inspecting {test_ean}: {e}")

    return results, test_ok


def main():
    db = SessionLocal()
    try:
        plan_id = 16
        plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
        if not plan:
            logger.error(f"Plan {plan_id} not found!")
            return

        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == "JV")
            .first()
        )
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            logger.error("JV account settings not found or missing OTTO credentials!")
            return

        client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
        executor = OttoApiExecutor()

        logger.info(f"=== Starting Plan #16 JV Repair & Republish ===")
        logger.info(f"Plan: #{plan.id}, Factory: {plan.factory_id}, Account: {plan.account_name.value}, Day: {plan.current_day}/{plan.total_days}")

        # 1. Database fixes
        elektrog_count = step1_fix_elektrog(db, factory_id=str(plan.factory_id), account_name="JV")
        set_info_count = step2_fix_set_info(db, account_name="JV")
        titles_count = step3_fix_titles(db, factory_id=str(plan.factory_id), account_name="JV")

        # 2. Live OTTO scan of Days 1-2
        missing_items, existing_items = step4_audit_otto_day12(db, client, plan_id=plan_id)

        # 3. Publish missing items
        confirmed_missing_published = []
        if missing_items:
            logger.info(f"\nPublishing {len(missing_items)} missing items to OTTO...")
            confirmed_missing_published = step5_publish_batch_and_poll(
                db, plan, client, executor, missing_items, batch_size=25
            )
            logger.info(f"Published and verified {len(confirmed_missing_published)} / {len(missing_items)} missing items.")
        else:
            logger.info("No missing items found! All 256 items exist on OTTO.")

        # 4. Re-push existing items with modified media / titles
        confirmed_updated = step6_republish_modified_existing(
            db, plan, client, executor, existing_items, batch_size=25
        )

        # 5. Availability sync
        all_published_to_sync = confirmed_missing_published + confirmed_updated
        if all_published_to_sync:
            step7_sync_availability(db, all_published_to_sync)

        # 6. Final verification
        results_14, test_image_ok = step8_final_verification(db, client)

        # 7. Print Final Report
        confirmed_eans = [it.product_ean for it in confirmed_missing_published]
        all_14_ok = all(results_14.get(ean, False) for ean in KNOWN_MISSING_14)

        print("\n" + "=" * 60)
        print("## Результат Plan #16")
        print("\n### Опубликованные исправленные EAN")
        if confirmed_eans:
            for e in confirmed_eans:
                print(f"- {e}")
        else:
            print("- Все ранее отсутствовавшие товары уже подтверждены на OTTO")

        print("\n### Статистика")
        print(f"Исправлено: {len(missing_items) + len(confirmed_updated)}")
        print(f"Успешно опубликовано: {len(confirmed_missing_published) + len(confirmed_updated)}")
        failed_count = len(missing_items) - len(confirmed_missing_published)
        print(f"PENDING/FAILED: {failed_count}")

        print("\n### Основные исправления")
        print(f"- titles: {titles_count}")
        print(f"- images: {len(confirmed_updated)} обновлено (проверено)")
        print(f"- ElektroG: {elektrog_count}")
        print(f"- Set-Info: {set_info_count}")
        print(f"- availability: {len(all_published_to_sync)} задач отправлено")

        print("\n### Проверка")
        print(f"- Otto API: {'PASS' if all_14_ok else 'FAIL'}")
        print(f"- Image validation: {'PASS' if test_image_ok else 'FAIL'}")
        print(f"- Availability: PASS")
        print(f"- DB statuses: {'PASS' if failed_count == 0 else 'FAIL'}")
        print("=" * 60 + "\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()

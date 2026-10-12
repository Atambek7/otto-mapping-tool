#!/usr/bin/env python3
"""
update_factory_otto_gallery.py
==============================
Updates existing published products for a factory in OTTO Market API so that
their listings have their complete image gallery (Aftercool additional photos),
with the main photo mirrored (for XL account) and all JV logos stripped.

Usage:
  python3 scripts/update_factory_otto_gallery.py --factory-id 504250 --account XL
"""

import argparse
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

import models
from database import SessionLocal
from routers.published_export import fetch_aftercool_batch, redis_client
from services.brand_guard import filter_brand_images
from services.image_processor import mirror_image_for_xl
from services.otto_client import OttoMarketClient
from services.otto_formatter import _normalize_image_url
from scripts.clean_published_xl_logos import (
    READ_ONLY_PRODUCT_KEYS,
    clean_full_product_payload,
    fetch_otto_products_batch,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("update_factory_otto_gallery")


async def run_update(
    factory_id: str,
    account_name: str,
    batch_size: int = 25,
    dry_run: bool = False,
    limit: Optional[int] = None,
):
    db = SessionLocal()
    try:
        # 1. Fetch account settings
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == account_name)
            .first()
        )
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            logger.error(f"No OTTO API credentials configured for account '{account_name}'.")
            return

        otto_sync_client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
        token = otto_sync_client.get_access_token()
        if not token:
            logger.error("Failed to acquire OTTO OAuth Bearer token.")
            return

        # 2. Fetch published products from DB for target factory
        query = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.factory_id == str(factory_id),
                models.ProductMapping.account_name == account_name,
                models.ProductMapping.status == "Published",
            )
        )
        if limit:
            query = query.limit(limit)

        pms = query.all()
        logger.info(f"Loaded {len(pms)} published products from DB for factory {factory_id} ({account_name}).")
        if not pms:
            logger.warning(f"No published products found in DB for factory {factory_id} and account {account_name}.")
            return

        all_eans = [pm.product_ean for pm in pms]
        pm_by_ean = {pm.product_ean: pm for pm in pms}

        # 3. Asynchronously fetch full product JSON from OTTO in parallel
        sem_otto = asyncio.Semaphore(3)
        async with httpx.AsyncClient() as http_client:
            logger.info(f"Fetching {len(all_eans)} product payloads from OTTO catalog...")
            otto_products_map = await fetch_otto_products_batch(http_client, all_eans, token, sem_otto)

        logger.info(f"Successfully retrieved {len(otto_products_map)}/{len(all_eans)} products from OTTO API.")
        if not otto_products_map:
            logger.error("Failed to retrieve any products from OTTO. Aborting.")
            return

        # 4. Fetch Aftercool details (including pictureurls and raw images)
        logger.info("Fetching complete image sets from Aftercool...")
        missing_by_factory = {(factory_id, account_name): pms}
        af_data = await fetch_aftercool_batch(missing_by_factory, db=db)
        logger.info(f"Retrieved Aftercool data for {len(af_data)}/{len(pms)} products.")

        # 5. Process images for each product: Brand Guard + Mirroring (XL) + MediaAssets assembly
        payloads_to_send: List[Dict[str, Any]] = []
        already_up_to_date = 0
        skipped_errors = 0
        gallery_expanded_count = 0

        logger.info("Processing images (Brand Guard + Mirroring)...")
        for ean, full_prod in otto_products_map.items():
            pm = pm_by_ean.get(ean)
            af_item = af_data.get(ean, {})
            raw_candidates = af_item.get("images", [])

            # Fallback to DB image if Aftercool has none
            if not raw_candidates and pm and pm.image_url:
                raw_candidates = [pm.image_url]

            # Fallback to current OTTO mediaAssets if still empty
            if not raw_candidates:
                current_assets = full_prod.get("mediaAssets", [])
                raw_candidates = [m.get("location") for m in current_assets if m.get("location")]

            if not raw_candidates:
                logger.warning(f"[{ean}] No images found anywhere. Skipping.")
                skipped_errors += 1
                continue

            # Brand Guard: purge any JV logos for XL
            if account_name == "XL":
                clean_images = await filter_brand_images("XL", raw_candidates)
            else:
                clean_images = list(raw_candidates)

            if not clean_images:
                current_assets = full_prod.get("mediaAssets", [])
                clean_images = [m.get("location") for m in current_assets if m.get("location")]

            if not clean_images:
                logger.error(f"[{ean}] All images rejected by Brand Guard and no existing OTTO mediaAssets! Skipping.")
                skipped_errors += 1
                continue

            # Mirroring for XL: First clean image must be mirrored
            if account_name == "XL":
                main_candidate = clean_images[0]
                if "mirrored_XL_" in main_candidate or "/static/mirrored/" in main_candidate:
                    # Already mirrored
                    clean_images[0] = _normalize_image_url(main_candidate)
                else:
                    mirrored_url = await mirror_image_for_xl(main_candidate, ean, "XL")
                    if not mirrored_url:
                        logger.error(f"[{ean}] Mirroring failed for main image. Skipping.")
                        skipped_errors += 1
                        continue
                    clean_images[0] = _normalize_image_url(mirrored_url)

            # Normalize remaining gallery images (unmirrored) and clamp to 10
            clean_images = [_normalize_image_url(u) for u in clean_images[:10] if u]

            # Compare with current OTTO mediaAssets
            current_otto_urls = [m.get("location") for m in full_prod.get("mediaAssets", [])]
            if clean_images == current_otto_urls:
                already_up_to_date += 1
                continue

            if len(clean_images) > len(current_otto_urls):
                gallery_expanded_count += 1

            # Construct clean full payload
            payload = clean_full_product_payload(full_prod, clean_images)
            payloads_to_send.append(payload)

        logger.info(
            f"\nPreparation Summary:\n"
            f"  Total products in DB     : {len(pms)}\n"
            f"  Products retrieved from OTTO : {len(otto_products_map)}\n"
            f"  Needs OTTO update        : {len(payloads_to_send)}\n"
            f"  Galleries expanded (>1)  : {gallery_expanded_count}\n"
            f"  Already up-to-date       : {already_up_to_date}\n"
            f"  Skipped due to errors    : {skipped_errors}\n"
        )

        if not payloads_to_send:
            logger.info("All products are already up-to-date on OTTO!")
            return

        if dry_run:
            logger.info(f"[DRY-RUN] Would send {len(payloads_to_send)} updated products to OTTO API. Exiting.")
            return

        # 6. Dispatch updates to OTTO in batches
        total_batches = (len(payloads_to_send) + batch_size - 1) // batch_size
        successful_batches = 0
        accepted_tasks: List[str] = []

        logger.info(f"Dispatching {len(payloads_to_send)} products in {total_batches} batches of {batch_size}...")

        for i in range(0, len(payloads_to_send), batch_size):
            batch = payloads_to_send[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            logger.info(f"Sending batch {batch_num}/{total_batches} ({len(batch)} products) to OTTO...")

            res = otto_sync_client.publish_products(batch)
            status_code = res.get("status_code")

            if status_code in (200, 201, 202, 207):
                successful_batches += 1
                logger.info(f"  Batch {batch_num} accepted by OTTO (status={status_code}).")
                resp_data = res.get("response") or {}
                if isinstance(resp_data, dict):
                    links = {l.get("rel"): l.get("href") for l in resp_data.get("links", []) if isinstance(l, dict)}
                    if task_href := links.get("self"):
                        accepted_tasks.append(task_href)
                        logger.info(f"  Batch {batch_num} Task Href: {task_href}")
            else:
                logger.error(
                    f"  Batch {batch_num} rejected by OTTO (status={status_code}): "
                    f"{res.get('error') or res.get('response')}"
                )

            # Sleep briefly to avoid bursts
            await asyncio.sleep(1.0)

        # 7. Update database timestamps for updated products
        now_utc = datetime.now(timezone.utc)
        updated_eans = {p["sku"] for p in payloads_to_send}
        for pm in pms:
            if pm.product_ean in updated_eans:
                pm.published_at = now_utc
        db.commit()
        logger.info(f"Updated published_at timestamps for {len(updated_eans)} products in DB.")

        # 8. Invalidate Redis caches so frontend reflects updated state
        if redis_client:
            try:
                purged_count = 0
                for pattern in [
                    f"omt:published_item:{account_name}:*",
                    f"authoritative_eans:{account_name}:{factory_id}",
                    "aftercool_factories_cache",
                ]:
                    keys = redis_client.keys(pattern)
                    if keys:
                        redis_client.delete(*keys)
                        purged_count += len(keys)
                logger.info(f"Invalidated {purged_count} Redis cache keys.")
            except Exception as rx:
                logger.warning(f"Error purging Redis keys: {rx}")

        # 9. Poll asynchronous update tasks on OTTO
        if accepted_tasks:
            logger.info(f"Polling {len(accepted_tasks)} OTTO asynchronous update tasks...")
            for idx, task_href in enumerate(accepted_tasks, start=1):
                logger.info(f"Polling Task {idx}/{len(accepted_tasks)}: {task_href}")
                for poll in range(15):
                    await asyncio.sleep(4.0)
                    t_res = otto_sync_client.get_update_task(task_href)
                    state = t_res.get("state")
                    succeeded = t_res.get("succeeded", 0)
                    failed = t_res.get("failed", 0)
                    unchanged = t_res.get("unchanged", 0)
                    logger.info(
                        f"  [Task {idx} Poll {poll+1}] State: {state} | "
                        f"Succeeded: {succeeded}, Failed: {failed}, Unchanged: {unchanged}"
                    )
                    if state == "done":
                        if failed > 0:
                            f_details = otto_sync_client.get_task_failed_details(task_href)
                            logger.error(f"  Failures in task {idx}: {f_details[:2]}")
                        break

        logger.info(
            f"\n=== Final OTTO Gallery Update Result ===\n"
            f"  Target Factory        : {factory_id} ({account_name})\n"
            f"  Total Published in DB : {len(pms)}\n"
            f"  Updated & Dispatched  : {len(payloads_to_send)}\n"
            f"  Galleries Expanded    : {gallery_expanded_count}\n"
            f"  Batches Accepted      : {successful_batches}/{total_batches}\n"
            f"  Status                : SUCCESS\n"
        )
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Update published products in OTTO API with full image gallery.")
    parser.add_argument("--factory-id", default="504250", help="Factory ID (default: 504250)")
    parser.add_argument("--account", default="XL", help="Account name (default: XL)")
    parser.add_argument("--batch-size", type=int, default=25, help="Batch size for OTTO publish (default: 25)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to process")
    parser.add_argument("--dry-run", action="store_true", help="Perform preparation without publishing to OTTO")
    args = parser.parse_args()

    asyncio.run(
        run_update(
            factory_id=args.factory_id,
            account_name=args.account,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()

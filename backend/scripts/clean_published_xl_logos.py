import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from database import SessionLocal
import models
from services.brand_guard import filter_brand_images
from services.image_processor import mirror_image_for_xl
from services.otto_client import OttoMarketClient
from scripts.fix_xl_logo_cleanup import fetch_aftercool_images

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clean_published_xl_logos")

READ_ONLY_PRODUCT_KEYS = {
    "moin", "order", "links", "releaseDate", "status", "archived",
    "creationDate", "lastModifiedDate", "publishedDate"
}


async def fetch_otto_products_batch(
    client: httpx.AsyncClient,
    eans: List[str],
    token: str,
    sem: asyncio.Semaphore,
) -> Dict[str, Dict[str, Any]]:
    """
    Asynchronously fetches full product variation payloads from OTTO API.
    Uses concurrency limit with exponential backoff on 429 to avoid rate limits.
    Returns a dictionary mapping {sku: full_product_json}.
    """
    headers = {"Authorization": f"Bearer {token}"}
    results: Dict[str, Dict[str, Any]] = {}

    async def fetch_one(ean: str):
        async with sem:
            for attempt in range(4):
                try:
                    resp = await client.get(
                        f"https://api.otto.market/v5/products/{ean}",
                        headers=headers,
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        results[ean] = resp.json()
                        break
                    elif resp.status_code == 429:
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                    else:
                        break
                except Exception as e:
                    logger.debug(f"[{ean}] Error fetching from OTTO: {e}")
                    await asyncio.sleep(0.5)

    await asyncio.gather(*[fetch_one(ean) for ean in eans])
    return results


def clean_full_product_payload(full_prod: Dict[str, Any], clean_images: List[str]) -> Dict[str, Any]:
    """
    Takes full OTTO product variation dictionary from GET /v5/products/{sku},
    removes read-only keys, ensures productLine <= 70 chars,
    and updates mediaAssets with normalized absolute URLs.
    """
    from services.otto_formatter import _normalize_image_url

    payload = full_prod.copy()
    for key in READ_ONLY_PRODUCT_KEYS:
        payload.pop(key, None)

    # Ensure productLine is <= 70 chars (strict OTTO API v5 limit)
    desc = payload.get("productDescription")
    if isinstance(desc, dict) and "productLine" in desc:
        desc["productLine"] = str(desc["productLine"])[:70].strip()

    # Update mediaAssets (max 10) with normalized absolute URLs
    clean_images = [_normalize_image_url(u) for u in clean_images[:10] if u]
    payload["mediaAssets"] = [{"type": "IMAGE", "location": url} for url in clean_images]
    return payload


async def main():
    from services.otto_formatter import _normalize_image_url

    db = SessionLocal()
    try:
        # 1. Load XL credentials
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == "XL")
            .first()
        )
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            logger.error("No XL credentials configured.")
            return

        otto_sync_client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
        token = otto_sync_client.get_access_token()

        # 2. Fetch published XL product mappings from DB for factories 483410 and 483411
        pms = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.factory_id.in_(["483410", "483411"]),
                models.ProductMapping.account_name == "XL",
                models.ProductMapping.status == "Published",
            )
            .all()
        )
        all_eans = [pm.product_ean for pm in pms]
        logger.info(f"Loaded {len(all_eans)} published products from DB for factories 483410 and 483411.")

        # 3. Asynchronously fetch full product JSON from OTTO in parallel with semaphore=3
        sem_otto = asyncio.Semaphore(3)
        async with httpx.AsyncClient() as http_client:
            logger.info("Fetching existing full product objects from OTTO catalog...")
            otto_products_map = await fetch_otto_products_batch(http_client, all_eans, token, sem_otto)

        logger.info(f"Successfully loaded {len(otto_products_map)}/{len(all_eans)} full products from OTTO catalog.")
        if not otto_products_map:
            logger.info("No products found on OTTO to update.")
            return

        # 4. Fetch full image sets from Aftercool for active factories
        active_eans = list(otto_products_map.keys())
        eans_by_factory: Dict[str, List[str]] = {}
        pm_by_ean = {pm.product_ean: pm for pm in pms}

        for ean in active_eans:
            pm = pm_by_ean.get(ean)
            fid = str(pm.factory_id) if pm and pm.factory_id else "unknown"
            eans_by_factory.setdefault(fid, []).append(ean)

        af_images_map: Dict[str, List[str]] = {}
        for fid, eans in eans_by_factory.items():
            logger.info(f"Fetching Aftercool images for factory {fid} ({len(eans)} products)...")
            try:
                imgs = await fetch_aftercool_images(eans, "XL", factory_id=fid if fid != "unknown" else None)
                af_images_map.update(imgs)
            except Exception as e:
                logger.warning(f"Error fetching Aftercool images for factory {fid}: {e}")

        # 5. Process each product: Brand Guard + Mirror + Payload assembly
        payloads_to_send: List[Dict[str, Any]] = []
        skipped_already_clean = 0
        skipped_errors = 0
        logos_removed_count = 0

        logger.info("Processing images with Brand Guard (JV logo removal) and Mirroring...")

        for ean, full_prod in otto_products_map.items():
            # Source images: prioritize Aftercool, fallback to OTTO current mediaAssets
            raw_candidates = af_images_map.get(ean, [])
            if not raw_candidates:
                current_assets = full_prod.get("mediaAssets", [])
                raw_candidates = [m.get("location") for m in current_assets if m.get("location")]

            if not raw_candidates:
                logger.warning(f"[{ean}] No images found anywhere. Skipping.")
                skipped_errors += 1
                continue

            # Step A: Filter JV logos via 3-tier Brand Guard
            clean_images = await filter_brand_images("XL", raw_candidates)
            if not clean_images:
                logger.error(f"[{ean}] All images were rejected by Brand Guard. Skipping to avoid empty gallery.")
                skipped_errors += 1
                continue

            if len(clean_images) < len(raw_candidates):
                logos_removed_count += (len(raw_candidates) - len(clean_images))
                logger.info(f"[{ean}] JV logo removed: {len(raw_candidates)} -> {len(clean_images)} images.")

            # Step B: Mirror the first clean image (XL requirement)
            # CRITICAL: Verify whether the first image is ALREADY mirrored to prevent double-mirroring
            main_candidate = clean_images[0]
            if "mirrored_XL_" in main_candidate or "/static/mirrored/" in main_candidate:
                # Already mirrored! Keep untouched so it stays mirrored
                clean_images[0] = _normalize_image_url(main_candidate)
            else:
                mirrored_url = await mirror_image_for_xl(main_candidate, ean, "XL")
                if not mirrored_url:
                    logger.error(f"[{ean}] Failed to mirror main image. Skipping update to enforce XL mirror rule.")
                    skipped_errors += 1
                    continue
                clean_images[0] = _normalize_image_url(mirrored_url)

            # Normalize all clean images to absolute URLs and limit to 10
            clean_images = [_normalize_image_url(u) for u in clean_images[:10] if u]

            # Step C: Compare with current OTTO mediaAssets to avoid unnecessary updates
            current_otto_urls = [m.get("location") for m in full_prod.get("mediaAssets", [])]
            if clean_images == current_otto_urls:
                skipped_already_clean += 1
                continue

            # Step D: Construct full product variation payload
            full_payload = clean_full_product_payload(full_prod, clean_images)
            payloads_to_send.append(full_payload)

        logger.info(
            f"\nPreparation summary:\n"
            f"  Total catalog products  : {len(otto_products_map)}\n"
            f"  Needs update            : {len(payloads_to_send)}\n"
            f"  Already clean & mirrored: {skipped_already_clean}\n"
            f"  Skipped due to error    : {skipped_errors}\n"
            f"  Logos detected & purged : {logos_removed_count}\n"
        )

        if not payloads_to_send:
            logger.info("All products are already clean and properly mirrored on OTTO!")
            return

        # 6. Send full product updates in batches of 25 to OTTO
        batch_size = 25
        total_batches = (len(payloads_to_send) + batch_size - 1) // batch_size
        successful_updates = 0

        for i in range(0, len(payloads_to_send), batch_size):
            batch = payloads_to_send[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            logger.info(f"Sending batch {batch_num}/{total_batches} ({len(batch)} full products) to OTTO...")

            res = otto_sync_client.publish_products(batch)
            status_code = res.get("status_code")

            if status_code in (200, 201, 202, 207):
                logger.info(f"Batch {batch_num} accepted by OTTO (status={status_code}).")
                resp_data = res.get("response") or {}
                task_href = None
                if isinstance(resp_data, dict):
                    links = {l.get("rel"): l.get("href") for l in resp_data.get("links", []) if isinstance(l, dict)}
                    task_href = links.get("self")

                # Poll OTTO update task if asynchronous (status 202)
                if task_href:
                    logger.info(f"Polling OTTO task {task_href}...")
                    for poll in range(40):
                        time.sleep(4)
                        t_res = otto_sync_client.get_update_task(task_href)
                        state = t_res.get("state")
                        succeeded = t_res.get("succeeded", 0)
                        failed = t_res.get("failed", 0)
                        logger.info(f"  Poll {poll+1}: state={state}, succeeded={succeeded}, failed={failed}")
                        if state == "done":
                            if failed > 0:
                                f_details = otto_sync_client.get_task_failed_details(task_href)
                                logger.error(f"  OTTO reported {failed} failures in batch {batch_num}: {f_details[:2]}")
                            successful_updates += succeeded
                            break
                else:
                    successful_updates += len(batch)
            else:
                logger.error(f"Batch {batch_num} rejected by OTTO (status={status_code}): {res.get('error') or res.get('response')}")

        logger.info(
            f"\n=== Final Cleanup Summary ===\n"
            f"  Successfully updated in OTTO : {successful_updates}\n"
            f"  Already clean & mirrored     : {skipped_already_clean}\n"
            f"  Errors / skipped             : {skipped_errors}\n"
        )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

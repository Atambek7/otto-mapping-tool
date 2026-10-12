#!/usr/bin/env python3
"""
fix_xl_logo_cleanup.py
======================
Finds all published XL products that still contain a JVMÖBEL logo in their
Aftercool gallery (including additional images) and removes it by re-publishing
the corrected mediaAssets to OTTO.

Usage (inside omt_backend container):
  python3 scripts/fix_xl_logo_cleanup.py [--dry-run] [--limit N] [--batch-size N]
"""

import asyncio
import logging
import argparse

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("logo_cleanup")


async def fetch_aftercool_images(eans: list, account: str, factory_id: str = None) -> dict:
    """
    Fetch full image lists from Aftercool for a list of EANs.
    Returns dict: {ean: [img_url, ...]}
    """
    from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL
    from services.executors.otto_api import extract_images_from_aftercool_row

    session_cookie = await get_aftercool_session()
    result = {}

    async with httpx.AsyncClient(
        cookies={"session": session_cookie},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        # Try to batch fetch by factory if provided
        if factory_id:
            offset = 0
            limit = 500
            while True:
                try:
                    resp = await client.get(
                        f"{AFTERCOOL_BASE_URL}/api/products",
                        params={
                            "factory_id": factory_id,
                            "account": account,
                            "dataset": "lister",
                            "include_row": 1,
                            "limit": limit,
                            "offset": offset,
                        }
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    items_list = data.get("items", data) if isinstance(data, dict) else data
                    if not isinstance(items_list, list) or not items_list:
                        break
                    for p in items_list:
                        row = p.get("row", {})
                        ean = str(p.get("ean") or row.get("EAN") or "").strip()
                        if ean and ean in eans:
                            imgs = extract_images_from_aftercool_row(row, p, account=None)  # No filtering yet
                            if imgs:
                                result[ean] = imgs
                    offset += len(items_list)
                    total = data.get("total") if isinstance(data, dict) else None
                    if total is not None and offset >= total:
                        break
                    if len(items_list) < limit:
                        break
                except Exception as ex:
                    logger.warning(f"Error in batch fetch: {ex}")
                    break

        # Fetch remaining individually
        missing = [e for e in eans if e not in result]
        sem = asyncio.Semaphore(10)
        async def fetch_one(ean: str):
            async with sem:
                try:
                    resp = await client.get(
                        f"{AFTERCOOL_BASE_URL}/api/products",
                        params={"q": ean, "account": account, "dataset": "lister", "include_row": 1, "limit": 2}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        items_list = data.get("items", data) if isinstance(data, dict) else data
                        for p in (items_list or []):
                            row = p.get("row", {})
                            p_ean = str(p.get("ean") or row.get("EAN") or "").strip()
                            if p_ean == ean:
                                imgs = extract_images_from_aftercool_row(row, p, account=None)
                                if imgs:
                                    result[ean] = imgs
                                break
                except Exception as ex:
                    logger.debug(f"Error fetching {ean}: {ex}")

        if missing:
            await asyncio.gather(*[fetch_one(e) for e in missing])

    return result


async def main(dry_run: bool = False, limit: int = 0, batch_size: int = 100):
    from database import SessionLocal
    import models
    from services.brand_guard import filter_brand_images
    from services.otto_client import OttoMarketClient

    db = SessionLocal()
    try:
        # 1. Get all published XL products
        q = db.query(models.ProductMapping).filter(
            models.ProductMapping.account_name == "XL",
            models.ProductMapping.status == "Published",
        )
        if limit > 0:
            q = q.limit(limit)
        published = q.all()
        logger.info(f"Checking {len(published)} published XL products for JV logo...")

        # 2. Load XL credentials from AccountSettings
        settings = db.query(models.AccountSettings).filter(
            models.AccountSettings.account_name == "XL"
        ).first()
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            logger.error("OTTO XL credentials not configured in AccountSettings. Aborting.")
            return

        otto = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)

        fixed_count = 0
        skipped_count = 0
        error_count = 0
        logo_eans = []

        # 3. Process in batches
        all_eans = [pm.product_ean for pm in published]
        # Group by factory_id for efficient batch fetching
        factory_groups: dict = {}
        for pm in published:
            fid = str(pm.factory_id) if pm.factory_id else "unknown"
            factory_groups.setdefault(fid, []).append(pm)

        logger.info(f"Found {len(factory_groups)} factories to process.")

        for fid, factory_pms in factory_groups.items():
            eans_in_factory = [pm.product_ean for pm in factory_pms]
            logger.info(f"Fetching images for factory {fid} ({len(eans_in_factory)} products)...")

            try:
                image_map = await fetch_aftercool_images(eans_in_factory, "XL", factory_id=fid if fid != "unknown" else None)
            except Exception as ex:
                logger.warning(f"Could not fetch images for factory {fid}: {ex}")
                image_map = {}

            for pm in factory_pms:
                ean = pm.product_ean
                images = image_map.get(ean, [])

                if not images:
                    skipped_count += 1
                    continue

                # Filter logos using async brand guard (Tier 1+2+3)
                clean_images = await filter_brand_images("XL", images)

                if len(clean_images) == len(images):
                    skipped_count += 1
                    continue  # No logos

                removed = len(images) - len(clean_images)
                logo_eans.append(ean)
                logger.info(
                    f"[{ean}] Logo found! {len(images)} images -> {len(clean_images)} "
                    f"(removed {removed} logo(s))"
                )
                for img in images:
                    if img not in clean_images:
                        logger.info(f"  Removed: {img}")

                if not clean_images:
                    logger.warning(f"[{ean}] No images remain after logo removal. Skipping.")
                    skipped_count += 1
                    continue

                if dry_run:
                    logger.info(f"  [DRY RUN] Would re-publish {ean} with {len(clean_images)} clean images.")
                    fixed_count += 1
                    continue

                # Build mediaAssets (max 10)
                clean_images = clean_images[:10]
                media_assets = [{"type": "IMAGE", "location": url} for url in clean_images]
                payload = {"sku": ean, "mediaAssets": media_assets}

                try:
                    result = otto.publish_products([payload])
                    sc = result.get("status_code")
                    if sc in (200, 202, 207):
                        logger.info(f"[{ean}] Re-published OK (status={sc}, {len(clean_images)} images).")
                        fixed_count += 1
                    else:
                        logger.error(f"[{ean}] FAILED (status={sc}): {str(result)[:200]}")
                        error_count += 1
                except Exception as ex:
                    logger.error(f"[{ean}] Exception during re-publish: {ex}")
                    error_count += 1

        logger.info(
            f"\n=== Logo Cleanup Summary ===\n"
            f"  Products with logos : {len(logo_eans)}\n"
            f"  Fixed (re-published): {fixed_count}\n"
            f"  Skipped (no logo)   : {skipped_count}\n"
            f"  Errors              : {error_count}\n"
            + (f"  EANs with logos: {logo_eans}" if logo_eans else "")
        )

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulate without re-publishing")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of products to check")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for Aftercool requests")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size))

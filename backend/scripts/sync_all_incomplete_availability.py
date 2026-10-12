#!/usr/bin/env python3
"""
sync_all_incomplete_availability.py
==================================
Synchronizes stock and shipping profiles for all published items that previously failed
availability sync (e.g. due to Otto asynchronous product indexing delays).
Clears error_message and updates ProductMapping status to 'Published'.
"""

import time
import logging
from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_availability import apply_otto_availability

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_all_incomplete_availability")

def sync_incomplete_items():
    db = SessionLocal()
    try:
        items = db.query(models.PublishQueueItem).filter(
            models.PublishQueueItem.error_message.ilike("%Availability sync incomplete%")
        ).all()

        total = len(items)
        logger.info(f"Found {total} items with incomplete availability.")
        if total == 0:
            return

        # Group by account
        by_account = {}
        for it in items:
            acc_name = it.account_name.value if hasattr(it.account_name, "value") else str(it.account_name)
            by_account.setdefault(acc_name, []).append(it)

        total_synced = 0
        total_failed = 0

        for acc_name, acc_items in by_account.items():
            logger.info(f"\n--- Processing {len(acc_items)} items for account {acc_name} ---")
            settings = db.query(models.AccountSettings).filter(
                models.AccountSettings.account_name == acc_name
            ).first()
            if not settings or not settings.otto_client_id or not settings.otto_client_secret:
                logger.error(f"Missing Otto API credentials for account {acc_name}!")
                continue

            client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)

            # Process in batches of 25
            batch_size = 25
            for i in range(0, len(acc_items), batch_size):
                batch = acc_items[i : i + batch_size]
                b_num = (i // batch_size) + 1
                total_b = (len(acc_items) + batch_size - 1) // batch_size
                logger.info(f"Processing batch {b_num}/{total_b} ({len(batch)} items)...")

                plan = None
                if batch[0].plan_id:
                    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == batch[0].plan_id).first()

                res = apply_otto_availability(
                    client=client,
                    items=batch,
                    account_name=acc_name,
                    db=db,
                    plan=plan
                )

                qty_failed = res.get("quantity_failed_skus", [])
                deliv_failed = res.get("delivery_failed_skus", [])
                unknown = res.get("unknown_skus", [])

                failed_skus = set(qty_failed + deliv_failed + unknown)
                batch_synced_eans = []

                for it in batch:
                    if it.product_ean not in failed_skus:
                        it.error_message = None
                        it.status = models.QueueItemStatus.PUBLISHED
                        batch_synced_eans.append(it.product_ean)
                        total_synced += 1
                    else:
                        logger.warning(f"Item {it.product_ean} failed availability: qty_fail={it.product_ean in qty_failed}, deliv_fail={it.product_ean in deliv_failed}")
                        total_failed += 1

                # Update ProductMapping for succeeded items
                if batch_synced_eans:
                    db.query(models.ProductMapping).filter(
                        models.ProductMapping.product_ean.in_(batch_synced_eans),
                        models.ProductMapping.account_name == acc_name,
                    ).update({
                        "status": "Published",
                        "last_error": None,
                    }, synchronize_session=False)

                db.commit()
                time.sleep(1.0)  # Gentle rate limiting between batches

        logger.info(f"\n=== Completed Availability Recovery ===")
        logger.info(f"Total synced successfully: {total_synced}")
        logger.info(f"Total failed: {total_failed}")

    finally:
        db.close()

if __name__ == "__main__":
    sync_incomplete_items()

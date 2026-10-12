#!/usr/bin/env python3
"""
republish_plan17_day1.py
========================
Republishes the 119 failed Day 1 items for factory 483411 (Plan #17) to OTTO Market API.
Enforces:
1. Zero JV logos across all mediaAssets.
2. Mirroring of the 1st photo for XL.
3. Batching in chunks of 25 with task polling until OTTO finishes validation.
4. Immediate availability sync (Stock = 20, Shipping Profile = 37 days).
"""

import sys
import time
import logging
from datetime import datetime, timezone

from database import SessionLocal
import models
from services.executors.otto_api import OttoApiExecutor
from services.otto_client import OttoMarketClient
from tasks import dispatch_otto_availability_batches

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("republish_plan17_day1")


def main():
    db = SessionLocal()
    try:
        plan_id = 17
        plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
        if not plan:
            logger.error(f"Plan {plan_id} not found!")
            return

        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == "XL")
            .first()
        )
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            logger.error("XL account settings not found or missing OTTO credentials!")
            return

        client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
        executor = OttoApiExecutor()

        # 1. Fetch Day 1 items
        day1_items = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.plan_id == plan_id,
                models.PublishQueueItem.scheduled_day == 1,
            )
            .all()
        )

        logger.info(f"Loaded {len(day1_items)} items for Plan {plan_id} Day 1.")

        # Handle any item already created in OTTO (e.g. test item 4071489344431)
        now_utc = datetime.now(timezone.utc)
        items_to_publish = []
        for it in day1_items:
            if it.status == models.QueueItemStatus.PUBLISHED:
                continue
            if it.status == models.QueueItemStatus.PROCESSING:
                # Check if it exists in OTTO
                token = client.get_access_token()
                import requests
                r = requests.get(f"https://api.otto.market/v5/products/{it.product_ean}", headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    logger.info(f"[{it.product_ean}] Already active on OTTO, marking PUBLISHED.")
                    it.status = models.QueueItemStatus.PUBLISHED
                    it.published_at = now_utc
                    it.error_message = None
                    pm = db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean).first()
                    if pm:
                        pm.status = "Published"
                        pm.published_at = now_utc
                    continue

            # Item needs to be published
            it.status = models.QueueItemStatus.PENDING
            it.error_message = None
            it.attempts = 0
            items_to_publish.append(it)

        db.commit()
        logger.info(f"Identified {len(items_to_publish)} items to publish to OTTO.")

        if not items_to_publish:
            logger.info("All items are already published!")
            return

        # 2. Publish in batches of 25
        batch_size = 25
        total_batches = (len(items_to_publish) + batch_size - 1) // batch_size
        total_succeeded = 0
        total_failed = 0
        published_ids = []

        for b_idx in range(0, len(items_to_publish), batch_size):
            batch = items_to_publish[b_idx : b_idx + batch_size]
            b_num = (b_idx // batch_size) + 1
            logger.info(f"\n--- Publishing Batch {b_num}/{total_batches} ({len(batch)} items) ---")

            res = executor.publish_batch(plan, batch, db)
            logger.info(f"Batch dispatch result: {res}")

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
                        if failed > 0:
                            f_details = client.get_task_failed_details(task_href)
                            logger.error(f"  Failures in batch: {f_details[:3]}")
                            # Mark failed items
                            failed_skus = set()
                            for f_item in f_details:
                                v_uri = f_item.get("variation", "")
                                sku = v_uri.split("/")[-1] if "/" in v_uri else ""
                                if sku:
                                    failed_skus.add(sku)
                            for it in batch:
                                if it.product_ean in failed_skus:
                                    it.status = models.QueueItemStatus.FAILED
                                else:
                                    it.status = models.QueueItemStatus.PUBLISHED
                                    it.published_at = now_utc
                                    it.error_message = None
                                    published_ids.append(it.id)
                        else:
                            for it in batch:
                                it.status = models.QueueItemStatus.PUBLISHED
                                it.published_at = now_utc
                                it.error_message = None
                                published_ids.append(it.id)

                        # Update ProductMapping for published items
                        published_eans = [it.product_ean for it in batch if it.status == models.QueueItemStatus.PUBLISHED]
                        if published_eans:
                            db.query(models.ProductMapping).filter(
                                models.ProductMapping.product_ean.in_(published_eans),
                                models.ProductMapping.account_name == "XL",
                            ).update({
                                "status": "Published",
                                "published_at": now_utc,
                            }, synchronize_session=False)

                        db.commit()
                        total_succeeded += succeeded
                        total_failed += failed
                        break
            else:
                # Synchronous success or failure
                for it in batch:
                    if it.status == models.QueueItemStatus.PUBLISHED:
                        published_ids.append(it.id)
                        total_succeeded += 1
                    elif it.status == models.QueueItemStatus.FAILED:
                        total_failed += 1

        db.commit()

        logger.info(
            f"\n=== Publishing Summary for Plan {plan_id} Day 1 ===\n"
            f"  Succeeded : {total_succeeded}\n"
            f"  Failed    : {total_failed}\n"
        )

        # 3. Dispatch Availability sync for all newly published items
        all_day1_published = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.plan_id == plan_id,
                models.PublishQueueItem.scheduled_day == 1,
                models.PublishQueueItem.status == models.QueueItemStatus.PUBLISHED,
            )
            .all()
        )
        avail_ids = [it.id for it in all_day1_published]
        logger.info(f"Dispatching Availability sync for {len(avail_ids)} Day 1 published items...")
        try:
            dispatch_otto_availability_batches(
                item_ids=avail_ids,
                account_name="XL",
                plan_id=plan_id,
                base_countdown=10,
                jitter_step=3,
            )
            logger.info("Availability sync tasks successfully dispatched.")
        except Exception as e:
            logger.warning(f"Error dispatching availability sync: {e}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
import requests

from database import SessionLocal
import models
from services.executors.otto_api import OttoApiExecutor
from services.otto_client import OttoMarketClient
from tasks import dispatch_otto_availability_batches

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("republish_day1_plan15")


def main():
    db = SessionLocal()
    try:
        # 1. Load Plan 15
        plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == 15).first()
        if not plan:
            logger.error("Plan 15 not found.")
            return

        logger.info(f"Plan 15: Factory {plan.factory_id} ({plan.factory_name}), Account={plan.account_name}, Status={plan.status}, Day={plan.current_day}")

        # 2. Get Day 1 items that need republishing
        # These are items on scheduled_day == 1 that had availability sync error or 404 on OTTO
        settings = db.query(models.AccountSettings).filter(models.AccountSettings.account_name == "XL").first()
        client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
        token = client.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        day1_items = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.plan_id == 15,
                models.PublishQueueItem.scheduled_day == 1,
            )
            .all()
        )
        logger.info(f"Total Day 1 items in Plan 15: {len(day1_items)}")

        items_to_publish = []
        for it in day1_items:
            r = requests.get(f"https://api.otto.market/v5/products/{it.product_ean}", headers=headers)
            if r.status_code != 200:
                items_to_publish.append(it)

        logger.info(f"Found {len(items_to_publish)} Day 1 items NOT yet on OTTO.")
        if not items_to_publish:
            logger.info("All Day 1 items already exist on OTTO!")
            return

        # Double-check that days 3-7 are untouched
        later_days = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.plan_id == 15,
                models.PublishQueueItem.scheduled_day > 2,
            )
            .all()
        )
        logger.info(f"Safety check: Days 3-7 have {len(later_days)} items (all will remain untouched).")
        for it in later_days:
            assert it.status == models.QueueItemStatus.PENDING, f"Day {it.scheduled_day} item {it.id} is not PENDING!"

        # 3. Reset status of items_to_publish to PENDING
        for it in items_to_publish:
            it.status = models.QueueItemStatus.PENDING
            it.attempts = 0
            it.error_message = None
            it.published_at = None
        db.commit()
        logger.info(f"Reset {len(items_to_publish)} items to PENDING.")

        # 4. Publish batch through OttoApiExecutor
        executor = OttoApiExecutor()
        logger.info(f"Publishing {len(items_to_publish)} items via OttoApiExecutor...")
        res = executor.publish_batch(plan, items_to_publish, db)
        logger.info(f"publish_batch result: {res}")

        # Check if tasks are in PROCESSING with otto_task_href
        task_href = None
        for it in items_to_publish:
            if it.otto_task_href:
                task_href = it.otto_task_href
                break

        if not task_href:
            logger.error(f"No otto_task_href returned! Check items: {[it.error_message for it in items_to_publish[:5]]}")
            return

        logger.info(f"OTTO update task accepted: {task_href}")

        # 5. Poll OTTO task until done
        for poll in range(30):
            time.sleep(5)
            t_res = client.get_update_task(task_href)
            state = t_res.get("state")
            total = t_res.get("total")
            succeeded = t_res.get("succeeded")
            failed = t_res.get("failed")
            unchanged = t_res.get("unchanged")
            logger.info(f"Poll #{poll+1}: state={state}, total={total}, succeeded={succeeded}, failed={failed}, unchanged={unchanged}")

            if state == "done":
                if failed and failed > 0:
                    f_details = client.get_task_failed_details(task_href)
                    logger.error(f"OTTO reported {failed} failures: {f_details[:3]}")
                else:
                    logger.info("OTTO task finished with 0 failures!")
                break

        # 6. Check OTTO status for all items
        confirmed_published = []
        for it in items_to_publish:
            r = requests.get(f"https://api.otto.market/v5/products/{it.product_ean}", headers=headers)
            if r.status_code == 200:
                confirmed_published.append(it)
                it.status = models.QueueItemStatus.PUBLISHED
                it.published_at = datetime.now(timezone.utc)
                it.error_message = None
                m = db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean == it.product_ean,
                    models.ProductMapping.account_name == "XL"
                ).first()
                if m:
                    m.status = "Published"
                    m.published_at = it.published_at

        db.commit()
        logger.info(f"Confirmed {len(confirmed_published)}/{len(items_to_publish)} products created on OTTO!")

        # 7. Dispatch Availability sync for all confirmed items
        if confirmed_published:
            item_ids = [it.id for it in confirmed_published]
            logger.info(f"Dispatching Availability sync for {len(item_ids)} items...")
            n_tasks = dispatch_otto_availability_batches(
                item_ids=item_ids,
                account_name="XL",
                plan_id=plan.id,
                base_countdown=10,
                force=True
            )
            logger.info(f"Dispatched {n_tasks} availability tasks.")

        logger.info("Finished republish_day1_plan15 successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

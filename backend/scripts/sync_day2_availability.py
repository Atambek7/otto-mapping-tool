import os
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_availability import apply_otto_availability
from sqlalchemy.sql import func

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def sync_day2_items():
    db = SessionLocal()
    try:
        plan_id = 19
        account_name = "JV"

        plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
        if not plan:
            logging.error(f"Plan {plan_id} not found!")
            return False

        failed_items = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.plan_id == plan_id,
                models.PublishQueueItem.status == models.QueueItemStatus.FAILED,
            )
            .order_by(models.PublishQueueItem.id)
            .all()
        )

        logging.info(f"Found {len(failed_items)} failed items in Plan #{plan_id} ({account_name}).")
        if not failed_items:
            logging.info("No failed items to sync.")
            return True

        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == account_name)
            .first()
        )
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            logging.error(f"Missing OTTO API credentials for account {account_name}")
            return False

        client = OttoMarketClient(
            client_id=settings.otto_client_id,
            client_secret=settings.otto_client_secret,
        )

        item_eans = [it.product_ean for it in failed_items]
        logging.info(f"Starting availability sync for {len(item_eans)} items via OTTO Availability API...")

        res = apply_otto_availability(
            client=client,
            items=failed_items,
            account_name=account_name,
            db=db,
            plan=plan,
        )

        logging.info(f"Availability sync result: {res.get('status')}")
        logging.info(f"Quantity failed SKUs: {len(res.get('quantity_failed_skus', []))}")
        logging.info(f"Delivery failed SKUs: {len(res.get('delivery_failed_skus', []))}")
        logging.info(f"Unknown SKUs: {len(res.get('unknown_skus', []))}")
        if res.get("error"):
            logging.warning(f"Reported error: {res.get('error')}")

        qty_failed = set(res.get("quantity_failed_skus", []))
        deliv_failed = set(res.get("delivery_failed_skus", []))
        failed_set = qty_failed.union(deliv_failed)

        succeeded_count = 0
        failed_count = 0

        acc_enum = models.AccountType(account_name) if hasattr(models.AccountType, account_name) else account_name

        for it in failed_items:
            if it.product_ean not in failed_set:
                it.status = models.QueueItemStatus.PUBLISHED
                it.error_message = None
                succeeded_count += 1

                m = (
                    db.query(models.ProductMapping)
                    .filter(
                        models.ProductMapping.product_ean == it.product_ean,
                        models.ProductMapping.account_name == acc_enum,
                    )
                    .first()
                )
                if m:
                    m.status = "Published"
                    m.last_error = None
                    if not m.published_at:
                        m.published_at = func.now()
            else:
                failed_count += 1
                it.error_message = f"Availability sync incomplete: {res.get('error')}"[:500]
                m = (
                    db.query(models.ProductMapping)
                    .filter(
                        models.ProductMapping.product_ean == it.product_ean,
                        models.ProductMapping.account_name == acc_enum,
                    )
                    .first()
                )
                if m:
                    m.status = "Error"
                    m.last_error = it.error_message

        db.commit()

        logging.info(f"=== SUMMARY ===")
        logging.info(f"Successfully synced and marked Published: {succeeded_count}")
        logging.info(f"Still failed: {failed_count}")

        # Check total plan breakdown
        all_items = db.query(models.PublishQueueItem).filter(models.PublishQueueItem.plan_id == plan_id).all()
        breakdown = {}
        for it in all_items:
            breakdown[it.status.value if hasattr(it.status, "value") else str(it.status)] = (
                breakdown.get(it.status.value if hasattr(it.status, "value") else str(it.status), 0) + 1
            )
        logging.info(f"Plan #{plan_id} total status breakdown: {breakdown}")

        return failed_count == 0
    finally:
        db.close()


if __name__ == "__main__":
    success = sync_day2_items()
    sys.exit(0 if success else 1)

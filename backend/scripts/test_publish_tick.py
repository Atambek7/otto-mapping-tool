import os
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone, timedelta
from database import SessionLocal
import models
from tasks import process_scheduled_publish_tick
from services.distribution import distribute_products_round_robin

def test_tick():
    db = SessionLocal()
    try:
        factory_id = "503718"
        account = models.AccountType.JV

        # 1. Fetch 10 mappings
        mappings = db.query(models.ProductMapping).filter(
            models.ProductMapping.factory_id == factory_id,
            models.ProductMapping.account_name == account
        ).limit(10).all()
        assert len(mappings) > 0, "No mappings found for test"

        items = [{"product_ean": m.product_ean, "category_id": m.category_id or "c1"} for m in mappings]
        distribution = distribute_products_round_robin(items, days=5, daily_time="08:00")

        # 2. Create test plan
        plan = models.PublishPlan(
            factory_id=factory_id,
            account_name=account,
            total_products=len(items),
            total_days=5,
            current_day=1,
            daily_time="08:00",
            status=models.PlanStatus.ACTIVE,
            executor_type=models.ExecutorType.BATCH_FILE
        )
        db.add(plan)
        db.flush()

        # Day 1 scheduled date set in the past so tick picks it up
        past_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
        future_dt = datetime.now(timezone.utc) + timedelta(days=1)

        queue_items = []
        for day, d_items in distribution["schedule"].items():
            dt = past_dt if day == 1 else future_dt
            for it in d_items:
                q = models.PublishQueueItem(
                    plan_id=plan.id,
                    product_ean=it["product_ean"],
                    account_name=account,
                    category_id=it["category_id"],
                    scheduled_day=day,
                    scheduled_date=dt,
                    status=models.QueueItemStatus.PENDING
                )
                queue_items.append(q)

        db.add_all(queue_items)
        db.commit()
        print(f"Created test plan {plan.id} with {len(queue_items)} items.")

        day1_count = len(distribution["schedule"][1])

        # 3. Run Celery task tick
        res = process_scheduled_publish_tick()
        print(f"Tick result: {res}")
        assert res["status"] == "processed"
        assert res["total_items"] == day1_count

        # 4. Verify DB state after tick
        db.refresh(plan)
        published_items = db.query(models.PublishQueueItem).filter(
            models.PublishQueueItem.plan_id == plan.id,
            models.PublishQueueItem.status == models.QueueItemStatus.PUBLISHED
        ).all()
        pending_items = db.query(models.PublishQueueItem).filter(
            models.PublishQueueItem.plan_id == plan.id,
            models.PublishQueueItem.status == models.QueueItemStatus.PENDING
        ).all()

        print(f"Published items: {len(published_items)} (expected {day1_count})")
        print(f"Pending items remaining: {len(pending_items)} (expected {len(items) - day1_count})")
        assert len(published_items) == day1_count
        assert len(pending_items) == len(items) - day1_count

        # Verify ProductMapping status updated to Published
        for p_item in published_items:
            pm = db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean == p_item.product_ean,
                models.ProductMapping.account_name == account
            ).first()
            assert pm.status == "Published", f"ProductMapping status not updated for EAN {pm.product_ean}"

        print("Verification passed! Product mappings updated to 'Published'.")

        # 5. Cleanup test plan
        db.delete(plan)
        db.commit()
        print("Cleaned up test plan.")
        print("\n=== CELERY TICK DISPATCH TEST PASSED! ===")

    finally:
        db.close()

if __name__ == "__main__":
    test_tick()

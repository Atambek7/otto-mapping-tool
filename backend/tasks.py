import os
import sys

app_dir = os.path.dirname(os.path.abspath(__file__))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

from celery import shared_task
from sqlalchemy import func
from sqlalchemy.orm import Session
from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_formatter import format_otto_product
# fetch_product_info_for_export removed
@shared_task(bind=True, acks_late=True, max_retries=0)
def process_scheduled_publish_tick(self, target_plan_id: int = None):
    """
    Periodic tick executed by Celery Beat (every 1 minute).
    Finds active PublishPlans with pending items scheduled <= now (UTC).
    If target_plan_id is specified, restricts execution to only that plan.
    Dispatches each batch to the configured PublishExecutor.

    acks_late=True: task is only acknowledged after successful completion.
    Safe distributed lock: Uses a UUID token and Lua release script with 180s TTL.
    """
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict
    from services.executors import OttoApiExecutor, BatchExportExecutor
    import redis
    import uuid
    import os

    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url)
    lock_key = f"drip_feed_tick_lock_{target_plan_id}" if target_plan_id else "drip_feed_tick_lock"
    lock_val = str(uuid.uuid4())
    acquired = r.set(lock_key, lock_val, nx=True, ex=180)  # 3 minutes TTL
    if not acquired:
        return {"status": "skipped", "message": "Previous tick is still running. Skipping to prevent double-publish."}

    LUA_RELEASE_LOCK = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    db: Session = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)

        # 1. Fetch pending items for active plans
        query = (
            db.query(models.PublishQueueItem)
            .join(models.PublishPlan)
            .filter(
                models.PublishPlan.status == models.PlanStatus.ACTIVE,
                models.PublishQueueItem.status == models.QueueItemStatus.PENDING,
            )
        )
        if target_plan_id:
            # Manual trigger: publish pending items for current day without waiting for the exact hour
            query = query.filter(
                models.PublishPlan.id == target_plan_id,
                models.PublishQueueItem.scheduled_day <= models.PublishPlan.current_day
            )
        else:
            # Automatic periodic tick: publish when scheduled timestamp has arrived
            query = query.filter(models.PublishQueueItem.scheduled_date <= now_utc)

        pending_items = query.order_by(models.PublishQueueItem.scheduled_date.asc()).all()

        if not pending_items:
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

            # Anti-collision circuit breaker
            opp_account = models.AccountType.XL if plan.account_name == models.AccountType.JV else models.AccountType.JV
            ready_to_publish_items = []
            for it in plan_items:
                # 1. Check if opposite account has already published this EAN within stagger window
                opp_mapping = db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean == it.product_ean,
                    models.ProductMapping.account_name == opp_account,
                    models.ProductMapping.published_at.isnot(None)
                ).first()
                if opp_mapping and opp_mapping.published_at:
                    min_allowed_dt = opp_mapping.published_at + timedelta(days=2)
                    if now_utc < min_allowed_dt:
                        it.scheduled_date = min_allowed_dt
                        continue

                # 2. Check if opposite account has an item currently in PROCESSING
                opp_processing = db.query(models.PublishQueueItem).filter(
                    models.PublishQueueItem.product_ean == it.product_ean,
                    models.PublishQueueItem.account_name == opp_account,
                    models.PublishQueueItem.status == models.QueueItemStatus.PROCESSING
                ).first()
                if opp_processing:
                    it.scheduled_date = now_utc + timedelta(days=2)
                    continue

                ready_to_publish_items.append(it)

            if not ready_to_publish_items:
                db.commit()
                continue

            executor = executors.get(plan.executor_type, executors[models.ExecutorType.OTTO_API])
            res = executor.publish_batch(plan, ready_to_publish_items, db)
            results[plan_id] = res

            # Mark mapped products as Published if synchronously published
            published_eans = [it.product_ean for it in ready_to_publish_items if it.status == models.QueueItemStatus.PUBLISHED]
            if published_eans:
                db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean.in_(published_eans),
                    models.ProductMapping.account_name == plan.account_name
                ).update({
                    "status": "Published",
                    "published_at": now_utc
                }, synchronize_session=False)

            # Update current_day to max scheduled_day of items reached
            latest_day = max((it.scheduled_day for it in plan_items), default=plan.current_day)
            if latest_day > plan.current_day:
                plan.current_day = latest_day

            # Check if all items in plan are finished (no pending AND no processing left)
            unfinished_count = (
                db.query(models.PublishQueueItem)
                .filter(
                    models.PublishQueueItem.plan_id == plan_id,
                    models.PublishQueueItem.status.in_([
                        models.QueueItemStatus.PENDING,
                        models.QueueItemStatus.PROCESSING
                    ])
                )
                .count()
            )

            if unfinished_count == 0:
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
        # Failsafe: sweep any ACTIVE plans that have no PENDING or PROCESSING items left
        # and mark them as COMPLETED. This catches edge cases where a plan was left active.
        try:
            active_plans = db.query(models.PublishPlan).filter(models.PublishPlan.status == models.PlanStatus.ACTIVE).all()
            for p in active_plans:
                unfinished = db.query(models.PublishQueueItem).filter(
                    models.PublishQueueItem.plan_id == p.id,
                    models.PublishQueueItem.status.in_([models.QueueItemStatus.PENDING, models.QueueItemStatus.PROCESSING])
                ).count()
                if unfinished == 0:
                    p.status = models.PlanStatus.COMPLETED
            db.commit()
        except Exception:
            db.rollback()

        db.close()
        try:
            r.eval(LUA_RELEASE_LOCK, 1, lock_key, lock_val)
        except Exception:
            pass


OTTO_AVAILABILITY_BATCH_SIZE = int(os.environ.get("OTTO_AVAILABILITY_BATCH_SIZE", "100"))


@shared_task(bind=True, max_retries=10, acks_late=True, name="tasks.apply_otto_availability_task")
def apply_otto_availability_task(
    self,
    item_ids: list[int],
    account_name: str,
    plan_id: int | None = None,
    pending_quantity_skus: list[str] | None = None,
    pending_delivery_skus: list[str] | None = None,
    aftercool_product_map: dict | None = None,
):
    """
    Celery task to reliably synchronize Stock (=20) and Shipping Profile in OTTO
    after catalog publication.

    Features:
    - Runs in chunks/batches (up to 100 SKUs).
    - Checks structured responses for Stock and Delivery.
    - Isolates only unindexed SKUs for retry.
    - If Stock succeeded and Delivery failed with unknown SKU, retry ONLY retries Delivery.
    - Max 6 retries with 60s countdown (7 attempts total).
    - Updates PublishQueueItem.error_message if retries exhausted.
    """
    import logging
    from database import SessionLocal
    import models
    from services.otto_client import OttoMarketClient
    from services.otto_availability import apply_otto_availability
    import redis

    db = SessionLocal()
    try:
        items = db.query(models.PublishQueueItem).filter(models.PublishQueueItem.id.in_(item_ids)).all()
        if not items:
            logging.info(f"[apply_otto_availability_task] No items found for IDs {item_ids}")
            return {"status": "empty", "dispatched": 0}

        acc_str = str(account_name).upper()
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == acc_str)
            .first()
        )
        if not settings or not settings.otto_client_id or not settings.otto_client_secret:
            err = f"Missing OTTO API credentials for account {acc_str}"
            logging.error(f"[apply_otto_availability_task] {err}")
            for it in items:
                it.error_message = f"Availability sync error: {err}"
            db.commit()
            return {"status": "credentials_error", "error": err}

        client = OttoMarketClient(
            client_id=settings.otto_client_id,
            client_secret=settings.otto_client_secret,
        )

        plan = None
        if plan_id:
            plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
        elif items and items[0].plan_id:
            plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == items[0].plan_id).first()

        res = apply_otto_availability(
            client=client,
            items=items,
            account_name=acc_str,
            db=db,
            plan=plan,
            aftercool_product_map=aftercool_product_map,
            target_quantity_skus=pending_quantity_skus,
            target_delivery_skus=pending_delivery_skus,
        )

        qty_failed = res.get("quantity_failed_skus", [])
        deliv_failed = res.get("delivery_failed_skus", [])
        unknown_skus = res.get("unknown_skus", [])

        if not qty_failed and not deliv_failed:
            logging.info(
                f"[apply_otto_availability_task] Successfully synced availability for "
                f"{len(items)} items ({acc_str})."
            )
            # Clear Redis deduplication locks
            redis_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
            try:
                r = redis.from_url(redis_url)
                for it_id in item_ids:
                    r.delete(f"otto_avail_sched:{acc_str}:{it_id}")
            except Exception:
                pass
            return {"status": "success", "synced_items": len(items)}

        # Check for transient API status codes (429 or 5xx) in quantities or delivery
        is_transient_error = False
        qty_res_data = res.get("quantities_res") or {}
        deliv_res_data = res.get("delivery_res") or {}
        qty_code = qty_res_data.get("status_code", 200)
        deliv_code = deliv_res_data.get("status_code", 200)
        if any(code in (429, 500, 502, 503, 504) for code in (qty_code, deliv_code)):
            is_transient_error = True

        # If there are unknown SKUs or transient network/server errors and retries remain, retry ONLY the failed SKUs
        if (unknown_skus or is_transient_error) and self.request.retries < self.max_retries:
            attempt_num = self.request.retries + 1
            reason = f"{len(unknown_skus)} unknown SKUs" if unknown_skus else f"transient API status (qty:{qty_code}, deliv:{deliv_code})"
            retry_delay = min(300, 60 + (self.request.retries * 30))
            logging.warning(
                f"[apply_otto_availability_task] Otto retryable condition ({reason}). "
                f"Retrying in {retry_delay}s (attempt {attempt_num}/{self.max_retries})."
            )
            raise self.retry(
                countdown=retry_delay,
                kwargs={
                    "pending_quantity_skus": qty_failed,
                    "pending_delivery_skus": deliv_failed,
                    "aftercool_product_map": aftercool_product_map,
                }
            )

        # Either retries exhausted or non-retryable error
        err_msg = res.get("error") or "Unknown error syncing availability"
        logging.error(
            f"[apply_otto_availability_task] Availability sync failed for {acc_str} after "
            f"{self.request.retries} retries: {err_msg}"
        )
        from sqlalchemy.sql import func
        failed_skus_set = set(qty_failed + deliv_failed)
        for it in items:
            if it.product_ean in failed_skus_set:
                it.status = models.QueueItemStatus.FAILED
                it.error_message = f"Availability sync incomplete: OTTO Availability error: {err_msg}"[:500]
                if m := db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean == it.product_ean,
                    models.ProductMapping.account_name == it.account_name
                ).first():
                    m.status = "Error"
                    m.last_error = it.error_message
            else:
                it.status = models.QueueItemStatus.PUBLISHED
                it.error_message = None
                if m := db.query(models.ProductMapping).filter(
                    models.ProductMapping.product_ean == it.product_ean,
                    models.ProductMapping.account_name == it.account_name
                ).first():
                    m.status = "Published"
                    m.last_error = None
                    if not m.published_at:
                        m.published_at = func.now()
        db.commit()

        # Clean up locks
        redis_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
        try:
            r = redis.from_url(redis_url)
            for it_id in item_ids:
                r.delete(f"otto_avail_sched:{acc_str}:{it_id}")
        except Exception:
            pass

        return {
            "status": "partial_or_failed",
            "quantity_failed_skus": qty_failed,
            "delivery_failed_skus": deliv_failed,
            "error": err_msg,
        }
    finally:
        db.close()


@shared_task(name="tasks.recover_incomplete_availability")
def recover_incomplete_availability():
    """
    Periodic safety sweep (runs every 15 minutes) to automatically recover any published items
    whose availability or shipping profile was delayed/rejected by OTTO indexing lag.
    """
    import logging
    from database import SessionLocal
    import models
    from services.otto_client import OttoMarketClient
    from services.otto_availability import apply_otto_availability

    db = SessionLocal()
    try:
        items = db.query(models.PublishQueueItem).filter(
            models.PublishQueueItem.error_message.ilike("%Availability sync incomplete%")
        ).limit(100).all()

        if not items:
            return {"status": "noop", "recovered": 0}

        by_account = {}
        for it in items:
            acc_name = it.account_name.value if hasattr(it.account_name, "value") else str(it.account_name)
            by_account.setdefault(acc_name, []).append(it)

        recovered_count = 0
        for acc_name, acc_items in by_account.items():
            settings = db.query(models.AccountSettings).filter(
                models.AccountSettings.account_name == acc_name
            ).first()
            if not settings or not settings.otto_client_id or not settings.otto_client_secret:
                continue

            client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)

            for i in range(0, len(acc_items), 25):
                batch = acc_items[i : i + 25]
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
                failed_set = set(qty_failed + deliv_failed + unknown)

                synced_eans = []
                for it in batch:
                    if it.product_ean not in failed_set:
                        it.error_message = None
                        it.status = models.QueueItemStatus.PUBLISHED
                        synced_eans.append(it.product_ean)
                        recovered_count += 1

                if synced_eans:
                    db.query(models.ProductMapping).filter(
                        models.ProductMapping.product_ean.in_(synced_eans),
                        models.ProductMapping.account_name == acc_name,
                    ).update({
                        "status": "Published",
                        "last_error": None,
                    }, synchronize_session=False)

                db.commit()

        logging.info(f"[recover_incomplete_availability] Successfully recovered {recovered_count} items.")
        return {"status": "success", "recovered": recovered_count}
    except Exception as e:
        logging.error(f"[recover_incomplete_availability] Error during recovery: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()



def dispatch_otto_availability_batches(
    item_ids: list[int],
    account_name: str,
    plan_id: int | None = None,
    base_countdown: int = 60,
    jitter_step: int = 5,
    aftercool_product_map: dict | None = None,
    force: bool = False,
) -> int:
    """
    Slices item_ids into chunks of OTTO_AVAILABILITY_BATCH_SIZE (default 100),
    checks Redis deduplication lock to avoid double-scheduling the same items,
    and enqueues apply_otto_availability_task with staggered countdowns.
    If force=True, deletes any existing deduplication lock before acquiring.
    Returns the number of scheduled tasks.
    """
    if not item_ids:
        return 0

    import redis
    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    try:
        r = redis.from_url(redis_url)
    except Exception:
        r = None

    # Filter out items that are already scheduled using Redis set NX
    eligible_ids = []
    for item_id in item_ids:
        if r:
            lock_key = f"otto_avail_sched:{account_name}:{item_id}"
            if force:
                try:
                    r.delete(lock_key)
                except Exception:
                    pass
            # 20 minutes TTL protects against duplicate scheduling
            if r.set(lock_key, "1", nx=True, ex=1200):
                eligible_ids.append(item_id)
        else:
            eligible_ids.append(item_id)

    if not eligible_ids:
        return 0

    batch_size = max(1, OTTO_AVAILABILITY_BATCH_SIZE)
    batches = [eligible_ids[i:i + batch_size] for i in range(0, len(eligible_ids), batch_size)]

    dispatched = 0
    for idx, batch in enumerate(batches):
        countdown = max(0, base_countdown + (idx * jitter_step))
        apply_otto_availability_task.apply_async(
            args=[batch, account_name, plan_id],
            kwargs={"aftercool_product_map": aftercool_product_map},
            countdown=countdown,
        )
        dispatched += 1

    return dispatched


@shared_task(bind=True, acks_late=True, max_retries=0)
def poll_otto_processing_items(self):
    """
    Periodic task executed by Celery Beat every 2 minutes.
    Polls Otto Market update-tasks for items currently in PROCESSING status.
    Confirms actual publish status (done/failed) and updates DB accordingly.
    Limits to 10 poll attempts (~20 minutes) per item before marking as FAILED.
    """
    import redis
    import uuid
    from datetime import datetime, timezone
    from collections import defaultdict
    from services.otto_client import OttoMarketClient
    from services.executors.otto_api import parse_otto_error_payload

    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url)
    lock_key = "otto_poll_processing_lock"
    lock_val = str(uuid.uuid4())
    acquired = r.set(lock_key, lock_val, nx=True, ex=120)
    if not acquired:
        return {"status": "skipped", "message": "Previous poll task is still running."}

    LUA_RELEASE_LOCK = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    db: Session = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)

        # 1. Fetch items in PROCESSING state with a valid task href
        processing_items = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.status == models.QueueItemStatus.PROCESSING,
                models.PublishQueueItem.otto_task_href.isnot(None)
            )
            .all()
        )

        if not processing_items:
            return {"status": "idle", "message": "No items in PROCESSING state."}

        # 2. Group items by account_name to use the correct credentials
        items_by_account = defaultdict(list)
        for it in processing_items:
            acc_str = it.account_name.value if hasattr(it.account_name, "value") else str(it.account_name)
            items_by_account[acc_str].append(it)

        total_checked = 0
        total_published = 0
        total_failed = 0
        still_processing = 0
        touched_plan_ids = set()

        for acc_str, acc_items in items_by_account.items():
            settings = (
                db.query(models.AccountSettings)
                .filter(models.AccountSettings.account_name == acc_str)
                .first()
            )
            if not settings or not settings.otto_client_id or not settings.otto_client_secret:
                continue

            client = OttoMarketClient(
                client_id=settings.otto_client_id,
                client_secret=settings.otto_client_secret
            )

            # Group items by otto_task_href
            items_by_task = defaultdict(list)
            for it in acc_items:
                items_by_task[it.otto_task_href].append(it)

            for task_href, task_items in items_by_task.items():
                total_checked += len(task_items)
                for it in task_items:
                    touched_plan_ids.add(it.plan_id)

                try:
                    task_res = client.get_update_task(task_href)
                    state = task_res.get("state")
                    failed_count = task_res.get("failed", 0)

                    if state == "done":
                        if failed_count > 0:
                            # Fetch failed details
                            failed_href = task_items[0].otto_task_failed_href or task_href
                            failed_details = client.get_task_failed_details(failed_href)
                            err_msg_by_ean = {}
                            if isinstance(failed_details, list):
                                for res_entry in failed_details:
                                    res_ean = res_entry.get("ean") or res_entry.get("sku") or res_entry.get("logref") or ""
                                    if not res_ean:
                                        var_path = res_entry.get("variation", "")
                                        res_ean = var_path.split("/")[-1] if "/" in var_path else var_path
                                    if not res_ean and isinstance(res_entry.get("errors"), list):
                                        for sub_err in res_entry["errors"]:
                                            if isinstance(sub_err, dict) and sub_err.get("logref"):
                                                res_ean = sub_err.get("logref")
                                                break
                                    readable_err, attrs_to_remove = parse_otto_error_payload(res_entry)
                                    if res_ean:
                                        err_msg_by_ean[str(res_ean).strip()] = {
                                            "readable_err": readable_err,
                                            "attrs_to_remove": attrs_to_remove
                                        }

                            succeeded_count = task_res.get("succeeded", 0)

                            for it in task_items:
                                it.attempts += 1
                                if it.product_ean in err_msg_by_ean:
                                    err_data = err_msg_by_ean[it.product_ean]
                                    attrs_to_remove = err_data["attrs_to_remove"]
                                    readable_err = err_data["readable_err"]
                                    
                                    if attrs_to_remove and it.attempts < 5:
                                        # Auto-remediation: delete disallowed attributes case-insensitively
                                        attrs_lower = [a.lower().strip() for a in attrs_to_remove]
                                        db.query(models.ProductAttribute).filter(
                                            models.ProductAttribute.product_ean == it.product_ean,
                                            models.ProductAttribute.account_name == it.account_name,
                                            func.lower(models.ProductAttribute.attribute_name).in_(attrs_lower)
                                        ).delete(synchronize_session=False)
                                        
                                        it.status = models.QueueItemStatus.PENDING
                                        it.error_message = f"Авто-исправление: удалены лишние атрибуты ({', '.join(attrs_to_remove)}). Ожидает повторной отправки. Исходная ошибка: {readable_err}"
                                        if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                            m.status = "Error"
                                            m.last_error = it.error_message
                                    else:
                                        it.status = models.QueueItemStatus.FAILED
                                        if attrs_to_remove and it.attempts >= 5:
                                            it.error_message = f"Превышен лимит авто-исправлений (5 попыток). Ошибка: {readable_err}"[:500]
                                        else:
                                            it.error_message = readable_err[:500]
                                        total_failed += 1
                                        if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                            m.status = "Error"
                                            m.last_error = it.error_message
                                elif failed_count >= len(task_items) or succeeded_count == 0:
                                    it.status = models.QueueItemStatus.FAILED
                                    sample_err = list(err_msg_by_ean.values())[0]["readable_err"] if err_msg_by_ean else task_res.get("message", "Errors during processing")
                                    it.error_message = f"Otto validation error: {sample_err}"[:500]
                                    total_failed += 1
                                    if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                        m.status = "Error"
                                        m.last_error = it.error_message
                                elif failed_count > len(err_msg_by_ean):
                                    it.status = models.QueueItemStatus.FAILED
                                    it.error_message = f"Otto reported {failed_count} failures in batch. SKU status unconfirmed."[:500]
                                    total_failed += 1
                                    if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                        m.status = "Error"
                                        m.last_error = it.error_message
                                else:
                                    it.status = models.QueueItemStatus.PUBLISHED
                                    it.published_at = now_utc
                                    it.error_message = None
                                    total_published += 1
                                    if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                        m.status = "Published"
                                        m.published_at = now_utc
                        else:
                            # Succeeded with 0 failures
                            for it in task_items:
                                it.status = models.QueueItemStatus.PUBLISHED
                                it.published_at = now_utc
                                it.error_message = None
                                total_published += 1
                                if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                    m.status = "Published"
                                    m.published_at = now_utc

                        # Dispatch Availability API (Stock=20 and Shipping Profile) in batches with delayed countdown
                        succeeded_items = [it for it in task_items if it.status == models.QueueItemStatus.PUBLISHED]
                        if succeeded_items:
                            try:
                                plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == succeeded_items[0].plan_id).first()
                                dispatch_otto_availability_batches(
                                    item_ids=[it.id for it in succeeded_items],
                                    account_name=acc_str,
                                    plan_id=plan.id if plan else None,
                                    base_countdown=60,
                                    jitter_step=5,
                                )
                            except Exception as avail_err:
                                import logging
                                logging.warning(f"[poll_otto_processing_items] Failed to dispatch availability tasks: {avail_err}")
                    else:
                        # Task still in progress in Otto
                        for it in task_items:
                            it.poll_attempts += 1
                            if it.poll_attempts >= 10:
                                it.status = models.QueueItemStatus.FAILED
                                it.error_message = "Превышено время ожидания обработки OTTO (20 минут). Проверьте статус в личном кабинете OTTO."
                                total_failed += 1
                                if m := db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == it.product_ean, models.ProductMapping.account_name == it.account_name).first():
                                    m.status = "Error"
                            else:
                                still_processing += 1
                except Exception as task_err:
                    for it in task_items:
                        it.poll_attempts += 1
                        if it.poll_attempts >= 10:
                            it.status = models.QueueItemStatus.FAILED
                            it.error_message = f"Ошибка проверки статуса OTTO: {str(task_err)[:400]}"
                            total_failed += 1

        # Check if any affected plans are now completed
        for plan_id in touched_plan_ids:
            remaining = (
                db.query(models.PublishQueueItem)
                .filter(
                    models.PublishQueueItem.plan_id == plan_id,
                    models.PublishQueueItem.status.in_([
                        models.QueueItemStatus.PENDING,
                        models.QueueItemStatus.PROCESSING
                    ])
                )
                .count()
            )
            if remaining == 0:
                plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
                if plan and plan.status == models.PlanStatus.ACTIVE:
                    plan.status = models.PlanStatus.COMPLETED

        db.commit()
        return {
            "status": "completed",
            "checked": total_checked,
            "published": total_published,
            "failed": total_failed,
            "still_processing": still_processing,
        }
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
        try:
            r.eval(LUA_RELEASE_LOCK, 1, lock_key, lock_val)
        except Exception:
            pass


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
        initial_total = len(product_eans) if product_eans else 100
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": initial_total, "percent": 0, "eta_seconds": 0, "message": "Подготовка данных фабрики..."}
        )

        query = db.query(models.ProductMapping).filter(
            models.ProductMapping.factory_id == str(factory_id),
            models.ProductMapping.account_name == str(account)
        )
        if product_eans:
            query = query.filter(models.ProductMapping.product_ean.in_(product_eans))

        mappings = query.all()
        needs_sync = not mappings or (any(not m.product_name for m in mappings) and all(not m.last_synced_at for m in mappings))
        if needs_sync:
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
        initial_total = len(product_eans) if product_eans else 100
        self.update_state(
            state="PROGRESS",
            meta={"current": 0, "total": initial_total, "percent": 0, "eta_seconds": 0, "message": "Подготовка товаров и атрибутов..."}
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
        needs_sync = not mappings or (any(not m.product_name for m in mappings) and all(not m.last_synced_at for m in mappings))
        if needs_sync:
            from routers.aftercool import force_sync_metadata
            try:
                asyncio.run(force_sync_metadata(factory_id=factory_id, account=account, dataset="lister", db=db))
            except Exception as e:
                logging.warning(f"Auto-sync metadata for ZIP export failed: {e}")

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
        target_acc = (account or "JV").upper()
        all_db_attrs = db.query(
            models.ProductAttribute.product_ean,
            models.ProductAttribute.attribute_name,
            models.ProductAttribute.attribute_value
        ).filter(
            models.ProductAttribute.product_ean.in_(all_eans),
            models.ProductAttribute.account_name == target_acc
        ).all()
        attrs_by_ean = {}
        for p_ean, a_name, a_val in all_db_attrs:
            if p_ean not in attrs_by_ean:
                attrs_by_ean[p_ean] = {}
            attrs_by_ean[p_ean][a_name] = a_val

        from services.brand_guard import sanitize_brand_attributes
        for ean in attrs_by_ean:
            attrs_by_ean[ean] = sanitize_brand_attributes(target_acc, attrs_by_ean[ean])

        # Mirror images for XL account if needed
        is_xl = (account or "").upper() == "XL"
        mirrored_map = {}
        mirrored_bytes_map: dict[str, bytes] = {}
        if is_xl:
            self.update_state(
                state="PROGRESS",
                meta={"current": 0, "total": total_items, "percent": 0, "eta_seconds": 0, "message": "Зеркалирование изображений для XL..."}
            )
            import httpx
            async def run_mirroring():
                sem = asyncio.Semaphore(30)
                limits = httpx.Limits(max_connections=60, max_keepalive_connections=30)
                async with httpx.AsyncClient(timeout=10.0, limits=limits, follow_redirects=True) as http_client:
                    async def mirror_one(m):
                        if m.image_url:
                            async with sem:
                                m_url = await mirror_image_for_xl(
                                    m.image_url, 
                                    m.product_ean, 
                                    "XL", 
                                    client=http_client, 
                                    bytes_out=mirrored_bytes_map
                                )
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
            max_concurrency=60,
            timeout=8.0,
            max_remote=99999,
            progress_callback=on_img_progress,
            preloaded_bytes=mirrored_bytes_map
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

                # 2. SQLite .db per category (In-Memory serialized directly into ZIP)
                cat_conn = sqlite3.connect(":memory:")
                cat_cur = cat_conn.cursor()
                cat_cur.execute("PRAGMA synchronous = OFF")
                cat_cur.execute("PRAGMA journal_mode = OFF")
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
                db_bytes = cat_conn.serialize()
                cat_conn.close()
                zip_file.writestr(f"2. Database .db/{cat_name}_{target_name}_{len(prods)}.db", db_bytes)

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

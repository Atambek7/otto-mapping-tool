"""Durable, one-time stock and delivery setup after a new Drip publication.

The database claim and attempt row are committed before HTTP. A worker crash,
timeout, or 5xx therefore cannot turn into an automatic duplicate write.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError

import models

ALLOWED_ACCOUNTS = {"JV", "XL"}
MAX_AUTOMATIC_ATTEMPTS = 10
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600


def _account_value(value):
    return value.value if hasattr(value, "value") else str(value)


def _payload_hash(payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_delivery_payload(db, account, ean, plan=None, aftercool_product_map=None):
    """Freeze the existing shipping-profile decision for this newly published SKU."""
    from services.otto_availability import is_chinese_product, resolve_shipping_profile_id

    account = _account_value(account).upper()
    account_enum = models.AccountType(account)
    mapping = db.query(models.ProductMapping).filter_by(
        account_name=account_enum, product_ean=str(ean)
    ).first()
    attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.account_name == account_enum,
        models.ProductAttribute.product_ean == str(ean),
        models.ProductAttribute.attribute_name.in_([
            "Ursprungsland", "ursprungsland", "Herkunftsland", "herkunftsland",
            "Herstellungsland", "herstellungsland", "Produktionsland",
            "produktionsland", "Herstellerland", "herstellerland",
        ]),
    ).all()
    origin_attrs = {attr.attribute_name: attr.attribute_value for attr in attrs}
    cached = (aftercool_product_map or {}).get(str(ean), {})
    factory_name = getattr(plan, "factory_name", "") or ""
    factory_id = getattr(plan, "factory_id", "") or (mapping.factory_id if mapping else "")
    is_china = is_chinese_product(
        factory_name=factory_name,
        factory_id=factory_id,
        image_urls=[mapping.image_url] if mapping and mapping.image_url else [],
        product_name=mapping.product_name if mapping else "",
        kurzbeschreibung=cached.get("kurzbeschreibung", ""),
        attributes=origin_attrs,
    )
    return {
        "sku": str(ean),
        "shippingProfileId": resolve_shipping_profile_id(account, is_china),
        "processingTime": "56" if is_china else "37",
    }


def register_initial(db, account, ean, queue_item_id, plan=None, delivery_payload=None):
    """Insert the one-time state in the caller's transaction; never commits it."""
    account = _account_value(account).upper()
    if account not in ALLOWED_ACCOUNTS or not ean or not queue_item_id:
        raise ValueError("Invalid account, EAN or queue item")

    account_enum = models.AccountType(account)
    queue_item = db.query(models.PublishQueueItem).filter_by(
        id=queue_item_id,
        account_name=account_enum,
        product_ean=str(ean),
    ).first()
    if queue_item is None:
        return False
    scoped_plan = db.query(models.PublishPlan).filter_by(
        id=queue_item.plan_id,
        account_name=account_enum,
        executor_type=models.ExecutorType.OTTO_API,
    ).first()
    if scoped_plan is None or (plan is not None and plan.id != scoped_plan.id):
        return False
    plan = scoped_plan
    if delivery_payload is None:
        delivery_payload = build_delivery_payload(db, account, ean, plan)
    if delivery_payload.get("sku") != str(ean):
        raise ValueError("Delivery payload SKU does not match the newly published EAN")

    state = models.InitialAvailabilityState(
        account_name=account,
        product_ean=str(ean),
        queue_item_id=queue_item_id,
        plan_id=plan.id,
        factory_id=str(getattr(plan, "factory_id", "") or "") or None,
        factory_name=str(getattr(plan, "factory_name", "") or "") or None,
        category_id=queue_item.category_id,
        stock_state="pending",
        delivery_state="pending",
        delivery_payload_json=delivery_payload,
        next_dispatch_at=datetime.now(timezone.utc),
    )
    try:
        with db.begin_nested():
            db.add(state)
            db.flush()
        return True
    except IntegrityError:
        return False


def should_register_initial(db, account, item, previously_published=False):
    """Exclude republishing and historical/legacy listings from initialization."""
    account = _account_value(account).upper()
    if account not in ALLOWED_ACCOUNTS or previously_published:
        return False
    prior_queue = db.query(models.PublishQueueItem.id).filter(
        models.PublishQueueItem.account_name == models.AccountType(account),
        models.PublishQueueItem.product_ean == item.product_ean,
        models.PublishQueueItem.id != item.id,
        models.PublishQueueItem.published_at.isnot(None),
    ).first()
    if prior_queue:
        return False
    prior_sync = db.query(models.AvailabilitySyncLog.id).filter(
        models.AvailabilitySyncLog.account_name == account,
        models.AvailabilitySyncLog.product_ean == item.product_ean,
        models.AvailabilitySyncLog.sync_result == "ok",
    ).first()
    if prior_sync:
        return False
    return db.query(models.InitialAvailabilityState.id).filter_by(
        account_name=account, product_ean=item.product_ean
    ).first() is None


def register_for_success(db, account, item, plan, aftercool_product_map=None,
                         previously_published=False):
    if not should_register_initial(db, account, item, previously_published):
        return False
    payload = build_delivery_payload(db, account, item.product_ean, plan, aftercool_product_map)
    return register_initial(db, account, item.product_ean, item.id, plan, payload)


def _operation_fields(state, operation):
    if operation == "stock":
        return (models.InitialAvailabilityState.stock_state,
                models.InitialAvailabilityState.stock_attempts,
                models.InitialAvailabilityState.stock_next_attempt_at,
                models.InitialAvailabilityState.stock_last_error)
    if operation == "delivery":
        return (models.InitialAvailabilityState.delivery_state,
                models.InitialAvailabilityState.delivery_attempts,
                models.InitialAvailabilityState.delivery_next_attempt_at,
                models.InitialAvailabilityState.delivery_last_error)
    raise ValueError("Unknown availability operation")


def claim_step(db, state_id, operation, trigger_kind="automatic"):
    """Atomically claim one permitted attempt and durably record it before HTTP."""
    state_col, attempts_col, next_col, _ = _operation_fields(None, operation)
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(models.InitialAvailabilityState)
        .where(
            models.InitialAvailabilityState.id == state_id,
            state_col.in_(["pending", "retryable_failed"]),
            attempts_col < MAX_AUTOMATIC_ATTEMPTS + models.InitialAvailabilityState.manual_retry_count,
            or_(next_col.is_(None), next_col <= now),
        )
        .values({state_col.key: "sending", attempts_col.key: attempts_col + 1,
                 next_col.key: None})
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    state = (db.query(models.InitialAvailabilityState).populate_existing()
             .filter_by(id=state_id).one())
    attempt_number = getattr(state, attempts_col.key)
    payload = ([{"sku": state.product_ean, "quantity": 20}]
               if operation == "stock" else state.delivery_payload_json)
    if not payload:
        db.rollback()
        if operation == "delivery":
            mark_not_runnable(db, state_id, operation, "Delivery payload is missing; manual correction required.")
        return None
    attempt = models.InitialAvailabilityAttempt(
        state_id=state.id,
        operation=operation,
        attempt_number=attempt_number,
        trigger_kind=trigger_kind or state.next_attempt_trigger_kind,
        payload_hash=_payload_hash(payload),
        outcome="sending",
    )
    db.add(attempt)
    if trigger_kind == "manual":
        state.next_attempt_trigger_kind = "automatic"
    db.commit()
    return attempt.id, payload


def classify_response(response, expected_sku=None):
    """Classify only explicit server outcomes; 202 is accepted, not complete."""
    code = int((response or {}).get("status_code") or 0)
    if code in (200, 201, 204):
        return "confirmed"
    if code == 202:
        return "accepted"
    if code == 429:
        return "retryable_failed"
    if expected_sku and code in (207, 400, 404, 422):
        from services.otto_availability import extract_unknown_skus
        if expected_sku in extract_unknown_skus(response or {}, [expected_sku]):
            return "retryable_failed"
    if code == 207:
        return "unknown"
    if 400 <= code < 500:
        return "failed"
    # A network exception (0), 5xx, or malformed response may have followed a
    # server-side application, so it must be reconciled before any retry.
    return "unknown"


def finish_step(db, state_id, operation, attempt_id, outcome, response=None, error=None):
    if outcome not in {"confirmed", "accepted", "retryable_failed", "failed", "unknown"}:
        raise ValueError("Invalid availability result")
    state_col, _, next_col, error_col = _operation_fields(None, operation)
    state = db.query(models.InitialAvailabilityState).filter_by(id=state_id).with_for_update().one()
    if getattr(state, state_col.key) != "sending":
        return False
    now = datetime.now(timezone.utc)
    attempt = db.query(models.InitialAvailabilityAttempt).filter_by(
        id=attempt_id, state_id=state_id, operation=operation
    ).one()
    if outcome == "retryable_failed" and getattr(state, operation + "_attempts") < MAX_AUTOMATIC_ATTEMPTS:
        delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, getattr(state, operation + "_attempts") - 1)))
        setattr(state, next_col.key, now + timedelta(seconds=delay))
    else:
        if outcome == "retryable_failed":
            outcome = "failed"
        setattr(state, next_col.key, None)
    message = error or (response or {}).get("error") or (response or {}).get("text")
    setattr(state, state_col.key, outcome)
    setattr(state, error_col.key, str(message)[:1000] if message else None)
    attempt.outcome = outcome
    attempt.status_code = (response or {}).get("status_code")
    attempt.error_summary = str(message)[:1000] if message else None
    attempt.completed_at = now
    # The PostgreSQL scan is the outbox dispatcher. A retryable rejection is
    # eligible only at its durable next_attempt_at; no Celery self.retry exists.
    if state.stock_state == "pending" or state.delivery_state == "pending":
        state.next_dispatch_at = now
    else:
        due_times = [getattr(state, f"{op}_next_attempt_at") for op in ("stock", "delivery")
                     if getattr(state, f"{op}_state") == "retryable_failed"]
        state.next_dispatch_at = min(due_times) if due_times else None
    state.dispatch_lease_until = None
    state.updated_at = now
    db.commit()
    return True


def finalize_dispatch(db, state_id):
    """Release the outbox lease after a delivery attempt is handled."""
    state = db.query(models.InitialAvailabilityState).filter_by(id=state_id).with_for_update().first()
    if not state:
        return
    state.dispatch_lease_until = None
    if state.stock_state == "pending" or state.delivery_state == "pending":
        state.next_dispatch_at = datetime.now(timezone.utc)
    elif state.next_dispatch_at is None:
        due = [getattr(state, f"{op}_next_attempt_at") for op in ("stock", "delivery")
               if getattr(state, f"{op}_state") == "retryable_failed"]
        state.next_dispatch_at = min(due) if due else None
    db.commit()


def mark_not_runnable(db, state_id, operation, message):
    state_col, _, next_col, error_col = _operation_fields(None, operation)
    state = db.query(models.InitialAvailabilityState).filter_by(id=state_id).with_for_update().one()
    if getattr(state, state_col.key) not in ("pending", "retryable_failed"):
        return False
    setattr(state, state_col.key, "failed")
    setattr(state, next_col.key, None)
    setattr(state, error_col.key, message[:1000])
    state.dispatch_lease_until = None
    state.next_dispatch_at = None
    db.commit()
    return True


def apply_guarded(client, items, account_name, db, plan=None, aftercool_product_map=None,
                  target_quantity_skus=None, target_delivery_skus=None):
    """Compatibility helper: process only registered states, never legacy items."""
    from services.otto_client import OttoMarketClient

    acc = _account_value(account_name).upper()
    if acc not in ALLOWED_ACCOUNTS:
        return {"status": "rejected", "quantity_failed_skus": [],
                "delivery_failed_skus": [], "unknown_skus": [],
                "error": "Invalid account"}
    results = {"quantity_failed_skus": [], "delivery_failed_skus": [],
               "unknown_skus": [], "error": None}
    for item in items:
        sku = str(item.product_ean)
        if _account_value(item.account_name).upper() != acc:
            results["quantity_failed_skus"].append(sku)
            results["delivery_failed_skus"].append(sku)
            continue
        state = db.query(models.InitialAvailabilityState).filter_by(
            account_name=acc, product_ean=sku, queue_item_id=item.id
        ).first()
        if state is None:
            results["quantity_failed_skus"].append(sku)
            results["delivery_failed_skus"].append(sku)
            results["error"] = "Legacy item without one-time availability authorization"
            continue
        for operation, requested in (
            ("stock", target_quantity_skus is None or sku in target_quantity_skus),
            ("delivery", target_delivery_skus is None or sku in target_delivery_skus),
        ):
            if not requested:
                continue
            claim_result = claim_step(db, state.id, operation)
            if not claim_result:
                continue
            attempt_id, payload = claim_result
            try:
                if operation == "stock":
                    response = client.update_initial_quantity_once(sku)
                else:
                    response = client.update_initial_delivery_once(payload)
                outcome = classify_response(response, sku)
                finish_step(db, state.id, operation, attempt_id, outcome, response=response)
            except Exception as exc:
                finish_step(db, state.id, operation, attempt_id, "unknown", error=str(exc))
        db.expire_all()
        state = db.query(models.InitialAvailabilityState).filter_by(id=state.id).one()
        if state.stock_state not in ("confirmed", "accepted"):
            results["quantity_failed_skus"].append(sku)
        if state.delivery_state not in ("confirmed", "accepted"):
            results["delivery_failed_skus"].append(sku)
        for op in ("stock", "delivery"):
            value = getattr(state, f"{op}_state")
            if value in ("unknown", "sending", "accepted"):
                results["unknown_skus"].append(sku)
    return results

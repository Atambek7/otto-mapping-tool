"""Read-only OTTO checks and local recovery of failed queue items.

Remote observations are separate from submission status. A successful GET proves
that the account owns this product record, not that it is ONLINE or buyable.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

import requests

import models
from services.aftercool_service import (
    iter_products, parse_price, redis_client, resolve_aftercool_account,
)
from services.otto_client import OttoMarketClient


def observation_key(account: str, item_id: int) -> str:
    return f"otto_queue_observation:{account}:{item_id}"


def attempt_fingerprint(item: Any) -> str:
    value = f"{item.plan_id}:{item.product_ean}:{item.attempts}:{item.error_message or ''}"
    return hashlib.sha256(value.encode()).hexdigest()


def load_observations(account: str, items: list, *, strict: bool = False) -> dict:
    """Only use observations of the exact failed attempt, in the same account."""
    if not items:
        return {}
    try:
        if redis_client is None:
            raise RuntimeError("Observation storage unavailable")
        raw = redis_client.mget([observation_key(account, item.id) for item in items])
        result = {}
        for item, value in zip(items, raw):
            if not value or item.status != models.QueueItemStatus.FAILED:
                continue
            try:
                record = json.loads(value)
                if (
                    record.get("account") == account
                    and record.get("ean") == item.product_ean
                    and record.get("fingerprint") == attempt_fingerprint(item)
                    and record.get("exists") is True
                    and record.get("checked_at")
                ):
                    result[item.id] = record
            except (ValueError, TypeError, AttributeError):
                if strict:
                    raise RuntimeError("Invalid OTTO observation; reconcile before retrying")
        return result
    except Exception:
        if strict:
            raise
        return {}


def check_product(client: OttoMarketClient, token: str, ean: str) -> dict:
    """No product writes. Never infer absence from an auth/server/network error."""
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.get(
            f"{client.base_url}/v5/products/{ean}",
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        )
        result = {"http_status": response.status_code, "exists": None, "checked_at": checked_at}
        if response.status_code == 200:
            product = response.json()
            if str(product.get("sku")) == ean and str(product.get("ean")) == ean:
                result["exists"] = True
        elif response.status_code == 404:
            result["exists"] = False
        return result
    except (requests.RequestException, ValueError, AttributeError):
        return {"exists": None, "checked_at": checked_at}


async def _factory_prices(factory_id: str, account: str, wanted: set) -> dict:
    source = resolve_aftercool_account(factory_id, account)
    prices = {}
    async for page in iter_products(factory_id, account=source, dataset="lister", page_size=500):
        for product in page:
            if product.ean in wanted:
                prices[product.ean] = {
                    "price": str(product.price) if product.price is not None else None,
                    "price_af": str(product.price_af) if product.price_af is not None else None,
                }
    if set(prices) != wanted:
        raise ValueError("Incomplete Aftercool result; no repair report produced")
    return prices


def build_report(db, plan_id: int, account: str, factory_id: str) -> dict:
    plan = db.query(models.PublishPlan).filter_by(id=plan_id, account_name=account, factory_id=factory_id).one()
    items = db.query(models.PublishQueueItem).filter_by(
        plan_id=plan.id, account_name=account, status=models.QueueItemStatus.FAILED,
    ).order_by(models.PublishQueueItem.id).limit(1001).all()
    if not items or len(items) > 1000:
        raise ValueError("Select a plan with 1–1000 failed items")
    wanted = {item.product_ean for item in items}
    mappings = db.query(models.ProductMapping).filter(
        models.ProductMapping.account_name == account,
        models.ProductMapping.factory_id == factory_id,
        models.ProductMapping.product_ean.in_(wanted),
    ).all()
    by_ean = {m.product_ean: m for m in mappings}
    if set(by_ean) != wanted:
        raise ValueError("Account/factory mapping mismatch")
    settings = db.query(models.AccountSettings).filter_by(account_name=account).one()
    client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
    snapshot = [{
        "id": item.id, "ean": item.product_ean, "fingerprint": attempt_fingerprint(item),
        "old_price": by_ean[item.product_ean].price,
        "old_price_af": by_ean[item.product_ean].price_af,
        "has_manual_price": bool(by_ean[item.product_ean].has_manual_price),
    } for item in items]
    # Release the read transaction before doing any network calls.
    db.rollback()
    prices = asyncio.run(_factory_prices(factory_id, account, wanted))
    token = client.get_access_token()
    checks = []
    for index, row in enumerate(snapshot):
        if index:
            time.sleep(1)  # Stay below the product endpoint's request limit.
        checks.append(check_product(client, token, row["ean"]))
    for row, check in zip(snapshot, checks):
        row["aftercool"] = prices[row["ean"]]
        row["otto"] = check
    return {
        "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_id": plan_id, "account": account, "factory_id": factory_id,
        "items": snapshot,
    }


def refresh_unknown(db, report: dict) -> dict:
    """Retry only inconclusive observations, paced to avoid endpoint throttling."""
    settings = db.query(models.AccountSettings).filter_by(account_name=report["account"]).one()
    client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
    db.rollback()
    token = client.get_access_token()
    for row in report["items"]:
        if row["otto"].get("exists") is None:
            time.sleep(1)
            row["otto"] = check_product(client, token, row["ean"])
    return report


def apply_report(db, report: dict) -> dict:
    """Fill missing local prices only; never enqueue work or write to OTTO.

    Check all optimistic guards before changing anything. Observations have no TTL:
    they are historical facts with a visible check timestamp, tied to one attempt.
    Redis persistence is separate from the DB; report can be reapplied after failure.
    """
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(report["created_at"])).total_seconds()
    if report.get("version") != 1 or not 0 <= age <= 1800:
        raise ValueError("Report invalid or older than 30 minutes; inspect again")
    if redis_client is None:
        raise RuntimeError("Observation storage unavailable")
    redis_client.ping()
    account, factory_id, plan_id = report["account"], report["factory_id"], report["plan_id"]
    if account not in ("JV", "XL") or not 1 <= len(report["items"]) <= 1000:
        raise ValueError("Invalid report scope")
    db.query(models.PublishPlan).filter_by(id=plan_id, account_name=account, factory_id=factory_id).one()
    changed = 0
    observations = {}
    try:
        for row in report["items"]:
            item = db.query(models.PublishQueueItem).filter_by(
                id=row["id"], plan_id=plan_id, account_name=account,
                product_ean=row["ean"], status=models.QueueItemStatus.FAILED,
            ).with_for_update().one()
            if attempt_fingerprint(item) != row["fingerprint"]:
                raise ValueError("Queue attempt changed; inspect again")
            mapping = db.query(models.ProductMapping).filter_by(
                product_ean=row["ean"], account_name=account, factory_id=factory_id,
            ).with_for_update().one()
            # Recheck manual flag under row lock, including edits made after inspection.
            # Missing backup fields can be filled without altering any positive price.
            updated = False
            price = parse_price(row["aftercool"].get("price"))
            price_af = parse_price(row["aftercool"].get("price_af"))
            if not mapping.has_manual_price and parse_price(mapping.price) is None and price is not None:
                mapping.price = str(price)
                updated = True
            if parse_price(mapping.price_af) is None and price_af is not None:
                mapping.price_af = str(price_af)
                updated = True
            if updated:
                mapping.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
                changed += 1
            if row["otto"].get("exists") is True:
                observations[observation_key(account, item.id)] = json.dumps({
                    "account": account, "ean": item.product_ean,
                    "fingerprint": row["fingerprint"],
                    "exists": True, "checked_at": row["otto"]["checked_at"],
                })
        db.commit()
    except Exception:
        db.rollback()
        raise
    if observations:
        redis_client.mset(observations)
    return {"prices_recovered": changed, "existing_otto_cards": len(observations), "items_checked": len(report["items"])}

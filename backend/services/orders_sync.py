"""
Orders sync service.
Fetches order data (real or mock) and upserts daily aggregates into order_stats table.
"""
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
from typing import Optional
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

import models
from services.otto_orders_client import OttoOrdersClient


def sync_orders_for_account(
    db: Session,
    account_name: str,
    days_back: int = 90,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> dict:
    """
    Syncs order statistics for a single account.
    - Fetches raw order records via OttoOrdersClient (real or mock).
    - Aggregates by calendar date.
    - Upserts into order_stats (idempotent — safe to run repeatedly).

    Returns a summary dict: {account, days_synced, total_orders, total_items, total_revenue}
    """
    to_date = to_date or date.today()
    from_date = from_date or (to_date - timedelta(days=days_back))

    if not client_id or not client_secret:
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == account_name)
            .first()
        )
        if settings:
            client_id = client_id or getattr(settings, "otto_client_id", None)
            client_secret = client_secret or getattr(settings, "otto_client_secret", None)

    client = OttoOrdersClient(client_id=client_id, client_secret=client_secret)
    raw_records = client.fetch_orders(from_date, to_date)

    # Clean old detailed orders and aggregate stats in this date range for the account
    db.query(models.OttoOrder).filter(
        models.OttoOrder.account_name == account_name,
        models.OttoOrder.order_calendar_date >= from_date,
        models.OttoOrder.order_calendar_date <= to_date,
    ).delete(synchronize_session=False)

    db.query(models.OrderStat).filter(
        models.OrderStat.account_name == account_name,
        models.OrderStat.order_date >= from_date,
        models.OrderStat.order_date <= to_date,
    ).delete(synchronize_session=False)
    db.commit()

    now_utc = datetime.now(timezone.utc)

    # Deduplicate raw_records by sales_order_id
    seen_sids = set()
    unique_records = []
    for rec in raw_records:
        sid = rec.get("sales_order_id")
        if sid and sid in seen_sids:
            continue
        if sid:
            seen_sids.add(sid)
        unique_records.append(rec)

    # Save detailed orders and items
    for rec in unique_records:
        order_obj = models.OttoOrder(
            account_name=account_name,
            sales_order_id=rec["sales_order_id"],
            order_number=rec["order_number"],
            order_date=rec["order_date"],
            order_calendar_date=rec["order_calendar_date"],
            weekday=rec["weekday"],
            total_revenue=Decimal(str(rec["revenue"])),
            items_count=rec["quantity"],
            synced_at=now_utc,
        )
        db.add(order_obj)
        db.flush()

        for it in rec.get("items", []):
            item_obj = models.OttoOrderItem(
                order_id=order_obj.id,
                position_item_id=it["position_item_id"],
                sku=it.get("sku"),
                ean=it.get("ean"),
                article_number=it.get("article_number"),
                product_title=it.get("product_title"),
                price=Decimal(str(it.get("price", 0))),
                currency=it.get("currency", "EUR"),
                fulfillment_status=it.get("fulfillment_status"),
                shop_url=it.get("shop_url"),
            )
            db.add(item_obj)

    db.commit()

    # Aggregate by date for instant chart loading
    by_date: dict = defaultdict(lambda: {"orders_count": 0, "items_count": 0, "revenue": Decimal("0")})
    for rec in raw_records:
        d = rec["order_calendar_date"]
        by_date[d]["orders_count"] += 1
        by_date[d]["items_count"] += int(rec.get("quantity", 1))
        by_date[d]["revenue"] += Decimal(str(rec.get("revenue", 0)))

    rows_to_upsert = []
    for order_date, agg in by_date.items():
        rows_to_upsert.append({
            "account_name": account_name,
            "order_date": order_date,
            "weekday": order_date.weekday(),  # 0=Monday .. 6=Sunday
            "orders_count": agg["orders_count"],
            "items_count": agg["items_count"],
            "revenue": agg["revenue"],
            "synced_at": now_utc,
        })

    if rows_to_upsert:
        stmt = pg_insert(models.OrderStat).values(rows_to_upsert)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_order_stats_account_date",
            set_={
                "orders_count": stmt.excluded.orders_count,
                "items_count": stmt.excluded.items_count,
                "revenue": stmt.excluded.revenue,
                "synced_at": stmt.excluded.synced_at,
            },
        )
        db.execute(stmt)
        db.commit()

    return {
        "account": account_name,
        "period_from": from_date.isoformat(),
        "period_to": to_date.isoformat(),
        "days_synced": len(by_date),
        "total_orders": sum(v["orders_count"] for v in by_date.values()),
        "total_items": sum(v["items_count"] for v in by_date.values()),
        "total_revenue": float(sum(v["revenue"] for v in by_date.values())),
    }


def sync_all_accounts(db: Session, days_back: int = 90) -> list:
    """
    Syncs order stats for all accounts (JV and XL).
    Fetches their credentials from AccountSettings.
    Falls back gracefully to mock if credentials are missing.
    """
    results = []
    for account in ("JV", "XL"):
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == account)
            .first()
        )
        client_id = getattr(settings, "otto_client_id", None) if settings else None
        client_secret = getattr(settings, "otto_client_secret", None) if settings else None

        try:
            result = sync_orders_for_account(
                db=db,
                account_name=account,
                days_back=days_back,
                client_id=client_id,
                client_secret=client_secret,
            )
            results.append({"success": True, **result})
        except Exception as e:
            results.append({"success": False, "account": account, "error": str(e)})

    return results

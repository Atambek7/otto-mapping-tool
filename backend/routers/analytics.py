"""
Analytics router: weekday order statistics.
Provides aggregated sales data for the dashboard chart.
"""
from datetime import date, timedelta, datetime, timezone
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, BackgroundTasks, HTTPException
from sqlalchemy import func, case, or_
from sqlalchemy.orm import Session
import sqlalchemy as sa

from database import get_db
import models
from services.orders_sync import sync_orders_for_account, sync_all_accounts
from services.otto_orders_client import USE_MOCK_DATA
from services.otto_client import OttoMarketClient
from routers.aftercool import redis_client, AFTERCOOL_BASE_URL, get_aftercool_session
import json
import asyncio
import re
import httpx
import time

router = APIRouter(prefix="/analytics", tags=["Analytics"])

WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

_otto_token_cache: dict[str, tuple[str, float]] = {}

def get_cached_otto_token(account_name: str, db: Session) -> Optional[str]:
    now = time.time()
    cached = _otto_token_cache.get(account_name)
    if cached and cached[1] > now:
        return cached[0]
    setting = db.query(models.AccountSettings).filter(models.AccountSettings.account_name == account_name).first()
    if setting and setting.otto_client_id and setting.otto_client_secret:
        try:
            client = OttoMarketClient(setting.otto_client_id, setting.otto_client_secret)
            token = client.get_access_token()
            _otto_token_cache[account_name] = (token, now + 1800)
            return token
        except Exception as e:
            print(f"[Otto Token Error] {account_name}: {e}")
    return None


@router.get("/weekday-stats")
def get_weekday_stats(
    account_name: models.AccountType = Query(..., description="JV or XL"),
    weeks: Optional[int] = Query(default=12, ge=1, le=104, description="How many past weeks to include"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """
    Returns aggregated order statistics grouped by weekday for the chosen period.
    Supports either pre-set `weeks` or specific `from_date` / `to_date` range.
    """
    acc_val = account_name.value if hasattr(account_name, "value") else str(account_name)

    start_date: date
    end_date: date
    eff_weeks: int

    if from_date and to_date:
        try:
            start_date = date.fromisoformat(from_date)
            end_date = date.fromisoformat(to_date)
            if start_date > end_date:
                start_date, end_date = end_date, start_date
        except Exception:
            start_date = date.today() - timedelta(weeks=weeks or 12)
            end_date = date.today()
        days_span = (end_date - start_date).days + 1
        eff_weeks = max(1, round(days_span / 7))
    else:
        eff_weeks = weeks or 12
        end_date = date.today()
        start_date = end_date - timedelta(weeks=eff_weeks)

    cache_key = f"otto:analytics:weekday:{acc_val}:{start_date.isoformat()}:{end_date.isoformat()}"

    if not refresh and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            print(f"[Redis Analytics Cache] Read error: {e}")

    # Aggregate: SUM of orders/items/revenue per weekday
    rows = (
        db.query(
            models.OrderStat.weekday,
            func.sum(models.OrderStat.orders_count).label("orders"),
            func.sum(models.OrderStat.items_count).label("items"),
            func.sum(models.OrderStat.revenue).label("revenue"),
        )
        .filter(
            models.OrderStat.account_name == account_name,
            models.OrderStat.order_date >= start_date,
            models.OrderStat.order_date <= end_date,
        )
        .group_by(models.OrderStat.weekday)
        .order_by(models.OrderStat.weekday)
        .all()
    )

    # If no data at all — auto-seed with mock data so UI is never blank
    if not rows:
        try:
            sync_orders_for_account(
                db=db,
                account_name=acc_val,
                from_date=start_date,
                to_date=end_date,
            )
        except Exception:
            pass

        rows = (
            db.query(
                models.OrderStat.weekday,
                func.sum(models.OrderStat.orders_count).label("orders"),
                func.sum(models.OrderStat.items_count).label("items"),
                func.sum(models.OrderStat.revenue).label("revenue"),
            )
            .filter(
                models.OrderStat.account_name == account_name,
                models.OrderStat.order_date >= start_date,
                models.OrderStat.order_date <= end_date,
            )
            .group_by(models.OrderStat.weekday)
            .order_by(models.OrderStat.weekday)
            .all()
        )

    stats_by_weekday = {row.weekday: row for row in rows}

    weekdays = []
    max_orders = max((row.orders for row in rows), default=1) or 1

    for wd in range(7):
        row = stats_by_weekday.get(wd)
        orders = int(row.orders) if row else 0
        items = int(row.items) if row else 0
        revenue = float(row.revenue) if row else 0.0
        avg_per_week = round(orders / eff_weeks, 1) if eff_weeks else 0

        weekdays.append({
            "weekday": wd,
            "label": WEEKDAY_LABELS[wd],
            "orders": orders,
            "items": items,
            "revenue": revenue,
            "avg_per_week": avg_per_week,
            # Normalised 0..1 for chart bar height
            "intensity": round(orders / max_orders, 3) if max_orders else 0,
        })

    # Determine peak day
    peak = max(weekdays, key=lambda x: x["orders"]) if weekdays else None

    # Last sync time
    last_sync_row = (
        db.query(func.max(models.OrderStat.synced_at))
        .filter(models.OrderStat.account_name == account_name)
        .scalar()
    )

    result = {
        "account_name": acc_val,
        "weeks": eff_weeks,
        "period_from": start_date.isoformat(),
        "period_to": end_date.isoformat(),
        "weekdays": weekdays,
        "peak_day": {"weekday": peak["weekday"], "label": peak["label"]} if peak else None,
        "total_orders": sum(w["orders"] for w in weekdays),
        "total_revenue": round(sum(w["revenue"] for w in weekdays), 2),
        "last_synced_at": last_sync_row.isoformat() if last_sync_row else None,
        "is_mock": USE_MOCK_DATA,
    }

    if redis_client:
        try:
            redis_client.set(cache_key, json.dumps(result), ex=300)
        except Exception as e:
            print(f"[Redis Analytics Cache] Write error: {e}")

    return result


@router.get("/weekday-orders")
async def get_weekday_orders(
    account_name: models.AccountType = Query(..., description="JV or XL"),
    weekday: int = Query(..., ge=0, le=6, description="0=Monday .. 6=Sunday"),
    weeks: Optional[int] = Query(default=12, ge=1, le=104),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Returns individual orders and position items purchased on the specified weekday
    within the chosen date range, with enriched product images and hover previews.
    """
    if from_date and to_date:
        try:
            start_date = date.fromisoformat(from_date)
            end_date = date.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        end_date = date.today()
        start_date = end_date - timedelta(weeks=weeks or 12)

    query = (
        db.query(models.OttoOrder)
        .filter(
            models.OttoOrder.account_name == account_name,
            models.OttoOrder.weekday == weekday,
            models.OttoOrder.order_calendar_date >= start_date,
            models.OttoOrder.order_calendar_date <= end_date,
        )
    )

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                models.OttoOrder.order_number.ilike(term),
                models.OttoOrder.items.any(models.OttoOrderItem.product_title.ilike(term)),
                models.OttoOrder.items.any(models.OttoOrderItem.sku.ilike(term)),
                models.OttoOrder.items.any(models.OttoOrderItem.ean.ilike(term)),
            )
        )

    orders = query.order_by(models.OttoOrder.order_date.desc()).all()

    # Collect all item keys (EAN, SKU) for image resolution
    all_keys = set()
    for o in orders:
        for it in o.items:
            if it.ean:
                all_keys.add(it.ean)
            if it.sku:
                all_keys.add(it.sku)

    image_map: dict[str, str] = {}

    # 1. Check Redis cache first
    if redis_client and all_keys:
        try:
            for k in list(all_keys):
                cached_img = redis_client.get(f"order_img:{k}")
                if cached_img:
                    image_map[k] = cached_img
        except Exception as e:
            print(f"[Redis Order Img] Read error: {e}")

    # 2. Lookup in ProductMapping cross-account (without account_name constraint)
    missing_keys = [k for k in all_keys if k not in image_map]
    if missing_keys:
        mappings = (
            db.query(models.ProductMapping.product_ean, models.ProductMapping.image_url)
            .filter(
                models.ProductMapping.product_ean.in_(missing_keys),
                models.ProductMapping.image_url.isnot(None),
                models.ProductMapping.image_url != "",
            )
            .all()
        )
        for ean, img in mappings:
            if ean and img:
                image_map[ean] = img

    # 3. Query official Otto Market API for true published product images
    still_missing = [k for k in all_keys if k not in image_map]
    if still_missing:
        token_jv = get_cached_otto_token("JV", db)
        token_xl = get_cached_otto_token("XL", db)
        headers_jv = {"Authorization": f"Bearer {token_jv}", "Accept": "application/json"} if token_jv else None
        headers_xl = {"Authorization": f"Bearer {token_xl}", "Accept": "application/json"} if token_xl else None

        if headers_jv or headers_xl:
            sem = asyncio.Semaphore(5)
            async with httpx.AsyncClient(timeout=6.0) as client:
                async def fetch_otto_img(k: str):
                    async with sem:
                        try:
                            # Primary account headers first, fallback to other account
                            acc_str = account_name.value if hasattr(account_name, "value") else str(account_name)
                            h_primary = headers_jv if acc_str == "JV" or not headers_xl else headers_xl
                            h_secondary = headers_xl if h_primary is headers_jv else headers_jv

                            r = await client.get(f"https://api.otto.market/v5/products/{k}", headers=h_primary)
                            if r.status_code == 404 and h_secondary:
                                r = await client.get(f"https://api.otto.market/v5/products/{k}", headers=h_secondary)

                            if r.status_code == 200:
                                assets = r.json().get("mediaAssets", [])
                                loc = next((a.get("location") for a in assets if a.get("location")), None)
                                if loc and str(loc).startswith("http"):
                                    return k, str(loc)
                        except Exception:
                            pass
                        return k, None

                tasks = [fetch_otto_img(k) for k in still_missing]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, tuple) and len(res) == 2 and res[1]:
                        image_map[res[0]] = res[1]

    # 4. For remaining items, search Aftercool by exact EAN/SKU (without dataset filter)
    still_missing = [k for k in all_keys if k not in image_map]
    if still_missing:
        try:
            session = await get_aftercool_session()
            sem = asyncio.Semaphore(5)
            async with httpx.AsyncClient(timeout=6.0) as client:
                async def fetch_ac_img(k: str):
                    async with sem:
                        try:
                            resp = await client.get(
                                f"{AFTERCOOL_BASE_URL}/api/products",
                                params={"q": k, "include_row": 1, "limit": 1},
                                cookies={"session": session}
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                items = data.get("items", []) if isinstance(data, dict) else (data or [])
                                if items:
                                    row = items[0].get("row") or {}
                                    img = row.get("GalleryURL") or row.get("PictureURL") or items[0].get("img")
                                    if img and str(img).startswith("http"):
                                        return k, str(img)
                        except Exception:
                            pass
                        return k, None

                tasks = [fetch_ac_img(k) for k in still_missing]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, tuple) and len(res) == 2 and res[1]:
                        image_map[res[0]] = res[1]
        except Exception as e:
            print(f"[Aftercool order img fetch error]: {e}")

    # 5. Cache newly resolved exact images in Redis
    if redis_client and image_map:
        try:
            for k, img in image_map.items():
                if img:
                    redis_client.set(f"order_img:{k}", img, ex=604800)
        except Exception as e:
            print(f"[Redis Order Img] Write error: {e}")

    orders_data = []
    total_rev = 0.0
    total_items = 0

    for o in orders:
        rev = float(o.total_revenue)
        total_rev += rev
        total_items += o.items_count

        items_data = []
        for it in o.items:
            img = image_map.get(it.ean) or image_map.get(it.sku)

            items_data.append({
                "id": it.id,
                "product_title": it.product_title,
                "price": float(it.price),
                "currency": it.currency,
                "sku": it.sku,
                "ean": it.ean,
                "article_number": it.article_number,
                "fulfillment_status": it.fulfillment_status,
                "shop_url": it.shop_url,
                "image_url": img,
            })

        orders_data.append({
            "id": o.id,
            "order_number": o.order_number,
            "order_date": o.order_date.isoformat(),
            "total_revenue": rev,
            "items_count": o.items_count,
            "items": items_data,
        })

    acc_val = account_name.value if hasattr(account_name, "value") else str(account_name)
    return {
        "weekday": weekday,
        "label": WEEKDAY_LABELS[weekday],
        "account_name": acc_val,
        "period_from": start_date.isoformat(),
        "period_to": end_date.isoformat(),
        "total_orders": len(orders_data),
        "total_items": total_items,
        "total_revenue": round(total_rev, 2),
        "orders": orders_data,
    }


@router.post("/sync")
def trigger_sync(
    account_name: Optional[models.AccountType] = Query(None, description="JV, XL, or omit for both"),
    days_back: int = Query(default=90, ge=7, le=365),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """
    Manually triggers order statistics synchronisation.
    Runs in the background so the HTTP response returns immediately.
    """
    def _run(acc: Optional[str]):
        from database import SessionLocal
        _db = SessionLocal()
        try:
            d_from = date.fromisoformat(from_date) if from_date else None
            d_to = date.fromisoformat(to_date) if to_date else None
            if acc:
                settings = (
                    _db.query(models.AccountSettings)
                    .filter(models.AccountSettings.account_name == acc)
                    .first()
                )
                sync_orders_for_account(
                    db=_db,
                    account_name=acc,
                    days_back=days_back,
                    from_date=d_from,
                    to_date=d_to,
                    client_id=getattr(settings, "otto_client_id", None) if settings else None,
                    client_secret=getattr(settings, "otto_client_secret", None) if settings else None,
                )
            else:
                sync_all_accounts(_db, days_back=days_back)

            if redis_client:
                pattern = f"otto:analytics:weekday:{acc}:*" if acc else "otto:analytics:weekday:*"
                for k in redis_client.keys(pattern):
                    redis_client.delete(k)
        except Exception as e:
            import logging
            logging.error(f"[analytics.sync] Error: {e}")
        finally:
            _db.close()

    target = account_name.value if account_name else None
    background_tasks.add_task(_run, target)

    return {
        "success": True,
        "message": f"Sync started for {target or 'JV + XL'}. Data will appear in ~5 seconds.",
    }

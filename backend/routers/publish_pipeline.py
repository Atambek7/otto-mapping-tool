from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional, Dict, Any
import logging
import os
import secrets
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database import get_db
import models
from services.distribution import distribute_products_round_robin
from services.categories_repo import get_category_by_id
from services.factory_scope import get_authoritative_factory_eans
from services.queue_reconciliation import load_observations
from scripts.reconcile_factories import reconcile_all_factories

logger = logging.getLogger("publish_pipeline")

router = APIRouter(prefix="/pipeline", tags=["Publish Pipeline"])


# ====== REQUEST SCHEMAS ======

class PipelinePreviewRequest(BaseModel):
    account_name: models.AccountType
    days: int = Field(default=7, ge=1, le=30)
    daily_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Europe/Berlin"
    product_eans: Optional[List[str]] = None
    exclude_published: bool = False
    force_refresh: bool = False
    anti_collision_enabled: bool = True
    stagger_days: int = Field(default=2, ge=1, le=30)


class PipelineCreateRequest(BaseModel):
    account_name: models.AccountType
    days: int = Field(default=7, ge=1, le=30)
    daily_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Europe/Berlin"
    executor_type: models.ExecutorType = models.ExecutorType.OTTO_API
    product_eans: Optional[List[str]] = None
    exclude_published: bool = False
    anti_collision_enabled: bool = True
    stagger_days: int = Field(default=2, ge=1, le=30)


def compute_anti_collision_min_dates(
    db: Session,
    account_name: models.AccountType,
    eans: List[str],
    stagger_days: int,
    tz_name: str
) -> Dict[str, date]:
    opp_account = models.AccountType.XL if account_name == models.AccountType.JV else models.AccountType.JV
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    stagger_td = timedelta(days=stagger_days)
    min_dates: Dict[str, date] = {}

    # 1. Check opposite account published mappings
    opp_mappings = (
        db.query(models.ProductMapping)
        .filter(
            models.ProductMapping.product_ean.in_(eans),
            models.ProductMapping.account_name == opp_account,
            models.ProductMapping.status == "Published"
        )
        .all()
    )
    for om in opp_mappings:
        dt = om.published_at or om.updated_at
        pub_d = dt.astimezone(tz).date() if dt else today
        min_d = pub_d + stagger_td
        min_dates[om.product_ean] = max(min_dates.get(om.product_ean, min_d), min_d)

    # 2. Check opposite account active queue items
    opp_queue = (
        db.query(models.PublishQueueItem)
        .filter(
            models.PublishQueueItem.product_ean.in_(eans),
            models.PublishQueueItem.account_name == opp_account,
            models.PublishQueueItem.status.in_([
                models.QueueItemStatus.PENDING,
                models.QueueItemStatus.PROCESSING,
                models.QueueItemStatus.PUBLISHED
            ])
        )
        .all()
    )
    for oq in opp_queue:
        dt = oq.published_at or oq.scheduled_date
        if dt:
            q_d = dt.astimezone(tz).date()
            min_d = q_d + stagger_td
            min_dates[oq.product_ean] = max(min_dates.get(oq.product_ean, min_d), min_d)

    return min_dates


# ====== ENDPOINTS ======

@router.post("/preview/{factory_id}")
async def preview_pipeline(
    factory_id: str,
    req: PipelinePreviewRequest,
    db: Session = Depends(get_db)
):
    """
    Dry-run preview calculating daily breakdown and category distribution,
    strictly scoped against Aftercool authoritative inventory.
    """
    # 1. Scoping
    auth_eans = None
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.account_name == req.account_name
    )

    if req.product_eans:
        # User selected specific products -> scope directly without external network call
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
    else:
        # Full factory preview -> check cache or authoritative scope
        auth_res = await get_authoritative_factory_eans(
            factory_id=factory_id,
            account=req.account_name.value if hasattr(req.account_name, "value") else str(req.account_name),
            force_refresh=req.force_refresh
        )
        if auth_res.status == "error":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Ошибка Aftercool API ({auth_res.error_message or 'не удалось получить каталог'}). Операция остановлена."
            )
        elif auth_res.status == "empty":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"В каталоге Aftercool не найдено товаров для фабрики '{factory_id}' и аккаунта '{req.account_name.value}'."
            )
        else:
            query = query.filter(models.ProductMapping.product_ean.in_(auth_res.eans))

    raw_mappings = query.all()
    if not raw_mappings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No products found for factory '{factory_id}' and account '{req.account_name.value}'."
        )

    # Calculate metrics
    already_published = sum(1 for m in raw_mappings if m.status == "Published")
    ready_products = sum(1 for m in raw_mappings if m.status == "Ready")
    incomplete_mappings = len(raw_mappings) - ready_products

    # Drip Feed distributes Ready products and Error products with category
    eligible_mappings = [m for m in raw_mappings if m.status in ("Ready", "Error") and bool(m.category_id)]
    error_products = sum(1 for m in eligible_mappings if m.status == "Error")
    
    if req.exclude_published:
        mappings = [m for m in eligible_mappings if m.status != "Published"]
    else:
        mappings = eligible_mappings

    if not mappings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Нет товаров со статусом 'Готов' или 'Ошибка' (с категорией) для фабрики '{factory_id}'."
        )

    # Get category names for preview readability
    cat_ids = {m.category_id for m in mappings if m.category_id}
    from services.categories_service import resolve_category_names
    cat_names = resolve_category_names(cat_ids, db)

    def resolve_cat_name(cid: Optional[str]) -> str:
        if not cid:
            return "Uncategorized"
        if cid in cat_names:
            return cat_names[cid]
        c_info = get_category_by_id(cid)
        if c_info and c_info.get("name"):
            return c_info["name"]
        return cid

    items = [
        {
            "product_ean": m.product_ean,
            "category_id": m.category_id or "Uncategorized",
            "category_name": resolve_cat_name(m.category_id),
            "product_name": m.product_name,
            "price": m.price,
            "image_url": m.image_url,
        }
        for m in mappings
    ]

    min_dates = {}
    if req.anti_collision_enabled:
        eans = [m.product_ean for m in mappings]
        min_dates = compute_anti_collision_min_dates(
            db=db,
            account_name=req.account_name,
            eans=eans,
            stagger_days=req.stagger_days,
            tz_name=req.timezone
        )

    distribution = distribute_products_round_robin(
        items=items,
        days=req.days,
        daily_time=req.daily_time,
        tz_name=req.timezone,
        min_dates=min_dates
    )

    # Format days summary for the UI
    days_summary = []
    stagger_delayed_total = 0
    for day_num, day_items in distribution["schedule"].items():
        scheduled_dt = day_items[0]["scheduled_date"].isoformat() if day_items else None
        cat_counts = {}
        day_stagger_count = 0
        for it in day_items:
            cname = it.get("category_name") or it.get("category_id")
            cat_counts[cname] = cat_counts.get(cname, 0) + 1
            if it.get("stagger_delayed"):
                day_stagger_count += 1
                stagger_delayed_total += 1

        days_summary.append({
            "day": day_num,
            "scheduled_date": scheduled_dt,
            "total_items": len(day_items),
            "categories": cat_counts,
            "stagger_delayed_count": day_stagger_count
        })

    return {
        "factory_id": factory_id,
        "account_name": req.account_name,
        "total_factory_products": len(auth_eans) if auth_eans else len(raw_mappings),
        "total_products": len(items),
        "already_published": already_published,
        "ready_products": ready_products,
        "error_products": error_products,
        "incomplete_mappings": incomplete_mappings,
        "days": distribution["days"],
        "daily_time": req.daily_time,
        "timezone": req.timezone,
        "anti_collision_enabled": req.anti_collision_enabled,
        "stagger_days": req.stagger_days,
        "stagger_delayed_total": stagger_delayed_total,
        "start_date": distribution["start_date"],
        "days_summary": days_summary,
        "category_breakdown": distribution["category_summary"]
    }


@router.post("/create/{factory_id}")
async def create_pipeline_plan(
    factory_id: str,
    req: PipelineCreateRequest,
    db: Session = Depends(get_db)
):
    """
    Creates a PublishPlan and populates PublishQueueItems using Interleaved Round-Robin,
    strictly scoped against Aftercool authoritative inventory.
    """
    # 1. Check if an active plan already exists
    existing_active = (
        db.query(models.PublishPlan)
        .filter(
            models.PublishPlan.factory_id == factory_id,
            models.PublishPlan.account_name == req.account_name,
            models.PublishPlan.status.in_([models.PlanStatus.ACTIVE, models.PlanStatus.PAUSED])
        )
        .first()
    )
    if existing_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An active publish plan (ID {existing_active.id}) already exists for factory '{factory_id}' and account '{req.account_name.value}'."
        )

    # 2. Scoping
    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.account_name == req.account_name
    )

    if req.product_eans:
        # User selected specific products -> scope directly without external network call
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))
    else:
        auth_res = await get_authoritative_factory_eans(
            factory_id=factory_id,
            account=req.account_name.value if hasattr(req.account_name, "value") else str(req.account_name)
        )
        if auth_res.status == "error":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Ошибка Aftercool API ({auth_res.error_message or 'не удалось получить каталог'}). Создание плана остановлено."
            )
        elif auth_res.status == "empty":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"В каталоге Aftercool не найдено товаров для фабрики '{factory_id}' и аккаунта '{req.account_name.value}'."
            )
        else:
            query = query.filter(models.ProductMapping.product_ean.in_(auth_res.eans))

    if req.exclude_published:
        query = query.filter(models.ProductMapping.status != "Published")

    mappings = query.all()
    if not mappings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No products found for factory '{factory_id}' and account '{req.account_name.value}'."
        )

    # Filter strictly eligible products (Ready or Error with category)
    eligible_mappings = [m for m in mappings if m.status in ("Ready", "Error") and bool(m.category_id)]
    if not eligible_mappings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ни один из выбранных товаров не готов к публикации на Otto (статус должен быть 'Готов' или 'Ошибка' с выбранной категорией)."
        )
    mappings = eligible_mappings

    # Auto-heal factory_id on matched records in-memory (committed atomically later with plan)
    for m in mappings:
        if m.factory_id != factory_id:
            m.factory_id = factory_id

    from services.validator import get_missing_required_attributes
    all_eans = [m.product_ean for m in mappings]
    all_attrs = db.query(models.ProductAttribute).filter(
        models.ProductAttribute.product_ean.in_(all_eans),
        models.ProductAttribute.account_name == req.account_name
    ).all()
    attrs_by_ean = {}
    for a in all_attrs:
        attrs_by_ean.setdefault(a.product_ean, {})[a.attribute_name] = a.attribute_value

    items = []
    for m in mappings:
        m_attrs = attrs_by_ean.get(m.product_ean, {})
        missing = get_missing_required_attributes(m.category_id, m_attrs)
        # Update missing_attributes on the mapping while we are here
        m.missing_attributes = missing
        
        items.append({
            "product_ean": m.product_ean,
            "category_id": m.category_id or "Uncategorized",
            "missing_attributes": missing
        })

    min_dates = {}
    if req.anti_collision_enabled:
        eans = [m.product_ean for m in mappings]
        min_dates = compute_anti_collision_min_dates(
            db=db,
            account_name=req.account_name,
            eans=eans,
            stagger_days=req.stagger_days,
            tz_name=req.timezone
        )

    # 3. Calculate distribution
    distribution = distribute_products_round_robin(
        items=items,
        days=req.days,
        daily_time=req.daily_time,
        tz_name=req.timezone,
        min_dates=min_dates
    )

    # 4. Create PublishPlan record
    plan = models.PublishPlan(
        factory_id=factory_id,
        factory_name=factory_id,  # factory_id is the canonical name; product_name would be a product title
        account_name=req.account_name,
        total_products=len(items),
        total_days=distribution["days"],
        current_day=1,
        daily_time=req.daily_time,
        status=models.PlanStatus.ACTIVE,
        executor_type=req.executor_type
    )
    db.add(plan)
    db.flush()

    # 5. Populate PublishQueueItem records
    queue_items = []
    for day_num, day_items in distribution["schedule"].items():
        for itm in day_items:
            q_item = models.PublishQueueItem(
                plan_id=plan.id,
                product_ean=itm["product_ean"],
                account_name=req.account_name,
                category_id=itm["category_id"],
                scheduled_day=itm["scheduled_day"],
                scheduled_date=itm["scheduled_date"],
                status=models.QueueItemStatus.PENDING,
                missing_attributes=itm["missing_attributes"]
            )
            queue_items.append(q_item)

    db.add_all(queue_items)
    db.commit()
    db.refresh(plan)

    # Immediately trigger publish tick if Day 1 is due right now
    now_utc = datetime.now(timezone.utc)
    has_due_items = any(
        itm.status == models.QueueItemStatus.PENDING and itm.scheduled_date <= now_utc
        for itm in queue_items
    )
    if has_due_items:
        try:
            from tasks import process_scheduled_publish_tick
            process_scheduled_publish_tick.apply_async()
            logger.info(f"Triggered immediate publish tick for plan {plan.id} (Day 1 is due)")
        except Exception as e:
            logger.warning(f"Could not trigger immediate tick for plan {plan.id}: {e}")

    return {
        "success": True,
        "plan_id": plan.id,
        "factory_id": plan.factory_id,
        "account_name": plan.account_name,
        "total_products": plan.total_products,
        "total_days": plan.total_days,
        "status": plan.status,
        "daily_time": plan.daily_time,
        "start_date": distribution["start_date"]
    }


@router.get("/drip-factories")
def list_drip_factories(
    account_name: models.AccountType = Query(...),
    db: Session = Depends(get_db),
):
    """List factories with stored evidence of an OTTO Drip Feed submission.

    Some older failures have no retained submission evidence and are not counted.
    """
    from sqlalchemy import or_
    evidence = or_(models.PublishQueueItem.otto_submission_attempted_at.isnot(None),
                   (models.PublishQueueItem.otto_task_href.isnot(None)) &
                   (models.PublishQueueItem.otto_task_href != ""),
                   models.PublishQueueItem.published_at.isnot(None))
    rows = (db.query(models.PublishPlan.factory_id,
                     func.max(models.PublishPlan.factory_name).label('factory_name'),
                     func.count(func.distinct(models.PublishQueueItem.id)).label('submission_items'),
                     func.max(models.PublishQueueItem.otto_submission_attempted_at).label('last_attempt_at'))
            .join(models.PublishQueueItem, models.PublishQueueItem.plan_id == models.PublishPlan.id)
            .filter(models.PublishPlan.account_name == account_name,
                    models.PublishQueueItem.account_name == account_name,
                    models.PublishPlan.executor_type == models.ExecutorType.OTTO_API,
                    evidence)
            .group_by(models.PublishPlan.factory_id)
            .order_by(models.PublishPlan.factory_id).all())
    availability_rows = (db.query(
        models.InitialAvailabilityState.factory_id,
        func.count(models.InitialAvailabilityState.id).label("total"),
        func.sum(case((
            (models.InitialAvailabilityState.stock_state == "confirmed") &
            (models.InitialAvailabilityState.delivery_state == "confirmed"), 1
        ), else_=0)).label("confirmed"),
        func.sum(case((
            models.InitialAvailabilityState.stock_state.in_(["pending", "retryable_failed"]) |
            models.InitialAvailabilityState.delivery_state.in_(["pending", "retryable_failed"]), 1
        ), else_=0)).label("pending"),
        func.sum(case((
            models.InitialAvailabilityState.stock_state.in_(["sending", "failed", "unknown", "accepted"]) |
            models.InitialAvailabilityState.delivery_state.in_(["sending", "failed", "unknown", "accepted"]), 1
        ), else_=0)).label("needs_review"),
    ).filter(models.InitialAvailabilityState.account_name == account_name.value)
      .group_by(models.InitialAvailabilityState.factory_id).all())
    availability_by_factory = {
        row.factory_id: {"total": int(row.total or 0), "confirmed": int(row.confirmed or 0),
                         "pending": int(row.pending or 0), "needs_review": int(row.needs_review or 0)}
        for row in availability_rows
    }
    factories = [{
        'id': row.factory_id,
        'name': row.factory_name or row.factory_id,
        'submission_items': int(row.submission_items or 0),
        'last_attempt_at': row.last_attempt_at.isoformat() if row.last_attempt_at else None,
        'initial_availability': availability_by_factory.get(
            row.factory_id, {"total": 0, "confirmed": 0, "pending": 0, "needs_review": 0}
        ),
    } for row in rows]
    return {'account': account_name.value, 'count': len(factories),
            'factories': factories, 'history_complete': False,
            'evidence_note': 'Stored attempt markers, task links, or queue publication timestamps; this is not proof of current ONLINE status.'}


@router.get("/plans")
def list_pipeline_plans(
    account_name: models.AccountType = Query(..., description="Target account: JV or XL"),
    factory_id: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    List publish plans for the specified account with progress metrics.
    """
    query = db.query(models.PublishPlan).filter(
        models.PublishPlan.account_name == account_name
    )
    if factory_id:
        query = query.filter(models.PublishPlan.factory_id == factory_id)

    plans = query.order_by(models.PublishPlan.created_at.desc()).all()
    if not plans:
        return []

    plan_ids = [p.id for p in plans]

    # Single aggregated query instead of 3 COUNT queries per plan (avoids N+1)
    stats_rows = (
        db.query(
            models.PublishQueueItem.plan_id,
            func.sum(
                case((models.PublishQueueItem.status == models.QueueItemStatus.PUBLISHED, 1), else_=0)
            ).label("published"),
            func.sum(
                case((models.PublishQueueItem.status == models.QueueItemStatus.PROCESSING, 1), else_=0)
            ).label("processing"),
            func.sum(
                case((models.PublishQueueItem.status == models.QueueItemStatus.FAILED, 1), else_=0)
            ).label("failed"),
            func.sum(
                case((models.PublishQueueItem.status == models.QueueItemStatus.PENDING, 1), else_=0)
            ).label("pending"),
        )
        .filter(models.PublishQueueItem.plan_id.in_(plan_ids))
        .group_by(models.PublishQueueItem.plan_id)
        .all()
    )
    stats_by_plan: Dict[int, Dict[str, int]] = {
        row.plan_id: {
            "published": int(row.published),
            "processing": int(row.processing),
            "failed": int(row.failed),
            "pending": int(row.pending)
        }
        for row in stats_rows
    }

    # The stored queue status remains the result of the submission attempt. For
    # the live progress projection, subtract only failed items whose exact
    # attempt has a matching, account-scoped read-only OTTO existence check.
    active_plan_ids = [
        p.id for p in plans
        if p.status in (models.PlanStatus.ACTIVE, models.PlanStatus.PAUSED)
    ]
    existing_by_plan: Dict[int, int] = {}
    if active_plan_ids:
        failed_items = (
            db.query(models.PublishQueueItem)
            .filter(
                models.PublishQueueItem.plan_id.in_(active_plan_ids),
                models.PublishQueueItem.account_name == account_name,
                models.PublishQueueItem.status == models.QueueItemStatus.FAILED,
            )
            .order_by(models.PublishQueueItem.id.asc())
            .all()
        )
        account_value = account_name.value if hasattr(account_name, "value") else str(account_name)
        observations = load_observations(account_value, failed_items)
        for item in failed_items:
            if item.id in observations:
                existing_by_plan[item.plan_id] = existing_by_plan.get(item.plan_id, 0) + 1

    result = []
    for p in plans:
        s = stats_by_plan.get(p.id, {"published": 0, "processing": 0, "failed": 0, "pending": 0})
        published_cnt = s["published"]
        processing_cnt = s["processing"]
        submission_failed_cnt = s["failed"]
        existing_cnt = existing_by_plan.get(p.id, 0)
        failed_cnt = max(0, submission_failed_cnt - existing_cnt)
        pending_cnt = s["pending"]

        result.append({
            "id": p.id,
            "factory_id": p.factory_id,
            "factory_name": p.factory_name,
            "account_name": p.account_name,
            "total_products": p.total_products,
            "total_days": p.total_days,
            "current_day": p.current_day,
            "daily_time": p.daily_time,
            "status": p.status,
            "executor_type": p.executor_type,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "progress": {
                "published": published_cnt,
                "processing": processing_cnt,
                "failed": failed_cnt,
                "submission_failed": submission_failed_cnt,
                "existing": existing_cnt,
                "pending": pending_cnt,
                "percentage": round((published_cnt / p.total_products * 100), 1) if p.total_products else 0
            }
        })

    return result


@router.get("/plan/{plan_id}")
def get_plan_details(
    plan_id: int,
    db: Session = Depends(get_db)
):
    """
    Returns full details of a specific plan, including daily breakdown and item statuses.
    """
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")

    items = (
        db.query(models.PublishQueueItem)
        .filter(models.PublishQueueItem.plan_id == plan_id)
        .order_by(models.PublishQueueItem.scheduled_day.asc())
        .all()
    )

    days_breakdown: Dict[int, Dict[str, Any]] = {}
    for it in items:
        day = it.scheduled_day
        if day not in days_breakdown:
            days_breakdown[day] = {
                "day": day,
                "scheduled_date": it.scheduled_date.isoformat() if it.scheduled_date else None,
                "total": 0,
                "published": 0,
                "failed": 0,
                "pending": 0,
            }
        days_breakdown[day]["total"] += 1
        st_val = it.status.value if hasattr(it.status, "value") else str(it.status)
        if st_val in days_breakdown[day]:
            days_breakdown[day][st_val] += 1

    return {
        "id": plan.id,
        "factory_id": plan.factory_id,
        "account_name": plan.account_name,
        "total_products": plan.total_products,
        "total_days": plan.total_days,
        "current_day": plan.current_day,
        "daily_time": plan.daily_time,
        "status": plan.status,
        "executor_type": plan.executor_type,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "days": list(days_breakdown.values())
    }


@router.get("/plan/{plan_id}/items")
def get_plan_items(
    plan_id: int,
    status: Optional[str] = Query(None, description="Filter by status: pending, published, failed"),
    db: Session = Depends(get_db)
):
    """
    Returns full list of items/products in a publish plan, enriched with product details.
    """
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")

    query = db.query(models.PublishQueueItem).filter(models.PublishQueueItem.plan_id == plan_id)
    if status:
        query = query.filter(models.PublishQueueItem.status == status)

    queue_items = query.order_by(models.PublishQueueItem.scheduled_day.asc(), models.PublishQueueItem.id.asc()).all()

    eans = [it.product_ean for it in queue_items]
    mappings = {}
    if eans:
        mapping_rows = db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(eans),
            models.ProductMapping.account_name == plan.account_name
        ).all()
        mappings = {m.product_ean: m for m in mapping_rows}

    # Resolve category names
    cat_ids = {it.category_id for it in queue_items if it.category_id}
    from services.categories_service import resolve_category_names
    cat_names = resolve_category_names(cat_ids, db)

    account = plan.account_name.value if hasattr(plan.account_name, "value") else str(plan.account_name)
    observations = load_observations(account, queue_items)
    items_result = []
    for it in queue_items:
        m = mappings.get(it.product_ean)
        cat_display = cat_names.get(it.category_id, it.category_id)
        prod_title = m.product_name if (m and m.product_name and m.product_name.strip() not in ("Без названия", "None", "null")) else None
        prod_price = (m.price or m.price_af) if m else None
        items_result.append({
            "id": it.id,
            "product_ean": it.product_ean,
            "product_name": prod_title or "Без названия",
            "image_url": m.image_url if m else None,
            "price": prod_price,
            "category_id": it.category_id,
            "category_name": cat_display,
            "scheduled_day": it.scheduled_day,
            "scheduled_date": it.scheduled_date.isoformat() if it.scheduled_date else None,
            "status": it.status.value if hasattr(it.status, "value") else str(it.status),
            "attempts": it.attempts,
            "otto_product_exists": True if it.id in observations else None,
            "otto_checked_at": observations.get(it.id, {}).get("checked_at"),
            "error_message": it.error_message,
            "missing_attributes": it.missing_attributes or [],
            "published_at": it.published_at.isoformat() if it.published_at else None,
        })

    return {
        "plan_id": plan.id,
        "factory_id": plan.factory_id,
        "factory_name": plan.factory_name,
        "account_name": plan.account_name,
        "total_items": len(items_result),
        "items": items_result
    }


@router.post("/plan/{plan_id}/retry-failed")
def retry_failed_items(plan_id: int, db: Session = Depends(get_db)):
    """
    Resets failed items back to pending and reactivates the plan if needed.
    """
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")

    failed_items = (
        db.query(models.PublishQueueItem)
        .filter(
            models.PublishQueueItem.plan_id == plan_id,
            models.PublishQueueItem.status == models.QueueItemStatus.FAILED
        )
        .all()
    )
    account = plan.account_name.value if hasattr(plan.account_name, "value") else str(plan.account_name)
    try:
        observations = load_observations(account, failed_items, strict=True)
    except Exception:
        raise HTTPException(status_code=503, detail="Не удалось проверить сохранённую сверку OTTO. Повторная отправка остановлена.")
    # Existing cards must not be resent by a blanket retry of historical failures.
    failed_items = [item for item in failed_items if item.id not in observations]
    if not failed_items:
        return {"success": True, "message": "Нет ошибок для повторной отправки; найденные карточки OTTO пропущены.", "retried_count": 0}

    now_utc = datetime.now(timezone.utc)
    for it in failed_items:
        it.status = models.QueueItemStatus.PENDING
        it.error_message = None
        if it.scheduled_date <= now_utc:
            it.scheduled_date = now_utc

    if plan.status in (models.PlanStatus.COMPLETED, models.PlanStatus.PAUSED):
        plan.status = models.PlanStatus.ACTIVE

    db.commit()
    return {"success": True, "retried_count": len(failed_items)}


@router.post("/plan/{plan_id}/pause")
def pause_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")
    if plan.status != models.PlanStatus.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Cannot pause plan in status '{plan.status}'.")

    plan.status = models.PlanStatus.PAUSED
    db.commit()
    return {"success": True, "plan_id": plan.id, "status": plan.status}


@router.post("/plan/{plan_id}/resume")
def resume_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")
    if plan.status != models.PlanStatus.PAUSED:
        raise HTTPException(status_code=400, detail=f"Cannot resume plan in status '{plan.status}'.")

    plan.status = models.PlanStatus.ACTIVE
    db.commit()
    return {"success": True, "plan_id": plan.id, "status": plan.status}


@router.post("/plan/{plan_id}/cancel")
def cancel_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")

    plan.status = models.PlanStatus.CANCELLED
    db.commit()
    return {"success": True, "plan_id": plan.id, "status": plan.status}


@router.post("/plan/{plan_id}/trigger-now")
def trigger_plan_now(plan_id: int, db: Session = Depends(get_db)):
    """
    Immediately runs publish dispatch for pending items up to today without waiting for beat tick.
    Dispatches asynchronously via Celery worker so the UI never blocks.
    """
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")

    from tasks import process_scheduled_publish_tick
    task = process_scheduled_publish_tick.apply_async(kwargs={"target_plan_id": plan_id})
    return {"success": True, "task_id": task.id, "status": "queued"}


@router.post("/reconcile/{factory_id}")
async def reconcile_factory_endpoint(factory_id: str):
    """
    Reconciles the specified factory against Aftercool's authoritative product inventory.
    """
    res = await reconcile_all_factories(target_factory_id=factory_id)
    return res


@router.post("/reconcile-all")
async def reconcile_all_factories_endpoint():
    """
    Reconciles all factories against Aftercool's authoritative product inventory.
    """
    res = await reconcile_all_factories()
    return res


class RetryAvailabilityRequest(BaseModel):
    eans: Optional[List[str]] = None
    plan_id: Optional[int] = None
    account_name: Optional[models.AccountType] = None
    reason: str = Field(min_length=5, max_length=1000)
    evidence_ref: str = Field(min_length=1, max_length=512)


class AvailabilityReviewRequest(BaseModel):
    account_name: models.AccountType
    operation: str = Field(pattern=r"^(stock|delivery)$")
    outcome: str = Field(pattern=r"^(APPLIED|NOT_APPLIED|UNRESOLVED)$")
    reason: str = Field(min_length=5, max_length=1000)
    evidence_ref: str = Field(min_length=1, max_length=512)


class DeliveryPayloadCorrectionRequest(BaseModel):
    account_name: models.AccountType
    shipping_profile_id: str = Field(min_length=36, max_length=36)
    processing_time: str = Field(pattern=r"^(37|56)$")
    reason: str = Field(min_length=5, max_length=1000)
    evidence_ref: str = Field(min_length=1, max_length=512)


def require_availability_reviewer(token: Optional[str]) -> str:
    expected = os.environ.get("AVAILABILITY_REVIEW_TOKEN", "").strip()
    actor = os.environ.get("AVAILABILITY_REVIEWER_ID", "").strip()
    if not expected or not actor:
        raise HTTPException(status_code=503, detail="Availability review is not configured.")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid reviewer token.")
    return actor


@router.post("/retry-availability")
def retry_availability_endpoint(
    req: RetryAvailabilityRequest,
    x_availability_review_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Authorize a retry only for an explicitly rejected step after correction."""
    actor = require_availability_reviewer(x_availability_review_token)

    if req.plan_id is not None and req.eans:
        raise HTTPException(status_code=400, detail="Provide either plan_id or eans, not both.")
    if not req.plan_id and not req.eans:
        raise HTTPException(status_code=400, detail="Either plan_id or eans must be provided.")
    if req.plan_id:
        plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == req.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail=f"Plan {req.plan_id} not found.")
        account = plan.account_name
        if req.account_name and req.account_name != account:
            raise HTTPException(status_code=404, detail="Plan not found in the requested account.")
        query = db.query(models.InitialAvailabilityState).filter_by(
            plan_id=plan.id, account_name=account.value
        )
    else:
        if not req.account_name:
            raise HTTPException(status_code=400, detail="account_name is required with EANs.")
        account = req.account_name
        query = db.query(models.InitialAvailabilityState).filter(
            models.InitialAvailabilityState.account_name == account.value,
            models.InitialAvailabilityState.product_ean.in_(req.eans or []),
        )

    states = query.with_for_update().all()
    now = datetime.now(timezone.utc)
    retried = []
    for state in states:
        changed = False
        for operation in ("stock", "delivery"):
            state_name = f"{operation}_state"
            if getattr(state, state_name) != "failed":
                continue
            setattr(state, state_name, "pending")
            setattr(state, f"{operation}_next_attempt_at", None)
            setattr(state, f"{operation}_last_error", None)
            db.add(models.InitialAvailabilityReviewEvent(
                state_id=state.id, account_name=state.account_name,
                product_ean=state.product_ean, operation=operation,
                outcome="RETRY_AUTHORIZED", actor_id=actor,
                reason=req.reason.strip(), evidence_ref=req.evidence_ref.strip(),
            ))
            changed = True
        if changed:
            state.manual_retry_count += 1
            state.next_attempt_trigger_kind = "manual"
            state.next_dispatch_at = now
            state.dispatch_lease_until = None
            retried.append(state.id)
    db.commit()

    dispatched = 0
    if retried:
        try:
            from tasks import dispatch_pending_initial_availability
            dispatched = dispatch_pending_initial_availability(limit=min(500, len(retried)))
        except Exception:
            logger.exception("Manual availability retry remains in the database outbox")
    return {"success": True, "account_name": account.value,
            "authorized_state_ids": retried, "authorized_count": len(retried),
            "dispatched_count": dispatched,
            "skipped_count": len(states) - len(retried)}


@router.get("/initial-availability")
def list_initial_availability(
    account_name: models.AccountType = Query(...),
    factory_id: Optional[str] = Query(None),
    plan_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(models.InitialAvailabilityState).filter_by(account_name=account_name.value)
    if factory_id:
        query = query.filter(models.InitialAvailabilityState.factory_id == factory_id)
    if plan_id:
        query = query.filter(models.InitialAvailabilityState.plan_id == plan_id)
    rows = query.order_by(models.InitialAvailabilityState.updated_at.desc())\
        .offset(offset).limit(limit).all()
    return {
        "account": account_name.value,
        "items": [{
            "id": row.id, "ean": row.product_ean, "plan_id": row.plan_id,
            "factory_id": row.factory_id, "factory_name": row.factory_name,
            "stock_state": row.stock_state, "delivery_state": row.delivery_state,
            "stock_attempts": row.stock_attempts, "delivery_attempts": row.delivery_attempts,
            "stock_last_error": row.stock_last_error,
            "delivery_last_error": row.delivery_last_error,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        } for row in rows],
        "limit": limit, "offset": offset,
    }


@router.get("/initial-availability/{state_id}/history")
def initial_availability_history(
    state_id: int,
    account_name: models.AccountType = Query(...),
    db: Session = Depends(get_db),
):
    state = db.query(models.InitialAvailabilityState).filter_by(
        id=state_id, account_name=account_name.value
    ).first()
    if not state:
        raise HTTPException(status_code=404, detail="Availability state not found.")
    attempts = db.query(models.InitialAvailabilityAttempt).filter_by(state_id=state_id)\
        .order_by(models.InitialAvailabilityAttempt.id).all()
    reviews = db.query(models.InitialAvailabilityReviewEvent).filter_by(state_id=state_id)\
        .order_by(models.InitialAvailabilityReviewEvent.id).all()
    return {
        "account": state.account_name, "ean": state.product_ean,
        "attempts": [{"operation": row.operation, "attempt": row.attempt_number,
                      "trigger": row.trigger_kind, "outcome": row.outcome,
                      "status_code": row.status_code, "error": row.error_summary,
                      "started_at": row.started_at.isoformat() if row.started_at else None,
                      "completed_at": row.completed_at.isoformat() if row.completed_at else None}
                     for row in attempts],
        "reviews": [{"operation": row.operation, "outcome": row.outcome,
                     "actor_id": row.actor_id, "reason": row.reason,
                     "evidence_ref": row.evidence_ref,
                     "payload_hash": row.payload_hash,
                     "created_at": row.created_at.isoformat() if row.created_at else None}
                    for row in reviews],
    }


@router.post("/initial-availability/{state_id}/delivery-correction")
def correct_initial_delivery_payload(
    state_id: int,
    req: DeliveryPayloadCorrectionRequest,
    x_availability_review_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    actor = require_availability_reviewer(x_availability_review_token)
    state = db.query(models.InitialAvailabilityState).filter_by(
        id=state_id, account_name=req.account_name.value
    ).with_for_update().first()
    if not state:
        raise HTTPException(status_code=404, detail="Availability state not found.")
    if state.delivery_state != "failed":
        raise HTTPException(status_code=409, detail="Delivery payload can be corrected only after a confirmed rejection or reviewed NOT_APPLIED outcome.")

    from services.otto_availability import OTTO_SHIPPING_PROFILES
    profile_key = "CHINA_8_12_WEEKS" if req.processing_time == "56" else "EUROPE_4_8_WEEKS"
    allowed_profile = OTTO_SHIPPING_PROFILES[req.account_name.value][profile_key]
    if not secrets.compare_digest(req.shipping_profile_id, allowed_profile):
        raise HTTPException(status_code=422, detail="Shipping profile does not match the selected account and processing-time policy.")

    payload = {"sku": state.product_ean, "shippingProfileId": req.shipping_profile_id,
               "processingTime": req.processing_time}
    from services.initial_availability import _payload_hash
    state.delivery_payload_json = payload
    state.delivery_state = "pending"
    state.delivery_last_error = None
    state.delivery_next_attempt_at = None
    state.manual_retry_count += 1
    state.next_attempt_trigger_kind = "manual"
    state.next_dispatch_at = datetime.now(timezone.utc)
    state.dispatch_lease_until = None
    db.add(models.InitialAvailabilityReviewEvent(
        state_id=state.id, account_name=state.account_name,
        product_ean=state.product_ean, operation="delivery",
        outcome="PAYLOAD_CORRECTED", actor_id=actor,
        reason=req.reason.strip(), evidence_ref=req.evidence_ref.strip(),
        payload_hash=_payload_hash(payload),
    ))
    db.commit()
    dispatched = 0
    try:
        from tasks import dispatch_pending_initial_availability
        dispatched = dispatch_pending_initial_availability(limit=1)
    except Exception:
        logger.exception("Corrected initial delivery payload remains in the database outbox")
    return {"success": True, "state_id": state.id, "delivery_state": state.delivery_state,
            "dispatched_count": dispatched}


@router.post("/initial-availability/{state_id}/review")
def review_initial_availability(
    state_id: int,
    req: AvailabilityReviewRequest,
    x_availability_review_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    actor = require_availability_reviewer(x_availability_review_token)
    state = db.query(models.InitialAvailabilityState).filter_by(
        id=state_id, account_name=req.account_name.value
    ).with_for_update().first()
    if not state:
        raise HTTPException(status_code=404, detail="Availability state not found.")
    current = getattr(state, f"{req.operation}_state")
    if current not in {"unknown", "accepted", "sending"}:
        raise HTTPException(status_code=409, detail=f"State {current} does not require outcome reconciliation.")

    if req.outcome == "APPLIED":
        new_state = "confirmed"
        last_error = None
    elif req.outcome == "NOT_APPLIED":
        new_state = "failed"
        last_error = "Operator reviewed evidence and confirmed the operation was not applied."
    else:
        new_state = "accepted" if current == "accepted" else "unknown"
        last_error = "Outcome remains unresolved after operator review."
    setattr(state, f"{req.operation}_state", new_state)
    setattr(state, f"{req.operation}_last_error", last_error)
    setattr(state, f"{req.operation}_next_attempt_at", None)
    state.dispatch_lease_until = None
    state.next_dispatch_at = None
    db.add(models.InitialAvailabilityReviewEvent(
        state_id=state.id, account_name=state.account_name,
        product_ean=state.product_ean, operation=req.operation,
        outcome=req.outcome, actor_id=actor, reason=req.reason.strip(),
        evidence_ref=req.evidence_ref.strip(),
    ))
    db.commit()
    return {"success": True, "state_id": state.id, "operation": req.operation,
            "state": new_state}


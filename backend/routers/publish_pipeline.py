from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from database import get_db
import models
from services.distribution import distribute_products_round_robin
from services.categories_repo import get_category_by_id
from services.factory_scope import get_authoritative_factory_eans
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


class PipelineCreateRequest(BaseModel):
    account_name: models.AccountType
    days: int = Field(default=7, ge=1, le=30)
    daily_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Europe/Berlin"
    executor_type: models.ExecutorType = models.ExecutorType.OTTO_API
    product_eans: Optional[List[str]] = None
    exclude_published: bool = False


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
    # 1. Authoritative factory scoping
    auth_eans = await get_authoritative_factory_eans(
        factory_id=factory_id,
        account=req.account_name.value if hasattr(req.account_name, "value") else str(req.account_name),
        force_refresh=req.force_refresh
    )

    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.account_name == req.account_name
    )

    if auth_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(auth_eans))
    else:
        logger.warning(f"Authoritative EANs empty for factory {factory_id}, falling back to DB factory_id filter.")
        query = query.filter(models.ProductMapping.factory_id == factory_id)

    if req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))

    raw_mappings = query.all()
    if not raw_mappings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No products found for factory '{factory_id}' and account '{req.account_name.value}'."
        )

    # Auto-heal factory_id on matched records if needed
    auto_healed = 0
    for m in raw_mappings:
        if m.factory_id != factory_id:
            m.factory_id = factory_id
            auto_healed += 1
    if auto_healed > 0:
        db.commit()

    # Calculate metrics
    already_published = sum(1 for m in raw_mappings if m.status == "Published")
    ready_products = sum(1 for m in raw_mappings if m.category_id and m.category_id != "Uncategorized")
    incomplete_mappings = len(raw_mappings) - ready_products

    if req.exclude_published:
        mappings = [m for m in raw_mappings if m.status != "Published"]
    else:
        mappings = raw_mappings

    if not mappings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"All {len(raw_mappings)} products for factory '{factory_id}' are already marked as Published."
        )

    # Get category names for preview readability
    cat_ids = {m.category_id for m in mappings if m.category_id}
    cats = db.query(models.CategoryCache).filter(models.CategoryCache.id.in_(cat_ids)).all()
    cat_names = {c.id: c.name for c in cats}

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

    distribution = distribute_products_round_robin(
        items=items,
        days=req.days,
        daily_time=req.daily_time,
        tz_name=req.timezone
    )

    # Format days summary for the UI
    days_summary = []
    for day_num, day_items in distribution["schedule"].items():
        scheduled_dt = day_items[0]["scheduled_date"].isoformat() if day_items else None
        cat_counts = {}
        for it in day_items:
            cname = it.get("category_name") or it.get("category_id")
            cat_counts[cname] = cat_counts.get(cname, 0) + 1

        days_summary.append({
            "day": day_num,
            "scheduled_date": scheduled_dt,
            "total_items": len(day_items),
            "categories": cat_counts
        })

    return {
        "factory_id": factory_id,
        "account_name": req.account_name,
        "total_factory_products": len(auth_eans) if auth_eans else len(raw_mappings),
        "total_products": len(items),
        "already_published": already_published,
        "ready_products": ready_products,
        "incomplete_mappings": incomplete_mappings,
        "days": req.days,
        "daily_time": req.daily_time,
        "timezone": req.timezone,
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

    # 2. Authoritative factory scoping
    auth_eans = await get_authoritative_factory_eans(
        factory_id=factory_id,
        account=req.account_name.value if hasattr(req.account_name, "value") else str(req.account_name)
    )

    query = db.query(models.ProductMapping).filter(
        models.ProductMapping.account_name == req.account_name
    )

    if auth_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(auth_eans))
    else:
        query = query.filter(models.ProductMapping.factory_id == factory_id)

    if req.product_eans:
        query = query.filter(models.ProductMapping.product_ean.in_(req.product_eans))

    if req.exclude_published:
        query = query.filter(models.ProductMapping.status != "Published")

    mappings = query.all()
    if not mappings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No products found for factory '{factory_id}' and account '{req.account_name.value}'."
        )

    # Auto-heal factory_id on matched records if needed
    for m in mappings:
        if m.factory_id != factory_id:
            m.factory_id = factory_id
    db.commit()

    items = [
        {
            "product_ean": m.product_ean,
            "category_id": m.category_id or "Uncategorized",
        }
        for m in mappings
    ]

    # 3. Calculate distribution
    distribution = distribute_products_round_robin(
        items=items,
        days=req.days,
        daily_time=req.daily_time,
        tz_name=req.timezone
    )

    # 4. Create PublishPlan record
    plan = models.PublishPlan(
        factory_id=factory_id,
        factory_name=factory_id,  # factory_id is the canonical name; product_name would be a product title
        account_name=req.account_name,
        total_products=len(items),
        total_days=req.days,
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
                status=models.QueueItemStatus.PENDING
            )
            queue_items.append(q_item)

    db.add_all(queue_items)
    db.commit()
    db.refresh(plan)

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
        row.plan_id: {"published": int(row.published), "failed": int(row.failed), "pending": int(row.pending)}
        for row in stats_rows
    }

    result = []
    for p in plans:
        s = stats_by_plan.get(p.id, {"published": 0, "failed": 0, "pending": 0})
        published_cnt = s["published"]
        failed_cnt = s["failed"]
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
                "failed": failed_cnt,
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
    Uses .apply() so the task runs synchronously but within the Celery task context
    (enables proper error logging, state tracking).
    """
    plan = db.query(models.PublishPlan).filter(models.PublishPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found.")

    from tasks import process_scheduled_publish_tick
    result = process_scheduled_publish_tick.apply()
    return {"success": True, "task_id": result.id, "result": result.get(timeout=30)}


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


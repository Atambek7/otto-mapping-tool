import time
import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from database import get_db
import models
from routers.aftercool import redis_client

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
)

_catalog_cache = {
    "JV": 150386,
    "XL": 144177,
    "last_updated": 0
}

def get_catalog_total(account: str = "JV") -> int:
    global _catalog_cache
    acc = account.upper() if account else "JV"
    now = time.time()

    # 1. Check Redis factories cache first (sub-millisecond)
    if redis_client:
        try:
            cached_factories = redis_client.get("aftercool_factories_cache")
            if cached_factories:
                factories_data = json.loads(cached_factories)
                items = factories_data.get("items", []) if isinstance(factories_data, dict) else (factories_data or [])
                target_sum = sum(f.get("items_count", 0) for f in items if f.get("account") == acc and f.get("kind") == "lister")
                if target_sum > 0:
                    _catalog_cache[acc] = target_sum
                    _catalog_cache["last_updated"] = now
                    return target_sum
        except Exception:
            pass

    # 2. Return in-memory fallback without blocking on external HTTP
    return _catalog_cache.get(acc, 150386 if acc == "JV" else 144177)

@router.get("")
@router.get("/")
async def get_dashboard_stats(account: str = "JV", refresh: bool = False, db: Session = Depends(get_db)):
    acc = account.upper() if account else "JV"
    cache_key = f"otto:dashboard:stats:{acc}"

    if not refresh and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            print(f"[Redis Stats Cache] Read error: {e}")

    catalog_total = get_catalog_total(acc)

    stats_row = db.query(
        func.count(models.ProductMapping.id).label("imported_products"),
        func.count(case((models.ProductMapping.status == "Ready", 1))).label("ready"),
        func.count(case((models.ProductMapping.status == "Error", 1))).label("error"),
        func.count(case((models.ProductMapping.status == "Need Attributes", 1))).label("need_attributes"),
        func.count(case((
            (models.ProductMapping.category_id.is_(None)) | (models.ProductMapping.category_id == ""), 1
        ))).label("unmapped"),
        func.count(case((
            models.ProductMapping.category_id.isnot(None) &
            (models.ProductMapping.category_id != "") &
            ~models.ProductMapping.status.in_(["Ready", "Error", "Need Attributes"]), 1
        ))).label("mapped"),
    ).filter(models.ProductMapping.account_name == acc).first()

    result = {
        "total_products": catalog_total,
        "catalog_total": catalog_total,
        "imported_products": stats_row.imported_products if stats_row else 0,
        "ready": stats_row.ready if stats_row else 0,
        "error": stats_row.error if stats_row else 0,
        "need_attributes": stats_row.need_attributes if stats_row else 0,
        "mapped": stats_row.mapped if stats_row else 0,
        "unmapped": stats_row.unmapped if stats_row else 0
    }

    if redis_client:
        try:
            redis_client.set(cache_key, json.dumps(result), ex=60)
        except Exception as e:
            print(f"[Redis Stats Cache] Write error: {e}")

    return result



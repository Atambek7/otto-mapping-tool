import asyncio
import logging
from typing import Any, Dict, Optional
from celery import shared_task
from sqlalchemy.orm import Session

from database import SessionLocal
import models

logger = logging.getLogger("tasks_availability_sync")


async def fetch_aftercool_products_for_factory(
    factory_id: str,
    account: str,
    limit: int = 500,
    timeout: float = 40.0,
    target_eans: Optional[set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Read all necessary pages; never return a partial result after a failed page."""
    from services.aftercool_service import iter_products, resolve_aftercool_account

    if not factory_id:
        return {}
    source = await asyncio.to_thread(resolve_aftercool_account, factory_id, account)
    result = {}
    conflicts = set()
    signatures = {}
    async for page in iter_products(factory_id, account=source, dataset="lister",
                                    page_size=min(limit, 500), timeout=timeout):
        for product in page:
            if target_eans is None or product.ean in target_eans:
                signature = (product.price, product.price_af)
                if product.ean in signatures and signatures[product.ean] != signature:
                    conflicts.add(product.ean)
                signatures[product.ean] = signature
                result[product.ean] = product.raw_item
    for ean in conflicts:
        result[ean] = {"ean": ean, "price_conflict": True}
    return result


def run_availability_sync(
    db: Optional[Session] = None,
    batch_delay_sec: float = 1.0,
    max_items_per_call: int = 100,
    filter_account: Optional[str] = None,
    filter_factory_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Disabled compatibility entrypoint: periodic price/stock sync is retired."""
    return {
        "status": "disabled",
        "writes": 0,
        "message": "Periodic availability and price synchronization is disabled.",
    }


@shared_task(bind=True, name="tasks_availability_sync.sync_published_availability")
def sync_published_availability(
    self,
    batch_delay_sec: float = 1.0,
    max_items_per_call: int = 100,
    filter_account: Optional[str] = None,
    filter_factory_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility no-op for old queued periodic price/stock messages."""
    logger.warning("Ignoring retired periodic price/availability Celery task")
    return {"status": "disabled", "writes": 0}

import time
import asyncio
import logging
from typing import Any, Dict, Optional
from celery import shared_task
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_availability import DEFAULT_STOCK_QUANTITY, extract_stock_from_aftercool_item

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
    """Sync OTTO quantities and LOCAL Aftercool prices for Published mappings.

    Does not submit OTTO prices. Dry run performs no DB writes or OTTO calls.
    Missing/failed Aftercool reads never produce fallback inventory updates.
    """
    from services.aftercool_service import apply_aftercool_prices, parse_aftercool_product, parse_price

    own_db = db is None
    if own_db:
        db = SessionLocal()
    summary = {"dry_run": dry_run, "would_sync": 0, "total_published": 0, "factories_processed": 0, "synced_ok": 0,
               "synced_failed": 0, "skipped": 0, "price_updated": 0,
               "price_changes_detected": 0, "out_of_stock_detected": 0, "errors": [], "error_count": 0}
    def record_error(message):
        summary["error_count"] += 1
        if len(summary["errors"]) < 100:
            summary["errors"].append(message)

    max_items_per_call = min(max(1, max_items_per_call), 100)
    try:
        query = db.query(models.ProductMapping).filter(models.ProductMapping.status == "Published")
        if filter_account:
            account = filter_account.strip().upper()
            if account not in ("JV", "XL"):
                raise ValueError("Account must be JV or XL")
            query = query.filter(models.ProductMapping.account_name == account)
        if filter_factory_id:
            query = query.filter(models.ProductMapping.factory_id == str(filter_factory_id).strip())
        summary["total_published"] = query.count()
        # Iterate one factory and at most 500 mappings/Aftercool matches at a time.
        groups = query.with_entities(models.ProductMapping.account_name, models.ProductMapping.factory_id).distinct().all()
        for account, fid in groups:
            acc_name = account.value if hasattr(account, "value") else str(account)
            summary["factories_processed"] += 1
            factory_query = query.filter(models.ProductMapping.account_name == account,
                                         models.ProductMapping.factory_id == fid)
            client = None
            if not dry_run:
                settings = db.query(models.AccountSettings).filter_by(account_name=acc_name).first()
                if not settings or not settings.otto_client_id or not settings.otto_client_secret:
                    record_error(f"Missing OTTO API credentials for {acc_name}")
                    summary["synced_failed"] += factory_query.count()
                    continue
                client = OttoMarketClient(settings.otto_client_id, settings.otto_client_secret)
            last_id = 0
            while True:
                mappings = factory_query.filter(models.ProductMapping.id > last_id).order_by(models.ProductMapping.id).limit(500).all()
                if not mappings:
                    break
                last_id = mappings[-1].id
                eans = {m.product_ean for m in mappings}
                try:
                    if not fid:
                        raise ValueError("Factory ID is missing")
                    products = asyncio.run(fetch_aftercool_products_for_factory(str(fid), acc_name, target_eans=eans))
                except Exception as exc:
                    record_error(f"Aftercool {acc_name}/{fid}: {exc}")
                    summary["synced_failed"] += len(mappings)
                    # Close the read transaction before the next group/batch.
                    if not dry_run:
                        db.rollback()
                        for ean in eans:
                            db.add(models.AvailabilitySyncLog(
                                product_ean=ean, account_name=acc_name, factory_id=fid,
                                sync_result="failed", error_message=f"Aftercool read failed: {exc}",
                            ))
                        db.commit()
                    continue
                # Re-read under lock AFTER the network read so concurrent manual edits win.
                if not dry_run:
                    mappings = factory_query.filter(models.ProductMapping.product_ean.in_(eans)).populate_existing().with_for_update().all()
                payload = []
                sync_data = {}
                for mapping in mappings:
                    item = products.get(mapping.product_ean)
                    product = parse_aftercool_product({"ean": mapping.product_ean, **item}, str(fid)) if item else None
                    if item and item.get("price_conflict"):
                        summary["skipped"] += 1
                        record_error(f"Conflicting Aftercool prices: {acc_name}/{mapping.product_ean}")
                        if not dry_run:
                            db.add(models.AvailabilitySyncLog(
                                product_ean=mapping.product_ean, account_name=acc_name, factory_id=fid,
                                sync_result="skipped", error_message="Conflicting Aftercool prices; no OTTO update sent",
                            ))
                        continue
                    if not product or product.ean != mapping.product_ean:
                        summary["skipped"] += 1
                        record_error(f"Aftercool product missing: {acc_name}/{mapping.product_ean}")
                        if not dry_run:
                            db.add(models.AvailabilitySyncLog(
                                product_ean=mapping.product_ean, account_name=acc_name, factory_id=fid,
                                sync_result="skipped", error_message="Product not found in Aftercool factory; no OTTO update sent",
                            ))
                        continue
                    stock = extract_stock_from_aftercool_item(item)
                    stock = DEFAULT_STOCK_QUANTITY if stock is None else stock
                    summary["out_of_stock_detected"] += int(stock == 0)
                    old_price = mapping.price
                    changed = (not mapping.has_manual_price and product.price is not None
                               and parse_price(mapping.price) != product.price)
                    summary["price_changes_detected"] += int(changed)
                    if not dry_run:
                        apply_aftercool_prices(mapping, product.price, product.price_af)
                        summary["price_updated"] += int(changed)
                    sync_data[mapping.product_ean] = {
                        "old_price": old_price,
                        "new_price": str(product.price) if changed else old_price,
                    }
                    payload.append({"sku": mapping.product_ean, "quantity": stock})
                if dry_run:
                    summary["would_sync"] += len(payload)
                    continue
                # Release price locks before calling the external service.
                db.commit()
                for offset in range(0, len(payload), max_items_per_call):
                    chunk = payload[offset:offset + max_items_per_call]
                    try:
                        response = client.update_quantities(chunk)
                        ok = response.get("status_code") in (200, 204)
                        error = None if ok else str(response.get("error") or response.get("response") or response.get("status_code"))
                    except Exception as exc:
                        ok, error = False, str(exc)
                    summary["synced_ok" if ok else "synced_failed"] += len(chunk)
                    if not ok:
                        record_error(f"OTTO quantities {acc_name}/{fid}: {error}")
                    for item in chunk:
                        db.add(models.AvailabilitySyncLog(
                            product_ean=item["sku"], account_name=acc_name, factory_id=fid,
                            new_stock=item["quantity"], sync_result="ok" if ok else "failed",
                            error_message=error, **sync_data[item["sku"]],
                        ))
                    db.commit()
                    if batch_delay_sec > 0:
                        time.sleep(batch_delay_sec)
    except Exception as exc:
        logger.exception("Availability sync failed")
        record_error(str(exc))
        if not dry_run:
            db.rollback()
    finally:
        if own_db:
            db.close()
    return summary


@shared_task(bind=True, name="tasks_availability_sync.sync_published_availability")
def sync_published_availability(
    self,
    batch_delay_sec: float = 1.0,
    max_items_per_call: int = 100,
    filter_account: Optional[str] = None,
    filter_factory_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Periodic OTTO stock synchronization and local Aftercool price refresh.
    Does not update OTTO selling prices.
    """
    logger.info("[tasks_availability_sync.sync_published_availability] Celery task triggered.")
    return run_availability_sync(
        batch_delay_sec=batch_delay_sec,
        max_items_per_call=max_items_per_call,
        filter_account=filter_account,
        filter_factory_id=filter_factory_id,
        dry_run=False,
    )

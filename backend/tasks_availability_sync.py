import os
import time
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from celery import shared_task
from sqlalchemy.orm import Session

from database import SessionLocal
import models
from services.otto_client import OttoMarketClient
from services.otto_availability import DEFAULT_STOCK_QUANTITY, extract_stock_from_aftercool_item

logger = logging.getLogger("tasks_availability_sync")


def _is_pos(v: Any) -> bool:
    try:
        clean = str(v).replace("€", "").replace("EUR", "").replace(",", ".").strip()
        return float(clean) > 0.0001
    except (ValueError, TypeError):
        return False


async def fetch_aftercool_products_for_factory(
    factory_id: str,
    account: str,
    limit: int = 5000,
    timeout: float = 40.0,
) -> Dict[str, Dict[str, Any]]:
    """
    Fetches raw products from Aftercool for a given factory and account.
    Returns a dict mapping cleaned EAN -> product dict (including 'row').
    """
    import httpx
    from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL
    from services.aftercool_service import resolve_aftercool_account

    if not factory_id:
        return {}

    aftercool_acc = resolve_aftercool_account(str(factory_id).strip(), account)

    session = await get_aftercool_session()
    async with httpx.AsyncClient(cookies={"session": session}, timeout=timeout) as client:
        params = {
            "account": aftercool_acc,
            "factory_id": str(factory_id).strip(),
            "include_row": 1,
            "limit": limit,
            "offset": 0,
        }
        res = await client.get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
        if res.status_code in (401, 403):
            session = await get_aftercool_session(force_refresh=True)
            res = await client.get(
                f"{AFTERCOOL_BASE_URL}/api/products",
                params=params,
                cookies={"session": session},
            )

        if res.status_code != 200:
            logger.warning(
                f"[tasks_availability_sync] Aftercool API error {res.status_code} "
                f"for factory {factory_id} ({account}): {res.text[:200]}"
            )
            return {}

        data = res.json()
        items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        products_by_ean: Dict[str, Dict[str, Any]] = {}
        for it in items:
            row = it.get("row") if isinstance(it.get("row"), dict) else {}
            ean = str(it.get("ean") or row.get("EAN") or "").strip()
            if ean and ean not in ("—", "0", ""):
                products_by_ean[ean] = it
        return products_by_ean


def run_availability_sync(
    db: Optional[Session] = None,
    batch_delay_sec: float = 1.0,
    max_items_per_call: int = 100,
    filter_account: Optional[str] = None,
    filter_factory_id: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Core sync logic:
    1. Finds all Published products grouped by (account_name, factory_id).
    2. Fetches real stock and price from Aftercool.
    3. Pushes updated quantities to OTTO Availability API.
    4. Updates DB price if changed in Aftercool.
    5. Logs every action in AvailabilitySyncLog.
    """
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True

    summary = {
        "total_published": 0,
        "factories_processed": 0,
        "synced_ok": 0,
        "synced_failed": 0,
        "price_updated": 0,
        "out_of_stock_detected": 0,
        "errors": [],
    }

    try:
        # 1. Query published mappings
        query = db.query(models.ProductMapping).filter(models.ProductMapping.status == "Published")
        if filter_account:
            acc_enum = getattr(models.AccountType, filter_account.upper(), filter_account)
            query = query.filter(models.ProductMapping.account_name == acc_enum)
        if filter_factory_id:
            query = query.filter(models.ProductMapping.factory_id == str(filter_factory_id).strip())

        published_mappings = query.all()
        summary["total_published"] = len(published_mappings)
        logger.info(f"[tasks_availability_sync] Found {len(published_mappings)} Published products to inspect.")

        if not published_mappings:
            return summary

        # 2. Group by (account_name, factory_id)
        grouped_mappings: Dict[Tuple[str, str], List[models.ProductMapping]] = {}
        for m in published_mappings:
            acc_val = m.account_name.value if hasattr(m.account_name, "value") else str(m.account_name)
            fid_val = str(m.factory_id or "").strip()
            grouped_mappings.setdefault((acc_val, fid_val), []).append(m)

        for (acc_name, fid), mappings in grouped_mappings.items():
            logger.info(
                f"[tasks_availability_sync] Processing group {acc_name} / fid={fid} ({len(mappings)} items)..."
            )
            summary["factories_processed"] += 1

            # 3. Get OTTO API credentials for this account
            settings = (
                db.query(models.AccountSettings)
                .filter(models.AccountSettings.account_name == acc_name)
                .first()
            )
            if not settings or not settings.otto_client_id or not settings.otto_client_secret:
                err_msg = f"Missing OTTO API credentials for account {acc_name}"
                logger.error(f"[tasks_availability_sync] {err_msg}")
                summary["errors"].append(err_msg)
                for m in mappings:
                    log_entry = models.AvailabilitySyncLog(
                        product_ean=m.product_ean,
                        account_name=acc_name,
                        factory_id=fid,
                        sync_result="failed",
                        error_message=err_msg,
                    )
                    db.add(log_entry)
                db.commit()
                continue

            otto_client = OttoMarketClient(
                client_id=settings.otto_client_id,
                client_secret=settings.otto_client_secret,
                account_name=acc_name,
                partner_id=settings.otto_partner_id,
            )

            # 4. Fetch Aftercool products for this factory
            af_products: Dict[str, Dict[str, Any]] = {}
            if fid:
                try:
                    af_products = asyncio.run(fetch_aftercool_products_for_factory(fid, acc_name))
                except Exception as af_err:
                    logger.warning(
                        f"[tasks_availability_sync] Failed to fetch Aftercool products for {fid}: {af_err}"
                    )

            # 5. Build quantities update payload and detect price changes
            qty_payload_all: List[Dict[str, Any]] = []
            item_sync_data: Dict[str, Dict[str, Any]] = {}

            for m in mappings:
                ean = str(m.product_ean).strip()
                af_item = af_products.get(ean)

                # Real stock determination
                new_stock = DEFAULT_STOCK_QUANTITY
                if af_item:
                    extracted_stock = extract_stock_from_aftercool_item(af_item)
                    if extracted_stock is not None:
                        new_stock = extracted_stock

                if new_stock == 0:
                    summary["out_of_stock_detected"] += 1

                # Price inspection
                old_price = m.price
                new_price = None
                if af_item:
                    row = af_item.get("row") if isinstance(af_item.get("row"), dict) else {}
                    sofort = str(row.get("SofortkaufenPreis") or "").strip()
                    start = str(row.get("Startpreis") or "").strip()
                    candidate_price = sofort if _is_pos(sofort) else (start if _is_pos(start) else None)
                    if candidate_price and _is_pos(candidate_price):
                        new_price = candidate_price

                # Check if price changed
                price_changed = False
                if new_price and old_price and str(new_price).replace(",", ".") != str(old_price).replace(",", "."):
                    price_changed = True
                    m.price = new_price
                    summary["price_updated"] += 1
                    logger.info(
                        f"[tasks_availability_sync] Price update for EAN {ean} ({acc_name}): {old_price} -> {new_price}"
                    )

                item_sync_data[ean] = {
                    "mapping": m,
                    "new_stock": new_stock,
                    "old_stock": None,  # Not tracked in mapping previously
                    "old_price": old_price,
                    "new_price": new_price or old_price,
                    "price_changed": price_changed,
                }
                qty_payload_all.append({"sku": ean, "quantity": new_stock})

            if dry_run:
                logger.info(
                    f"[tasks_availability_sync] DRY-RUN: Prepared {len(qty_payload_all)} quantity items for {acc_name}/{fid}"
                )
                summary["synced_ok"] += len(qty_payload_all)
                continue

            # 6. Push quantities to OTTO API in chunks of max_items_per_call
            for i in range(0, len(qty_payload_all), max_items_per_call):
                chunk = qty_payload_all[i : i + max_items_per_call]
                logger.info(
                    f"[tasks_availability_sync] Sending batch of {len(chunk)} items to Otto Availability API ({acc_name})..."
                )
                try:
                    resp = otto_client.update_quantities(chunk)
                    status_code = resp.get("status_code", 0)
                    is_ok = status_code in (200, 204)

                    if is_ok:
                        summary["synced_ok"] += len(chunk)
                        logger.info(
                            f"[tasks_availability_sync] Batch of {len(chunk)} successfully synced to Otto ({acc_name})."
                        )
                    else:
                        summary["synced_failed"] += len(chunk)
                        logger.error(
                            f"[tasks_availability_sync] Otto API error {status_code} for batch: {resp.get('error') or resp.get('response')}"
                        )

                    for item in chunk:
                        c_ean = item["sku"]
                        data_ref = item_sync_data.get(c_ean, {})
                        log_entry = models.AvailabilitySyncLog(
                            product_ean=c_ean,
                            account_name=acc_name,
                            factory_id=fid,
                            old_stock=data_ref.get("old_stock"),
                            new_stock=item["quantity"],
                            old_price=data_ref.get("old_price"),
                            new_price=data_ref.get("new_price"),
                            sync_result="ok" if is_ok else "failed",
                            error_message=None if is_ok else str(resp.get("error") or resp.get("response")),
                        )
                        db.add(log_entry)

                except Exception as api_exc:
                    summary["synced_failed"] += len(chunk)
                    logger.error(f"[tasks_availability_sync] Exception calling update_quantities: {api_exc}")
                    for item in chunk:
                        c_ean = item["sku"]
                        data_ref = item_sync_data.get(c_ean, {})
                        log_entry = models.AvailabilitySyncLog(
                            product_ean=c_ean,
                            account_name=acc_name,
                            factory_id=fid,
                            old_stock=data_ref.get("old_stock"),
                            new_stock=item["quantity"],
                            old_price=data_ref.get("old_price"),
                            new_price=data_ref.get("new_price"),
                            sync_result="failed",
                            error_message=str(api_exc),
                        )
                        db.add(log_entry)

            db.commit()

            # Small delay between factories
            if batch_delay_sec > 0:
                time.sleep(batch_delay_sec)

    except Exception as e:
        logger.error(f"[tasks_availability_sync] Unhandled error during sync: {e}", exc_info=True)
        summary["errors"].append(str(e))
        if db:
            db.rollback()
    finally:
        if should_close_db and db:
            db.close()

    logger.info(f"[tasks_availability_sync] Completed availability sync: {summary}")
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
    Celery periodic task to keep Published products stock quantity and prices
    continuously synchronized between Aftercool and OTTO Market.
    """
    logger.info("[tasks_availability_sync.sync_published_availability] Celery task triggered.")
    return run_availability_sync(
        batch_delay_sec=batch_delay_sec,
        max_items_per_call=max_items_per_call,
        filter_account=filter_account,
        filter_factory_id=filter_factory_id,
        dry_run=False,
    )

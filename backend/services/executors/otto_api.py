from datetime import datetime, timezone
from typing import List, Dict, Any
from sqlalchemy.orm import Session
import models
from .base import BasePublishExecutor
from ..otto_client import OttoMarketClient
from ..otto_formatter import format_otto_product


class OttoApiExecutor(BasePublishExecutor):
    """
    Executes product publication directly through Otto Market v5 REST API.
    """

    def publish_batch(
        self,
        plan: models.PublishPlan,
        items: List[models.PublishQueueItem],
        db: Session
    ) -> Dict[str, Any]:
        account_name_str = plan.account_name.value if hasattr(plan.account_name, "value") else str(plan.account_name)
        
        # 1. Fetch account credentials
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == account_name_str)
            .first()
        )

        published_count = 0
        failed_count = 0
        skipped_count = 0

        if not settings or not getattr(settings, "otto_client_id", None) or not getattr(settings, "otto_client_secret", None):
            error_msg = f"Otto API credentials (client_id / client_secret) are not configured for account {account_name_str}."
            for item in items:
                item.attempts += 1
                item.error_message = error_msg
                item.status = models.QueueItemStatus.FAILED
            db.commit()
            return {
                "success": False,
                "error": error_msg,
                "total": len(items),
                "published": 0,
                "failed": len(items),
                "skipped": 0,
            }

        client = OttoMarketClient(
            client_id=settings.otto_client_id,
            client_secret=settings.otto_client_secret
        )

        # 2. Collect product mappings and attributes
        eans = [it.product_ean for it in items]
        mappings = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.product_ean.in_(eans),
                models.ProductMapping.account_name == plan.account_name
            )
            .all()
        )
        mapping_by_ean = {m.product_ean: m for m in mappings}

        category_ids = {m.category_id for m in mappings if m.category_id}
        from ..categories_service import resolve_category_names
        cat_name_by_id = resolve_category_names(category_ids, db)

        attributes = (
            db.query(models.ProductAttribute)
            .filter(models.ProductAttribute.product_ean.in_(eans))
            .all()
        )
        attrs_by_ean = {}
        for attr in attributes:
            attrs_by_ean.setdefault(attr.product_ean, {})[attr.attribute_name] = attr.attribute_value

        # 3. Format payloads and send
        import asyncio
        from ..image_processor import mirror_image_for_xl

        is_xl = account_name_str.upper() == "XL"
        mirrored_map = {}
        if is_xl:
            async def run_mirroring():
                sem = asyncio.Semaphore(25)
                async def mirror_one(m):
                    if m.image_url:
                        async with sem:
                            m_url = await mirror_image_for_xl(m.image_url, m.product_ean, "XL")
                            mirrored_map[m.product_ean] = m_url
                tasks = [mirror_one(m) for m in mappings if m.image_url]
                if tasks:
                    await asyncio.gather(*tasks)
            try:
                asyncio.run(run_mirroring())
            except Exception as e:
                import logging
                logging.warning(f"Error mirroring images for XL in OttoApiExecutor: {e}")

        payloads = []
        item_lookup = {}
        for item in items:
            m = mapping_by_ean.get(item.product_ean)
            if not m:
                item.attempts += 1
                item.status = models.QueueItemStatus.FAILED
                item.error_message = f"Product mapping not found for EAN {item.product_ean}"
                failed_count += 1
                continue

            target_img = mirrored_map.get(item.product_ean, m.image_url) if is_xl else m.image_url
            aftercool_info = {
                "name": m.product_name or "",
                "description": m.product_name or "",
                "price": m.price or "0",
                "images": [target_img] if target_img else []
            }
            mapping_dict = {"attributes": attrs_by_ean.get(item.product_ean, {})}
            cat_name = cat_name_by_id.get(m.category_id, m.category_id or "Standard")

            payload = format_otto_product(item.product_ean, aftercool_info, mapping_dict, cat_name)
            payloads.append(payload)
            item_lookup[item.product_ean] = item

        if not payloads:
            db.commit()
            return {
                "success": True,
                "total": len(items),
                "published": published_count,
                "failed": failed_count,
                "skipped": skipped_count,
            }

        # 4. Dispatch to Otto API
        try:
            res = client.publish_products(payloads)
            status_code = res.get("status_code", 500)

            if status_code in (200, 201):
                now_utc = datetime.now(timezone.utc)
                for item in item_lookup.values():
                    item.status = models.QueueItemStatus.PUBLISHED
                    item.published_at = now_utc
                    item.error_message = None
                    published_count += 1
            elif status_code == 429:
                # Rate limit hit: keep items in pending so next beat tick will retry
                for item in item_lookup.values():
                    item.attempts += 1
                    item.error_message = "Otto 429 Too Many Requests (Rate limit reached). Retrying on next tick."
                    # Keep status as PENDING
                    skipped_count += 1
            else:
                resp_text = str(res.get("response") or res.get("error") or "Unknown error")
                for item in item_lookup.values():
                    item.attempts += 1
                    item.status = models.QueueItemStatus.FAILED
                    item.error_message = f"Otto API error {status_code}: {resp_text[:500]}"
                    failed_count += 1

        except Exception as e:
            for item in item_lookup.values():
                item.attempts += 1
                item.status = models.QueueItemStatus.FAILED
                item.error_message = f"Exception during publish: {str(e)[:500]}"
                failed_count += 1

        db.commit()
        return {
            "success": published_count > 0,
            "total": len(items),
            "published": published_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }

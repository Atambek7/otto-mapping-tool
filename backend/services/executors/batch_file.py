import os
from datetime import datetime, timezone
from typing import List, Dict, Any
from sqlalchemy.orm import Session
import models
from .base import BasePublishExecutor


class BatchExportExecutor(BasePublishExecutor):
    """
    Executes staged daily exports by generating daily batch artifacts
    or preparing records for external Lister/Aftercool automated pickup.
    """

    def publish_batch(
        self,
        plan: models.PublishPlan,
        items: List[models.PublishQueueItem],
        db: Session
    ) -> Dict[str, Any]:
        now_utc = datetime.now(timezone.utc)
        published_count = 0
        failed_count = 0

        # Mark each item as successfully published into the daily batch
        for item in items:
            try:
                item.status = models.QueueItemStatus.PUBLISHED
                item.published_at = now_utc
                item.error_message = None
                published_count += 1
            except Exception as e:
                item.status = models.QueueItemStatus.FAILED
                item.error_message = str(e)
                failed_count += 1

        db.commit()

        return {
            "success": published_count > 0,
            "total": len(items),
            "published": published_count,
            "failed": failed_count,
            "skipped": 0,
        }

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from sqlalchemy.orm import Session
import models


class BasePublishExecutor(ABC):
    """
    Abstract interface for publishing batches of items.
    Decouples queue management and schedule logic from transport channel.
    """

    @abstractmethod
    def publish_batch(
        self,
        plan: models.PublishPlan,
        items: List[models.PublishQueueItem],
        db: Session
    ) -> Dict[str, Any]:
        """
        Executes delivery of queued items.
        Must update item statuses (e.g. published, failed) and timestamps.
        Returns a dict summary of results.
        """
        pass

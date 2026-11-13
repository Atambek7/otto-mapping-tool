"""Celery publisher for durable job IDs; business payload stays in PostgreSQL."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from jobs.transport import JobPublishError

EXECUTE_JOB_TASK = "ottorestart.execute_job"
DEFAULT_QUEUE = "otto-restart"


class CeleryJobTransport:
    def __init__(self, celery_app: Any, *, queue: str = DEFAULT_QUEUE) -> None:
        queue_name = str(queue or "").strip()
        if not queue_name or len(queue_name) > 128:
            raise ValueError("queue must contain 1–128 characters")
        self._app = celery_app
        self._queue = queue_name

    def publish_job(self, job_id: str) -> None:
        normalized_id = str(UUID(str(job_id)))
        try:
            from kombu.exceptions import OperationalError

            self._app.send_task(
                EXECUTE_JOB_TASK,
                kwargs={"job_id": normalized_id},
                queue=self._queue,
                serializer="json",
                ignore_result=True,
                delivery_mode=2,
            )
        except (OperationalError, OSError, TimeoutError):
            # Kombu exceptions can carry the broker URL; only expose a stable code.
            raise JobPublishError("BROKER_UNAVAILABLE") from None

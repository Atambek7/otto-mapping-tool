from celery import Celery
from celery.schedules import crontab
import os
import sys

app_dir = os.path.dirname(os.path.abspath(__file__))
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

# Create Celery app
# Default to localhost Redis
broker_url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery("otto_tasks", broker=broker_url, backend=result_backend, include=["tasks"])

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "process-scheduled-publish-every-15-mins": {
            "task": "tasks.process_scheduled_publish_tick",
            "schedule": 900.0,  # runs every 15 minutes
        },
        "sync-order-stats-daily": {
            "task": "tasks.sync_order_stats",
            "schedule": crontab(hour=3, minute=0),  # runs every day at 03:00 UTC
        },
    },
)

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

celery_app = Celery("otto_tasks", broker=broker_url, backend=result_backend, include=["tasks", "tasks_availability_sync"])

from kombu import Queue

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue="celery",
    task_queues=[
        Queue("celery"),
        Queue("otto_availability"),
    ],
    task_routes={
        "tasks.apply_otto_availability_task": {"queue": "otto_availability"},
        "tasks.recover_incomplete_availability": {"queue": "otto_availability"},
        "tasks_availability_sync.sync_published_availability": {"queue": "otto_availability"},
    },
    beat_schedule={
        "process-scheduled-publish-every-1-min": {
            "task": "tasks.process_scheduled_publish_tick",
            "schedule": 60.0,  # runs every 1 minute
        },
        "poll-otto-processing-every-2-min": {
            "task": "tasks.poll_otto_processing_items",
            "schedule": 120.0,  # runs every 2 minutes
        },
        "recover-incomplete-availability-every-15-min": {
            "task": "tasks.recover_incomplete_availability",
            "schedule": 900.0,  # runs every 15 minutes
        },
        "sync-order-stats-daily": {
            "task": "tasks.sync_order_stats",
            "schedule": crontab(hour=3, minute=0),  # runs every day at 03:00 UTC
        },
        "sync-availability-periodic": {
            "task": "tasks_availability_sync.sync_published_availability",
            "schedule": crontab(hour="*/6", minute=0),  # runs every 6 hours
        },
    },
)

"""Fail-closed Celery application factory for explicit worker invocations."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping
from urllib.parse import urlsplit

from sqlalchemy.engine import Engine

from jobs.celery_transport import DEFAULT_QUEUE, EXECUTE_JOB_TASK, CeleryJobTransport
from jobs.dispatcher import dispatch_once, job_allowlist
from jobs.service import BackgroundJob, run_job_by_id


class CeleryConfigurationError(RuntimeError):
    pass


DISPATCH_OUTBOX_TASK = "ottorestart.dispatch_outbox"
DISCOVERY_SCHEDULE_TASK = "ottorestart.discovery_schedule_tick"


def _enabled(environment: Mapping[str, str]) -> bool:
    raw = environment.get("OTTORESTART_CELERY_ENABLED", "false").strip().casefold()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off", ""}:
        return False
    raise CeleryConfigurationError("OTTORESTART_CELERY_ENABLED must be a boolean value.")


def _feature_enabled(environment: Mapping[str, str], name: str) -> bool:
    raw = environment.get(name, "false").strip().casefold()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off", ""}:
        return False
    raise CeleryConfigurationError(f"{name} must be a boolean value.")


def _manual_import_cutoff(environment: Mapping[str, str], mode: str) -> datetime | None:
    raw = environment.get("OTTORESTART_MANUAL_IMPORTS_AFTER", "").strip()
    if not raw:
        if mode == "PRODUCTION":
            raise CeleryConfigurationError(
                "OTTORESTART_MANUAL_IMPORTS_AFTER is required in PRODUCTION to keep existing jobs paused."
            )
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise CeleryConfigurationError(
            "OTTORESTART_MANUAL_IMPORTS_AFTER must be an RFC3339 UTC timestamp."
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CeleryConfigurationError(
            "OTTORESTART_MANUAL_IMPORTS_AFTER must include a UTC timezone."
        )
    return parsed.astimezone(timezone.utc)


def create_celery_app(
    environment: Mapping[str, str],
    *,
    engine_provider: Callable[[], Engine],
    executors: Mapping[str, Callable[[BackgroundJob], None]] | None = None,
    worker_id: str = "restart-celery-worker",
):
    """Build Celery only when explicitly enabled; this function never connects."""
    if not _enabled(environment):
        raise CeleryConfigurationError("Celery transport is disabled.")
    mode = environment.get("OTTORESTART_MODE", "LEGACY_READ_ONLY").strip().upper()
    if mode not in {"PRODUCTION", "TEST"}:
        raise CeleryConfigurationError("Celery transport requires PRODUCTION or TEST mode.")
    manual_imports_after = _manual_import_cutoff(environment, mode)

    broker_url = environment.get("OTTORESTART_CELERY_BROKER_URL", "").strip()
    try:
        parsed = urlsplit(broker_url)
        valid_broker = parsed.scheme in {"redis", "rediss"} and bool(parsed.hostname)
    except ValueError:
        valid_broker = False
    if not valid_broker:
        raise CeleryConfigurationError("An explicit Redis broker URL is required.")
    schedule_enabled = _feature_enabled(environment, "OTTORESTART_DISCOVERY_SCHEDULE_ENABLED")
    availability_write_enabled = _feature_enabled(environment, "OTTORESTART_AVAILABILITY_WRITE_ENABLED")
    allowed_job_types = job_allowlist(availability_write_enabled=availability_write_enabled)
    if schedule_enabled:
        if mode not in {"PRODUCTION", "TEST"}:
            raise CeleryConfigurationError("Weekly discovery requires an explicit PRODUCTION or TEST mode.")
        if not _feature_enabled(environment, "OTTORESTART_OTTO_READ_ENABLED"):
            raise CeleryConfigurationError("Weekly discovery requires the explicit OTTO read flag.")
    if not callable(engine_provider):
        raise CeleryConfigurationError("A database engine provider is required.")
    safe_worker_id = str(worker_id or "").strip()
    if not safe_worker_id or len(safe_worker_id) > 128:
        raise CeleryConfigurationError("worker_id must contain 1–128 characters.")

    try:
        from celery import Celery
    except ImportError as error:
        raise CeleryConfigurationError("Install the OttoRestart Celery dependencies first.") from error

    celery_app = Celery("ottorestart", broker=broker_url, backend=None)
    celery_app.conf.update(
        accept_content=["json"],
        task_serializer="json",
        result_serializer="json",
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_default_queue=DEFAULT_QUEUE,
        task_create_missing_queues=False,
        task_default_delivery_mode=2,
        broker_connection_timeout=2,
        broker_connection_max_retries=3,
        broker_connection_retry_on_startup=False,
        broker_transport_options={"visibility_timeout": 3600, "max_retries": 3},
        beat_schedule=(
            {
                "ottorestart-weekly-discovery-tick": {
                    "task": DISCOVERY_SCHEDULE_TASK,
                    "schedule": timedelta(minutes=1),
                }
            }
            if schedule_enabled
            else {}
        ),
    )

    executor_map = dict(executors) if executors is not None else {}
    if executors is None:
        # Submission is registered, but it is reached only through a
        # manually-started Drip schedule and its own production safety gates.
        from jobs.import_handler import create_import_executor
        from jobs.drip_handler import create_drip_release_executor
        from jobs.submission_handler import create_submission_executor
        from jobs.processing_handler import create_processing_executor
        from jobs.availability_handler import create_availability_executor

        executor_map.setdefault("import.process", create_import_executor(engine_provider))
        executor_map.setdefault("drip.release_item", create_drip_release_executor(engine_provider, environment=environment))
        executor_map.setdefault("otto.submit_product", create_submission_executor(engine_provider, environment=environment))
        processing_executor = create_processing_executor(engine_provider, environment=environment)
        executor_map.setdefault("otto.poll_product_task", processing_executor)
        executor_map.setdefault("otto.confirm_online", processing_executor)
        availability_executor = create_availability_executor(engine_provider, environment=environment)
        executor_map.setdefault("otto.availability.quantity", availability_executor)
        executor_map.setdefault("otto.availability.delivery", availability_executor)

    @celery_app.task(
        name=EXECUTE_JOB_TASK,
        ignore_result=True,
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def execute_job(job_id: str) -> None:
        run_job_by_id(
            engine_provider(),
            job_id=job_id,
            worker_id=safe_worker_id,
            executors=executor_map,
            allowed_job_types=allowed_job_types,
            created_after=manual_imports_after,
        )

    @celery_app.task(
        name=DISPATCH_OUTBOX_TASK,
        ignore_result=True,
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def dispatch_outbox() -> None:
        dispatch_once(
            engine_provider(),
            CeleryJobTransport(celery_app),
            allowed_job_types=allowed_job_types,
            created_after=manual_imports_after,
        )

    @celery_app.task(
        name=DISCOVERY_SCHEDULE_TASK,
        ignore_result=True,
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def discovery_schedule_tick() -> None:
        if not schedule_enabled:
            return
        from services.discovery_schedule_service import enqueue_due_scans

        enqueue_due_scans(engine_provider())

    return celery_app


def _load_runtime_app():
    if not _enabled(os.environ):
        return None
    if not os.environ.get("OTTORESTART_DATABASE_URL", "").strip():
        raise CeleryConfigurationError("OTTORESTART_DATABASE_URL is required for the worker.")
    from sqlalchemy import create_engine
    from migration_runner import check_schema_head

    engine = create_engine(os.environ["OTTORESTART_DATABASE_URL"], pool_pre_ping=True)
    try:
        celery_app = create_celery_app(
            os.environ,
            engine_provider=lambda: engine,
            worker_id=os.environ.get("OTTORESTART_WORKER_ID", "restart-celery-worker"),
        )
        check_schema_head(engine)
    except CeleryConfigurationError:
        engine.dispose()
        raise
    except Exception:
        engine.dispose()
        raise CeleryConfigurationError("The worker database schema is not ready.") from None
    return celery_app


# Celery CLI target. Importing this module in the default mode yields None and
# cannot fall back to AMQP, localhost Redis, or an in-memory broker.
app = _load_runtime_app()

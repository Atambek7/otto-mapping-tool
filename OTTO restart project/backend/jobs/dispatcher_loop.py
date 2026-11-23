"""Graceful one-shot outbox dispatcher loop for the explicit worker profile."""

from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Any, Mapping

from jobs.celery_app import DISPATCH_OUTBOX_TASK, app as celery_app

LOGGER = logging.getLogger("ottorestart.dispatcher")
DEFAULT_INTERVAL_SECONDS = 2.0
MIN_INTERVAL_SECONDS = 0.25
MAX_INTERVAL_SECONDS = 60.0


class DispatcherConfigurationError(ValueError):
    """Raised when the explicit dispatcher runtime is not configured safely."""


def dispatch_interval(environment: Mapping[str, str]) -> float:
    raw = environment.get("OTTORESTART_DISPATCH_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)).strip()
    try:
        interval = float(raw)
    except (TypeError, ValueError) as error:
        raise DispatcherConfigurationError("OTTORESTART_DISPATCH_INTERVAL_SECONDS is invalid.") from error
    if not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
        raise DispatcherConfigurationError("OTTORESTART_DISPATCH_INTERVAL_SECONDS is outside the allowed range.")
    return interval


def run_dispatcher(
    runtime_app: Any,
    *,
    stop_event: threading.Event,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> int:
    if not MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS:
        raise DispatcherConfigurationError("Dispatcher interval is outside the allowed range.")
    if runtime_app is None:
        raise DispatcherConfigurationError("Celery transport is disabled.")
    task = getattr(runtime_app, "tasks", {}).get(DISPATCH_OUTBOX_TASK)
    if task is None or not callable(getattr(task, "apply", None)):
        raise DispatcherConfigurationError("The outbox dispatcher task is not registered.")

    ticks = 0
    while not stop_event.is_set():
        try:
            result = task.apply()
            if callable(getattr(result, "failed", None)) and result.failed():
                LOGGER.error("DISPATCHER_TICK_FAILED")
        except Exception:
            # The task and broker may carry URLs in exception messages.
            LOGGER.error("DISPATCHER_TICK_FAILED")
        ticks += 1
        stop_event.wait(interval_seconds)
    return ticks


def main() -> None:
    interval = dispatch_interval(os.environ)
    if celery_app is None:
        raise DispatcherConfigurationError("Celery transport is disabled.")

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    LOGGER.info("OttoRestart outbox dispatcher started")
    try:
        run_dispatcher(celery_app, stop_event=stop_event, interval_seconds=interval)
    finally:
        LOGGER.info("OttoRestart outbox dispatcher stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        main()
    except DispatcherConfigurationError as error:
        LOGGER.error("DISPATCHER_CONFIGURATION_ERROR: %s", str(error))
        raise SystemExit(2) from None

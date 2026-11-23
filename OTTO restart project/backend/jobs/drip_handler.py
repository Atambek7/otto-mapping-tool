"""DB-only consumer for scheduled Drip Feed queue releases."""

from __future__ import annotations

import os
from typing import Callable, Mapping

from sqlalchemy.engine import Engine

from jobs.service import BackgroundJob, JobExecutionError
from runtime_mode import RuntimeConfig, RuntimeMode
from services.drip_service import DripError, release_scheduled_item


def create_drip_release_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
):
    """Build the explicit worker handler; no OTTO/Aftercool client is imported."""
    source = dict(os.environ if environment is None else environment)

    def execute(job: BackgroundJob) -> None:
        if job.job_type != "drip.release_item":
            raise JobExecutionError("INVALID_DRIP_JOB", retryable=False)
        payload = dict(job.payload)
        if not all(payload.get(key) for key in ("batchId", "itemId", "operationId", "revisionId", "qcResultId", "payloadHash")):
            raise JobExecutionError("INVALID_DRIP_JOB", retryable=False)
        if payload.get("scheduleGeneration") is None:
            raise JobExecutionError("INVALID_DRIP_JOB", retryable=False)
        try:
            config = RuntimeConfig.from_environment(source)
        except ValueError:
            raise JobExecutionError("INVALID_RUNTIME_CONFIGURATION", retryable=False) from None
        submit_enabled = (
            config.mode is RuntimeMode.PRODUCTION
            and config.otto_read_enabled
            and config.product_write_enabled
        )
        try:
            with engine_provider().begin() as connection:
                result = release_scheduled_item(
                    connection, account=job.account, payload=payload, submit_enabled=submit_enabled
                )
        except DripError as error:
            raise JobExecutionError(error.code, retryable=False) from None
        if result.get("code") == "NOT_DUE":
            # The DB dispatcher and worker clocks may differ slightly. Keep the
            # persistent job retryable instead of completing the release early.
            raise JobExecutionError("DRIP_ITEM_NOT_DUE", retryable=True)

    return execute

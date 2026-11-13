"""Read-only OTTO update-task and Marketplace Status poll workers."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable, Mapping

from sqlalchemy.engine import Engine

from integrations.otto_client import OttoApiError, OttoMarketClient
from jobs.service import BackgroundJob, JobExecutionError
from runtime_mode import RuntimeConfig, RuntimeMode
from services.otto_processing_service import (
    ONLINE_JOB_TYPE,
    TASK_JOB_TYPE,
    ProcessingError,
    defer_online_poll,
    defer_processing_poll,
    load_online_poll_context,
    load_processing_poll_context,
    record_online_poll_failure,
    record_online_status_response,
    record_processing_task_result,
)


def _retry_delay(value: float | None) -> int | None:
    try:
        seconds = int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(seconds, 86_400)) if seconds is not None else None


def _task_result(client: OttoMarketClient, context: Mapping[str, object]) -> tuple[str, str | None, str | None]:
    task_href = context.get("task_href")
    task_id = context.get("task_id")
    target_sku = context.get("target_sku")
    if not all(isinstance(value, str) and value for value in (task_href, task_id, target_sku)):
        return "NEEDS_REVIEW", "PROCESSING_POLL_CONTEXT_INVALID", None
    summary = client.read_update_task_href(str(task_href))
    state = summary.get("state") if isinstance(summary, Mapping) else None
    if not isinstance(state, str) or not state.strip():
        return "NEEDS_REVIEW", "OTTO_TASK_STATE_MISSING", None
    normalized_state = state.strip().casefold()
    if normalized_state != "done":
        return "POLLING", None, state.strip()

    rows_by_type: dict[str, list[dict[str, object]]] = {}
    try:
        for result_type in ("succeeded", "failed", "unchanged"):
            rows = client.get_update_task_results(str(task_id), result_type, max_pages=50)
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                return "NEEDS_REVIEW", "OTTO_TASK_RESULTS_INVALID", state.strip()
            rows_by_type[result_type] = [dict(row) for row in rows]
    except OttoApiError:
        raise
    except Exception:
        return "NEEDS_REVIEW", "OTTO_TASK_RESULTS_READ_FAILED", state.strip()

    for result_type, rows in rows_by_type.items():
        reported = summary.get(result_type)
        if reported is not None:
            if isinstance(reported, bool) or not isinstance(reported, int) or reported != len(rows):
                return "NEEDS_REVIEW", "OTTO_TASK_COUNT_MISMATCH", state.strip()
    entries: list[tuple[str, Mapping[str, object]]] = []
    for result_type, rows in rows_by_type.items():
        for row in rows:
            sku = row.get("sku")
            if not isinstance(sku, str) or sku != target_sku:
                return "NEEDS_REVIEW", "OTTO_TASK_TARGET_SKU_MISMATCH", state.strip()
            entries.append((result_type, row))
    if len(entries) != 1:
        return "NEEDS_REVIEW", "OTTO_TASK_TARGET_SKU_RESULT_MISSING_OR_DUPLICATE", state.strip()
    result_type, _row = entries[0]
    if result_type == "succeeded":
        return "SUCCEEDED", None, state.strip()
    if result_type == "failed":
        return "FAILED", "OTTO_TASK_SKU_FAILED", state.strip()
    return "NEEDS_REVIEW", "OTTO_TASK_UNCHANGED_NOT_SUCCESS_PROOF", state.strip()


def create_processing_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[str, RuntimeConfig], OttoMarketClient] | None = None,
):
    """Build GET-only poll handlers; no client is created until an enabled job runs."""
    source = dict(os.environ if environment is None else environment)
    build_client = client_factory or (
        lambda account, config: OttoMarketClient.from_environment(
            account, environment=source, runtime_config=config
        )
    )

    def execute(job: BackgroundJob) -> None:
        if job.job_type not in {TASK_JOB_TYPE, ONLINE_JOB_TYPE} or not isinstance(job.payload, Mapping):
            raise JobExecutionError("INVALID_OTTO_POLL_JOB", retryable=False)
        try:
            config = RuntimeConfig.from_environment(source)
        except ValueError:
            raise JobExecutionError("INVALID_RUNTIME_CONFIGURATION", retryable=False) from None
        if config.mode is not RuntimeMode.PRODUCTION:
            raise JobExecutionError("OTTO_POLL_MODE_DISABLED", retryable=False)
        if not config.otto_read_enabled:
            raise JobExecutionError("OTTO_READ_DISABLED", retryable=True, retry_delay_seconds=900)
        try:
            client = build_client(job.account, config)
        except OttoApiError as error:
            raise JobExecutionError(error.code, retryable=error.retryable,
                                    retry_delay_seconds=_retry_delay(error.retry_after_seconds)) from None

        if job.job_type == TASK_JOB_TYPE:
            attempt_id = job.payload.get("attemptId")
            if not isinstance(attempt_id, str):
                raise JobExecutionError("INVALID_OTTO_POLL_JOB", retryable=False)
            try:
                with engine_provider().begin() as connection:
                    context = load_processing_poll_context(
                        connection, account=job.account, attempt_id=attempt_id, job_id=job.id
                    )
            except ProcessingError as error:
                raise JobExecutionError(error.code, retryable=False) from None
            if context is None:
                return
            if context["deadline_at"] <= datetime.now(timezone.utc):
                try:
                    with engine_provider().begin() as connection:
                        defer_processing_poll(
                            connection, account=job.account, attempt_id=attempt_id, job_id=job.id,
                            error_code="OTTO_PROCESSING_DEADLINE",
                        )
                except ProcessingError as error:
                    raise JobExecutionError(error.code, retryable=False) from None
                return
            try:
                result, error_code, task_state = _task_result(client, context)
            except OttoApiError as error:
                if error.retryable:
                    try:
                        with engine_provider().begin() as connection:
                            defer_processing_poll(
                                connection, account=job.account, attempt_id=attempt_id, job_id=job.id,
                                delay_seconds=_retry_delay(error.retry_after_seconds),
                                error_code=error.code,
                            )
                    except ProcessingError as service_error:
                        raise JobExecutionError(service_error.code, retryable=False) from None
                    return
                result, error_code, task_state = "NEEDS_REVIEW", error.code, None
            except Exception:
                result, error_code, task_state = "NEEDS_REVIEW", "OTTO_TASK_READ_FAILED", None
            try:
                with engine_provider().begin() as connection:
                    if result == "POLLING":
                        defer_processing_poll(
                            connection, account=job.account, attempt_id=attempt_id, job_id=job.id,
                            task_state=task_state,
                        )
                    else:
                        record_processing_task_result(
                            connection, account=job.account, attempt_id=attempt_id,
                            job_id=job.id, result=result, error_code=error_code,
                        )
            except ProcessingError as error:
                raise JobExecutionError(error.code, retryable=False) from None
            except Exception:
                raise JobExecutionError("OTTO_PROCESSING_RESULT_PERSIST_FAILED", retryable=True,
                                        retry_delay_seconds=300) from None
            return

        operation_id = job.payload.get("operationId")
        if not isinstance(operation_id, str):
            raise JobExecutionError("INVALID_OTTO_POLL_JOB", retryable=False)
        try:
            with engine_provider().begin() as connection:
                context = load_online_poll_context(
                    connection, account=job.account, operation_id=operation_id, job_id=job.id
                )
        except ProcessingError as error:
            raise JobExecutionError(error.code, retryable=False) from None
        if context is None:
            return
        now = datetime.now(timezone.utc)
        if context["deadline_at"] <= now:
            try:
                with engine_provider().begin() as connection:
                    defer_online_poll(
                        connection, account=job.account, operation_id=operation_id, job_id=job.id,
                        error_code="MARKETPLACE_ONLINE_DEADLINE",
                    )
            except ProcessingError as error:
                raise JobExecutionError(error.code, retryable=False) from None
            return
        try:
            response = client.get_marketplace_status(str(context["target_sku"]))
        except OttoApiError as error:
            retryable = error.retryable or error.status_code == 404
            try:
                with engine_provider().begin() as connection:
                    record_online_poll_failure(
                        connection, account=job.account, operation_id=operation_id, job_id=job.id,
                        error_code=error.code, retryable=retryable,
                        retry_after_seconds=error.retry_after_seconds,
                    )
            except ProcessingError as service_error:
                raise JobExecutionError(service_error.code, retryable=False) from None
            return
        except Exception:
            try:
                with engine_provider().begin() as connection:
                    record_online_poll_failure(
                        connection, account=job.account, operation_id=operation_id, job_id=job.id,
                        error_code="MARKETPLACE_STATUS_READ_FAILED", retryable=True,
                    )
            except ProcessingError as service_error:
                raise JobExecutionError(service_error.code, retryable=False) from None
            return
        try:
            with engine_provider().begin() as connection:
                record_online_status_response(
                    connection, account=job.account, operation_id=operation_id,
                    job_id=job.id, response=response,
                )
        except ProcessingError as error:
            raise JobExecutionError(error.code, retryable=False) from None
        except Exception:
            raise JobExecutionError("MARKETPLACE_STATUS_PERSIST_FAILED", retryable=True,
                                    retry_delay_seconds=300) from None

    return execute

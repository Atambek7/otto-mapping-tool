"""Fail-closed workers for the two one-SKU new-listing availability jobs."""

from __future__ import annotations

import math
import os
from typing import Callable, Mapping

from sqlalchemy.engine import Engine

from integrations.otto_client import OttoApiError, OttoMarketClient
from jobs.service import BackgroundJob, JobExecutionError
from runtime_mode import RuntimeConfig, RuntimeMode
from services.availability_service import (
    AvailabilityError,
    claim_availability_dispatch,
    enqueue_availability_verification_job,
    is_retryable_availability_read_error,
    mark_interrupted_availability_unknown,
    read_availability_verification_target,
    record_availability_result,
    record_availability_verification_exhausted,
    record_availability_verification_result,
)

_JOB_ACTIONS = {
    "otto.availability.quantity": "QUANTITY",
    "otto.availability.delivery": "DELIVERY_INFORMATION",
}


def create_availability_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[str, RuntimeConfig], OttoMarketClient] | None = None,
):
    """Construct a handler without clients or network calls; execute one job at a time."""
    source = dict(os.environ if environment is None else environment)
    build_client = client_factory or (
        lambda account, config: OttoMarketClient.from_environment(
            account, environment=source, runtime_config=config
        )
    )

    def execute(job: BackgroundJob) -> None:
        action = _JOB_ACTIONS.get(job.job_type)
        if action is None or job.payload.get("action") != action:
            raise JobExecutionError("INVALID_AVAILABILITY_JOB", retryable=False)
        attempt_id = job.payload.get("availabilityAttemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise JobExecutionError("INVALID_AVAILABILITY_JOB", retryable=False)

        try:
            config = RuntimeConfig.from_environment(source)
        except ValueError:
            raise JobExecutionError("INVALID_RUNTIME_CONFIGURATION", retryable=False) from None
        if config.mode is not RuntimeMode.PRODUCTION:
            raise JobExecutionError("OTTO_AVAILABILITY_WRITE_MODE_DISABLED", retryable=False)
        if not config.otto_read_enabled or not config.availability_write_enabled:
            # Leave the durable attempt PREPARED; no external request was made.
            raise JobExecutionError("OTTO_AVAILABILITY_WRITE_DISABLED", retryable=True, retry_delay_seconds=900)
        try:
            client = build_client(job.account, config)
        except OttoApiError as error:
            raise JobExecutionError(error.code, retryable=True, retry_delay_seconds=900) from None

        try:
            with engine_provider().begin() as connection:
                dispatch = claim_availability_dispatch(
                    connection, account=job.account, attempt_id=attempt_id, job_id=job.id
                )
        except AvailabilityError as error:
            if error.code == "AVAILABILITY_DISPATCH_ALREADY_CLAIMED":
                try:
                    with engine_provider().begin() as connection:
                        recovery = mark_interrupted_availability_unknown(
                            connection, account=job.account, attempt_id=attempt_id
                        )
                except AvailabilityError:
                    raise JobExecutionError("AVAILABILITY_RECONCILIATION_FAILED", retryable=False) from None
                if recovery.get("idempotentReplay") or recovery.get("status") == "UNKNOWN":
                    # DISPATCHING was converted to UNKNOWN, or a previous
                    # result already exists. In either case this job must not
                    # dispatch again.
                    return
            raise JobExecutionError(error.code, retryable=False) from None

        payload = dispatch["payload"]
        try:
            if action == "QUANTITY":
                result = client.update_quantity(payload["sku"], payload["quantity"])
            else:
                result = client.update_delivery_information(
                    payload["sku"],
                    shipping_profile_id=payload["shippingProfileId"],
                    processing_time=payload["processingTime"],
                )
        except Exception:
            # Once dispatch is claimed, an exception is not retried: OTTO may
            # have applied the request. Persist only a stable safe code.
            try:
                with engine_provider().begin() as connection:
                    record_availability_result(
                        connection, account=job.account, attempt_id=attempt_id,
                        http_status=None, error_code="AVAILABILITY_WRITE_OUTCOME_UNKNOWN",
                    )
            except Exception:
                # A redelivered job will observe DISPATCHING and only mark it
                # unknown; it cannot repeat the POST.
                raise JobExecutionError("AVAILABILITY_RESULT_PERSIST_FAILED", retryable=True, retry_delay_seconds=300) from None
            return

        attempts_in_cycle = int(getattr(job, "attempts_in_cycle", 1) or 1)
        max_attempts = int(getattr(job, "max_attempts", 1) or 1)
        can_auto_retry = attempts_in_cycle < max_attempts
        try:
            with engine_provider().begin() as connection:
                recorded = record_availability_result(
                    connection, account=job.account, attempt_id=attempt_id,
                    http_status=result.status_code, response=result.response, error_code=result.error_code,
                    allow_retryable_rejection=can_auto_retry,
                )
        except Exception:
            # The dispatch fence remains durable; broker redelivery cannot send
            # the same POST a second time.
            raise JobExecutionError("AVAILABILITY_RESULT_PERSIST_FAILED", retryable=True, retry_delay_seconds=300) from None

        if recorded.get("retryable"):
            retry_after = getattr(result, "retry_after_seconds", None)
            if retry_after is not None:
                delay = max(1, min(int(math.ceil(float(retry_after))), 3600))
            else:
                delay = min(1800, 60 * (2 ** max(0, attempts_in_cycle - 1)))
            raise JobExecutionError(
                str(recorded.get("errorCode") or "OTTO_AVAILABILITY_RETRYABLE"),
                retryable=True,
                retry_delay_seconds=delay,
            )

        if recorded.get("status") == "ACCEPTED":
            try:
                with engine_provider().begin() as connection:
                    enqueue_availability_verification_job(
                        connection,
                        account=job.account,
                        operation_id=dispatch["operationId"],
                        attempt_id=attempt_id,
                        action=action,
                    )
            except Exception:
                # Non-fatal: the accepted write outcome is already persisted cleanly.
                pass

    return execute


def create_availability_verify_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[str, RuntimeConfig], OttoMarketClient] | None = None,
):
    """Verify via OTTO GET that an accepted or unconfirmed availability write is live."""
    source = dict(os.environ if environment is None else environment)
    build_client = client_factory or (
        lambda account, config: OttoMarketClient.from_environment(
            account, environment=source, runtime_config=config
        )
    )

    def execute(job: BackgroundJob) -> None:
        action = job.payload.get("action")
        attempt_id = job.payload.get("attemptId")
        if action not in {"QUANTITY", "DELIVERY_INFORMATION"} or not isinstance(attempt_id, str) or not attempt_id:
            raise JobExecutionError("INVALID_AVAILABILITY_VERIFY_JOB", retryable=False)

        try:
            config = RuntimeConfig.from_environment(source)
        except ValueError:
            raise JobExecutionError("INVALID_RUNTIME_CONFIGURATION", retryable=False) from None
        if config.mode is not RuntimeMode.PRODUCTION:
            raise JobExecutionError("OTTO_READ_MODE_DISABLED", retryable=False)
        if not config.otto_read_enabled:
            raise JobExecutionError("OTTO_READ_DISABLED", retryable=True, retry_delay_seconds=900)
        try:
            client = build_client(job.account, config)
        except OttoApiError as error:
            raise JobExecutionError(error.code, retryable=True, retry_delay_seconds=900) from None

        try:
            with engine_provider().begin() as connection:
                target = read_availability_verification_target(
                    connection, account=job.account, attempt_id=attempt_id
                )
        except AvailabilityError as error:
            if error.code == "AVAILABILITY_VERIFICATION_NOT_ELIGIBLE":
                return
            raise JobExecutionError(error.code, retryable=False) from None

        attempts_in_cycle = int(getattr(job, "attempts_in_cycle", 1) or 1)
        max_attempts = int(getattr(job, "max_attempts", 1) or 1)
        can_retry = attempts_in_cycle < max_attempts
        default_delay = min(1800, 15 * (2 ** max(0, attempts_in_cycle - 1)))

        try:
            if target["action"] == "QUANTITY":
                observed = client.get_quantity(str(target["targetSku"]))
            else:
                observed = client.get_product_delivery_information(str(target["targetSku"]))
        except OttoApiError as error:
            if can_retry and is_retryable_availability_read_error(error):
                retry_after = getattr(error, "retry_after_seconds", None)
                delay = max(1, min(int(math.ceil(float(retry_after))), 3600)) if retry_after is not None else default_delay
                raise JobExecutionError(error.code, retryable=True, retry_delay_seconds=delay) from None
            with engine_provider().begin() as connection:
                record_availability_verification_exhausted(
                    connection, account=job.account, attempt_id=attempt_id, error_code=error.code
                )
            raise JobExecutionError(error.code, retryable=False) from None

        with engine_provider().begin() as connection:
            recorded = record_availability_verification_result(
                connection, account=job.account, attempt_id=attempt_id, observed=observed
            )
            if not recorded.get("verified") and not can_retry:
                record_availability_verification_exhausted(
                    connection, account=job.account, attempt_id=attempt_id,
                    error_code=str(recorded.get("errorCode") or "OTTO_AVAILABILITY_VALUE_MISMATCH"),
                )
        if not recorded.get("verified"):
            code = str(recorded.get("errorCode") or "OTTO_AVAILABILITY_VALUE_MISMATCH")
            if can_retry:
                raise JobExecutionError(code, retryable=True, retry_delay_seconds=default_delay)
            raise JobExecutionError(code, retryable=False)

    return execute

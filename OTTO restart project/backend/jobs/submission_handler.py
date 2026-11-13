"""Fail-closed single-SKU product submission worker for OttoRestart."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Callable, Mapping

from sqlalchemy.engine import Engine

from integrations.otto_client import OttoApiError, OttoMarketClient
from jobs.service import (
    BackgroundJob,
    JobExecutionError,
    heartbeat_job,
)
from runtime_mode import RuntimeConfig, RuntimeMode
from services.aftercool_lookup_service import AftercoolLookupService
from services.otto_price_evidence import is_missing_otto_price_error
from services.preparation_service import PreparationError, price_snapshot_from_sources
from services.submission_service import (
    SubmissionError,
    begin_submission_attempt,
    claim_submission_dispatch,
    get_submission_attempt_for_job,
    load_prepared_attempt_context,
    load_submission_preflight_context,
    mark_interrupted_dispatch_unknown,
    record_submission_result,
    reject_prepared_attempt_for_review,
)

_TERMINAL_ATTEMPT_STATES = frozenset({"ACCEPTED", "REJECTED", "UNKNOWN"})


def create_submission_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[str, RuntimeConfig], OttoMarketClient] | None = None,
    aftercool_lookup_factory: Callable[[RuntimeConfig], AftercoolLookupService] | None = None,
):
    """Construct without network I/O; execute one immutable product attempt."""
    source = dict(os.environ if environment is None else environment)
    build_client = client_factory or (
        lambda account, config: OttoMarketClient.from_environment(
            account, environment=source, runtime_config=config
        )
    )
    build_aftercool_lookup = aftercool_lookup_factory or (
        lambda config: AftercoolLookupService(runtime_config=config)
    )

    def execute(job: BackgroundJob) -> None:
        if job.job_type != "otto.submit_product" or not isinstance(job.payload, Mapping):
            raise JobExecutionError("INVALID_SUBMISSION_JOB", retryable=False)

        try:
            with engine_provider().begin() as connection:
                attempt = get_submission_attempt_for_job(
                    connection, account=job.account, job_id=job.id
                )
        except SubmissionError as error:
            raise JobExecutionError(error.code, retryable=False) from None

        if attempt is not None and attempt["status"] in _TERMINAL_ATTEMPT_STATES:
            return
        if attempt is not None and attempt["status"] == "DISPATCHING":
            try:
                with engine_provider().begin() as connection:
                    mark_interrupted_dispatch_unknown(
                        connection, account=job.account, attempt_id=attempt["attemptId"]
                    )
            except SubmissionError:
                raise JobExecutionError("SUBMISSION_RECONCILIATION_FAILED", retryable=False) from None
            return
        if attempt is not None and attempt["status"] != "PREPARED":
            raise JobExecutionError("SUBMISSION_ATTEMPT_STATE_INVALID", retryable=False)

        def reject_prepared(error_code: str) -> None:
            if attempt is None:
                return
            try:
                with engine_provider().begin() as connection:
                    reject_prepared_attempt_for_review(
                        connection, account=job.account, attempt_id=attempt["attemptId"],
                        job_id=job.id, error_code=error_code,
                    )
            except SubmissionError:
                raise JobExecutionError("SUBMISSION_PREFLIGHT_REVIEW_FAILED", retryable=False) from None

        try:
            config = RuntimeConfig.from_environment(source)
        except ValueError:
            raise JobExecutionError("INVALID_RUNTIME_CONFIGURATION", retryable=False) from None
        if config.mode is not RuntimeMode.PRODUCTION:
            raise JobExecutionError("OTTO_PRODUCT_WRITE_MODE_DISABLED", retryable=False)
        if not config.otto_read_enabled or not config.product_write_enabled:
            raise JobExecutionError(
                "OTTO_PRODUCT_WRITE_DISABLED", retryable=True, retry_delay_seconds=900
            )

        try:
            if attempt is None:
                with engine_provider().begin() as connection:
                    context = load_submission_preflight_context(
                        connection, account=job.account, job=job.payload
                    )
            else:
                with engine_provider().begin() as connection:
                    context = load_prepared_attempt_context(
                        connection, account=job.account, attempt_id=attempt["attemptId"], job_id=job.id
                    )
        except SubmissionError as error:
            reject_prepared(error.code)
            raise JobExecutionError(error.code, retryable=False) from None

        try:
            client = build_client(job.account, config)
        except OttoApiError as error:
            raise JobExecutionError(
                error.code, retryable=error.retryable,
                retry_delay_seconds=_retry_delay(error.retry_after_seconds),
            ) from None

        stop = threading.Event()
        heartbeat_errors: list[str] = []
        heartbeat_thread: threading.Thread | None = None

        if job.lease_token:
            def heartbeat_loop() -> None:
                while not stop.wait(15):
                    try:
                        with engine_provider().begin() as connection:
                            heartbeat_job(
                                connection, job_id=job.id, account=job.account,
                                lease_token=job.lease_token or "", lease_seconds=60,
                            )
                    except Exception:
                        heartbeat_errors.append("SUBMISSION_LEASE_HEARTBEAT_FAILED")
                        stop.set()

            heartbeat_thread = threading.Thread(
                target=heartbeat_loop, name="ottorestart-submission-heartbeat", daemon=True
            )
            heartbeat_thread.start()

        attempt_id: str | None = attempt["attemptId"] if attempt else None
        dispatch_claimed = False
        try:
            try:
                current_price = client.get_product_prices(context["sourceSku"])
            except OttoApiError as error:
                if is_missing_otto_price_error(error):
                    current_price = {"sku": context["sourceSku"]}
                elif attempt is not None and not error.retryable:
                    reject_prepared(error.code)
                    raise JobExecutionError(
                        error.code, retryable=error.retryable,
                        retry_delay_seconds=_retry_delay(error.retry_after_seconds),
                    ) from None
                else:
                    raise JobExecutionError(
                        error.code, retryable=error.retryable,
                        retry_delay_seconds=_retry_delay(error.retry_after_seconds),
                    ) from None
            except Exception:
                raise JobExecutionError("OTTO_PRICE_READ_FAILED", retryable=True, retry_delay_seconds=300) from None
            if not isinstance(current_price, Mapping) or current_price.get("sku") != context["sourceSku"]:
                reject_prepared("SOURCE_SKU_MISMATCH")
                raise JobExecutionError("SOURCE_SKU_MISMATCH", retryable=False)
            aftercool_price_cents = None
            if context.get("sourceType") in {"AFTERBUY", "AFTERCOOL"}:
                if not config.aftercool_read_enabled:
                    reject_prepared("AFTERCOOL_READ_DISABLED")
                    raise JobExecutionError("AFTERCOOL_READ_DISABLED", retryable=False)
                aftercool = build_aftercool_lookup(config).lookup_product(
                    job.account,
                    str(context.get("sourceEan") or ""),
                    factory_hint=str(context.get("factoryId") or ""),
                )
                if aftercool.get("status") != "FOUND":
                    code = str(aftercool.get("reason_code") or aftercool.get("status") or "AFTERCOOL_LOOKUP_FAILED")
                    if aftercool.get("status") != "LOOKUP_ERROR":
                        reject_prepared(code)
                    raise JobExecutionError(code, retryable=aftercool.get("status") == "LOOKUP_ERROR", retry_delay_seconds=300)
                expected_product_id = str(context.get("aftercoolProductId") or "")
                if (
                    aftercool.get("account") != job.account
                    or aftercool.get("verified") is not True
                    or aftercool.get("data_scope") != "PRODUCTION"
                    or str(aftercool.get("ean") or "") != str(context.get("sourceEan") or "")
                    or str(aftercool.get("product_id") or aftercool.get("aftercool_product_id") or "") != expected_product_id
                    or str(aftercool.get("factory_id") or "") != str(context.get("factoryId") or "")
                ):
                    reject_prepared("AFTERCOOL_IDENTITY_MISMATCH")
                    raise JobExecutionError("AFTERCOOL_IDENTITY_MISMATCH", retryable=False)
                aftercool_price_cents = aftercool.get("price_cents")
            elif context.get("sourceType") != "OTTO_DIRECT":
                reject_prepared("SOURCE_TYPE_UNVERIFIED")
                raise JobExecutionError("SOURCE_TYPE_UNVERIFIED", retryable=False)
            if heartbeat_errors:
                raise JobExecutionError(heartbeat_errors[0], retryable=True, retry_delay_seconds=60)

            if attempt is None:
                try:
                    with engine_provider().begin() as connection:
                        prepared = begin_submission_attempt(
                            connection,
                            account=job.account,
                            item_id=context["itemId"],
                            background_job_id=job.id,
                            expected_operation_version=context["expectedOperationVersion"],
                            source_price_account=job.account,
                            source_price_response=current_price,
                            aftercool_price_cents=aftercool_price_cents,
                            source_price_checked_at=datetime.now(timezone.utc),
                            actor="system:otto-submission",
                            approved_evidence_types=config.gtin_approved_evidence_types,
                        )
                except SubmissionError as error:
                    raise JobExecutionError(error.code, retryable=False) from None
                attempt_id = prepared["attemptId"]
                context = {
                    **context,
                    "ottoProductPayload": prepared["ottoProductPayload"],
                }
            else:
                try:
                    current_snapshot = price_snapshot_from_sources(
                        job.account, current_price, source_type=str(context.get("sourceType") or ""),
                        aftercool_price_cents=aftercool_price_cents,
                    )
                except PreparationError as error:
                    reject_prepared(error.code)
                    raise JobExecutionError(error.code, retryable=False) from None
                if (
                    any(
                        current_snapshot.get(field) != context["priceSnapshot"].get(field)
                        for field in (
                            "currency", "standardPriceAmount", "ottoStandardPriceCents",
                            "aftercoolStartPriceCents", "originalPriceCents", "selectedSource",
                            "discountCents", "discountedPriceCents", "targetPriceCents",
                            "targetFractionCents", "formula",
                        )
                    )
                    or current_snapshot["originalPriceCents"] != context["sourcePriceCents"]
                    or current_snapshot["currency"] != context["sourcePriceCurrency"]
                ):
                    reject_prepared("SOURCE_PRICE_CHANGED")
                    raise JobExecutionError("SOURCE_PRICE_CHANGED", retryable=False)

            if heartbeat_errors:
                raise JobExecutionError(heartbeat_errors[0], retryable=True, retry_delay_seconds=60)
            if attempt_id is None:
                raise JobExecutionError("SUBMISSION_ATTEMPT_NOT_PREPARED", retryable=False)

            try:
                with engine_provider().begin() as connection:
                    dispatch = claim_submission_dispatch(
                        connection, account=job.account, attempt_id=attempt_id, job_id=job.id
                    )
                dispatch_claimed = True
            except SubmissionError as error:
                if error.code == "SUBMISSION_DISPATCH_ALREADY_CLAIMED":
                    try:
                        with engine_provider().begin() as connection:
                            mark_interrupted_dispatch_unknown(
                                connection, account=job.account, attempt_id=attempt_id
                            )
                    except SubmissionError:
                        raise JobExecutionError("SUBMISSION_RECONCILIATION_FAILED", retryable=False) from None
                    return
                raise JobExecutionError(error.code, retryable=False) from None

            if heartbeat_errors:
                try:
                    with engine_provider().begin() as connection:
                        mark_interrupted_dispatch_unknown(
                            connection, account=job.account, attempt_id=attempt_id
                        )
                except SubmissionError:
                    raise JobExecutionError("SUBMISSION_RECONCILIATION_FAILED", retryable=False) from None
                return

            result = client.submit_product_payload(
                context["ottoProductPayload"],
                expected_sku=dispatch["targetSku"],
                expected_ean=dispatch["targetEan"],
            )
            try:
                with engine_provider().begin() as connection:
                    record_submission_result(
                        connection, account=job.account, attempt_id=attempt_id,
                        http_status=result.status_code, response=result.response,
                        transport_error=result.error_code,
                    )
            except SubmissionError:
                # A redelivery sees the durable DISPATCHING fence and will
                # enter reconciliation instead of sending the payload again.
                raise JobExecutionError("SUBMISSION_RESULT_PERSIST_FAILED", retryable=True, retry_delay_seconds=300) from None
        except JobExecutionError:
            raise
        except Exception:
            # The POST transport itself converts uncertain failures into a
            # result; reaching this block after the fence is still treated as
            # uncertain and never triggers an automatic resend.
            if attempt_id and dispatch_claimed:
                try:
                    with engine_provider().begin() as connection:
                        record_submission_result(
                            connection, account=job.account, attempt_id=attempt_id,
                            http_status=None, transport_error="PRODUCT_WRITE_OUTCOME_UNKNOWN",
                        )
                except Exception:
                    raise JobExecutionError(
                        "SUBMISSION_RESULT_PERSIST_FAILED", retryable=True, retry_delay_seconds=300
                    ) from None
            else:
                raise JobExecutionError("SUBMISSION_PREFLIGHT_FAILED", retryable=True, retry_delay_seconds=300) from None
        finally:
            stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2)

    return execute


def _retry_delay(value: float | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(seconds, 86_400))

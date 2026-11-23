"""Read-only OTTO discovery job consumers for incremental and known-SKU runs."""

from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from typing import Callable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from integrations.otto_client import OttoApiError, OttoMarketClient
from jobs.service import BackgroundJob, JobExecutionError, heartbeat_job
from runtime_mode import RuntimeConfig, RuntimeMode
from services.discovery_scan_service import (
    MAX_FINAL_APPLY_SECONDS,
    MAX_RECONCILIATION_SKUS,
    MAX_SCAN_OBSERVATIONS,
    MAX_SCAN_PAGES,
    DiscoveryScanError,
    mark_run_failed,
    mark_run_started,
    promote_cursor,
    record_run_error,
    record_run_progress,
)
from services.discovery_service import (
    ReductionLimitExceeded,
    StatusResponseInput,
    apply_staged_run,
    stage_status_responses,
)

_SAFE_ERROR_CODE = re.compile(r"^[A-Z0-9_.-]{1,64}$")
STAGE_CHUNK_SIZE = 500


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _safe_error_code(error: OttoApiError) -> str:
    code = str(error.code or "OTTO_READ_ERROR").upper()
    return code if _SAFE_ERROR_CODE.fullmatch(code) else "OTTO_READ_ERROR"


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _mark_failed(engine_provider: Callable[[], Engine], job: BackgroundJob, code: str, page: int | None) -> None:
    with engine_provider().begin() as connection:
        mark_run_failed(connection, account=job.account, run_id=str(job.payload["runId"]), error_code=code, page=page)


def _record_error(
    engine_provider: Callable[[], Engine],
    job: BackgroundJob,
    code: str,
    *,
    terminal: bool,
    status_code: int | None = None,
    page: int | None = None,
) -> None:
    with engine_provider().begin() as connection:
        record_run_error(
            connection,
            account=job.account,
            run_id=str(job.payload["runId"]),
            error_code=code,
            status_code=status_code,
            page=page,
            terminal=terminal,
        )


def _mark_attempt_progress(
    engine_provider: Callable[[], Engine], job: BackgroundJob, *, attempt: int, run_type: str
) -> None:
    with engine_provider().begin() as connection:
        record_run_progress(
            connection,
            account=job.account,
            run_id=str(job.payload["runId"]),
            coverage_patch={
                "pageState": "RUNNING",
                "attempt": attempt,
                "pagesCompleted": 0,
                "recordsRead": 0,
                "lastPage": 0,
                "runType": run_type,
            },
        )


def _apply_complete_run(engine_provider: Callable[[], Engine], job: BackgroundJob, run_type: str) -> dict:
    run_id = str(job.payload["runId"])
    with engine_provider().begin() as connection:
        summary = apply_staged_run(
            connection,
            job.account,
            run_id,
            max_observations=MAX_SCAN_OBSERVATIONS if run_type == "INCREMENTAL_SCAN" else MAX_RECONCILIATION_SKUS,
            max_seconds=MAX_FINAL_APPLY_SECONDS,
        )
        if run_type != "INCREMENTAL_SCAN":
            connection.execute(
                text(
                    "UPDATE import_runs SET coverage_json=coverage_json || CAST(:patch AS jsonb) "
                    "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION'"
                ),
                {"patch": json.dumps({"pageState": "SUCCEEDED", "cursorPromoted": False}, separators=(",", ":")),
                 "run_id": run_id, "account": job.account},
            )
            return summary

        blocked = any(
            int(summary.get(key, 0) or 0) > 0
            for key in ("uncheckable", "ambiguous", "identity_conflicts", "stale_or_conflicting")
        )
        state = connection.execute(
            text(
                "SELECT run_state, cutoff_at FROM import_runs WHERE id=:run_id AND account=:account "
                "AND data_scope='PRODUCTION'"
            ),
            {"run_id": run_id, "account": job.account},
        ).mappings().first()
        if state is not None and state["run_state"] == "SUCCEEDED" and not blocked and summary.get("applied"):
            promote_cursor(
                connection,
                account=job.account,
                run_id=run_id,
                cutoff_at=state["cutoff_at"],
            )
            patch = {"pageState": "SUCCEEDED", "cursorPromoted": True}
        else:
            reasons = [
                key for key in ("uncheckable", "ambiguous", "identity_conflicts", "stale_or_conflicting")
                if int(summary.get(key, 0) or 0) > 0
            ]
            patch = {
                "pageState": "PARTIAL" if state and state["run_state"] == "PARTIAL" else "SUCCEEDED",
                "cursorPromoted": False,
            }
            if reasons:
                patch["cursorBlockedBy"] = reasons
        connection.execute(
            text(
                "UPDATE import_runs SET coverage_json=coverage_json || CAST(:patch AS jsonb) "
                "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION'"
            ),
            {"patch": json.dumps(patch, separators=(",", ":")), "run_id": run_id, "account": job.account},
        )
        return summary


def _stage_page(
    engine_provider: Callable[[], Engine],
    job: BackgroundJob,
    rows: list[dict],
    *,
    page_number: int,
    records_read: int,
    attempt: int,
) -> int:
    skus = [row.get("sku") for row in rows if isinstance(row.get("sku"), str)]
    linked_sources: dict[str, tuple[str, str | None]] = {}
    if skus:
        with engine_provider().connect() as connection:
            candidate_rows = connection.execute(
                text(
                    "SELECT ce.observed_sku, ce.source_product_id::text AS source_product_id, "
                    "ce.confirmed_ean, sp.aftercool_ean "
                    "FROM candidate_episodes ce JOIN source_products sp "
                    "ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
                    "WHERE ce.account=:account AND ce.data_scope='PRODUCTION' "
                    "AND ce.episode_state='OPEN' AND ce.observed_sku=ANY(:skus)"
                ),
                {"account": job.account, "skus": list(set(skus))},
            ).mappings().all()
            lineage_rows = connection.execute(
                text(
                    "SELECT op.target_sku, MIN(op.source_product_id::text) AS source_product_id, "
                    "MIN(sp.aftercool_ean) AS confirmed_ean, "
                    "COUNT(DISTINCT op.source_product_id) AS source_count, "
                    "COUNT(DISTINCT link.id) AS link_count, "
                    "COUNT(DISTINCT link.source_ean) AS source_ean_count, "
                    "BOOL_OR(link.id IS NOT NULL AND link.source_ean IS DISTINCT FROM sp.aftercool_ean) AS ean_conflict "
                    "FROM restart_operations op "
                    "LEFT JOIN ean_links link ON link.operation_id=op.id AND link.source_product_id=op.source_product_id "
                    "AND link.account=op.account "
                    "AND link.data_scope=op.data_scope AND link.new_ean=op.new_ean "
                    "LEFT JOIN source_products sp ON sp.id=op.source_product_id AND sp.account=op.account "
                    "AND sp.data_scope=op.data_scope "
                    "WHERE op.account=:account AND op.data_scope='PRODUCTION' "
                    "AND op.submission_state='CONFIRMED' AND op.target_sku=ANY(:skus) "
                    "GROUP BY op.target_sku"
                ),
                {"account": job.account, "skus": list(set(skus))},
            ).mappings().all()
        for candidate in candidate_rows:
            source_ean = candidate["aftercool_ean"]
            if not source_ean or candidate["confirmed_ean"] != source_ean:
                raise DiscoveryScanError(
                    "IDENTITY_CONFLICT",
                    "A known source candidate has incomplete or conflicting EAN lineage.",
                    status_code=409,
                )
            sku = str(candidate["observed_sku"])
            identity = (str(candidate["source_product_id"]), str(source_ean))
            if sku in linked_sources and linked_sources[sku] != identity:
                raise DiscoveryScanError("IDENTITY_CONFLICT", "A SKU maps to multiple source products.", status_code=409)
            linked_sources[sku] = identity
        for lineage in lineage_rows:
            if (
                int(lineage["source_count"]) != 1
                or int(lineage["link_count"]) < 1
                or int(lineage["source_ean_count"]) != 1
                or bool(lineage["ean_conflict"])
            ):
                raise DiscoveryScanError(
                    "IDENTITY_CONFLICT",
                    "A confirmed target SKU has conflicting or incomplete Restart lineage.",
                    status_code=409,
                )
            ean = lineage["confirmed_ean"]
            sku = str(lineage["target_sku"])
            identity = (
                str(lineage["source_product_id"]), str(ean) if ean else None
            )
            if sku in linked_sources and linked_sources[sku] != identity:
                raise DiscoveryScanError("IDENTITY_CONFLICT", "A SKU maps to multiple source products.", status_code=409)
            linked_sources[sku] = identity

    responses: list[StatusResponseInput] = []
    for row in rows:
        sku = row.get("sku")
        if not isinstance(sku, str) or not sku or sku != sku.strip() or len(sku) > 256:
            raise DiscoveryScanError("INVALID_MARKETPLACE_STATUS_ROW", "A status row has no valid SKU.", status_code=502)
        source_identity = linked_sources.get(sku)
        responses.append(
            StatusResponseInput(
                requested_sku=sku,
                http_status=200,
                body=row,
                source_product_id=source_identity[0] if source_identity else None,
                confirmed_ean=source_identity[1] if source_identity else None,
            )
        )

    for offset in range(0, len(responses), STAGE_CHUNK_SIZE):
        chunk = responses[offset : offset + STAGE_CHUNK_SIZE]
        with engine_provider().begin() as connection:
            stage_status_responses(connection, job.account, str(job.payload["runId"]), chunk)
    with engine_provider().begin() as connection:
        record_run_progress(
            connection,
            account=job.account,
            run_id=str(job.payload["runId"]),
            coverage_patch={
                "pageState": "RUNNING",
                "attempt": attempt,
                "pagesCompleted": page_number,
                "recordsRead": records_read,
                "lastPage": page_number,
            },
        )
    return len(responses)


def _run_incremental_scan(engine_provider: Callable[[], Engine], client, job: BackgroundJob) -> None:
    run_id = str(job.payload["runId"])
    with engine_provider().begin() as connection:
        run = mark_run_started(connection, account=job.account, run_id=run_id)
    if run["run_state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}:
        return
    if run["run_type"] != "INCREMENTAL_SCAN":
        _mark_failed(engine_provider, job, "DISCOVERY_RUN_TYPE_MISMATCH", None)
        raise JobExecutionError("DISCOVERY_RUN_TYPE_MISMATCH", retryable=False)
    window_from = run["window_from"]
    if window_from is None:
        _mark_failed(engine_provider, job, "SCAN_WINDOW_MISSING", None)
        raise JobExecutionError("SCAN_WINDOW_MISSING", retryable=False)

    attempt = int(job.attempts_in_cycle)
    _mark_attempt_progress(engine_provider, job, attempt=attempt, run_type="INCREMENTAL_SCAN")
    filters = {"fromDate": _iso_utc(window_from), "limit": 1000}
    pages_completed = 0
    records_read = 0
    try:
        for page_number, page_rows in enumerate(
            client.iter_marketplace_status_pages(filters, max_pages=MAX_SCAN_PAGES), start=1
        ):
            if records_read + len(page_rows) > MAX_SCAN_OBSERVATIONS:
                raise DiscoveryScanError(
                    "DISCOVERY_OBSERVATION_LIMIT",
                    "The scan reached its configured record cap before the page could be staged.",
                    status_code=413,
                )
            records_read += _stage_page(
                engine_provider,
                job,
                page_rows,
                page_number=page_number,
                records_read=records_read + len(page_rows),
                attempt=attempt,
            )
            pages_completed = page_number
    except OttoApiError as error:
        code = _safe_error_code(error)
        terminal = (
            not error.retryable
            or code == "PAGINATION_LIMIT_REACHED"
            or job.attempts_in_cycle >= job.max_attempts
        )
        _record_error(
            engine_provider,
            job,
            code,
            terminal=terminal,
            status_code=error.status_code,
            page=pages_completed + 1,
        )
        retry_delay = None
        if error.retryable and error.retry_after_seconds is not None:
            retry_delay = min(86_400, max(0, math.ceil(error.retry_after_seconds)))
        raise JobExecutionError(code, retryable=not terminal, retry_delay_seconds=retry_delay) from None
    except DiscoveryScanError as error:
        _mark_failed(engine_provider, job, error.code, pages_completed + 1)
        raise JobExecutionError(error.code, retryable=False) from None

    with engine_provider().begin() as connection:
        record_run_progress(
            connection,
            account=job.account,
            run_id=run_id,
            coverage_patch={
                "pageState": "FETCH_COMPLETE",
                "attempt": attempt,
                "pagesCompleted": pages_completed,
                "recordsRead": records_read,
                "lastPage": pages_completed,
            },
        )
    try:
        _apply_complete_run(engine_provider, job, "INCREMENTAL_SCAN")
    except ReductionLimitExceeded as error:
        _mark_failed(engine_provider, job, str(error), pages_completed)
        raise JobExecutionError(str(error), retryable=False) from None
    except Exception:
        terminal = job.attempts_in_cycle >= job.max_attempts
        if terminal:
            _mark_failed(engine_provider, job, "DISCOVERY_APPLY_FAILED", pages_completed)
        raise JobExecutionError("DISCOVERY_APPLY_FAILED", retryable=not terminal) from None


def _run_known_reconciliation(engine_provider: Callable[[], Engine], client, job: BackgroundJob) -> None:
    run_id = str(job.payload["runId"])
    with engine_provider().begin() as connection:
        run = mark_run_started(connection, account=job.account, run_id=run_id)
    if run["run_state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}:
        return
    if run["run_type"] != "KNOWN_SKU_RECONCILIATION":
        _mark_failed(engine_provider, job, "DISCOVERY_RUN_TYPE_MISMATCH", None)
        raise JobExecutionError("DISCOVERY_RUN_TYPE_MISMATCH", retryable=False)
    parameters = _json_object(run["parameters_json"])
    skus = parameters.get("skus")
    if not isinstance(skus, list) or not 1 <= len(skus) <= MAX_RECONCILIATION_SKUS:
        _mark_failed(engine_provider, job, "RECONCILIATION_SKU_LIST_INVALID", None)
        raise JobExecutionError("RECONCILIATION_SKU_LIST_INVALID", retryable=False)
    attempt = int(job.attempts_in_cycle)
    _mark_attempt_progress(engine_provider, job, attempt=attempt, run_type="KNOWN_SKU_RECONCILIATION")
    try:
        for index, sku in enumerate(skus, start=1):
            if not isinstance(sku, str) or not sku:
                raise DiscoveryScanError("RECONCILIATION_SKU_LIST_INVALID", "The stored SKU list is invalid.")
            body = client.get_marketplace_status(sku)
            if not isinstance(body, dict) or body.get("sku") != sku:
                raise DiscoveryScanError("SKU_MISMATCH", "OTTO status did not confirm the requested SKU.", status_code=502)
            with engine_provider().begin() as connection:
                stage_status_responses(
                    connection,
                    job.account,
                    run_id,
                    [StatusResponseInput(requested_sku=sku, http_status=200, body=body)],
                )
                record_run_progress(
                    connection,
                    account=job.account,
                    run_id=run_id,
                    coverage_patch={
                        "pageState": "RUNNING",
                        "attempt": attempt,
                        "pagesCompleted": index,
                        "recordsRead": index,
                        "lastPage": index,
                    },
                )
    except OttoApiError as error:
        code = _safe_error_code(error)
        terminal = not error.retryable or job.attempts_in_cycle >= job.max_attempts
        _record_error(engine_provider, job, code, terminal=terminal, status_code=error.status_code, page=index)
        retry_delay = min(86_400, max(0, math.ceil(error.retry_after_seconds))) if error.retry_after_seconds is not None else None
        raise JobExecutionError(code, retryable=not terminal, retry_delay_seconds=retry_delay) from None
    except DiscoveryScanError as error:
        _mark_failed(engine_provider, job, error.code, index)
        raise JobExecutionError(error.code, retryable=False) from None

    with engine_provider().begin() as connection:
        record_run_progress(
            connection,
            account=job.account,
            run_id=run_id,
            coverage_patch={
                "pageState": "FETCH_COMPLETE",
                "attempt": attempt,
                "pagesCompleted": len(skus),
                "recordsRead": len(skus),
                "lastPage": len(skus),
            },
        )
    try:
        _apply_complete_run(engine_provider, job, "KNOWN_SKU_RECONCILIATION")
    except ReductionLimitExceeded as error:
        _mark_failed(engine_provider, job, str(error), len(skus))
        raise JobExecutionError(str(error), retryable=False) from None
    except Exception:
        terminal = job.attempts_in_cycle >= job.max_attempts
        if terminal:
            _mark_failed(engine_provider, job, "RECONCILIATION_APPLY_FAILED", len(skus))
        raise JobExecutionError("RECONCILIATION_APPLY_FAILED", retryable=not terminal) from None


def create_discovery_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
    otto_client_factory: Callable[[str], object] | None = None,
):
    """Build workers lazily; no client, network, DB connection, or thread at import time."""
    source = dict(os.environ if environment is None else environment)
    config = RuntimeConfig.from_environment(source)

    def execute(job: BackgroundJob) -> None:
        if job.job_type not in {"discovery.scan", "discovery.reconcile"}:
            raise JobExecutionError("INVALID_DISCOVERY_JOB", retryable=False)
        run_id = job.payload.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise JobExecutionError("INVALID_DISCOVERY_JOB", retryable=False)
        if not config.otto_read_enabled:
            _mark_failed(engine_provider, job, "OTTO_READ_DISABLED", None)
            raise JobExecutionError("OTTO_READ_DISABLED", retryable=False)
        if config.mode not in {RuntimeMode.PRODUCTION, RuntimeMode.TEST}:
            _mark_failed(engine_provider, job, "PRODUCTION_MODE_REQUIRED", None)
            raise JobExecutionError("PRODUCTION_MODE_REQUIRED", retryable=False)
        if otto_client_factory is None and config.mode is not RuntimeMode.PRODUCTION:
            _mark_failed(engine_provider, job, "LIVE_CLIENT_REQUIRES_PRODUCTION", None)
            raise JobExecutionError("LIVE_CLIENT_REQUIRES_PRODUCTION", retryable=False)

        stop = threading.Event()
        heartbeat_errors: list[str] = []

        def heartbeat_loop() -> None:
            while not stop.wait(15):
                try:
                    with engine_provider().begin() as connection:
                        heartbeat_job(
                            connection,
                            job_id=job.id,
                            account=job.account,
                            lease_token=job.lease_token or "",
                            lease_seconds=60,
                        )
                except Exception:
                    heartbeat_errors.append("DISCOVERY_LEASE_HEARTBEAT_FAILED")
                    stop.set()

        thread = threading.Thread(target=heartbeat_loop, name="ottorestart-discovery-heartbeat", daemon=True)
        thread.start()
        try:
            client = (
                otto_client_factory(job.account)
                if otto_client_factory is not None
                else OttoMarketClient.from_environment(job.account, environment=source, runtime_config=config)
            )
            if job.job_type == "discovery.scan":
                _run_incremental_scan(engine_provider, client, job)
            else:
                _run_known_reconciliation(engine_provider, client, job)
        except OttoApiError as error:
            code = _safe_error_code(error)
            terminal = not error.retryable or job.attempts_in_cycle >= job.max_attempts
            _record_error(engine_provider, job, code, terminal=terminal, status_code=error.status_code)
            retry_delay = min(86_400, max(0, math.ceil(error.retry_after_seconds))) if error.retry_after_seconds is not None else None
            raise JobExecutionError(code, retryable=not terminal, retry_delay_seconds=retry_delay) from None
        except JobExecutionError:
            raise
        except Exception:
            terminal = job.attempts_in_cycle >= job.max_attempts
            if terminal:
                _mark_failed(engine_provider, job, "DISCOVERY_EXECUTOR_ERROR", None)
            else:
                _record_error(engine_provider, job, "DISCOVERY_EXECUTOR_ERROR", terminal=False)
            raise JobExecutionError("DISCOVERY_EXECUTOR_ERROR", retryable=not terminal) from None
        finally:
            stop.set()
            thread.join(timeout=2)
        if heartbeat_errors:
            terminal = job.attempts_in_cycle >= job.max_attempts
            if terminal:
                _mark_failed(engine_provider, job, heartbeat_errors[0], None)
            raise JobExecutionError(heartbeat_errors[0], retryable=not terminal)

    return execute

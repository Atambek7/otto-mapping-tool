"""Bounded scan run contracts and cursor management for OttoRestart."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.discovery_service import create_import_run
from services.operation_service import OperationError, idempotent_operation_action

OVERLAP_SECONDS = 48 * 60 * 60
MAX_INITIAL_WINDOW = timedelta(days=16)
MAX_SCAN_PAGES = 100
MAX_SCAN_OBSERVATIONS = 50_000
MAX_FINAL_APPLY_SECONDS = 120
MAX_RECONCILIATION_SKUS = 500
ACTIVE_RUN_STATES = ("PENDING", "RUNNING", "STAGED", "APPLYING")


class DiscoveryScanError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _require_aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DiscoveryScanError("INVALID_DATE", f"{field} must include a timezone.", status_code=422)
    return value.astimezone(timezone.utc)


def _scan_lock(connection: Connection, account: str) -> None:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
        {"lock_name": f"ottorestart-discovery-create:{account}"},
    )


def _reject_active_run(connection: Connection, account: str) -> None:
    rows = connection.execute(
        text(
            "SELECT run.id, job.state AS job_state FROM import_runs run "
            "LEFT JOIN background_jobs job ON job.account=run.account AND job.data_scope='PRODUCTION' "
            "AND job.job_type IN ('discovery.scan','discovery.reconcile') "
            "AND job.payload_json->>'runId'=run.id::text "
            "WHERE run.account=:account AND run.data_scope='PRODUCTION' "
            "AND run.run_type IN ('INCREMENTAL_SCAN','KNOWN_SKU_RECONCILIATION') "
            "AND run.run_state = ANY(:states) ORDER BY run.created_at DESC,run.id FOR UPDATE OF run"
        ),
        {"account": account, "states": list(ACTIVE_RUN_STATES)},
    ).mappings().all()
    for row in rows:
        if row["job_state"] in {"PENDING", "RUNNING", "RETRY_WAIT"}:
            raise DiscoveryScanError(
                "SCAN_ALREADY_ACTIVE",
                "A discovery scan or known-SKU reconciliation is already active for this account.",
            )
        error_code = "DISCOVERY_JOB_MISSING" if row["job_state"] is None else "DISCOVERY_JOB_TERMINAL"
        connection.execute(
            text(
                "UPDATE import_runs SET run_state='PARTIAL',status='COMPLETED',finished_at=CURRENT_TIMESTAMP, "
                "error_json=error_json || CAST(:error AS jsonb), "
                "coverage_json=coverage_json || CAST(:coverage AS jsonb) "
                "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION'"
            ),
            {
                "run_id": row["id"],
                "account": account,
                "error": json.dumps([{"code": error_code}], separators=(",", ":")),
                "coverage": json.dumps({"pageState": "PARTIAL", "lastErrorCode": error_code}, separators=(",", ":")),
            },
        )


def _serialize_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run_response(run_id: str, account: str, job_id: str, window_from: datetime | None, cutoff: datetime) -> dict[str, Any]:
    return {
        "runId": run_id,
        "jobId": job_id,
        "account": account,
        "runType": "INCREMENTAL_SCAN",
        "runState": "PENDING",
        "windowFrom": _serialize_time(window_from) if window_from else None,
        "cutoffAt": _serialize_time(cutoff),
        "httpStatus": 202,
    }


def _create_scan(
    connection: Connection,
    *,
    account: str,
    from_date: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    _scan_lock(connection, account)
    _reject_active_run(connection, account)
    current = _require_aware(now, "now")
    cursor = connection.execute(
        text(
            "SELECT last_successful_cutoff, overlap_seconds FROM discovery_cursors "
            "WHERE account=:account AND data_scope='PRODUCTION' FOR UPDATE"
        ),
        {"account": account},
    ).mappings().first()
    if cursor and cursor["last_successful_cutoff"] is not None:
        window_from = cursor["last_successful_cutoff"] - timedelta(seconds=int(cursor["overlap_seconds"]))
        if current - window_from > MAX_INITIAL_WINDOW:
            raise DiscoveryScanError(
                "CURSOR_WINDOW_TOO_WIDE",
                "The successful discovery cursor is older than the bounded scan window; operator review is required.",
                status_code=409,
            )
        if from_date is not None and _require_aware(from_date, "fromDate") != window_from:
            raise DiscoveryScanError(
                "FROM_DATE_MANAGED_BY_CURSOR",
                "fromDate is derived from the last successful cursor and overlap.",
                status_code=422,
            )
    else:
        if from_date is None:
            raise DiscoveryScanError(
                "INITIAL_FROM_DATE_REQUIRED",
                "The first incremental scan requires an explicit bounded fromDate.",
                status_code=422,
            )
        window_from = _require_aware(from_date, "fromDate")
        if window_from > current:
            raise DiscoveryScanError("INVALID_DATE_RANGE", "fromDate must not be in the future.", status_code=422)
        if current - window_from > MAX_INITIAL_WINDOW:
            raise DiscoveryScanError(
                "INITIAL_WINDOW_TOO_WIDE",
                "The first scan window is limited to 16 days; full-catalog scans are disabled.",
                status_code=422,
            )

    run_id = create_import_run(
        connection,
        account,
        "INCREMENTAL_SCAN",
        "INCREMENTAL",
        window_from=window_from,
        cutoff_at=current,
        parameters={
            "fromDate": _serialize_time(window_from),
            "cutoffAt": _serialize_time(current),
            "overlapSeconds": int(cursor["overlap_seconds"]) if cursor else OVERLAP_SECONDS,
            "maxPages": MAX_SCAN_PAGES,
            "maxObservations": MAX_SCAN_OBSERVATIONS,
        },
    )
    job = enqueue_job(
        connection,
        account=account,
        job_type="discovery.scan",
        idempotency_key=f"scan-run:{run_id}",
        payload={"runId": run_id},
        max_attempts=5,
    ).job
    return _run_response(run_id, account, job.id, window_from, current)


def create_incremental_scan(
    connection: Connection,
    *,
    account: str,
    actor: str,
    idempotency_key: str,
    from_date: datetime | None,
    request_body: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    if account not in {"JV", "XL"}:
        raise DiscoveryScanError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    if not actor.strip():
        raise DiscoveryScanError("ACTOR_REQUIRED", "An authenticated operator is required.", status_code=403)
    current = now or datetime.now(timezone.utc)

    def execute() -> dict[str, Any]:
        return _create_scan(connection, account=account, from_date=from_date, now=current)

    try:
        return idempotent_operation_action(
            connection,
            account=account,
            actor=actor,
            action="discovery-incremental-scan",
            idempotency_key=idempotency_key,
            request_body=dict(request_body),
            execute=execute,
        )
    except OperationError as error:
        if error.code == "IDEMPOTENCY_CONFLICT":
            raise DiscoveryScanError(error.code, str(error), status_code=409) from None
        raise DiscoveryScanError(error.code, str(error), status_code=422) from None


def _normalize_skus(skus: Sequence[str]) -> list[str]:
    if not isinstance(skus, Sequence) or isinstance(skus, (str, bytes)):
        raise DiscoveryScanError("INVALID_SKU_LIST", "skus must be a list of strings.", status_code=422)
    if not 1 <= len(skus) <= MAX_RECONCILIATION_SKUS:
        raise DiscoveryScanError(
            "RECONCILIATION_LIMIT", "Known-SKU reconciliation accepts 1–500 SKUs.", status_code=422
        )
    result: list[str] = []
    for sku in skus:
        if not isinstance(sku, str) or not sku or sku != sku.strip() or len(sku) > 256:
            raise DiscoveryScanError("INVALID_SKU", "Each SKU must be a non-empty trimmed string.", status_code=422)
        result.append(sku)
    if len(set(result)) != len(result):
        raise DiscoveryScanError("DUPLICATE_SKU", "SKU list must not contain duplicates.", status_code=422)
    return result


def _create_reconciliation(
    connection: Connection,
    *,
    account: str,
    skus: list[str],
    now: datetime,
) -> dict[str, Any]:
    _scan_lock(connection, account)
    _reject_active_run(connection, account)
    current = _require_aware(now, "now")
    found = connection.execute(
        text(
            "SELECT observed_sku FROM candidate_episodes WHERE account=:account "
            "AND data_scope='PRODUCTION' AND episode_state='OPEN' AND observed_sku=ANY(:skus)"
        ),
        {"account": account, "skus": skus},
    ).scalars().all()
    if set(found) != set(skus):
        raise DiscoveryScanError(
            "UNKNOWN_OR_CLOSED_SKU",
            "Reconciliation is limited to currently open production candidate SKUs in this account.",
            status_code=422,
        )
    run_id = create_import_run(
        connection,
        account,
        "KNOWN_SKU_RECONCILIATION",
        "INCREMENTAL",
        cutoff_at=current,
        parameters={"skus": skus, "skuCount": len(skus), "maxObservations": len(skus)},
    )
    job = enqueue_job(
        connection,
        account=account,
        job_type="discovery.reconcile",
        idempotency_key=f"reconcile-run:{run_id}",
        payload={"runId": run_id},
        max_attempts=5,
    ).job
    return {
        "runId": run_id,
        "jobId": job.id,
        "account": account,
        "runType": "KNOWN_SKU_RECONCILIATION",
        "runState": "PENDING",
        "skuCount": len(skus),
        "cutoffAt": _serialize_time(current),
        "httpStatus": 202,
    }


def create_known_sku_reconciliation(
    connection: Connection,
    *,
    account: str,
    actor: str,
    idempotency_key: str,
    skus: Sequence[str],
    request_body: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    if account not in {"JV", "XL"}:
        raise DiscoveryScanError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    normalized = _normalize_skus(skus)
    if not actor.strip():
        raise DiscoveryScanError("ACTOR_REQUIRED", "An authenticated operator is required.", status_code=403)
    current = now or datetime.now(timezone.utc)

    def execute() -> dict[str, Any]:
        return _create_reconciliation(connection, account=account, skus=normalized, now=current)

    try:
        return idempotent_operation_action(
            connection,
            account=account,
            actor=actor,
            action="discovery-known-sku-reconciliation",
            idempotency_key=idempotency_key,
            request_body=dict(request_body),
            execute=execute,
        )
    except OperationError as error:
        if error.code == "IDEMPOTENCY_CONFLICT":
            raise DiscoveryScanError(error.code, str(error), status_code=409) from None
        raise DiscoveryScanError(error.code, str(error), status_code=422) from None


def mark_run_started(connection: Connection, *, account: str, run_id: str) -> Mapping[str, Any]:
    row = connection.execute(
        text(
            "SELECT id, account, run_type, run_state, window_from, cutoff_at, parameters_json "
            "FROM import_runs WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"
        ),
        {"run_id": run_id, "account": account},
    ).mappings().first()
    if row is None:
        raise DiscoveryScanError("RUN_NOT_FOUND", "Discovery run is not available in this account.", status_code=404)
    if row["run_type"] not in {"INCREMENTAL_SCAN", "KNOWN_SKU_RECONCILIATION"}:
        raise DiscoveryScanError("INVALID_RUN_TYPE", "Job is not a discovery run.", status_code=409)
    if row["run_state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}:
        return row
    connection.execute(
        text(
            "UPDATE import_runs SET run_state=CASE WHEN run_state='PENDING' THEN 'RUNNING' ELSE run_state END, "
            "status='PROCESSING', started_at=COALESCE(started_at,CURRENT_TIMESTAMP) "
            "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION'"
        ),
        {"run_id": run_id, "account": account},
    )
    return row


def record_run_progress(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    coverage_patch: Mapping[str, Any],
) -> None:
    connection.execute(
        text(
            "UPDATE import_runs SET coverage_json=coverage_json || CAST(:patch AS jsonb), "
            "run_state=CASE WHEN run_state='RUNNING' THEN 'STAGED' ELSE run_state END, "
            "total_rows=(SELECT COUNT(*) FROM otto_status_observations "
            "WHERE run_id=:run_id AND account=:account AND data_scope='PRODUCTION') "
            "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION' "
            "AND run_state IN ('RUNNING','STAGED')"
        ),
        {"patch": json.dumps(dict(coverage_patch), separators=(",", ":")), "run_id": run_id, "account": account},
    )


def mark_run_failed(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    error_code: str,
    status_code: int | None = None,
    page: int | None = None,
) -> None:
    record_run_error(
        connection,
        account=account,
        run_id=run_id,
        error_code=error_code,
        status_code=status_code,
        page=page,
        terminal=True,
    )


def record_run_error(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    error_code: str,
    status_code: int | None = None,
    page: int | None = None,
    terminal: bool = False,
) -> None:
    entry = {"code": error_code}
    if status_code is not None:
        entry["httpStatus"] = int(status_code)
    if page is not None:
        entry["page"] = int(page)
    coverage = {"lastErrorCode": error_code}
    if page is not None:
        coverage["lastErrorPage"] = int(page)
    connection.execute(
        text(
            "UPDATE import_runs SET run_state=CASE WHEN :terminal THEN 'PARTIAL' ELSE run_state END, "
            "status=CASE WHEN :terminal THEN 'COMPLETED' ELSE status END, "
            "finished_at=CASE WHEN :terminal THEN CURRENT_TIMESTAMP ELSE finished_at END, "
            "error_json=error_json || CAST(:error AS jsonb), "
            "coverage_json=coverage_json || CAST(:coverage AS jsonb) "
            "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION' "
            "AND run_state NOT IN ('SUCCEEDED','PARTIAL','FAILED','CANCELLED')"
        ),
        {
            "error": json.dumps([entry], separators=(",", ":")),
            "coverage": json.dumps({**coverage, **({"pageState": "PARTIAL"} if terminal else {})}, separators=(",", ":")),
            "terminal": terminal,
            "run_id": run_id,
            "account": account,
        },
    )


def mark_run_terminal_error(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    error_code: str,
) -> None:
    mark_run_failed(connection, account=account, run_id=run_id, error_code=error_code)


def promote_cursor(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    cutoff_at: datetime,
) -> None:
    cutoff = _require_aware(cutoff_at, "cutoffAt")
    state = connection.execute(
        text(
            "SELECT run_state, run_type FROM import_runs WHERE id=:run_id AND account=:account "
            "AND data_scope='PRODUCTION' FOR UPDATE"
        ),
        {"run_id": run_id, "account": account},
    ).mappings().first()
    if not state or state["run_type"] != "INCREMENTAL_SCAN" or state["run_state"] != "SUCCEEDED":
        raise DiscoveryScanError("CURSOR_PROMOTION_REQUIRES_SUCCESS", "Only a successful incremental scan can advance its cursor.")
    connection.execute(
        text(
            "INSERT INTO discovery_cursors (account,data_scope,last_successful_cutoff,last_successful_run_id,overlap_seconds) "
            "VALUES (:account,'PRODUCTION',:cutoff,:run_id,:overlap) "
            "ON CONFLICT (account,data_scope) DO UPDATE SET "
            "last_successful_cutoff=EXCLUDED.last_successful_cutoff, "
            "last_successful_run_id=EXCLUDED.last_successful_run_id, overlap_seconds=EXCLUDED.overlap_seconds, "
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE discovery_cursors.last_successful_cutoff IS NULL "
            "OR discovery_cursors.last_successful_cutoff <= EXCLUDED.last_successful_cutoff"
        ),
        {"account": account, "cutoff": cutoff, "run_id": run_id, "overlap": OVERLAP_SECONDS},
    )


def list_scan_runs(connection: Connection, *, account: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            "SELECT id,account,run_type,run_state,window_from,cutoff_at,total_rows,coverage_json,error_json,created_at,started_at,finished_at "
            "FROM import_runs WHERE account=:account AND data_scope='PRODUCTION' "
            "AND run_type IN ('INCREMENTAL_SCAN','KNOWN_SKU_RECONCILIATION') "
            "ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"
        ),
        {"account": account, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(row) for row in rows]


def get_scan_run(connection: Connection, *, account: str, run_id: str) -> dict[str, Any] | None:
    try:
        normalized_id = str(UUID(run_id))
    except (TypeError, ValueError, AttributeError):
        return None
    row = connection.execute(
        text(
            "SELECT id,account,run_type,run_state,window_from,cutoff_at,total_rows,coverage_json,error_json,parameters_json,created_at,started_at,finished_at "
            "FROM import_runs WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION' "
            "AND run_type IN ('INCREMENTAL_SCAN','KNOWN_SKU_RECONCILIATION')"
        ),
        {"run_id": normalized_id, "account": account},
    ).mappings().first()
    return dict(row) if row else None

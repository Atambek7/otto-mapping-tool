"""Persistent, account-scoped Drip Feed schedules and immutable queue items.

This module only stages already-QC-approved Restart operations. Releasing a
scheduled item never submits a product or calls an external service.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.qc_service import QC_POLICY_VERSION

MAX_BATCH_ITEMS = 500
MAX_DAYS = 30
JOB_TYPE = "drip.release_item"


class DripError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, *, status: int = 409):
        self.code = code
        self.details = details or {}
        self.status = status
        super().__init__(message)


def _account(account: str) -> str:
    normalized = str(account or "").strip().upper()
    if normalized not in {"JV", "XL"}:
        raise DripError("INVALID_ACCOUNT", "Account must be JV or XL.", status=422)
    return normalized


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise DripError("INVALID_ID", f"{field} must be a UUID.", status=422) from None


def _operation_ids(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values:
        raise DripError("OPERATIONS_REQUIRED", "Select at least one READY operation.", status=422)
    if len(values) > MAX_BATCH_ITEMS:
        raise DripError("BATCH_LIMIT_EXCEEDED", f"A schedule can contain at most {MAX_BATCH_ITEMS} operations.", status=422)
    normalized = [_uuid(value, "operationId") for value in values]
    if len(normalized) != len(set(normalized)):
        raise DripError("DUPLICATE_OPERATION", "An operation may appear only once in a schedule.", status=422)
    return normalized


def _resolve_local_datetime(local_date: date, local_time: time, timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise DripError("INVALID_TIMEZONE", "Use a valid IANA timezone name.", status=422) from None
    if local_time.tzinfo is not None:
        raise DripError("INVALID_LOCAL_TIME", "Daily schedule time must be a local wall-clock time without an offset.", status=422)
    naive = datetime.combine(local_date, local_time)
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            candidates.append(aware)
    if not candidates:
        raise DripError("SCHEDULE_TIME_NONEXISTENT", "This local time does not exist because of a timezone transition.", status=422)
    if len(candidates) == 2 and candidates[0].utcoffset() != candidates[1].utcoffset():
        raise DripError("SCHEDULE_TIME_AMBIGUOUS", "This local time occurs twice because of a timezone transition; choose another time.", status=422)
    return candidates[0].astimezone(timezone.utc)


def _schedule(
    *, start_date: date, daily_time: time, timezone_name: str, total_days: int, now: datetime | None = None,
) -> list[datetime]:
    if type(total_days) is not int or not 1 <= total_days <= MAX_DAYS:
        raise DripError("INVALID_TOTAL_DAYS", f"totalDays must be between 1 and {MAX_DAYS}.", status=422)
    dates = [
        _resolve_local_datetime(start_date + timedelta(days=offset), daily_time, timezone_name)
        for offset in range(total_days)
    ]
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if dates[0] <= current.astimezone(timezone.utc):
        raise DripError("SCHEDULE_START_NOT_FUTURE", "The first schedule time must be in the future.", status=422)
    return dates


def _ready_operations(
    connection: Connection, *, account: str, operation_ids: list[str], lock: bool, allow_review: bool = False,
) -> list[dict[str, Any]]:
    suffix = "FOR UPDATE OF o" if lock else ""
    rows = connection.execute(
        text(
            """
            SELECT o.id::text AS operation_id, o.account, o.data_scope, o.status AS operation_status,
                   o.submission_state, o.current_revision_number, o.target_sku, o.new_ean,
                   ce.id::text AS episode_id, ce.period, ce.classification, ce.episode_state,
                   ce.confirmed_ean, sp.factory_id,
                   COALESCE(NULLIF(edit.category, ''), NULLIF(rev.content_json->>'category', ''), sp.category, '') AS category,
                   rev.id::text AS revision_id, rev.revision_number, rev.payload_hash,
                   qc.id::text AS qc_result_id, qc.outcome AS qc_outcome,
                   qc.policy_version AS qc_policy_version, qc.payload_hash AS qc_payload_hash
            FROM restart_operations o
            JOIN candidate_episodes ce ON ce.id=o.candidate_episode_id AND ce.account=o.account
              AND ce.data_scope=o.data_scope AND ce.source_product_id=o.source_product_id
            JOIN source_products sp ON sp.id=o.source_product_id AND sp.account=o.account AND sp.data_scope=o.data_scope
            LEFT JOIN operation_revisions rev ON rev.operation_id=o.id AND rev.account=o.account
              AND rev.data_scope='PRODUCTION' AND rev.revision_number=o.current_revision_number
            LEFT JOIN restart_product_edits edit ON edit.candidate_episode_id=ce.id
              AND edit.account=ce.account AND edit.data_scope=ce.data_scope
            LEFT JOIN LATERAL (
                SELECT result.id, result.outcome, result.policy_version, result.payload_hash
                FROM qc_results result
                WHERE result.operation_id=o.id AND result.revision_id=rev.id
                  AND result.account=o.account AND result.data_scope='PRODUCTION'
                ORDER BY result.created_at DESC, result.id DESC LIMIT 1
            ) qc ON TRUE
            WHERE o.id = ANY(CAST(:ids AS uuid[])) AND o.account=:account AND o.data_scope='PRODUCTION'
            ORDER BY o.id
            """ + suffix
        ),
        {"ids": operation_ids, "account": account},
    ).mappings().all()
    found = {str(row["operation_id"]): dict(row) for row in rows}
    missing = [operation_id for operation_id in operation_ids if operation_id not in found]
    if missing:
        raise DripError(
            "OPERATION_NOT_FOUND", "One or more selected operations are unavailable in this account.",
            {"operationIds": missing},
        )
    ordered = [found[operation_id] for operation_id in operation_ids]
    invalid: list[dict[str, str]] = []
    for row in ordered:
        reason = None
        if (allow_review and row["operation_status"] in {"DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW"}
            and row["submission_state"] == "NEVER_SUBMITTED"
            and row["episode_state"] == "OPEN" and row["classification"] == "EXACT_CODE"
            and str(row["category"] or "").strip()):
            continue
        if row["operation_status"] != "READY" or row["submission_state"] != "NEVER_SUBMITTED":
            reason = "OPERATION_NOT_READY"
        elif row["episode_state"] != "OPEN" or row["classification"] != "EXACT_CODE":
            reason = "RESTRICTION_NOT_CURRENT"
        elif not row["target_sku"] or not row["new_ean"] or not row["confirmed_ean"]:
            reason = "OPERATION_IDENTITY_INCOMPLETE"
        elif row["revision_id"] is None or int(row["revision_number"]) != int(row["current_revision_number"]):
            reason = "CURRENT_REVISION_MISSING"
        elif (
            row["qc_result_id"] is None or row["qc_outcome"] != "PASSED_AUTO"
            or row["qc_policy_version"] != QC_POLICY_VERSION
            or str(row["qc_payload_hash"]).strip() != str(row["payload_hash"]).strip()
        ):
            reason = "CURRENT_QC_NOT_PASSED"
        if reason:
            invalid.append({"operationId": str(row["operation_id"]), "code": reason})
    if invalid:
        raise DripError("OPERATIONS_NOT_SCHEDULABLE", "Every operation must have a current PASSED_AUTO QC result.", {"items": invalid})
    periods = {row["period"] for row in ordered}
    factories = {row["factory_id"] for row in ordered}
    if len(periods) != 1 or len(factories) != 1:
        raise DripError(
            "MIXED_COHORT", "A schedule may contain one account, one 3M/12M period, and one factory cohort.",
            {"periods": sorted(str(value) for value in periods), "factoryIds": sorted(str(value) for value in factories)},
        )
    return ordered


def _event(connection: Connection, *, account: str, actor: str, event_type: str, entity_type: str, entity_id: str, summary: str, metadata: dict[str, Any]) -> None:
    connection.execute(
        text(
            "INSERT INTO restart_events (id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json) "
            "VALUES (:id,:account,'PRODUCTION',:event_type,:entity_type,:entity_id,:summary,CAST(:metadata AS jsonb))"
        ),
        {"id": str(uuid4()), "account": account, "event_type": event_type,
         "entity_type": entity_type, "entity_id": entity_id, "summary": summary[:500],
         "metadata": json.dumps({"actor": actor, **metadata}, sort_keys=True, separators=(",", ":"))},
    )


def preview_schedule(
    connection: Connection, *, account: str, operation_ids: list[str], start_date: date,
    daily_time: time, timezone_name: str, total_days: int, now: datetime | None = None,
) -> dict[str, Any]:
    account = _account(account)
    ids = _operation_ids(operation_ids)
    ready = _ready_operations(connection, account=account, operation_ids=ids, lock=False, allow_review=True)
    day_starts = _schedule(start_date=start_date, daily_time=daily_time, timezone_name=timezone_name, total_days=total_days, now=now)
    day_operations: list[list[str]] = [[] for _ in range(total_days)]
    for index, row in enumerate(ready):
        day_index = min(total_days - 1, index * total_days // len(ready))
        day_operations[day_index].append(str(row["operation_id"]))
    distribution = [
        {"dayNumber": day_index + 1, "scheduledAt": scheduled_at.isoformat(),
         "operationIds": day_operations[day_index], "count": len(day_operations[day_index])}
        for day_index, scheduled_at in enumerate(day_starts)
    ]
    return {
        "account": account,
        "factoryId": ready[0]["factory_id"],
        "period": ready[0]["period"],
        "timezone": timezone_name,
        "totalDays": total_days,
        "itemCount": len(ready),
        "distribution": distribution,
        "externalServicesCalled": [],
        "publicationEnabled": False,
    }


def create_batch_draft(
    connection: Connection, *, account: str, name: str, operation_ids: list[str], start_date: date,
    daily_time: time, timezone_name: str, total_days: int, actor: str, now: datetime | None = None,
) -> dict[str, Any]:
    account = _account(account)
    title = str(name or "").strip()
    if not 1 <= len(title) <= 160:
        raise DripError("INVALID_BATCH_NAME", "Batch name must contain 1–160 characters.", status=422)
    ids = _operation_ids(operation_ids)
    ready = _ready_operations(connection, account=account, operation_ids=ids, lock=True, allow_review=True)
    schedule = _schedule(start_date=start_date, daily_time=daily_time, timezone_name=timezone_name, total_days=total_days, now=now)
    batch_id = str(uuid4())
    row = connection.execute(
        text(
            "INSERT INTO restart_batches (id,account,period,name,status,item_count,data_scope,factory_id,"
            "local_start_date,daily_time,timezone_name,scheduled_start_at,total_days,selected_operation_ids_json,"
            "created_by) VALUES (:id,:account,:period,:name,'DRAFT',:count,'PRODUCTION',:factory_id,:start_date,"
            ":daily_time,:timezone,:start_at,:total_days,CAST(:operation_ids AS jsonb),:actor) RETURNING *"
        ),
        {"id": batch_id, "account": account, "period": ready[0]["period"], "name": title,
         "count": len(ready), "factory_id": ready[0]["factory_id"], "start_date": start_date,
         "daily_time": daily_time, "timezone": timezone_name, "start_at": schedule[0],
         "total_days": total_days, "operation_ids": json.dumps(ids), "actor": actor[:200]},
    ).mappings().one()
    _event(connection, account=account, actor=actor, event_type="DRIP_BATCH_DRAFT_CREATED", entity_type="RESTART_BATCH",
           entity_id=batch_id, summary=f"Drip Feed draft created: {title}",
           metadata={"operationCount": len(ready), "period": ready[0]["period"], "timezone": timezone_name})
    return _batch_dto(row)


def _batch_dto(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "account": row["account"], "period": row["period"], "name": row["name"],
        "status": row["status"], "itemCount": int(row["item_count"]), "createdAt": row["created_at"].isoformat(),
        "factoryId": row["factory_id"], "localStartDate": row["local_start_date"].isoformat() if row["local_start_date"] else None,
        "dailyTime": row["daily_time"].isoformat(timespec="minutes") if row["daily_time"] else None,
        "timezone": row["timezone_name"], "scheduledStartAt": row["scheduled_start_at"].isoformat() if row["scheduled_start_at"] else None,
        "totalDays": int(row["total_days"]) if row["total_days"] is not None else None,
        "version": int(row["version"]), "startedAt": row["started_at"].isoformat() if row["started_at"] else None,
        "pausedAt": row["paused_at"].isoformat() if row["paused_at"] else None,
        "cancelledAt": row["cancelled_at"].isoformat() if row["cancelled_at"] else None,
    }


def _enqueue_release(connection: Connection, *, batch: Any, item: Any, now: datetime) -> None:
    generation = int(batch["schedule_generation"])
    enqueue_job(
        connection, account=str(batch["account"]), job_type=JOB_TYPE,
        idempotency_key=f"drip-release-{batch['id']}-{generation}-{item['id']}",
        payload={"batchId": str(batch["id"]), "itemId": str(item["id"]),
                 "operationId": str(item["operation_id"]), "revisionId": str(item["revision_id"]),
                 "qcResultId": str(item["qc_result_id"]), "payloadHash": str(item["payload_hash"]).strip(),
                 "scheduleGeneration": generation},
        available_at=max(item["scheduled_at"], now), max_attempts=5, now=now,
    )


def start_batch(
    connection: Connection, *, account: str, batch_id: str, expected_version: int, actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    account = _account(account)
    batch_id = _uuid(batch_id, "batchId")
    current = now or datetime.now(timezone.utc)
    batch = connection.execute(
        text("SELECT * FROM restart_batches WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": batch_id, "account": account},
    ).mappings().first()
    if batch is None:
        raise DripError("BATCH_NOT_FOUND", "Production Drip Feed batch was not found.", status=404)
    if int(batch["version"]) != expected_version:
        raise DripError("VERSION_CONFLICT", "Batch changed since it was read.", {"currentVersion": int(batch["version"])})
    if batch["status"] != "DRAFT":
        raise DripError("BATCH_NOT_STARTABLE", "Only a draft Drip Feed batch can be started.")
    stored_ids = batch["selected_operation_ids_json"]
    ids = stored_ids if isinstance(stored_ids, list) else json.loads(stored_ids or "[]")
    operations = _ready_operations(connection, account=account, operation_ids=ids, lock=True)
    days = _schedule(
        start_date=batch["local_start_date"], daily_time=batch["daily_time"], timezone_name=batch["timezone_name"],
        total_days=int(batch["total_days"]), now=current,
    )
    for index, operation in enumerate(operations):
        day_index = min(int(batch["total_days"]) - 1, index * int(batch["total_days"]) // len(operations))
        item = connection.execute(
            text(
                "INSERT INTO restart_items (batch_id,account,source_ean,row_number,data_scope,operation_id,"
                "revision_id,qc_result_id,payload_hash,scheduled_at,status) VALUES (:batch_id,:account,:source_ean,"
                ":row_number,'PRODUCTION',:operation_id,:revision_id,:qc_id,:payload_hash,:scheduled_at,'SCHEDULED') "
                "RETURNING *"
            ),
            {"batch_id": batch_id, "account": account, "source_ean": operation["confirmed_ean"],
             "row_number": index + 1, "operation_id": operation["operation_id"],
             "revision_id": operation["revision_id"], "qc_id": operation["qc_result_id"],
             "payload_hash": str(operation["payload_hash"]).strip(), "scheduled_at": days[day_index]},
        ).mappings().one()
        batch_for_job = dict(batch)
        batch_for_job["schedule_generation"] = 1
        _enqueue_release(connection, batch=batch_for_job, item=item, now=current)
    updated = connection.execute(
        text("UPDATE restart_batches SET status='SCHEDULED',started_at=:now,selected_operation_ids_json='[]'::jsonb,"
             "version=version+1,updated_at=:now WHERE id=:id AND account=:account RETURNING *"),
        {"id": batch_id, "account": account, "now": current},
    ).mappings().one()
    _event(connection, account=account, actor=actor, event_type="DRIP_BATCH_STARTED", entity_type="RESTART_BATCH",
           entity_id=batch_id, summary=f"Drip Feed schedule started: {batch['name']}",
           metadata={
               "itemCount": len(operations),
               "generation": 1,
               # The API handler does not know worker runtime flags. The worker
               # evaluates them when releasing each scheduled item.
               "submissionDecision": "DEFERRED_TO_WORKER_RUNTIME_GATES",
               "externalServicesCalled": [],
           })
    return _batch_dto(updated)


def list_batches(connection: Connection, *, account: str, page: int = 1, page_size: int = 50, status: str | None = None) -> dict[str, Any]:
    account = _account(account)
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
        raise DripError("INVALID_PAGINATION", "Page must be positive and pageSize must be between 1 and 200.", status=422)
    allowed = {"DRAFT", "SCHEDULED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"}
    if status and status not in allowed:
        raise DripError("INVALID_STATUS", "Unsupported production Drip Feed status.", status=422)
    where = "account=:account AND data_scope='PRODUCTION' AND status IN ('DRAFT','SCHEDULED','ACTIVE','PAUSED','COMPLETED','CANCELLED')"
    params: dict[str, Any] = {"account": account}
    if status:
        where += " AND status=:status"
        params["status"] = status
    total = int(connection.execute(text(f"SELECT COUNT(*) FROM restart_batches WHERE {where}"), params).scalar_one())
    rows = connection.execute(
        text(f"SELECT * FROM restart_batches WHERE {where} ORDER BY created_at DESC,id DESC LIMIT :limit OFFSET :offset"),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {"items": [_batch_dto(row) for row in rows],
            "page": {"page": page, "pageSize": page_size, "totalItems": total, "totalPages": total_pages}}


def get_batch(connection: Connection, *, account: str, batch_id: str) -> dict[str, Any]:
    account = _account(account)
    batch_id = _uuid(batch_id, "batchId")
    row = connection.execute(
        text("SELECT * FROM restart_batches WHERE id=:id AND account=:account AND data_scope='PRODUCTION' "
             "AND status IN ('DRAFT','SCHEDULED','ACTIVE','PAUSED','COMPLETED','CANCELLED')"),
        {"id": batch_id, "account": account},
    ).mappings().first()
    if row is None:
        raise DripError("BATCH_NOT_FOUND", "Production Drip Feed batch was not found.", status=404)
    result = _batch_dto(row)
    counts = connection.execute(
        text("SELECT status,COUNT(*) AS count FROM restart_items WHERE batch_id=:id AND account=:account "
             "AND data_scope='PRODUCTION' GROUP BY status"), {"id": batch_id, "account": account},
    ).mappings().all()
    result["itemCounts"] = {row["status"]: int(row["count"]) for row in counts}
    return result


def list_batch_items(connection: Connection, *, account: str, batch_id: str, page: int = 1, page_size: int = 50, query: str | None = None) -> dict[str, Any]:
    account = _account(account)
    batch = _uuid(batch_id, "batchId")
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
        raise DripError("INVALID_PAGINATION", "Page must be positive and pageSize must be between 1 and 200.", status=422)
    where = "item.batch_id=:batch_id AND item.account=:account AND item.data_scope='PRODUCTION'"
    params: dict[str, Any] = {"batch_id": batch, "account": account}
    if query and query.strip():
        where += " AND (item.source_ean ILIKE :query OR op.target_sku ILIKE :query OR ce.observed_sku ILIKE :query)"
        params["query"] = f"%{query.strip()[:100]}%"
    total = int(connection.execute(
        text("SELECT COUNT(*) FROM restart_items item JOIN restart_operations op ON op.id=item.operation_id "
             "AND op.account=item.account AND op.data_scope=item.data_scope JOIN candidate_episodes ce "
             "ON ce.id=op.candidate_episode_id AND ce.account=op.account AND ce.data_scope=op.data_scope "
             f"WHERE {where}"), params,
    ).scalar_one())
    rows = connection.execute(
        text(
            "SELECT item.id,item.batch_id,item.account,item.source_ean,item.row_number,item.operation_id::text,"
            "item.revision_id::text,item.qc_result_id::text,item.payload_hash,item.scheduled_at,item.status,"
            "item.release_attempts,item.last_error_code,item.ready_at,op.target_sku,ce.observed_sku,"
            "rev.revision_number,COALESCE(job_attempts.total_attempts,0) AS total_attempts "
            "FROM restart_items item JOIN restart_operations op ON op.id=item.operation_id AND op.account=item.account "
            "AND op.data_scope=item.data_scope JOIN candidate_episodes ce ON ce.id=op.candidate_episode_id "
            "AND ce.account=op.account AND ce.data_scope=op.data_scope JOIN operation_revisions rev "
            "ON rev.id=item.revision_id AND rev.operation_id=item.operation_id AND rev.account=item.account "
            "AND rev.data_scope=item.data_scope LEFT JOIN LATERAL (SELECT SUM(attempt.total_attempts) AS total_attempts "
            "FROM background_jobs job LEFT JOIN LATERAL (SELECT COUNT(*) AS total_attempts FROM background_job_attempts a "
            "WHERE a.job_id=job.id) attempt ON TRUE WHERE job.job_type=:job_type "
            "AND job.payload_json->>'itemId'=item.id::text AND job.account=item.account AND job.data_scope=item.data_scope) job_attempts ON TRUE "
            f"WHERE {where} ORDER BY item.row_number LIMIT :limit OFFSET :offset"
        ),
        {**params, "job_type": JOB_TYPE, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": [{"id": int(row["id"]), "batchId": str(row["batch_id"]), "account": row["account"],
                   "sourceEan": row["source_ean"], "rowNumber": int(row["row_number"]),
                   "operationId": str(row["operation_id"]), "revisionId": str(row["revision_id"]),
                   "qcResultId": str(row["qc_result_id"]), "payloadHash": str(row["payload_hash"]).strip(),
                   "scheduledAt": row["scheduled_at"].isoformat(), "status": row["status"],
                   "releaseAttempts": int(row["release_attempts"]), "lastErrorCode": row["last_error_code"],
                   "readyAt": row["ready_at"].isoformat() if row["ready_at"] else None,
                   "targetSku": row["target_sku"], "sourceSku": row["observed_sku"],
                   "revisionNumber": int(row["revision_number"]), "totalAttempts": int(row["total_attempts"] or 0)} for row in rows],
        "page": {"page": page, "pageSize": page_size, "totalItems": total, "totalPages": total_pages},
    }


def _lock_batch(connection: Connection, *, account: str, batch_id: str, expected_version: int) -> Any:
    batch = connection.execute(
        text("SELECT * FROM restart_batches WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": _uuid(batch_id, "batchId"), "account": _account(account)},
    ).mappings().first()
    if batch is None:
        raise DripError("BATCH_NOT_FOUND", "Production Drip Feed batch was not found.", status=404)
    if int(batch["version"]) != expected_version:
        raise DripError("VERSION_CONFLICT", "Batch changed since it was read.", {"currentVersion": int(batch["version"])})
    return batch


def _cancel_pending_jobs(connection: Connection, *, batch: Any, now: datetime) -> None:
    connection.execute(
        text("UPDATE background_jobs SET state='CANCELLED',completed_at=:now,lease_owner=NULL,lease_token=NULL,"
             "lease_until=NULL,dispatch_lease_token=NULL,dispatch_lease_until=NULL,updated_at=:now "
             "WHERE account=:account AND data_scope='PRODUCTION' AND job_type=:job_type "
             "AND payload_json->>'batchId'=:batch_id AND state IN ('PENDING','RETRY_WAIT')"),
        {"now": now, "account": batch["account"], "job_type": JOB_TYPE, "batch_id": str(batch["id"])},
    )


def _enqueue_scheduled_items(connection: Connection, *, batch: Any, now: datetime) -> int:
    rows = connection.execute(
        text("SELECT * FROM restart_items WHERE batch_id=:batch_id AND account=:account AND data_scope='PRODUCTION' "
             "AND status='SCHEDULED' ORDER BY row_number FOR UPDATE"),
        {"batch_id": batch["id"], "account": batch["account"]},
    ).mappings().all()
    for item in rows:
        _enqueue_release(connection, batch=batch, item=item, now=now)
    return len(rows)


def control_batch(
    connection: Connection, *, account: str, batch_id: str, action: str, expected_version: int, actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    batch = _lock_batch(connection, account=account, batch_id=batch_id, expected_version=expected_version)
    batch_id = str(batch["id"])
    if action == "pause":
        if batch["status"] not in {"SCHEDULED", "ACTIVE"}:
            raise DripError("BATCH_NOT_PAUSABLE", "Only a scheduled or active batch can be paused.")
        _cancel_pending_jobs(connection, batch=batch, now=current)
        connection.execute(
            text("UPDATE restart_batches SET status='PAUSED',paused_at=:now,schedule_generation=schedule_generation+1,"
                 "version=version+1,updated_at=:now WHERE id=:id AND account=:account"),
            {"now": current, "id": batch_id, "account": account},
        )
        event_type = "DRIP_BATCH_PAUSED"
    elif action == "resume":
        if batch["status"] != "PAUSED":
            raise DripError("BATCH_NOT_RESUMABLE", "Only a paused batch can be resumed.")
        generation = int(batch["schedule_generation"]) + 1
        updated = connection.execute(
            text("UPDATE restart_batches SET status=CASE WHEN scheduled_start_at>:now THEN 'SCHEDULED' ELSE 'ACTIVE' END,"
                 "paused_at=NULL,schedule_generation=:generation,version=version+1,updated_at=:now "
                 "WHERE id=:id AND account=:account RETURNING *"),
            {"now": current, "generation": generation, "id": batch_id, "account": account},
        ).mappings().one()
        batch = dict(updated)
        enqueued = _enqueue_scheduled_items(connection, batch=batch, now=current)
        event_type = "DRIP_BATCH_RESUMED"
    elif action == "cancel":
        if batch["status"] in {"COMPLETED", "CANCELLED"}:
            raise DripError("BATCH_NOT_CANCELLABLE", "A completed or cancelled batch cannot be cancelled again.")
        _cancel_pending_jobs(connection, batch=batch, now=current)
        connection.execute(
            text("UPDATE restart_items SET status='CANCELLED',version=version+1 WHERE batch_id=:id AND account=:account "
                 "AND data_scope='PRODUCTION' AND status IN ('SCHEDULED','READY_TO_SUBMIT')"),
            {"id": batch_id, "account": account},
        )
        connection.execute(
            text("UPDATE restart_batches SET status='CANCELLED',cancelled_at=:now,schedule_generation=schedule_generation+1,"
                 "version=version+1,updated_at=:now WHERE id=:id AND account=:account"),
            {"now": current, "id": batch_id, "account": account},
        )
        event_type = "DRIP_BATCH_CANCELLED"
    elif action == "retry":
        if batch["status"] not in {"SCHEDULED", "ACTIVE", "PAUSED"}:
            raise DripError("BATCH_NOT_RETRYABLE", "Only a non-terminal Drip Feed batch can retry scheduled releases.")
        generation = int(batch["schedule_generation"]) + 1
        _cancel_pending_jobs(connection, batch=batch, now=current)
        updated = connection.execute(
            text("UPDATE restart_batches SET schedule_generation=:generation,version=version+1,updated_at=:now "
                 "WHERE id=:id AND account=:account RETURNING *"),
            {"generation": generation, "now": current, "id": batch_id, "account": account},
        ).mappings().one()
        batch = dict(updated)
        enqueued = _enqueue_scheduled_items(connection, batch=batch, now=current)
        event_type = "DRIP_BATCH_RELEASES_RETRIED"
    else:
        raise DripError("INVALID_BATCH_ACTION", "Unsupported Drip Feed batch action.", status=422)
    _event(connection, account=account, actor=actor, event_type=event_type, entity_type="RESTART_BATCH",
           entity_id=batch_id, summary=f"Drip Feed action: {action}", metadata={"action": action, "enqueued": locals().get("enqueued", 0)})
    return get_batch(connection, account=account, batch_id=batch_id)


def release_scheduled_item(
    connection: Connection, *, account: str, payload: dict[str, Any],
    now: datetime | None = None, submit_enabled: bool = False,
) -> dict[str, Any]:
    """Mark a due queue item ready for S20; this function has no network path."""
    if type(submit_enabled) is not bool:
        raise DripError("INVALID_SUBMISSION_FLAG", "submit_enabled must be a boolean.", status=422)
    current = now or datetime.now(timezone.utc)
    account = _account(account)
    batch_id = _uuid(payload.get("batchId"), "batchId")
    item_id = payload.get("itemId")
    if not str(item_id).isdigit():
        raise DripError("INVALID_ITEM_ID", "itemId must be an integer.")
    batch = connection.execute(
        text("SELECT * FROM restart_batches WHERE id=:id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": batch_id, "account": account},
    ).mappings().first()
    if batch is None:
        return {"released": False, "code": "BATCH_NOT_FOUND"}
    if int(payload.get("scheduleGeneration", -1)) != int(batch["schedule_generation"]):
        return {"released": False, "code": "STALE_SCHEDULE_GENERATION"}
    item = connection.execute(
        text("SELECT * FROM restart_items WHERE id=:id AND batch_id=:batch_id AND account=:account "
             "AND data_scope='PRODUCTION' FOR UPDATE"),
        {"id": int(item_id), "batch_id": batch_id, "account": account},
    ).mappings().first()
    if item is None:
        return {"released": False, "code": "ITEM_NOT_FOUND"}
    if batch["status"] not in {"SCHEDULED", "ACTIVE"}:
        if batch["status"] == "CANCELLED" and item["status"] == "SCHEDULED":
            connection.execute(text("UPDATE restart_items SET status='CANCELLED',version=version+1 WHERE id=:id"), {"id": item_id})
        return {"released": False, "code": "BATCH_NOT_ACTIVE"}
    if item["status"] != "SCHEDULED":
        return {"released": item["status"] == "READY_TO_SUBMIT", "code": "ALREADY_PROCESSED"}
    if item["scheduled_at"] > current:
        return {"released": False, "code": "NOT_DUE"}
    operation = connection.execute(
        text("SELECT o.status,o.submission_state,o.version,o.current_revision_number,rev.id::text AS revision_id,rev.payload_hash,"
             "qc.id::text AS qc_id,qc.outcome,qc.payload_hash AS qc_hash "
             "FROM restart_operations o JOIN operation_revisions rev ON rev.operation_id=o.id AND rev.account=o.account "
             "AND rev.data_scope='PRODUCTION' AND rev.revision_number=o.current_revision_number "
             "LEFT JOIN LATERAL (SELECT r.id,r.outcome,r.payload_hash FROM qc_results r WHERE r.operation_id=o.id "
             "AND r.revision_id=rev.id AND r.account=o.account AND r.data_scope='PRODUCTION' "
             "ORDER BY r.created_at DESC,r.id DESC LIMIT 1) qc ON TRUE "
             "WHERE o.id=:operation_id AND o.account=:account AND o.data_scope='PRODUCTION' FOR UPDATE OF o"),
        {"operation_id": item["operation_id"], "account": account},
    ).mappings().first()
    valid = bool(
        operation and operation["status"] == "READY" and operation["submission_state"] == "NEVER_SUBMITTED"
        and str(operation["revision_id"]) == str(item["revision_id"])
        and str(operation["qc_id"]) == str(item["qc_result_id"])
        and operation["outcome"] == "PASSED_AUTO"
        and str(operation["qc_hash"]).strip() == str(item["payload_hash"]).strip()
        and str(operation["payload_hash"]).strip() == str(item["payload_hash"]).strip()
    )
    if not valid:
        connection.execute(
            text("UPDATE restart_items SET status='NEEDS_REVIEW',last_error_code='STALE_QC_OR_REVISION',"
                 "release_attempts=release_attempts+1,version=version+1 WHERE id=:id"), {"id": item_id},
        )
        return {"released": False, "code": "STALE_QC_OR_REVISION"}
    if batch["status"] == "SCHEDULED":
        connection.execute(
            text("UPDATE restart_batches SET status='ACTIVE',version=version+1,updated_at=:now WHERE id=:id AND account=:account"),
            {"now": current, "id": batch_id, "account": account},
        )
    connection.execute(
        text("UPDATE restart_items SET status='READY_TO_SUBMIT',ready_at=:now,release_attempts=release_attempts+1,"
             "last_error_code=NULL,version=version+1 WHERE id=:id AND status='SCHEDULED'"),
        {"now": current, "id": item_id},
    )
    result = {"released": True, "code": "READY_TO_SUBMIT", "itemId": int(item_id)}
    event_metadata: dict[str, Any] = {
        "batchId": batch_id, "operationId": str(item["operation_id"]), "revisionId": str(item["revision_id"]),
        "externalServicesCalled": [], "productSubmissionQueued": False,
    }
    if submit_enabled:
        from services.submission_service import enqueue_submission_job

        queued = enqueue_submission_job(
            connection, account=account, item_id=int(item_id), operation_id=str(item["operation_id"]),
            revision_id=str(item["revision_id"]), payload_hash=str(item["payload_hash"]).strip(),
            expected_operation_version=int(operation["version"]),
        )
        result["submissionJobId"] = queued["jobId"]
        event_metadata.update({"productSubmissionQueued": True, "submissionJobId": queued["jobId"]})
    _event(connection, account=account, actor="system:drip-release", event_type="DRIP_ITEM_READY_TO_SUBMIT",
           entity_type="RESTART_ITEM", entity_id=str(item_id), summary="Scheduled item released after current QC recheck",
           metadata=event_metadata)
    return result

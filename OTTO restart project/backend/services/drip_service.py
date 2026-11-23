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
from services.publication_state import (
    drip_item_status_sql,
    operation_publication_state_sql,
    publication_evidence_joins,
)

MAX_BATCH_ITEMS = 50000
MAX_DAYS = 30
JOB_TYPE = "drip.release_item"
ITEM_STATUS_GROUPS = {
    "queue": ("PENDING", "BUILDING", "DRAFT", "PREPARING", "QC_PENDING", "READY", "SCHEDULED", "READY_TO_SUBMIT"),
    "processing": ("SUBMITTING", "OTTO_PROCESSING"),
    "published": ("AWAITING_ONLINE", "PUBLISHED"),
    "errors": ("NEEDS_REVIEW", "FAILED", "RECONCILIATION_REQUIRED"),
}


def _item_group_counts(item_counts: dict[str, int]) -> dict[str, int]:
    return {"all": sum(item_counts.values()), **{
        group: sum(int(item_counts.get(status, 0)) for status in statuses)
        for group, statuses in ITEM_STATUS_GROUPS.items()
    }}


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
        if (allow_review and row["operation_status"] in {"DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"}
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


def apply_known_anti_collision_schedule(
    connection: Connection, *, account: str, operations: list[tuple[dict[str, Any], datetime]],
    now: datetime | None = None,
) -> list[tuple[dict[str, Any], datetime]]:
    """Account for currently published/in-flight opposite-account source EANs.

    The release-time advisory lock remains authoritative for activity that
    begins after this preview or while the schedule is waiting.
    """
    if not operations:
        return []
    account = _account(account)
    opposite = "XL" if account == "JV" else "JV"
    current = now or datetime.now(timezone.utc)
    eans = sorted({str(row.get("confirmed_ean") or row.get("source_ean") or "").strip()
                   for row, _ in operations} - {""})
    latest_by_ean: dict[str, datetime] = {}
    if eans:
        rows = connection.execute(text(
            "SELECT other.source_ean,MAX(CASE WHEN other.status='PUBLISHED' "
            "THEN COALESCE(obs.observed_at,o.updated_at) ELSE :now END) AS last_at "
            "FROM restart_items other JOIN restart_batches b ON b.id=other.batch_id AND b.account=other.account "
            "JOIN restart_operations o ON o.id=other.operation_id AND o.account=other.account "
            "LEFT JOIN otto_status_observations obs ON obs.id=o.online_observation_id AND obs.account=o.account "
            "WHERE other.account=:opposite AND other.data_scope='PRODUCTION' AND other.source_ean=ANY(CAST(:eans AS text[])) "
            "AND b.status<>'CANCELLED' AND other.status IN ('READY_TO_SUBMIT','SUBMITTING','OTTO_PROCESSING','AWAITING_ONLINE','PUBLISHED') "
            "GROUP BY other.source_ean"),
            {"opposite": opposite, "eans": eans, "now": current},
        ).mappings().all()
        latest_by_ean = {str(row["source_ean"]): row["last_at"] for row in rows if row["last_at"] is not None}
    result = []
    for operation, scheduled_at in operations:
        source_ean = str(operation.get("confirmed_ean") or operation.get("source_ean") or "").strip()
        last_at = latest_by_ean.get(source_ean)
        collision_safe_at = last_at + timedelta(days=2) if last_at is not None else scheduled_at
        result.append((operation, max(scheduled_at, collision_safe_at)))
    return result


def preview_schedule(
    connection: Connection, *, account: str, operation_ids: list[str], start_date: date,
    daily_time: time, timezone_name: str, total_days: int, now: datetime | None = None,
) -> dict[str, Any]:
    account = _account(account)
    ids = _operation_ids(operation_ids)
    ready = _ready_operations(connection, account=account, operation_ids=ids, lock=False, allow_review=True)
    day_starts = _schedule(start_date=start_date, daily_time=daily_time, timezone_name=timezone_name, total_days=total_days, now=now)
    from services.drip_distribution import distribute
    scheduled = apply_known_anti_collision_schedule(
        connection, account=account, operations=distribute(ready, day_starts), now=now,
    )
    grouped: dict[datetime, list[str]] = {start: [] for start in day_starts}
    for row, scheduled_at in scheduled:
        grouped.setdefault(scheduled_at, []).append(str(row["operation_id"]))
    zone = ZoneInfo(timezone_name)
    first_local_date = day_starts[0].astimezone(zone).date()
    distribution = [
        {"dayNumber": (scheduled_at.astimezone(zone).date() - first_local_date).days + 1,
         "scheduledAt": scheduled_at.isoformat(), "operationIds": operation_ids, "count": len(operation_ids)}
        for scheduled_at, operation_ids in sorted(grouped.items())
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
        idempotency_key=f"drip-release-{batch['id']}-{generation}-{item['id']}-{item['revision_id']}-{item['qc_result_id']}",
        payload={"batchId": str(batch["id"]), "itemId": str(item["id"]),
                 "operationId": str(item["operation_id"]), "revisionId": str(item["revision_id"]),
                 "qcResultId": str(item["qc_result_id"]), "payloadHash": str(item["payload_hash"]).strip(),
                 "scheduleGeneration": generation},
        available_at=max(item["scheduled_at"], now), max_attempts=5, now=now,
    )


def start_batch(
    connection: Connection, *, account: str, batch_id: str, expected_version: int, actor: str,
    now: datetime | None = None, auto_prepare: bool = False, heartbeat=None,
    scheduled_assignments: dict[str, datetime] | None = None,
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
    operations = _ready_operations(connection, account=account, operation_ids=ids, lock=True, allow_review=auto_prepare)
    if auto_prepare:
        from services.drip_preparation import start_automatic_batch
        return start_automatic_batch(connection, batch=batch, operations=operations, actor=actor, now=current,
                                     heartbeat=heartbeat, scheduled_assignments=scheduled_assignments)
    days = _schedule(
        start_date=batch["local_start_date"], daily_time=batch["daily_time"], timezone_name=batch["timezone_name"],
        total_days=int(batch["total_days"]), now=current,
    )
    from services.drip_distribution import distribute
    scheduled = apply_known_anti_collision_schedule(
        connection, account=account, operations=distribute(operations, days), now=current,
    )
    for index, (operation, scheduled_at) in enumerate(scheduled):
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
             "payload_hash": str(operation["payload_hash"]).strip(), "scheduled_at": scheduled_at},
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


def list_batches(
    connection: Connection, *, account: str, page: int = 1, page_size: int = 50,
    status: str | None = None, factory_id: str | None = None, source_type: str | None = None,
    plan_request_id: str | None = None, query: str | None = None, unlinked_only: bool = False,
) -> dict[str, Any]:
    account = _account(account)
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
        raise DripError("INVALID_PAGINATION", "Page must be positive and pageSize must be between 1 and 200.", status=422)
    allowed = {"DRAFT", "SCHEDULED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"}
    if status and status not in allowed:
        raise DripError("INVALID_STATUS", "Unsupported production Drip Feed status.", status=422)
    where = "b.account=:account AND b.data_scope='PRODUCTION' AND b.status IN ('DRAFT','SCHEDULED','ACTIVE','PAUSED','COMPLETED','CANCELLED')"
    params: dict[str, Any] = {"account": account}
    if status:
        where += " AND b.status=:status"
        params["status"] = status
    if factory_id and factory_id.strip():
        where += " AND b.factory_id=:factory_id"
        params["factory_id"] = factory_id.strip()[:200]
    if plan_request_id:
        where += " AND b.plan_request_id=:plan_request_id"
        params["plan_request_id"] = _uuid(plan_request_id, "planRequestId")
    if unlinked_only:
        where += " AND b.plan_request_id IS NULL"
    if source_type:
        source_values = {"AFTERBUY": ("AFTERBUY", "AFTERCOOL"), "OTTO_DIRECT": ("OTTO_DIRECT",)}.get(source_type)
        if not source_values:
            raise DripError("INVALID_SOURCE", "Source must be AFTERBUY or OTTO_DIRECT.", status=422)
        where += " AND (EXISTS (SELECT 1 FROM restart_items ri JOIN restart_operations ro ON ro.id=ri.operation_id "
        where += "AND ro.account=ri.account AND ro.data_scope=ri.data_scope JOIN source_products rsp "
        where += "ON rsp.id=ro.source_product_id AND rsp.account=ro.account AND rsp.data_scope=ro.data_scope "
        where += "WHERE ri.batch_id=b.id AND ri.account=b.account AND ri.data_scope=b.data_scope AND rsp.source_type=ANY(:source_types)) "
        where += "OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(b.selected_operation_ids_json,'[]'::jsonb)) selected(operation_id) "
        where += "JOIN restart_operations ro ON ro.id=selected.operation_id::uuid AND ro.account=b.account AND ro.data_scope=b.data_scope "
        where += "JOIN source_products rsp ON rsp.id=ro.source_product_id AND rsp.account=ro.account AND rsp.data_scope=ro.data_scope "
        where += "WHERE rsp.source_type=ANY(:source_types)))"
        params["source_types"] = list(source_values)
    if query and query.strip():
        where += " AND (b.name ILIKE :query OR b.factory_id ILIKE :query OR EXISTS ("
        where += "SELECT 1 FROM restart_items ri JOIN restart_operations ro ON ro.id=ri.operation_id "
        where += "AND ro.account=ri.account AND ro.data_scope=ri.data_scope JOIN candidate_episodes ce "
        where += "ON ce.id=ro.candidate_episode_id AND ce.account=ro.account AND ce.data_scope=ro.data_scope "
        where += "JOIN source_products sp ON sp.id=ro.source_product_id AND sp.account=ro.account AND sp.data_scope=ro.data_scope "
        where += "LEFT JOIN operation_revisions rev ON rev.id=ri.revision_id AND rev.operation_id=ri.operation_id "
        where += "AND rev.account=ri.account AND rev.data_scope=ri.data_scope WHERE ri.batch_id=b.id "
        where += "AND ri.account=b.account AND ri.data_scope=b.data_scope AND (ri.source_ean ILIKE :query "
        where += "OR ro.target_sku ILIKE :query OR ce.observed_sku ILIKE :query OR sp.aftercool_sku ILIKE :query "
        where += "OR sp.aftercool_ean ILIKE :query OR sp.original_title ILIKE :query OR rev.content_json->>'title' ILIKE :query)) OR EXISTS ("
        where += "SELECT 1 FROM jsonb_array_elements_text(COALESCE(b.selected_operation_ids_json,'[]'::jsonb)) selected(operation_id) "
        where += "JOIN restart_operations ro ON ro.id=selected.operation_id::uuid AND ro.account=b.account AND ro.data_scope=b.data_scope "
        where += "JOIN candidate_episodes ce ON ce.id=ro.candidate_episode_id AND ce.account=ro.account AND ce.data_scope=ro.data_scope "
        where += "JOIN source_products sp ON sp.id=ro.source_product_id AND sp.account=ro.account AND sp.data_scope=ro.data_scope "
        where += "LEFT JOIN operation_revisions rev ON rev.operation_id=ro.id AND rev.account=ro.account AND rev.data_scope=ro.data_scope "
        where += "AND rev.revision_number=ro.current_revision_number WHERE sp.original_title ILIKE :query OR ce.confirmed_ean ILIKE :query "
        where += "OR ce.observed_sku ILIKE :query OR ro.target_sku ILIKE :query OR ro.new_ean ILIKE :query "
        where += "OR rev.content_json->>'title' ILIKE :query))"
        params["query"] = f"%{query.strip()[:100]}%"
    total = int(connection.execute(text(f"SELECT COUNT(*) FROM restart_batches b WHERE {where}"), params).scalar_one())
    rows = connection.execute(
        text(f"SELECT b.* FROM restart_batches b WHERE {where} ORDER BY b.created_at DESC,b.id DESC LIMIT :limit OFFSET :offset"),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {"items": [_batch_dto(row) for row in rows],
            "page": {"page": page, "pageSize": page_size, "totalItems": total, "totalPages": total_pages}}


def list_batch_manifest(
    connection: Connection, *, account: str, batch_id: str, page: int = 1, page_size: int = 50,
    query: str | None = None, stage: str | None = None,
) -> dict[str, Any]:
    """Read the immutable operation selection stored on a batch before queue rows exist."""
    account = _account(account)
    batch_id = _uuid(batch_id, "batchId")
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
        raise DripError("INVALID_PAGINATION", "Page must be positive and pageSize must be between 1 and 200.", status=422)
    batch = connection.execute(
        text("SELECT id,account,data_scope,selected_operation_ids_json FROM restart_batches "
             "WHERE id=:id AND account=:account AND data_scope='PRODUCTION'"),
        {"id": batch_id, "account": account},
    ).mappings().first()
    if batch is None:
        raise DripError("BATCH_NOT_FOUND", "Production Drip Feed batch was not found.", status=404)

    allowed_stages = {
        "PREPARING", "DRAFT", "QC_PENDING", "READY", "QUEUED", "SCHEDULED", "READY_TO_SUBMIT",
        "NEEDS_REVIEW", "SUBMITTING", "OTTO_PROCESSING", "AWAITING_ONLINE", "PUBLISHED",
        "RECONCILIATION_REQUIRED", "FAILED", "CANCELLED",
    }
    where = "b.id=:batch_id AND b.account=:account AND b.data_scope='PRODUCTION'"
    params: dict[str, Any] = {"batch_id": batch_id, "account": account}
    if stage:
        if stage not in allowed_stages:
            raise DripError("INVALID_ITEM_STAGE", "Unsupported Drip Feed item stage.", status=422)
        where += " AND COALESCE(queue.status,op.status)=:stage"
        params["stage"] = stage
    if query and query.strip():
        where += " AND (ce.confirmed_ean ILIKE :query OR ce.observed_sku ILIKE :query OR op.target_sku ILIKE :query "
        where += "OR op.new_ean ILIKE :query OR sp.aftercool_sku ILIKE :query OR sp.aftercool_ean ILIKE :query "
        where += "OR sp.original_title ILIKE :query OR edit.title ILIKE :query OR rev.content_json->>'title' ILIKE :query)"
        params["query"] = f"%{query.strip()[:100]}%"

    joins = (
        "FROM restart_batches b CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(b.selected_operation_ids_json,'[]'::jsonb)) "
        "WITH ORDINALITY selected(operation_id,row_number) JOIN restart_operations op "
        "ON op.id=selected.operation_id::uuid AND op.account=b.account AND op.data_scope=b.data_scope "
        "JOIN candidate_episodes ce ON ce.id=op.candidate_episode_id AND ce.account=op.account AND ce.data_scope=op.data_scope "
        "JOIN source_products sp ON sp.id=op.source_product_id AND sp.account=op.account AND sp.data_scope=op.data_scope "
        "LEFT JOIN operation_revisions rev ON rev.operation_id=op.id AND rev.account=op.account AND rev.data_scope=op.data_scope "
        "AND rev.revision_number=op.current_revision_number LEFT JOIN restart_product_edits edit "
        "ON edit.candidate_episode_id=ce.id AND edit.account=ce.account AND edit.data_scope=ce.data_scope "
        "LEFT JOIN LATERAL (SELECT ri.status,ri.scheduled_at,ri.last_error_code FROM restart_items ri "
        "WHERE ri.batch_id=b.id AND ri.operation_id=op.id AND ri.account=b.account AND ri.data_scope=b.data_scope "
        "ORDER BY ri.id DESC LIMIT 1) queue ON TRUE "
    )
    total = int(connection.execute(text(f"SELECT COUNT(*) {joins} WHERE {where}"), params).scalar_one())
    rows = connection.execute(text(
        "SELECT selected.row_number,ce.id::text AS candidate_episode_id,b.period,ce.confirmed_ean AS source_ean,"
        "ce.observed_sku AS source_sku,sp.source_type,sp.factory_id,sp.factory_name,"
        "COALESCE(NULLIF(rev.content_json->>'title',''),NULLIF(edit.title,''),sp.original_title) AS title,"
        "COALESCE(CASE WHEN jsonb_typeof(rev.content_json->'images')='array' THEN NULLIF(rev.content_json->'images'->0->>'url','') END,sp.image_url) AS image_url,"
        "op.id::text AS operation_id,op.status AS operation_status,op.target_sku,op.new_ean,queue.scheduled_at,"
        "COALESCE(queue.status,op.status) AS queue_status,queue.last_error_code AS queue_error "
        f"{joins} WHERE {where} ORDER BY selected.row_number LIMIT :limit OFFSET :offset"
    ), {**params, "limit": page_size, "offset": (page - 1) * page_size}).mappings().all()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": [{
            "rowNumber": int(row["row_number"]), "candidateEpisodeId": row["candidate_episode_id"],
            "period": row["period"], "sourceEan": row["source_ean"], "sourceSku": row["source_sku"],
            "sourceType": row["source_type"], "factoryId": row["factory_id"], "factoryName": row["factory_name"],
            "title": row["title"], "imageUrl": row["image_url"], "operationId": row["operation_id"],
            "operationStatus": row["operation_status"], "targetSku": row["target_sku"], "newEan": row["new_ean"],
            "scheduledAt": row["scheduled_at"].isoformat() if row["scheduled_at"] else None,
            "queueStatus": row["queue_status"], "queueErrorCode": row["queue_error"],
        } for row in rows],
        "page": {"page": page, "pageSize": page_size, "totalItems": total, "totalPages": total_pages},
    }


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
    effective_status = drip_item_status_sql()
    counts = connection.execute(text(
        "SELECT " + effective_status + " AS status,COUNT(*) AS count "
        "FROM restart_items item JOIN restart_operations op ON op.id=item.operation_id "
        "AND op.account=item.account AND op.data_scope=item.data_scope "
        + publication_evidence_joins()
        + " WHERE item.batch_id=:id AND item.account=:account AND item.data_scope='PRODUCTION' "
        "GROUP BY " + effective_status
    ), {"id": batch_id, "account": account}).mappings().all()
    result["itemCounts"] = {row["status"]: int(row["count"]) for row in counts}
    result["itemGroupCounts"] = _item_group_counts(result["itemCounts"])
    result["queueItemCounts"] = {
        row["status"]: int(row["count"])
        for row in connection.execute(
            text("SELECT status,COUNT(*) AS count FROM restart_items WHERE batch_id=:id AND account=:account "
                 "AND data_scope='PRODUCTION' GROUP BY status"), {"id": batch_id, "account": account},
        ).mappings().all()
    }
    return result


def list_batch_items(
    connection: Connection, *, account: str, batch_id: str, page: int = 1, page_size: int = 50,
    query: str | None = None, status: str | None = None, stage: str | None = None, day: int | None = None,
) -> dict[str, Any]:
    account = _account(account)
    batch = _uuid(batch_id, "batchId")
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 200:
        raise DripError("INVALID_PAGINATION", "Page must be positive and pageSize must be between 1 and 200.", status=422)
    where = "item.batch_id=:batch_id AND item.account=:account AND item.data_scope='PRODUCTION'"
    params: dict[str, Any] = {"batch_id": batch, "account": account}
    effective_status = drip_item_status_sql()
    if status:
        if status not in {*ITEM_STATUS_GROUPS, "all"}:
            raise DripError('INVALID_ITEM_FILTER','Unsupported item filter.',status=422)
        if status != "all":
            where += " AND (" + effective_status + ")=ANY(:statuses)"
            params['statuses']=list(ITEM_STATUS_GROUPS[status])
    allowed_stages = {
        "PREPARING", "SCHEDULED", "READY_TO_SUBMIT", "NEEDS_REVIEW", "SUBMITTING",
        "OTTO_PROCESSING", "AWAITING_ONLINE", "PUBLISHED", "FAILED", "CANCELLED",
    }
    if stage:
        if stage not in allowed_stages:
            raise DripError("INVALID_ITEM_STAGE", "Unsupported Drip Feed item stage.", status=422)
        where += " AND (" + effective_status + ")=:stage"
        params["stage"] = stage
    if day is not None:
        if type(day) is not int or not 1 <= day <= 60:
            raise DripError("INVALID_ITEM_DAY", "Drip Feed day must be between 1 and 60.", status=422)
        where += " AND GREATEST(1,((COALESCE((SELECT manifest.scheduled_at FROM restart_plan_request_items manifest WHERE manifest.request_id=b.plan_request_id AND manifest.account=item.account AND manifest.data_scope=item.data_scope AND manifest.operation_id=item.operation_id LIMIT 1),item.scheduled_at) AT TIME ZONE b.timezone_name)::date - b.local_start_date + 1))=:day"
        params["day"] = day
    if query and query.strip():
        where += " AND (item.source_ean ILIKE :query OR op.target_sku ILIKE :query OR op.new_ean ILIKE :query "
        where += "OR ce.observed_sku ILIKE :query OR sp.aftercool_sku ILIKE :query OR sp.aftercool_ean ILIKE :query "
        where += "OR sp.original_title ILIKE :query OR edit.title ILIKE :query OR rev.content_json->>'title' ILIKE :query)"
        params["query"] = f"%{query.strip()[:100]}%"
    total = int(connection.execute(
        text("SELECT COUNT(*) FROM restart_items item JOIN restart_batches b ON b.id=item.batch_id "
             "AND b.account=item.account AND b.data_scope=item.data_scope JOIN restart_operations op ON op.id=item.operation_id "
             "AND op.account=item.account AND op.data_scope=item.data_scope " + publication_evidence_joins() + " JOIN candidate_episodes ce "
             "ON ce.id=op.candidate_episode_id AND ce.account=op.account AND ce.data_scope=op.data_scope "
             "JOIN source_products sp ON sp.id=op.source_product_id AND sp.account=op.account AND sp.data_scope=op.data_scope "
             "LEFT JOIN operation_revisions rev ON rev.id=item.revision_id AND rev.operation_id=item.operation_id "
             "AND rev.account=item.account AND rev.data_scope=item.data_scope LEFT JOIN restart_product_edits edit "
             "ON edit.candidate_episode_id=ce.id AND edit.account=ce.account AND edit.data_scope=ce.data_scope "
             f"WHERE {where}"), params,
    ).scalar_one())
    rows = connection.execute(
        text(
            "SELECT item.id,item.batch_id,item.account,item.source_ean,item.row_number,item.operation_id::text,"
            "ce.id::text AS candidate_episode_id,"
            "item.revision_id::text,item.qc_result_id::text,item.payload_hash,item.scheduled_at,item.status AS queue_status,"
            + effective_status + " AS effective_status," + operation_publication_state_sql() + " AS publication_state,"
            "item.release_attempts,item.last_error_code,item.ready_at,item.preparation_errors_json,op.target_sku,ce.observed_sku,"
            "COALESCE(NULLIF(edit.category,''),NULLIF(rev.content_json->>'category',''),sp.category,'') AS category,"
            "op.status AS operation_status,op.submission_state,op.online_observation_id::text AS online_observation_id,"
            "sp.source_type,sp.factory_name,sp.original_title,"
            "COALESCE(CASE WHEN jsonb_typeof(rev.content_json->'images')='array' THEN NULLIF(rev.content_json->'images'->0->>'url','') END,sp.image_url) AS image_url,"
            "COALESCE(NULLIF(rev.content_json->>'title',''),NULLIF(edit.title,''),sp.original_title) AS display_title,"
            "publication_marketplace.marketplace_status AS online_status,"
            "publication_marketplace.observed_at AS online_observed_at,"
            "publication_marketplace.id::text AS marketplace_observation_id,"
            "stock.status AS stock_status,COALESCE(stock.verified,FALSE) AS stock_verified,"
            "delivery.status AS delivery_status,COALESCE(delivery.verified,FALSE) AS delivery_verified,"
            "qc.outcome AS qc_outcome,"
            "qc.result_json->'issues' AS qc_issues,qc.created_at AS qc_created_at,"
            "submission.status AS submission_status,submission.http_status AS submission_http_status,"
            "submission.error_code AS submission_error_code,submission.attempt_number AS submission_attempt_number,"
            "submission.started_at AS submission_started_at,submission.completed_at AS submission_completed_at,"
            "submission.processing_state,submission.task_state,submission.processing_error_code,submission.processing_updated_at,"
            "GREATEST(1,((COALESCE((SELECT manifest.scheduled_at FROM restart_plan_request_items manifest WHERE manifest.request_id=b.plan_request_id AND manifest.account=item.account AND manifest.data_scope=item.data_scope AND manifest.operation_id=item.operation_id LIMIT 1),item.scheduled_at) AT TIME ZONE b.timezone_name)::date - b.local_start_date + 1)) AS scheduled_day,"
            "rev.revision_number,COALESCE(job_attempts.total_attempts,0) AS total_attempts "
            "FROM restart_items item JOIN restart_batches b ON b.id=item.batch_id AND b.account=item.account AND b.data_scope=item.data_scope "
            "JOIN restart_operations op ON op.id=item.operation_id AND op.account=item.account "
            "AND op.data_scope=item.data_scope " + publication_evidence_joins() + " JOIN candidate_episodes ce ON ce.id=op.candidate_episode_id "
            "AND ce.account=op.account AND ce.data_scope=op.data_scope JOIN source_products sp ON sp.id=op.source_product_id "
            "AND sp.account=op.account AND sp.data_scope=op.data_scope LEFT JOIN operation_revisions rev "
            "ON rev.id=item.revision_id AND rev.operation_id=item.operation_id AND rev.account=item.account "
            "AND rev.data_scope=item.data_scope LEFT JOIN restart_product_edits edit ON edit.candidate_episode_id=ce.id "
            "AND edit.account=ce.account AND edit.data_scope=ce.data_scope "
            "LEFT JOIN LATERAL (SELECT a.status, EXISTS(SELECT 1 FROM restart_events ev WHERE ev.account=op.account "
            "AND ev.data_scope='PRODUCTION' AND ev.entity_type='RESTART_OPERATION' AND ev.entity_id=op.id::text "
            "AND ev.event_type IN ('OTTO_AVAILABILITY_VERIFIED','OTTO_AVAILABILITY_MANUALLY_RESOLVED') "
            "AND ev.metadata_json->>'action'='QUANTITY' AND ev.metadata_json->>'resolution'='APPLIED' "
            "AND (ev.metadata_json->>'attemptId'=a.id::text OR (a.completed_at IS NOT NULL AND ev.created_at >= a.completed_at))) AS verified "
            "FROM availability_attempts a WHERE a.operation_id=op.id "
            "AND a.account=op.account AND a.data_scope='PRODUCTION' AND a.action='QUANTITY' ORDER BY a.created_at DESC,a.id DESC LIMIT 1) stock ON TRUE "
            "LEFT JOIN LATERAL (SELECT a.status, EXISTS(SELECT 1 FROM restart_events ev WHERE ev.account=op.account "
            "AND ev.data_scope='PRODUCTION' AND ev.entity_type='RESTART_OPERATION' AND ev.entity_id=op.id::text "
            "AND ev.event_type IN ('OTTO_AVAILABILITY_VERIFIED','OTTO_AVAILABILITY_MANUALLY_RESOLVED') "
            "AND ev.metadata_json->>'action'='DELIVERY_INFORMATION' AND ev.metadata_json->>'resolution'='APPLIED' "
            "AND (ev.metadata_json->>'attemptId'=a.id::text OR (a.completed_at IS NOT NULL AND ev.created_at >= a.completed_at))) AS verified "
            "FROM availability_attempts a WHERE a.operation_id=op.id "
            "AND a.account=op.account AND a.data_scope='PRODUCTION' AND a.action='DELIVERY_INFORMATION' ORDER BY a.created_at DESC,a.id DESC LIMIT 1) delivery ON TRUE "
            "LEFT JOIN qc_results qc ON qc.id=item.qc_result_id AND qc.operation_id=item.operation_id "
            "AND qc.account=item.account AND qc.data_scope=item.data_scope "
            "LEFT JOIN LATERAL (SELECT s.status,s.http_status,s.error_code,s.attempt_number,s.started_at,s.completed_at,"
            "p.state AS processing_state,p.last_task_state AS task_state,p.last_error_code AS processing_error_code,p.updated_at AS processing_updated_at "
            "FROM submission_attempts s LEFT JOIN otto_processing_polls p ON p.attempt_id=s.id AND p.account=s.account AND p.data_scope=s.data_scope "
            "WHERE s.item_id=item.id AND s.account=item.account AND s.data_scope=item.data_scope "
            "ORDER BY s.attempt_number DESC LIMIT 1) submission ON TRUE "
            "LEFT JOIN LATERAL (SELECT SUM(attempt.total_attempts) AS total_attempts "
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
                   "operationId": str(row["operation_id"]), "candidateEpisodeId": row["candidate_episode_id"],
                   "revisionId": str(row["revision_id"]) if row["revision_id"] else None,
                   "qcResultId": str(row["qc_result_id"]) if row["qc_result_id"] else None, "payloadHash": str(row["payload_hash"]).strip() if row["payload_hash"] else None,
                   "scheduledAt": row["scheduled_at"].isoformat(), "status": row["effective_status"],
                   "queueStatus": row["queue_status"], "publicationState": row["publication_state"],
                   "releaseAttempts": int(row["release_attempts"]), "lastErrorCode": row["last_error_code"],
                   "readyAt": row["ready_at"].isoformat() if row["ready_at"] else None,
                   "targetSku": row["target_sku"], "sourceSku": row["observed_sku"],
                   "category": row["category"],
                   "revisionNumber": int(row["revision_number"]) if row["revision_number"] else None,
                   "totalAttempts": int(row["total_attempts"] or 0),"title":row["display_title"],
                   "imageUrl":row["image_url"],"sourceType":row["source_type"],"factoryName":row["factory_name"],
                   "operationStatus":row["operation_status"],"submissionState":row["submission_state"],
                   "onlineStatus":row["online_status"],"onlineObservedAt":row["online_observed_at"].isoformat() if row["online_observed_at"] else None,
                   "marketplaceObservationId":row["marketplace_observation_id"],
                   "qcOutcome":row["qc_outcome"],"qcIssues":row["qc_issues"] or [],"qcCreatedAt":row["qc_created_at"].isoformat() if row["qc_created_at"] else None,
                   "preparationErrors":row["preparation_errors_json"] or [],
                   "submissionStatus":row["submission_status"],"submissionHttpStatus":row["submission_http_status"],
                   "submissionErrorCode":row["submission_error_code"],"submissionAttemptNumber":row["submission_attempt_number"],
                   "submissionStartedAt":row["submission_started_at"].isoformat() if row["submission_started_at"] else None,
                   "submissionCompletedAt":row["submission_completed_at"].isoformat() if row["submission_completed_at"] else None,
                   "processingState":row["processing_state"],"taskState":row["task_state"],"processingErrorCode":row["processing_error_code"],
                   "processingUpdatedAt":row["processing_updated_at"].isoformat() if row["processing_updated_at"] else None,
                   "scheduledDay":int(row["scheduled_day"]) if row["scheduled_day"] is not None else None,
                   "stockStatus":row["stock_status"] or "NOT_STARTED","stockVerified":bool(row["stock_verified"]),
                   "deliveryStatus":row["delivery_status"] or "NOT_STARTED","deliveryVerified":bool(row["delivery_verified"])} for row in rows],
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
             "WHERE account=:account AND data_scope='PRODUCTION' AND state IN ('PENDING','RETRY_WAIT') AND ("
             "(job_type=:release_type AND payload_json->>'batchId'=:batch_id) OR "
             "(job_type='drip.prepare_batch' AND payload_json->>'batchId'=:batch_id) OR "
             "(job_type='drip.prepare_item' AND payload_json->>'itemId' IN (SELECT item.id::text FROM restart_items item "
             "WHERE item.batch_id=:batch_id AND item.account=:account AND item.data_scope='PRODUCTION')))"),
        {"now": now, "account": batch["account"], "release_type": JOB_TYPE, "batch_id": str(batch["id"])},
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
        connection.execute(text("UPDATE restart_items SET preparation_generation=preparation_generation+1,version=version+1 "
            "WHERE batch_id=:id AND account=:account AND data_scope='PRODUCTION' AND status='PREPARING'"),
            {"id":batch_id,"account":account})
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
        has_preparing = connection.execute(text("SELECT EXISTS(SELECT 1 FROM restart_items WHERE batch_id=:id "
            "AND account=:account AND data_scope='PRODUCTION' AND status='PREPARING')"),
            {"id":batch_id,"account":account}).scalar_one()
        if has_preparing:
            from services.drip_preparation import enqueue_preparation_batch
            enqueue_preparation_batch(connection,batch=batch,account=account,now=current)
        event_type = "DRIP_BATCH_RESUMED"
    elif action == "cancel":
        if batch["status"] in {"COMPLETED", "CANCELLED"}:
            raise DripError("BATCH_NOT_CANCELLABLE", "A completed or cancelled batch cannot be cancelled again.")
        _cancel_pending_jobs(connection, batch=batch, now=current)
        connection.execute(
            text("UPDATE restart_items item SET status='CANCELLED',preparation_generation=preparation_generation+1,version=version+1 "
                 "WHERE item.batch_id=:id AND item.account=:account AND item.data_scope='PRODUCTION' "
                 "AND item.status IN ('PREPARING','NEEDS_REVIEW','FAILED','SCHEDULED','READY_TO_SUBMIT') "
                 "AND EXISTS (SELECT 1 FROM restart_operations o WHERE o.id=item.operation_id AND o.account=item.account "
                 "AND o.data_scope=item.data_scope AND o.submission_state='NEVER_SUBMITTED')"),
            {"id": batch_id, "account": account},
        )
        connection.execute(
            text("UPDATE restart_batches SET status='CANCELLED',cancelled_at=:now,schedule_generation=schedule_generation+1,"
                 "version=version+1,updated_at=:now WHERE id=:id AND account=:account"),
            {"now": current, "id": batch_id, "account": account},
        )
        event_type = "DRIP_BATCH_CANCELLED"
    elif action == "retry-errors":
        from services.drip_preparation import retry_preparation
        return retry_preparation(connection, batch=batch, actor=actor, now=current)
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
        # Fence old preparation messages and give the new scheduler a fresh
        # idempotency key for every item whose preparation is still pending.
        connection.execute(text("UPDATE restart_items SET preparation_generation=preparation_generation+1,version=version+1 "
            "WHERE batch_id=:id AND account=:account AND data_scope='PRODUCTION' AND status='PREPARING'"),
            {"id":batch_id,"account":account})
        enqueued = 0
        if batch["status"] != "PAUSED":
            enqueued += _enqueue_scheduled_items(connection, batch=batch, now=current)
            has_preparing = connection.execute(text("SELECT EXISTS(SELECT 1 FROM restart_items WHERE batch_id=:id "
                "AND account=:account AND data_scope='PRODUCTION' AND status='PREPARING')"),
                {"id":batch_id,"account":account}).scalar_one()
            if has_preparing:
                from services.drip_preparation import enqueue_preparation_batch
                enqueue_preparation_batch(connection,batch=batch,account=account,now=current)
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
    for field, column in (("revisionId", "revision_id"), ("qcResultId", "qc_result_id"),
                          ("payloadHash", "payload_hash"), ("operationId", "operation_id")):
        if field in payload and str(payload[field]).strip() != str(item[column]).strip():
            return {"released": False, "code": "STALE_RELEASE_SNAPSHOT"}
    if batch["status"] not in {"SCHEDULED", "ACTIVE"}:
        if batch["status"] == "CANCELLED" and item["status"] == "SCHEDULED":
            connection.execute(text("UPDATE restart_items SET status='CANCELLED',version=version+1 WHERE id=:id"), {"id": item_id})
        return {"released": False, "code": "BATCH_NOT_ACTIVE"}
    if item["status"] != "SCHEDULED":
        return {"released": item["status"] == "READY_TO_SUBMIT", "code": "ALREADY_PROCESSED"}
    if item["scheduled_at"] > current:
        return {"released": False, "code": "NOT_DUE"}
    # Serialize the same source identity across JV/XL before releasing either POST.
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"), {"identity": "restart-stagger:" + item["source_ean"]})
    opposite = "XL" if account == "JV" else "JV"
    latest = connection.execute(text(
        "SELECT MAX(CASE WHEN other.status='PUBLISHED' THEN COALESCE(obs.observed_at,o.updated_at) ELSE :now END) "
        "FROM restart_items other JOIN restart_batches b ON b.id=other.batch_id AND b.account=other.account "
        "JOIN restart_operations o ON o.id=other.operation_id AND o.account=other.account "
        "LEFT JOIN otto_status_observations obs ON obs.id=o.online_observation_id AND obs.account=o.account "
        "WHERE other.account=:opposite AND other.data_scope='PRODUCTION' AND other.source_ean=:ean "
        "AND b.status<>'CANCELLED' AND other.status IN ('READY_TO_SUBMIT','SUBMITTING','OTTO_PROCESSING','AWAITING_ONLINE','PUBLISHED')"),
        {"opposite":opposite,"ean":item["source_ean"],"now":current}).scalar_one_or_none()
    if latest is not None and latest + timedelta(days=2) > current:
        retry_at = latest + timedelta(days=2)
        enqueue_job(connection, account=account, job_type=JOB_TYPE,
            idempotency_key=f"drip-stagger-{item_id}-{int(retry_at.timestamp())}", payload=payload,
            available_at=retry_at, max_attempts=5, now=current)
        _event(connection,account=account,actor="system:drip-release",event_type="DRIP_ANTI_COLLISION_DELAYED",entity_type="RESTART_ITEM",
            entity_id=str(item_id),summary="Publication delayed by the two-day JV/XL source identity interval",metadata={"availableAt":retry_at.isoformat()})
        return {"released":False,"code":"ANTI_COLLISION_DELAYED"}
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

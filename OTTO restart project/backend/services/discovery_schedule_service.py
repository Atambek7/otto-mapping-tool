"""Baseline confirmation and opt-in weekly discovery scheduling."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from services.discovery_scan_service import (
    DiscoveryScanError,
    MAX_INITIAL_WINDOW,
    _create_scan,
    _scan_lock,
)

DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_WEEKDAY = 0  # Monday
DEFAULT_LOCAL_TIME = time(3, 0)
MAX_SCHEDULED_ACCOUNTS_PER_TICK = 2
BASELINE_CLASSIFICATIONS = {"EXACT_CODE", "TITLE_ONLY", "NONE"}


class DiscoveryScheduleError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return result if isinstance(result, dict) else {}
    return {}


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip() or len(name) > 96:
        raise DiscoveryScheduleError("INVALID_TIMEZONE", "Use a valid IANA timezone name.", status_code=422)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise DiscoveryScheduleError("INVALID_TIMEZONE", "Use a valid IANA timezone name.", status_code=422) from None


def next_weekly_slot(
    now: datetime,
    *,
    weekday: int = DEFAULT_WEEKDAY,
    local_time: time = DEFAULT_LOCAL_TIME,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise DiscoveryScheduleError("INVALID_CLOCK", "Scheduler clock must include a timezone.")
    if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
        raise DiscoveryScheduleError("INVALID_WEEKDAY", "Weekday must be between 0 and 6.", status_code=422)
    if not isinstance(local_time, time) or local_time.tzinfo is not None:
        raise DiscoveryScheduleError("INVALID_LOCAL_TIME", "Local schedule time must not include a timezone.", status_code=422)
    zone = _zone(timezone_name)
    current_local = now.astimezone(zone)
    days = (weekday - current_local.weekday()) % 7
    target_date = current_local.date() + timedelta(days=days)

    for _ in range(2):
        naive = datetime.combine(target_date, local_time)
        candidate = naive.replace(tzinfo=zone, fold=0)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if round_trip != naive:
            raise DiscoveryScheduleError(
                "NONEXISTENT_LOCAL_TIME",
                "This weekly local time does not exist on a daylight-saving transition.",
                status_code=422,
            )
        if candidate.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            return candidate.astimezone(timezone.utc)
        target_date += timedelta(days=7)
    raise DiscoveryScheduleError("SCHEDULE_SLOT_UNAVAILABLE", "Could not calculate the next weekly slot.")


def _schedule_dto(row: Mapping[str, Any] | None, *, account: str) -> dict[str, Any]:
    if row is None:
        return {
            "account": account,
            "baselineConfirmed": False,
            "baselineRunId": None,
            "baselineConfirmedAt": None,
            "enabled": False,
            "timezone": DEFAULT_TIMEZONE,
            "weekday": DEFAULT_WEEKDAY,
            "localTime": DEFAULT_LOCAL_TIME.isoformat(timespec="minutes"),
            "nextAt": None,
            "lastScheduledSlotAt": None,
        }
    return {
        "account": account,
        "baselineConfirmed": row["baseline_confirmed_at"] is not None,
        "baselineRunId": str(row["baseline_run_id"]) if row["baseline_run_id"] else None,
        "baselineConfirmedAt": row["baseline_confirmed_at"],
        "enabled": bool(row["schedule_enabled"]),
        "timezone": row["schedule_timezone"],
        "weekday": int(row["schedule_weekday"]),
        "localTime": row["schedule_local_time"].isoformat(timespec="minutes"),
        "nextAt": row["schedule_next_at"],
        "lastScheduledSlotAt": row["last_scheduled_slot_at"],
    }


def get_schedule(connection: Connection, *, account: str) -> dict[str, Any]:
    row = connection.execute(
        text(
            "SELECT baseline_run_id, baseline_confirmed_at, schedule_enabled, schedule_timezone, "
            "schedule_weekday, schedule_local_time, schedule_next_at, last_scheduled_slot_at "
            "FROM discovery_cursors WHERE account=:account AND data_scope='PRODUCTION'"
        ),
        {"account": account},
    ).mappings().first()
    return _schedule_dto(row, account=account)


def confirm_baseline(
    connection: Connection,
    *,
    account: str,
    run_id: str,
    actor: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if account not in {"JV", "XL"} or not actor.strip():
        raise DiscoveryScheduleError("INVALID_BASELINE_CONFIRMATION", "A valid account and actor are required.", status_code=422)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise DiscoveryScheduleError("INVALID_CLOCK", "Confirmation time must include a timezone.")
    _scan_lock(connection, account)
    run = connection.execute(
        text(
            "SELECT id, run_mode, run_state, cutoff_at FROM import_runs "
            "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION' AND run_type='FILE_IMPORT' "
            "FOR UPDATE"
        ),
        {"run_id": run_id, "account": account},
    ).mappings().first()
    if run is None:
        raise DiscoveryScheduleError("BASELINE_RUN_NOT_FOUND", "The account-scoped import run was not found.", status_code=404)
    if run["run_mode"] != "BASELINE":
        raise DiscoveryScheduleError("BASELINE_RUN_MODE_REQUIRED", "Only an explicitly imported BASELINE run can confirm coverage.", status_code=422)
    if run["run_state"] not in {"SUCCEEDED", "PARTIAL"} or run["cutoff_at"] is None:
        raise DiscoveryScheduleError("BASELINE_RUN_NOT_FINISHED", "Wait until the baseline import has finished before confirming it.", status_code=409)

    rows = connection.execute(
        text(
            "SELECT row_state, row_json FROM import_rows WHERE run_id=:run_id AND account=:account "
            "AND data_scope='PRODUCTION' ORDER BY row_number"
        ),
        {"run_id": run_id, "account": account},
    ).mappings().all()
    verified_count = 0
    candidate_count = 0
    for row in rows:
        if row["row_state"] == "DUPLICATE":
            continue
        if row["row_state"] == "INVALID":
            raise DiscoveryScheduleError("BASELINE_HAS_INVALID_ROWS", "Correct invalid identifiers and import the complete baseline again.", status_code=422)
        payload = _json_object(row["row_json"])
        status = _json_object(payload.get("ottoStatus"))
        sku = payload.get("confirmedSku") or payload.get("sku") or payload.get("normalizedIdentifier")
        classification = payload.get("classification")
        if (
            not isinstance(sku, str)
            or status.get("sku") != sku
            or classification not in BASELINE_CLASSIFICATIONS
        ):
            raise DiscoveryScheduleError(
                "BASELINE_STATUS_UNVERIFIED",
                "Every unique baseline identifier needs a matching successful OTTO status response.",
                status_code=422,
            )
        verified_count += 1
        candidate_count += int(classification in {"EXACT_CODE", "TITLE_ONLY"})
    if not verified_count:
        raise DiscoveryScheduleError("BASELINE_EMPTY", "A baseline must contain at least one verified unique identifier.", status_code=422)

    connection.execute(
        text(
            "INSERT INTO discovery_cursors (account, data_scope, last_successful_cutoff, last_successful_run_id, "
            "baseline_run_id, baseline_confirmed_by, baseline_confirmed_at) "
            "VALUES (:account, 'PRODUCTION', :cutoff_at, :run_id, :run_id, :actor, :now) "
            "ON CONFLICT (account, data_scope) DO UPDATE SET "
            "last_successful_cutoff=CASE WHEN discovery_cursors.last_successful_cutoff IS NULL "
            "OR discovery_cursors.last_successful_cutoff < EXCLUDED.last_successful_cutoff "
            "THEN EXCLUDED.last_successful_cutoff ELSE discovery_cursors.last_successful_cutoff END, "
            "last_successful_run_id=CASE WHEN discovery_cursors.last_successful_cutoff IS NULL "
            "OR discovery_cursors.last_successful_cutoff < EXCLUDED.last_successful_cutoff "
            "THEN EXCLUDED.last_successful_run_id ELSE discovery_cursors.last_successful_run_id END, "
            "baseline_run_id=EXCLUDED.baseline_run_id, baseline_confirmed_by=EXCLUDED.baseline_confirmed_by, "
            "baseline_confirmed_at=EXCLUDED.baseline_confirmed_at, updated_at=CURRENT_TIMESTAMP"
        ),
        {"account": account, "cutoff_at": run["cutoff_at"], "run_id": run_id, "actor": actor[:128], "now": current},
    )
    existing = connection.execute(
        text(
            "SELECT 1 FROM restart_events WHERE account=:account AND data_scope='PRODUCTION' "
            "AND event_type='DISCOVERY_BASELINE_CONFIRMED' AND entity_id=:run_id LIMIT 1"
        ),
        {"account": account, "run_id": run_id},
    ).scalar_one_or_none()
    if existing is None:
        connection.execute(
            text(
                "INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json) "
                "VALUES (:id, :account, 'PRODUCTION', 'DISCOVERY_BASELINE_CONFIRMED', 'IMPORT_RUN', :run_id, "
                "'Оператор подтвердил покрытие baseline для обнаружения 3M/12M', CAST(:metadata AS jsonb))"
            ),
            {"id": str(uuid4()), "account": account, "run_id": run_id,
             "metadata": json.dumps({"actor": actor[:128], "verifiedRows": verified_count, "candidateRows": candidate_count}, separators=(",", ":"))},
        )
    return {
        "account": account,
        "baselineRunId": str(run_id),
        "baselineConfirmedAt": current,
        "cutoffAt": run["cutoff_at"],
        "verifiedRows": verified_count,
        "candidateRows": candidate_count,
    }


def update_schedule(
    connection: Connection,
    *,
    account: str,
    enabled: bool,
    weekday: int = DEFAULT_WEEKDAY,
    local_time: time = DEFAULT_LOCAL_TIME,
    timezone_name: str = DEFAULT_TIMEZONE,
    now: datetime | None = None,
) -> dict[str, Any]:
    if account not in {"JV", "XL"}:
        raise DiscoveryScheduleError("INVALID_ACCOUNT", "Account must be JV or XL.", status_code=422)
    if not isinstance(enabled, bool):
        raise DiscoveryScheduleError("INVALID_ENABLED", "enabled must be a boolean.", status_code=422)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise DiscoveryScheduleError("INVALID_CLOCK", "Scheduler clock must include a timezone.")
    next_at = None
    if enabled:
        baseline = connection.execute(
            text("SELECT baseline_confirmed_at, last_successful_cutoff, overlap_seconds "
                 "FROM discovery_cursors WHERE account=:account AND data_scope='PRODUCTION' FOR UPDATE"),
            {"account": account},
        ).mappings().first()
        if baseline is None or baseline["baseline_confirmed_at"] is None:
            raise DiscoveryScheduleError("BASELINE_CONFIRMATION_REQUIRED", "Confirm a complete baseline before enabling the weekly schedule.")
        if baseline["last_successful_cutoff"] is None or current.astimezone(timezone.utc) - (
            baseline["last_successful_cutoff"] - timedelta(seconds=int(baseline["overlap_seconds"]))
        ) > MAX_INITIAL_WINDOW:
            raise DiscoveryScheduleError("BASELINE_TOO_OLD", "Import a fresh baseline before enabling weekly discovery.")
        next_at = next_weekly_slot(current, weekday=weekday, local_time=local_time, timezone_name=timezone_name)
    else:
        # Invalid stale settings cannot be saved even when pausing; this makes
        # a subsequent enable deterministic and visible to the operator.
        _zone(timezone_name)
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            raise DiscoveryScheduleError("INVALID_WEEKDAY", "Weekday must be between 0 and 6.", status_code=422)
        if not isinstance(local_time, time) or local_time.tzinfo is not None:
            raise DiscoveryScheduleError("INVALID_LOCAL_TIME", "Local schedule time must not include a timezone.", status_code=422)
    connection.execute(
        text(
            "INSERT INTO discovery_cursors (account, data_scope, schedule_enabled, schedule_timezone, "
            "schedule_weekday, schedule_local_time, schedule_next_at) "
            "VALUES (:account, 'PRODUCTION', :enabled, :timezone, :weekday, :local_time, :next_at) "
            "ON CONFLICT (account, data_scope) DO UPDATE SET schedule_enabled=EXCLUDED.schedule_enabled, "
            "schedule_timezone=EXCLUDED.schedule_timezone, schedule_weekday=EXCLUDED.schedule_weekday, "
            "schedule_local_time=EXCLUDED.schedule_local_time, schedule_next_at=EXCLUDED.schedule_next_at, "
            "updated_at=CURRENT_TIMESTAMP"
        ),
        {"account": account, "enabled": enabled, "timezone": timezone_name, "weekday": weekday,
         "local_time": local_time, "next_at": next_at},
    )
    row = connection.execute(
        text(
            "SELECT baseline_run_id, baseline_confirmed_at, schedule_enabled, schedule_timezone, "
            "schedule_weekday, schedule_local_time, schedule_next_at, last_scheduled_slot_at "
            "FROM discovery_cursors WHERE account=:account AND data_scope='PRODUCTION'"
        ),
        {"account": account},
    ).mappings().one()
    return _schedule_dto(row, account=account)


def enqueue_due_scans(engine: Engine, *, now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise DiscoveryScheduleError("INVALID_CLOCK", "Scheduler clock must include a timezone.")
    results: list[dict[str, Any]] = []
    with engine.begin() as connection:
        due_accounts = connection.execute(
            text(
                "SELECT account FROM discovery_cursors WHERE data_scope='PRODUCTION' AND schedule_enabled "
                "AND baseline_confirmed_at IS NOT NULL AND schedule_next_at <= :now "
                "ORDER BY schedule_next_at, account LIMIT :limit"
            ),
            {"now": current, "limit": MAX_SCHEDULED_ACCOUNTS_PER_TICK},
        ).scalars().all()
        for account in due_accounts:
            _scan_lock(connection, str(account))
            cursor = connection.execute(
                text(
                    "SELECT schedule_next_at, schedule_timezone, schedule_weekday, schedule_local_time "
                    "FROM discovery_cursors WHERE account=:account AND data_scope='PRODUCTION' "
                    "AND schedule_enabled AND schedule_next_at <= :now FOR UPDATE SKIP LOCKED"
                ),
                {"account": account, "now": current},
            ).mappings().first()
            if cursor is None:
                continue
            slot = cursor["schedule_next_at"]
            try:
                created = _create_scan(connection, account=str(account), from_date=None, now=current)
            except DiscoveryScanError as error:
                if error.code == "SCAN_ALREADY_ACTIVE":
                    continue
                if error.code == "CURSOR_WINDOW_TOO_WIDE":
                    connection.execute(
                        text(
                            "UPDATE discovery_cursors SET schedule_enabled=FALSE, schedule_next_at=NULL, "
                            "updated_at=CURRENT_TIMESTAMP WHERE account=:account AND data_scope='PRODUCTION'"
                        ),
                        {"account": account},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json) "
                            "VALUES (:id, :account, 'PRODUCTION', 'DISCOVERY_SCHEDULE_PAUSED', 'DISCOVERY_SCHEDULE', :account, "
                            "'Расписание поставлено на паузу: окно обнаружения превысило ограничение', CAST(:metadata AS jsonb))"
                        ),
                        {"id": str(uuid4()), "account": account,
                         "metadata": json.dumps({"code": error.code, "pausedAt": current.isoformat()}, separators=(",", ":"))},
                    )
                    results.append({"account": str(account), "schedulePaused": True, "reason": error.code})
                    continue
                raise
            next_at = next_weekly_slot(
                current,
                weekday=int(cursor["schedule_weekday"]),
                local_time=cursor["schedule_local_time"],
                timezone_name=str(cursor["schedule_timezone"]),
            )
            connection.execute(
                text(
                    "UPDATE discovery_cursors SET last_scheduled_slot_at=:slot, schedule_next_at=:next_at, "
                    "updated_at=CURRENT_TIMESTAMP WHERE account=:account AND data_scope='PRODUCTION'"
                ),
                {"slot": slot, "next_at": next_at, "account": account},
            )
            results.append(created)
    return results

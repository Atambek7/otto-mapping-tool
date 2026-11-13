"""Offline staging and reduction of already supplied OTTO status responses.

No OTTO or Aftercool client is imported here. Callers own the surrounding
database transaction; failed staging or application must be rolled back by the
caller so a run and its observations remain atomic.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional, Sequence
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from jobs.service import enqueue_job
from services.factory_service import begin_observation_reduction, finish_observation_reduction

from services.otto_status_service import classify_marketplace_status_response

Account = Literal["JV", "XL"]
RunType = Literal["FILE_IMPORT", "INCREMENTAL_SCAN", "KNOWN_SKU_RECONCILIATION"]
RunMode = Literal["BASELINE", "BASELINE_EXTEND", "INCREMENTAL"]


class ReductionLimitExceeded(RuntimeError):
    """Raised before a scan reducer exceeds its configured transaction budget."""


@dataclass(frozen=True)
class StatusResponseInput:
    requested_sku: str
    http_status: Optional[int]
    body: Any
    source_product_id: Optional[str] = None
    confirmed_ean: Optional[str] = None

    @classmethod
    def from_saved_export_record(cls, record: Mapping[str, Any]) -> "StatusResponseInput":
        """Adapt one persisted diagnostic record; does not read files or make requests."""
        request = record.get("request")
        requested_sku = request.get("requested_sku") if isinstance(request, Mapping) else None
        if not isinstance(requested_sku, str) or not requested_sku:
            raise ValueError("saved response record has no requested_sku")
        if "body" not in record:
            raise ValueError("saved response record has no body field")
        http_status = record.get("http_status")
        if http_status is not None and not isinstance(http_status, int):
            raise ValueError("saved response record has an invalid http_status")
        return cls(requested_sku=requested_sku, http_status=http_status, body=record["body"])


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("response payload must contain JSON-compatible values") from error


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _codes_and_titles(errors: Any) -> tuple[list[Any], list[Any]]:
    if not isinstance(errors, list):
        return [], []
    codes: list[Any] = []
    titles: list[Any] = []
    for error in errors:
        if isinstance(error, Mapping):
            if "code" in error:
                codes.append(error["code"])
            if "title" in error:
                titles.append(error["title"])
    return codes, titles


def create_import_run(
    connection: Connection,
    account: Account,
    run_type: RunType,
    run_mode: RunMode,
    *,
    source_filename: Optional[str] = None,
    window_from: Optional[datetime] = None,
    cutoff_at: Optional[datetime] = None,
    parameters: Optional[Mapping[str, Any]] = None,
) -> str:
    """Create a production-scoped run in the caller's transaction."""
    if account not in {"JV", "XL"}:
        raise ValueError("account must be JV or XL")
    if run_type not in {"FILE_IMPORT", "INCREMENTAL_SCAN", "KNOWN_SKU_RECONCILIATION"}:
        raise ValueError("unsupported production run type")
    if run_mode not in {"BASELINE", "BASELINE_EXTEND", "INCREMENTAL"}:
        raise ValueError("unsupported run mode")
    if run_type != "FILE_IMPORT" and run_mode != "INCREMENTAL":
        raise ValueError("scan and reconciliation runs must use INCREMENTAL mode")
    run_id = str(uuid4())
    connection.execute(
        text(
            """
            INSERT INTO import_runs (
                id, account, data_scope, source_filename, run_type, run_mode, run_state,
                status, window_from, cutoff_at, parameters_json, total_rows, created_at
            ) VALUES (
                :id, :account, 'PRODUCTION', :source_filename, :run_type, :run_mode, 'PENDING',
                'PENDING', :window_from, :cutoff_at, CAST(:parameters_json AS jsonb), 0, CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "id": run_id,
            "account": account,
            "source_filename": source_filename,
            "run_type": run_type,
            "run_mode": run_mode,
            "window_from": window_from,
            "cutoff_at": cutoff_at,
            "parameters_json": _json_text(dict(parameters or {})),
        },
    )
    return run_id


def _validate_confirmed_identity(
    connection: Connection,
    account: str,
    source_product_id: Optional[str],
    confirmed_ean: Optional[str],
) -> None:
    if confirmed_ean is not None and source_product_id is None:
        raise ValueError("confirmed EAN requires a scoped source_product_id")
    if source_product_id is None:
        return
    row = connection.execute(
        text(
            """
            SELECT aftercool_ean
            FROM source_products
            WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'
            """
        ),
        {"id": source_product_id, "account": account},
    ).mappings().first()
    if row is None:
        raise ValueError("source product is missing or outside the production account scope")
    if confirmed_ean is not None and row["aftercool_ean"] != confirmed_ean:
        raise ValueError("confirmed EAN does not match the scoped source product")


def stage_status_responses(
    connection: Connection,
    account: Account,
    run_id: str,
    responses: Sequence[StatusResponseInput],
) -> dict[str, int]:
    """Persist supplied status responses idempotently without changing episodes."""
    run = connection.execute(
        text(
            """
            SELECT run_state FROM import_runs
            WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'
            FOR UPDATE
            """
        ),
        {"run_id": run_id, "account": account},
    ).mappings().first()
    if run is None:
        raise ValueError("run is missing or outside the production account scope")
    if run["run_state"] not in {"PENDING", "RUNNING", "STAGED"}:
        raise ValueError("run is not accepting staged responses")

    counts = {"received": len(responses), "inserted": 0, "duplicates": 0, "uncheckable": 0}
    sequence_start = connection.execute(
        text(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM otto_status_observations "
            "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"run_id": run_id, "account": account},
    ).scalar_one()
    if run["run_state"] == "PENDING":
        connection.execute(
            text(
                "UPDATE import_runs SET run_state = 'RUNNING', status = 'PROCESSING', "
                "started_at = COALESCE(started_at, CURRENT_TIMESTAMP) "
                "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
            ),
            {"run_id": run_id, "account": account},
        )

    for sequence_no, item in enumerate(responses, start=int(sequence_start) + 1):
        if not isinstance(item, StatusResponseInput):
            raise TypeError("responses must contain StatusResponseInput values")
        if not item.requested_sku.strip():
            raise ValueError("requested_sku must not be empty")
        _validate_confirmed_identity(
            connection, account, item.source_product_id, item.confirmed_ean
        )
        result = classify_marketplace_status_response(
            item.body, item.requested_sku, item.http_status
        )
        serialized_body = _json_text(item.body)
        errors = item.body.get("errors") if isinstance(item.body, Mapping) else None
        codes, titles = _codes_and_titles(errors)
        response_hash = hashlib.sha256(
            _json_text(
                {
                    "requested_sku": item.requested_sku,
                    "http_status": item.http_status,
                    "body": item.body,
                }
            ).encode("utf-8")
        ).hexdigest()
        response_sku = item.body.get("sku") if isinstance(item.body, Mapping) else None
        body_moin = item.body.get("moin") if isinstance(item.body, Mapping) else None
        body_status = item.body.get("status") if isinstance(item.body, Mapping) else None
        active = item.body.get("active") if isinstance(item.body, Mapping) else None
        marketplace_status = item.body.get("marketPlaceStatus") if isinstance(item.body, Mapping) else None
        timestamp = _parse_timestamp(result.last_modified)

        inserted = connection.execute(
            text(
                """
                INSERT INTO otto_status_observations (
                    run_id, account, data_scope, sequence_no, requested_sku, response_sku,
                    source_product_id, confirmed_ean, moin, http_status, classification, period,
                    reason, status, market_place_status, active, last_modified,
                    errors_json, error_codes_json, error_titles_json, unknown_codes_json,
                    response_json, response_hash, reduction_status
                ) VALUES (
                    :run_id, :account, 'PRODUCTION', :sequence_no, :requested_sku, :response_sku,
                    :source_product_id, :confirmed_ean, :moin, :http_status, :classification, :period,
                    :reason, :status, :market_place_status, :active, :last_modified,
                    CAST(:errors_json AS jsonb), CAST(:error_codes_json AS jsonb),
                    CAST(:error_titles_json AS jsonb), CAST(:unknown_codes_json AS jsonb),
                    CAST(:response_json AS jsonb), :response_hash, 'PENDING'
                )
                ON CONFLICT (run_id, requested_sku, response_hash) DO NOTHING
                RETURNING id
                """
            ),
            {
                "run_id": run_id,
                "account": account,
                "sequence_no": sequence_no,
                "requested_sku": item.requested_sku,
                "response_sku": response_sku if isinstance(response_sku, str) else None,
                "source_product_id": item.source_product_id,
                "confirmed_ean": item.confirmed_ean,
                "moin": body_moin if isinstance(body_moin, str) else None,
                "http_status": item.http_status,
                "classification": result.classification,
                "period": result.period,
                "reason": result.reason,
                "status": body_status if isinstance(body_status, str) else None,
                "market_place_status": marketplace_status if isinstance(marketplace_status, str) else None,
                "active": active if isinstance(active, bool) else None,
                "last_modified": timestamp,
                "errors_json": _json_text(errors),
                "error_codes_json": _json_text(codes),
                "error_titles_json": _json_text(titles),
                "unknown_codes_json": _json_text(list(result.unknown_codes)),
                "response_json": serialized_body,
                "response_hash": response_hash,
            },
        ).scalar_one_or_none()
        if inserted is None:
            counts["duplicates"] += 1
        else:
            counts["inserted"] += 1
        if result.classification == "UNCHECKABLE":
            counts["uncheckable"] += 1

    stored_rows = connection.execute(
        text(
            "SELECT COUNT(*) FROM otto_status_observations "
            "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"run_id": run_id, "account": account},
    ).scalar_one()
    connection.execute(
        text(
            "UPDATE import_runs SET run_state = 'STAGED', status = 'PROCESSING', total_rows = :total "
            "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"total": stored_rows, "run_id": run_id, "account": account},
    )
    return counts


def _episode_freshness(episode: Mapping[str, Any], observation: Mapping[str, Any]) -> str:
    if observation["response_hash"] == episode["last_response_hash"]:
        return "REPEATED"
    previous = episode["last_modified"]
    incoming = observation["last_modified"]
    if previous is not None and incoming is None:
        return "UNORDERED"
    if previous is None and incoming is None:
        return "CONFLICT"
    if previous is not None and incoming is not None:
        if incoming < previous:
            return "STALE"
        if incoming == previous:
            return "CONFLICT"
    return "FRESH"


def _record_reduction(connection: Connection, observation_id: Any, state: str) -> None:
    connection.execute(
        text("UPDATE otto_status_observations SET reduction_status = :state WHERE id = :id"),
        {"id": observation_id, "state": state},
    )


def _validated_episode_identity(
    episode: Mapping[str, Any], observation: Mapping[str, Any]
) -> bool:
    if observation["source_product_id"] is not None:
        if episode["source_product_id"] not in {None, observation["source_product_id"]}:
            return False
    if observation["confirmed_ean"] is not None:
        if episode["confirmed_ean"] not in {None, observation["confirmed_ean"]}:
            return False
    return True


def _apply_restriction(
    connection: Connection,
    run: Mapping[str, Any],
    observation: Mapping[str, Any],
    episode: Optional[Mapping[str, Any]],
) -> str:
    if episode is not None:
        if not _validated_episode_identity(episode, observation):
            _record_reduction(connection, observation["id"], "IDENTITY_CONFLICT")
            return "identity_conflicts"
        freshness = _episode_freshness(episode, observation)
        if freshness == "REPEATED":
            # An identical OTTO response may now have a verified source from a
            # subsequent lookup. Attach that identity without changing status history.
            if observation["source_product_id"] is not None and (
                episode["source_product_id"] is None
                or (episode["confirmed_ean"] is None and observation["confirmed_ean"] is not None)
            ):
                connection.execute(
                    text(
                        "UPDATE candidate_episodes SET "
                        "source_product_id=COALESCE(source_product_id, :source_product_id), "
                        "confirmed_ean=COALESCE(confirmed_ean, :confirmed_ean), "
                        "routing_state='ROUTED', routing_error_code=NULL, "
                        "routed_at=COALESCE(routed_at,CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=:episode_id AND account=:account AND data_scope='PRODUCTION'"
                    ),
                    {"source_product_id": observation["source_product_id"],
                     "confirmed_ean": observation["confirmed_ean"],
                     "episode_id": episode["id"], "account": observation["account"]},
                )
            _record_reduction(connection, observation["id"], freshness)
            return "repeated"
        if freshness != "FRESH":
            _record_reduction(connection, observation["id"], freshness)
            return "stale_or_conflicting"
        connection.execute(
            text(
                """
                UPDATE candidate_episodes
                SET period = :period, classification = :classification,
                    source_product_id = COALESCE(source_product_id, :source_product_id),
                    confirmed_ean = COALESCE(confirmed_ean, :confirmed_ean),
                    routing_state = CASE
                        WHEN COALESCE(source_product_id, :source_product_id) IS NOT NULL THEN 'ROUTED'
                        WHEN routing_state = 'PROCESSING' THEN 'PROCESSING'
                        ELSE 'PENDING'
                    END,
                    routing_error_code = CASE
                        WHEN COALESCE(source_product_id, :source_product_id) IS NOT NULL THEN NULL
                        ELSE routing_error_code
                    END,
                    routed_at = CASE
                        WHEN COALESCE(source_product_id, :source_product_id) IS NOT NULL
                            THEN COALESCE(routed_at, CURRENT_TIMESTAMP)
                        ELSE NULL
                    END,
                    moin = COALESCE(:moin, moin), last_observation_id = :observation_id,
                    last_response_hash = :response_hash, last_modified = :last_modified,
                    last_detected_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = :episode_id AND account = :account AND data_scope = 'PRODUCTION'
                """
            ),
            {
                "period": observation["period"],
                "classification": observation["classification"],
                "source_product_id": observation["source_product_id"],
                "confirmed_ean": observation["confirmed_ean"],
                "moin": observation["moin"],
                "observation_id": observation["id"],
                "response_hash": observation["response_hash"],
                "last_modified": observation["last_modified"],
                "episode_id": episode["id"],
                "account": observation["account"],
            },
        )
        _record_reduction(connection, observation["id"], "UPDATED")
        return "updated"

    origin = "BASELINE" if run["run_mode"] in {"BASELINE", "BASELINE_EXTEND"} else "NEW"
    connection.execute(
        text(
            """
            INSERT INTO candidate_episodes (
                account, data_scope, observed_sku, canonical_identity, source_product_id,
                confirmed_ean, moin, period, classification, origin, episode_state,
                routing_state, routed_at,
                first_observation_id, last_observation_id, last_response_hash, last_modified
            ) VALUES (
                :account, 'PRODUCTION', :observed_sku, :canonical_identity, :source_product_id,
                :confirmed_ean, :moin, :period, :classification, :origin, 'OPEN',
                CASE WHEN CAST(:source_product_id AS UUID) IS NULL THEN 'PENDING' ELSE 'ROUTED' END,
                CASE WHEN CAST(:source_product_id AS UUID) IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                :observation_id, :observation_id, :response_hash, :last_modified
            )
            """
        ),
        {
            "account": observation["account"],
            "observed_sku": observation["requested_sku"],
            "canonical_identity": f"OTTO_SKU:{observation['requested_sku']}",
            "source_product_id": observation["source_product_id"],
            "confirmed_ean": observation["confirmed_ean"],
            "moin": observation["moin"],
            "period": observation["period"],
            "classification": observation["classification"],
            "origin": origin,
            "observation_id": observation["id"],
            "response_hash": observation["response_hash"],
            "last_modified": observation["last_modified"],
        },
    )
    _record_reduction(connection, observation["id"], "OPENED")
    return "opened"


def _apply_clear_status(
    connection: Connection,
    observation: Mapping[str, Any],
    episode: Optional[Mapping[str, Any]],
) -> str:
    if episode is None:
        _record_reduction(connection, observation["id"], "NONE")
        return "no_open_episode"
    if not _validated_episode_identity(episode, observation):
        _record_reduction(connection, observation["id"], "IDENTITY_CONFLICT")
        return "identity_conflicts"
    freshness = _episode_freshness(episode, observation)
    if freshness == "REPEATED":
        _record_reduction(connection, observation["id"], freshness)
        return "repeated"
    if freshness != "FRESH":
        _record_reduction(connection, observation["id"], freshness)
        return "stale_or_conflicting"
    connection.execute(
        text(
            """
            UPDATE candidate_episodes
            SET episode_state = 'CLOSED', closed_by_observation_id = :observation_id,
                last_observation_id = :observation_id, closed_at = CURRENT_TIMESTAMP,
                last_response_hash = :response_hash, last_modified = :last_modified,
                last_detected_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = :episode_id AND account = :account AND data_scope = 'PRODUCTION'
            """
        ),
        {
            "observation_id": observation["id"],
            "response_hash": observation["response_hash"],
            "last_modified": observation["last_modified"],
            "episode_id": episode["id"],
            "account": observation["account"],
        },
    )
    _record_reduction(connection, observation["id"], "CLOSED")
    return "closed"


def apply_staged_run(
    connection: Connection,
    account: Account,
    run_id: str,
    *,
    max_observations: int | None = None,
    max_seconds: float | None = None,
) -> dict[str, Any]:
    """Apply fully staged observations using one caller-owned DB transaction."""
    if max_observations is not None and (
        isinstance(max_observations, bool)
        or not isinstance(max_observations, int)
        or max_observations < 1
    ):
        raise ValueError("max_observations must be a positive integer")
    if max_seconds is not None and (not isinstance(max_seconds, (int, float)) or max_seconds <= 0):
        raise ValueError("max_seconds must be positive")
    apply_started = time.monotonic()
    # Serialize account reducers before locking a run or touching episode rows.
    # FactoryService also locks affected factory rows in stable ID order.
    factory_before = begin_observation_reduction(connection, account=account, run_id=run_id)
    run = connection.execute(
        text(
            """
            SELECT id, account, run_type, run_mode, run_state, coverage_json
            FROM import_runs
            WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'
            FOR UPDATE
            """
        ),
        {"run_id": run_id, "account": account},
    ).mappings().first()
    if run is None:
        raise ValueError("run is missing or outside the production account scope")
    if run["run_state"] in {"SUCCEEDED", "PARTIAL"}:
        coverage = run["coverage_json"] or {}
        return {**coverage, "already_applied": True}
    if run["run_state"] != "STAGED":
        raise ValueError("run must be STAGED before observations can be applied")

    observation_count = int(connection.execute(
        text(
            "SELECT COUNT(*) FROM otto_status_observations "
            "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"run_id": run_id, "account": account},
    ).scalar_one())
    if max_observations is not None and observation_count > max_observations:
        raise ReductionLimitExceeded("DISCOVERY_OBSERVATION_LIMIT")

    if run["run_type"] == "INCREMENTAL_SCAN":
        uncheckable_count = int(connection.execute(
            text(
                "SELECT COUNT(*) FROM otto_status_observations "
                "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION' "
                "AND classification = 'UNCHECKABLE'"
            ),
            {"run_id": run_id, "account": account},
        ).scalar_one())
    else:
        uncheckable_count = 0

    if uncheckable_count:
        connection.execute(
            text(
                "UPDATE otto_status_observations SET reduction_status = 'UNCHECKABLE' "
                "WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION' "
                "AND classification = 'UNCHECKABLE'"
            ),
            {"run_id": run_id, "account": account},
        )
        summary = {
            "observations": observation_count,
            "opened": 0,
            "updated": 0,
            "closed": 0,
            "repeated": 0,
            "stale_or_conflicting": 0,
            "identity_conflicts": 0,
            "ambiguous": 0,
            "uncheckable": uncheckable_count,
            "no_open_episode": 0,
            "applied": False,
        }
        connection.execute(
            text(
                "UPDATE import_runs SET run_state = 'PARTIAL', status = 'COMPLETED', "
                "finished_at = CURRENT_TIMESTAMP, coverage_json = coverage_json || CAST(:coverage AS jsonb) "
                "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
            ),
            {"coverage": _json_text(summary), "run_id": run_id, "account": account},
        )
        return summary

    counts = {
        "observations": observation_count, "opened": 0, "updated": 0, "closed": 0,
        "repeated": 0, "stale_or_conflicting": 0, "identity_conflicts": 0,
        "ambiguous": 0, "uncheckable": 0, "no_open_episode": 0,
    }
    offset = 0
    while offset < observation_count:
        if max_seconds is not None and time.monotonic() - apply_started > max_seconds:
            raise ReductionLimitExceeded("DISCOVERY_APPLY_TIME_LIMIT")
        batch = connection.execute(
            text(
                """
                SELECT * FROM otto_status_observations
                WHERE run_id = :run_id AND account = :account AND data_scope = 'PRODUCTION'
                ORDER BY sequence_no, observed_at, id
                LIMIT :limit OFFSET :offset
                FOR UPDATE
                """
            ),
            {"run_id": run_id, "account": account, "limit": 500, "offset": offset},
        ).mappings().all()
        if not batch:
            break
        for observation in batch:
            if max_seconds is not None and time.monotonic() - apply_started > max_seconds:
                raise ReductionLimitExceeded("DISCOVERY_APPLY_TIME_LIMIT")
            classification = observation["classification"]
            if classification in {"UNCHECKABLE", "AMBIGUOUS"}:
                reduction_state = classification
                _record_reduction(connection, observation["id"], reduction_state)
                counts[classification.lower()] += 1
                continue

            identity = f"{account}:{observation['requested_sku']}"
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": identity},
            )
            episode = connection.execute(
                text(
                    """
                    SELECT * FROM candidate_episodes
                    WHERE account = :account AND observed_sku = :sku
                      AND data_scope = 'PRODUCTION' AND episode_state = 'OPEN'
                    FOR UPDATE
                    """
                ),
                {"account": account, "sku": observation["requested_sku"]},
            ).mappings().first()

            if classification in {"EXACT_CODE", "TITLE_ONLY"}:
                action = _apply_restriction(connection, run, observation, episode)
            else:  # NONE is a valid, explicit response that can close an episode.
                action = _apply_clear_status(connection, observation, episode)
            counts[action] += 1
        offset += len(batch)

    has_uncheckable = counts["uncheckable"] > 0

    # Every confirmed candidate without a source link receives one durable,
    # idempotent routing job in the same transaction as episode reduction.
    # This closes the crash window between detection and Aftercool enrichment.
    pending_routes = connection.execute(
        text(
            """
            SELECT ce.id::text AS episode_id, ce.last_observation_id::text AS observation_id
            FROM candidate_episodes ce
            JOIN otto_status_observations obs
              ON obs.id = ce.last_observation_id
             AND obs.account = ce.account
             AND obs.data_scope = ce.data_scope
            WHERE obs.run_id = :run_id
              AND ce.account = :account
              AND ce.data_scope = 'PRODUCTION'
              AND ce.episode_state = 'OPEN'
              AND ce.source_product_id IS NULL
              AND ce.classification IN ('EXACT_CODE', 'TITLE_ONLY')
              AND ce.routing_state IN ('PENDING', 'ERROR', 'REVIEW')
            ORDER BY ce.id
            """
        ),
        {"run_id": run_id, "account": account},
    ).mappings().all()
    for route in pending_routes:
        connection.execute(
            text(
                "UPDATE candidate_episodes SET routing_state='PENDING', routing_error_code=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                "AND data_scope='PRODUCTION' AND source_product_id IS NULL"
            ),
            {"episode_id": route["episode_id"], "account": account},
        )
        enqueue_job(
            connection,
            account=account,
            job_type="discovery.enrich",
            idempotency_key=f"candidate-route:{route['episode_id']}:{route['observation_id']}",
            payload={"episodeId": route["episode_id"], "observationId": route["observation_id"]},
            max_attempts=3,
        )

    summary = {**counts, "applied": True}
    connection.execute(
        text(
            "UPDATE import_runs SET run_state = :run_state, status = 'COMPLETED', "
            "finished_at = CURRENT_TIMESTAMP, coverage_json = coverage_json || CAST(:coverage AS jsonb) "
            "WHERE id = :run_id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {
            "run_state": "PARTIAL" if has_uncheckable else "SUCCEEDED",
            "coverage": _json_text(summary),
            "run_id": run_id,
            "account": account,
        },
    )
    coverage_refresh_eligible = (
        run["run_type"] == "INCREMENTAL_SCAN"
        and not any(
            counts[key] > 0
            for key in ("uncheckable", "ambiguous", "identity_conflicts", "stale_or_conflicting")
        )
        and not has_uncheckable
    )
    finish_observation_reduction(
        connection,
        account=account,
        run_id=run_id,
        before=factory_before,
        coverage_refresh_eligible=coverage_refresh_eligible,
    )
    return summary

"""Evidence-backed production factory projections for OttoRestart."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

FACTORY_COVERAGE_MAX_AGE_DAYS = 16


class FactoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _check_account(account: str) -> None:
    if account not in {"JV", "XL"}:
        raise FactoryError("INVALID_ACCOUNT", "Account must be JV or XL.")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _factory_ids_for_run(conn: Connection, account: str, run_id: str) -> list[str]:
    values = conn.execute(
        text(
            "SELECT DISTINCT sp.factory_id FROM otto_status_observations o "
            "LEFT JOIN candidate_episodes ce ON ce.account=o.account AND ce.data_scope=o.data_scope "
            "AND ce.observed_sku=o.requested_sku AND ce.episode_state='OPEN' "
            "JOIN source_products sp ON sp.id=COALESCE(o.source_product_id,ce.source_product_id) "
            "AND sp.account=o.account "
            "AND sp.data_scope=o.data_scope "
            "WHERE o.run_id=:run_id AND o.account=:account AND o.data_scope='PRODUCTION' "
            "AND sp.factory_id IS NOT NULL AND btrim(sp.factory_id)<>'' ORDER BY sp.factory_id"
        ),
        {"run_id": run_id, "account": account},
    ).scalars()
    return [str(value) for value in values]


def _ensure_production_factories(conn: Connection, account: str, factory_ids: Iterable[str]) -> list[str]:
    ids = sorted({str(value).strip() for value in factory_ids if value and str(value).strip()})
    if not ids:
        return []

    # A name is copied only when all verified source rows agree. Ambiguous
    # source names fall back to the stable factory ID rather than row order.
    conn.execute(
        text(
            "INSERT INTO restart_factories (account, id, name, status, data_scope) "
            "SELECT account, factory_id, "
            "CASE WHEN COUNT(DISTINCT NULLIF(btrim(factory_name),'')) <= 1 "
            "THEN COALESCE(MAX(NULLIF(btrim(factory_name),'')), factory_id) ELSE factory_id END, "
            "'UNKNOWN', 'PRODUCTION' FROM source_products "
            "WHERE account=:account AND data_scope='PRODUCTION' AND factory_id=ANY(:factory_ids) "
            "GROUP BY account, factory_id "
            "ON CONFLICT (account,id,data_scope) DO UPDATE SET name=EXCLUDED.name "
            "WHERE restart_factories.name=restart_factories.id"
        ),
        {"account": account, "factory_ids": ids},
    )
    # Explicit ordering gives concurrent reducers one stable lock order.
    conn.execute(
        text(
            "SELECT id FROM restart_factories WHERE account=:account AND data_scope='PRODUCTION' "
            "AND id=ANY(:factory_ids) ORDER BY id FOR UPDATE"
        ),
        {"account": account, "factory_ids": ids},
    ).all()
    return ids


def _open_episode_sets(conn: Connection, account: str, factory_ids: Iterable[str]) -> dict[str, set[str]]:
    ids = sorted(set(factory_ids))
    result = {factory_id: set() for factory_id in ids}
    if not ids:
        return result
    rows = conn.execute(
        text(
            "SELECT sp.factory_id, ce.id FROM candidate_episodes ce "
            "JOIN source_products sp ON sp.id=ce.source_product_id AND sp.account=ce.account "
            "AND sp.data_scope=ce.data_scope "
            "WHERE ce.account=:account AND ce.data_scope='PRODUCTION' AND ce.episode_state='OPEN' "
            "AND sp.factory_id=ANY(:factory_ids) ORDER BY sp.factory_id, ce.id"
        ),
        {"account": account, "factory_ids": ids},
    ).all()
    for factory_id, episode_id in rows:
        result[str(factory_id)].add(str(episode_id))
    return result


def begin_observation_reduction(conn: Connection, *, account: str, run_id: str) -> dict[str, set[str]]:
    """Serialize account apply, lock affected factories, and snapshot candidate membership."""
    _check_account(account)
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
        {"lock_name": f"ottorestart-factory-apply:{account}"},
    )
    factory_ids = _factory_ids_for_run(conn, account, run_id)
    _ensure_production_factories(conn, account, factory_ids)
    return _open_episode_sets(conn, account, factory_ids)


def _coverage_is_fresh(row: Mapping[str, Any]) -> bool:
    return bool(row["coverage_fresh"])


def _factory_snapshot(conn: Connection, *, account: str, factory_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT ce.id AS episode_id, ce.period, ce.origin, ce.episode_state, "
            "ce.closed_by_observation_id, ce.confirmed_ean, sp.aftercool_ean, "
            "op.id AS operation_id, op.status AS operation_status, op.new_ean, "
            "op.target_sku, op.processing_evidence_event_id, op.online_observation_id, "
            "pe.entity_type AS processing_entity_type, pe.entity_id AS processing_entity_id, "
            "pe.metadata_json AS processing_metadata, pe.created_at AS processing_created_at, "
            "oo.market_place_status AS online_status, oo.requested_sku AS online_requested_sku, "
            "oo.response_sku AS online_response_sku, oo.observed_at AS online_observed_at "
            "FROM candidate_episodes ce JOIN source_products sp "
            "ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
            "LEFT JOIN restart_operations op ON op.candidate_episode_id=ce.id AND op.account=ce.account "
            "AND op.data_scope='PRODUCTION' "
            "LEFT JOIN restart_events pe ON pe.id=op.processing_evidence_event_id AND pe.account=op.account "
            "AND pe.data_scope='PRODUCTION' "
            "LEFT JOIN otto_status_observations oo ON oo.id=op.online_observation_id AND oo.account=op.account "
            "AND oo.data_scope='PRODUCTION' "
            "WHERE ce.account=:account AND ce.data_scope='PRODUCTION' AND sp.factory_id=:factory_id "
            "ORDER BY ce.first_detected_at, ce.id"
        ),
        {"account": account, "factory_id": factory_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def _confirmed_published(row: Mapping[str, Any]) -> bool:
    metadata = row.get("processing_metadata") or {}
    return bool(
        row.get("operation_status") == "PUBLISHED"
        and row.get("processing_evidence_event_id")
        and row.get("online_observation_id")
        and row.get("target_sku")
        and row.get("processing_entity_type") == "OTTO_PROCESSING_RESULT"
        and str(row.get("processing_entity_id")) == str(row.get("operation_id"))
        and metadata.get("result") == "SUCCEEDED"
        and metadata.get("targetSku") == row.get("target_sku")
        and row.get("online_status") == "ONLINE"
        and row.get("online_requested_sku") == row.get("target_sku")
        and row.get("online_response_sku") == row.get("target_sku")
        and row.get("online_observed_at") is not None
        and row.get("processing_created_at") is not None
        and row.get("online_observed_at") >= row.get("processing_created_at")
    )


def _projection(conn: Connection, *, account: str, factory_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = conn.execute(
        text(
            "SELECT candidate_generation, coverage_confirmed_at, coverage_generation, coverage_run_id, "
            "coverage_evidence_json, last_completed_at, first_completed_at, last_scanned_at, "
            "coverage_confirmed_at >= CURRENT_TIMESTAMP - (:max_age * INTERVAL '1 day') "
            "AND coverage_generation=candidate_generation AS coverage_fresh "
            "FROM restart_factories WHERE account=:account AND id=:factory_id "
            "AND data_scope='PRODUCTION' FOR UPDATE"
        ),
        {"account": account, "factory_id": factory_id, "max_age": FACTORY_COVERAGE_MAX_AGE_DAYS},
    ).mappings().first()
    if row is None:
        raise FactoryError("FACTORY_NOT_FOUND", "No production factory exists for this account and ID.")

    episodes = _factory_snapshot(conn, account=account, factory_id=factory_id)
    active = [episode for episode in episodes if episode["episode_state"] == "OPEN"]
    published = [episode for episode in active if _confirmed_published(episode)]
    unresolved = [episode for episode in active if not _confirmed_published(episode)]
    new_pending = [episode for episode in unresolved if episode["origin"] == "NEW"]
    operation_statuses = [episode["operation_status"] for episode in active if episode["operation_status"]]

    coverage_fresh = _coverage_is_fresh(row)
    if unresolved:
        status = "NOT_COMPLETE"
    elif coverage_fresh:
        status = "COMPLETE"
    else:
        status = "UNKNOWN"

    metrics = {
        "status": status,
        "totalCandidates": len(active),
        "publishedCount": len(published),
        "readyCount": sum(value in {"READY", "QUEUED", "SUBMITTING", "OTTO_PROCESSING", "AWAITING_ONLINE"} for value in operation_statuses),
        "errorCount": sum(value in {"NEEDS_REVIEW", "RECONCILIATION_REQUIRED", "FAILED"} for value in operation_statuses),
        "newCandidatesCount": len(new_pending),
        "period3mCount": sum(episode["period"] == "3M" for episode in active),
        "period12mCount": sum(episode["period"] == "12M" for episode in active),
        "unresolvedCount": len(unresolved),
    }
    metadata = {
        **metrics,
        "candidateGeneration": int(row["candidate_generation"]),
        "coverageGeneration": int(row["coverage_generation"]) if row["coverage_generation"] is not None else None,
        "coverageConfirmedAt": row["coverage_confirmed_at"].isoformat() if row["coverage_confirmed_at"] else None,
        "coverageRunId": str(row["coverage_run_id"]) if row["coverage_run_id"] else None,
        "coverageSource": (row["coverage_evidence_json"] or {}).get("source"),
        "coverageFresh": coverage_fresh,
        "firstCompletedAt": row["first_completed_at"].isoformat() if row["first_completed_at"] else None,
        "lastCompletedAt": row["last_completed_at"].isoformat() if row["last_completed_at"] else None,
        "lastScannedAt": row["last_scanned_at"].isoformat() if row["last_scanned_at"] else None,
        "wasPreviouslyCompleted": bool(row["last_completed_at"] and status == "NOT_COMPLETE"),
    }
    return metadata, episodes


def _store_completion_if_new(
    conn: Connection, *, account: str, factory_id: str, projection: Mapping[str, Any],
    episodes: list[dict[str, Any]],
) -> bool:
    if projection["status"] != "COMPLETE":
        return False

    episode_ids = [str(item["episode_id"]) for item in episodes]
    operation_ids = sorted({str(item["operation_id"]) for item in episodes if item["operation_id"]})
    published_eans = sorted({str(item["new_ean"]) for item in episodes if item["new_ean"] and _confirmed_published(item)})
    evidence = {
        "coverage": {
            "source": projection.get("coverageSource"),
            "confirmedAt": projection.get("coverageConfirmedAt"),
            "runId": projection.get("coverageRunId"),
            "candidateGeneration": projection["candidateGeneration"],
        },
        "episodes": [
            {
                "episodeId": str(item["episode_id"]),
                "state": item["episode_state"],
                "period": item["period"],
                "sourceEan": item["aftercool_ean"] or item["confirmed_ean"],
                "targetSku": item["target_sku"],
                "targetEan": item["new_ean"],
                "operationId": str(item["operation_id"]) if item["operation_id"] else None,
                "operationStatus": item["operation_status"],
                "processingEventId": item["processing_evidence_event_id"],
                "processingEventCreatedAt": item["processing_created_at"],
                "onlineObservationId": str(item["online_observation_id"]) if item["online_observation_id"] else None,
                "onlineObservedAt": item["online_observed_at"],
                "restrictionClearedByObservationId": str(item["closed_by_observation_id"]) if item["closed_by_observation_id"] else None,
                "publishedConfirmed": _confirmed_published(item),
            }
            for item in episodes
        ],
    }
    inserted = conn.execute(
        text(
            "INSERT INTO factory_completion_history "
            "(id, account, factory_id, data_scope, completed_at, candidates_count, published_eans_json, "
            "generation, episode_ids_json, operation_ids_json, evidence_json, created_at) "
            "VALUES (:id, :account, :factory_id, 'PRODUCTION', CURRENT_TIMESTAMP, :candidate_count, "
            "CAST(:eans AS jsonb), :generation, CAST(:episode_ids AS jsonb), CAST(:operation_ids AS jsonb), "
            "CAST(:evidence AS jsonb), CURRENT_TIMESTAMP) "
            "ON CONFLICT (account, factory_id, generation) WHERE data_scope='PRODUCTION' AND generation IS NOT NULL "
            "DO NOTHING RETURNING id"
        ),
        {
            "id": str(uuid4()), "account": account, "factory_id": factory_id,
            "candidate_count": projection["totalCandidates"], "eans": _json(published_eans),
            "generation": projection["candidateGeneration"], "episode_ids": _json(episode_ids),
            "operation_ids": _json(operation_ids), "evidence": _json(evidence),
        },
    ).scalar_one_or_none()
    if inserted is None:
        return False
    conn.execute(
        text(
            "UPDATE restart_factories SET first_completed_at=COALESCE(first_completed_at,CURRENT_TIMESTAMP), "
            "last_completed_at=CURRENT_TIMESTAMP WHERE account=:account AND id=:factory_id "
            "AND data_scope='PRODUCTION'"
        ),
        {"account": account, "factory_id": factory_id},
    )
    return True


def recalculate_factory(conn: Connection, *, account: str, factory_id: str) -> dict[str, Any]:
    """Recalculate and persist a factory projection; never infer coverage from an empty catalog."""
    _check_account(account)
    identity = str(factory_id).strip()
    if not identity:
        raise FactoryError("INVALID_FACTORY_ID", "Factory ID is required.")
    projection, episodes = _projection(conn, account=account, factory_id=identity)
    conn.execute(
        text(
            "UPDATE restart_factories SET status=:status, projection_json=CAST(:projection AS jsonb), "
            "updated_at=CURRENT_TIMESTAMP WHERE account=:account AND id=:factory_id "
            "AND data_scope='PRODUCTION'"
        ),
        {"status": projection["status"], "projection": _json(projection), "account": account, "factory_id": identity},
    )
    completion_recorded = _store_completion_if_new(
        conn, account=account, factory_id=identity, projection=projection, episodes=episodes
    )
    if completion_recorded:
        timestamps = conn.execute(
            text(
                "SELECT first_completed_at,last_completed_at FROM restart_factories "
                "WHERE account=:account AND id=:factory_id AND data_scope='PRODUCTION'"
            ),
            {"account": account, "factory_id": identity},
        ).one()
        projection = {
            **projection,
            "firstCompletedAt": timestamps[0].isoformat() if timestamps[0] else None,
            "lastCompletedAt": timestamps[1].isoformat() if timestamps[1] else None,
        }
        conn.execute(
            text(
                "UPDATE restart_factories SET projection_json=CAST(:projection AS jsonb) "
                "WHERE account=:account AND id=:factory_id AND data_scope='PRODUCTION'"
            ),
            {"projection": _json(projection), "account": account, "factory_id": identity},
        )
    projection = {**projection, "completionRecorded": completion_recorded}
    return {"factoryId": identity, "account": account, **projection}


def record_routed_candidate(
    conn: Connection, *, account: str, factory_id: str, factory_name: str | None = None
) -> dict[str, Any]:
    """Attach a newly resolved candidate to its factory and invalidate old coverage."""
    _check_account(account)
    identity = str(factory_id or "").strip()
    if not identity:
        raise FactoryError("INVALID_FACTORY_ID", "Factory ID is required.")
    conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
        {"lock_name": f"ottorestart-factory-apply:{account}"},
    )
    _ensure_production_factories(conn, account, [identity])
    if factory_name and factory_name.strip():
        conn.execute(
            text(
                "UPDATE restart_factories SET name=:name WHERE account=:account AND id=:factory_id "
                "AND data_scope='PRODUCTION' AND (name=id OR name IS NULL OR btrim(name)='')"
            ),
            {"name": factory_name.strip(), "account": account, "factory_id": identity},
        )
    conn.execute(
        text(
            "UPDATE restart_factories SET candidate_generation=candidate_generation+1, "
            "updated_at=CURRENT_TIMESTAMP WHERE account=:account AND id=:factory_id "
            "AND data_scope='PRODUCTION'"
        ),
        {"account": account, "factory_id": identity},
    )
    return recalculate_factory(conn, account=account, factory_id=identity)


def finish_observation_reduction(
    conn: Connection, *, account: str, run_id: str, before: Mapping[str, set[str]],
    coverage_refresh_eligible: bool,
) -> list[dict[str, Any]]:
    """Persist generation changes and refresh only already-established, gap-free coverage."""
    factory_ids = sorted(before)
    after = _open_episode_sets(conn, account, factory_ids)
    for factory_id in factory_ids:
        if before.get(factory_id, set()) != after.get(factory_id, set()):
            conn.execute(
                text(
                    "UPDATE restart_factories SET candidate_generation=candidate_generation+1, "
                    "updated_at=CURRENT_TIMESTAMP WHERE account=:account AND id=:factory_id "
                    "AND data_scope='PRODUCTION'"
                ),
                {"account": account, "factory_id": factory_id},
            )

    run = conn.execute(
        text("SELECT run_type, run_state, window_from, cutoff_at FROM import_runs "
             "WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION'"),
        {"run_id": run_id, "account": account},
    ).mappings().one()
    if coverage_refresh_eligible and run["run_type"] == "INCREMENTAL_SCAN" and run["run_state"] == "SUCCEEDED":
        # A delta scan can extend a previously verified baseline only while the
        # old coverage is fresh and the requested window overlaps its proof.
        conn.execute(
            text(
                "UPDATE restart_factories rf SET last_scanned_at=CURRENT_TIMESTAMP, "
                "coverage_generation=rf.candidate_generation, coverage_confirmed_at=CURRENT_TIMESTAMP, "
                "coverage_run_id=:run_id, "
                "coverage_evidence_json=jsonb_set(COALESCE(rf.coverage_evidence_json,'{}'::jsonb), "
                "'{lastIncrementalRun}', jsonb_build_object('runId',CAST(:run_id AS TEXT), "
                "'windowFrom',:window_from,'cutoffAt',:cutoff_at,'appliedAt',CURRENT_TIMESTAMP), true), "
                "updated_at=CURRENT_TIMESTAMP FROM import_runs ir "
                "WHERE rf.account=:account AND rf.data_scope='PRODUCTION' AND ir.id=:run_id "
                "AND ir.account=rf.account AND ir.data_scope=rf.data_scope "
                "AND rf.coverage_confirmed_at >= CURRENT_TIMESTAMP - (:max_age * INTERVAL '1 day') "
                "AND :window_from IS NOT NULL AND :window_from <= rf.coverage_confirmed_at"
            ),
            {"account": account, "run_id": run_id, "window_from": run["window_from"],
             "cutoff_at": run["cutoff_at"], "max_age": FACTORY_COVERAGE_MAX_AGE_DAYS},
        )
        conn.execute(
            text("UPDATE restart_factories SET last_scanned_at=CURRENT_TIMESTAMP "
                 "WHERE account=:account AND data_scope='PRODUCTION'"),
            {"account": account},
        )

    results: list[dict[str, Any]] = []
    for factory_id in factory_ids:
        results.append(recalculate_factory(conn, account=account, factory_id=factory_id))
    return results


class FactoryService:
    @staticmethod
    def list_factories(
        conn: Connection,
        account: str,
        status_filter: str | None = None,
        sort_by_new: bool = False,
    ) -> list[dict[str, Any]]:
        _check_account(account)
        rows = conn.execute(
            text(
                "SELECT id, account, name, "
                "CASE WHEN status='COMPLETE' AND NOT COALESCE(coverage_confirmed_at >= CURRENT_TIMESTAMP "
                "- (:max_age * INTERVAL '1 day') AND coverage_generation=candidate_generation, FALSE) "
                "THEN 'UNKNOWN' ELSE status END AS status, "
                "first_completed_at, last_completed_at, last_scanned_at, "
                "projection_json, candidate_generation, coverage_generation, coverage_confirmed_at, coverage_run_id, "
                "coverage_evidence_json, "
                "COALESCE(coverage_confirmed_at >= CURRENT_TIMESTAMP - (:max_age * INTERVAL '1 day') "
                "AND coverage_generation=candidate_generation, FALSE) AS coverage_fresh "
                "FROM restart_factories WHERE account=:account "
                "AND data_scope='PRODUCTION' ORDER BY name,id"
            ),
            {"account": account, "max_age": FACTORY_COVERAGE_MAX_AGE_DAYS},
        ).mappings().all()
        results = []
        for row in rows:
            projection = row["projection_json"] or {}
            status = row["status"]
            if status_filter and status_filter.upper() != "ALL" and status != status_filter.upper():
                continue
            result = {
                "id": str(row["id"]), "account": account, "name": row["name"], "status": status,
                "totalCandidates": int(projection.get("totalCandidates", 0)),
                "publishedCount": int(projection.get("publishedCount", 0)),
                "readyCount": int(projection.get("readyCount", 0)),
                "errorCount": int(projection.get("errorCount", 0)),
                "newCandidatesCount": int(projection.get("newCandidatesCount", 0)),
                "period3mCount": int(projection.get("period3mCount", 0)),
                "period12mCount": int(projection.get("period12mCount", 0)),
                "unresolvedCount": int(projection.get("unresolvedCount", 0)),
                "firstCompletedAt": row["first_completed_at"].isoformat() if row["first_completed_at"] else None,
                "lastCompletedAt": row["last_completed_at"].isoformat() if row["last_completed_at"] else None,
                "lastScannedAt": row["last_scanned_at"].isoformat() if row["last_scanned_at"] else None,
                "wasPreviouslyCompleted": bool(row["last_completed_at"] and status == "NOT_COMPLETE"),
                "candidateGeneration": int(row["candidate_generation"]),
                "coverageGeneration": int(row["coverage_generation"]) if row["coverage_generation"] is not None else None,
                "coverageConfirmedAt": row["coverage_confirmed_at"].isoformat() if row["coverage_confirmed_at"] else None,
                "coverageRunId": str(row["coverage_run_id"]) if row["coverage_run_id"] else None,
                "coverageSource": (row["coverage_evidence_json"] or {}).get("source"),
                "coverageFresh": bool(row["coverage_fresh"]),
            }
            results.append(result)
        if sort_by_new:
            results.sort(key=lambda item: (item["newCandidatesCount"] > 0, item["newCandidatesCount"], item["totalCandidates"]), reverse=True)
        return results

    @staticmethod
    def get_completion_history(conn: Connection, account: str, factory_id: str) -> list[dict[str, Any]]:
        _check_account(account)
        rows = conn.execute(
            text(
                "SELECT id, completed_at, candidates_count, published_eans_json, generation, "
                "episode_ids_json, operation_ids_json, evidence_json FROM factory_completion_history "
                "WHERE account=:account AND factory_id=:factory_id AND data_scope='PRODUCTION' "
                "ORDER BY completed_at DESC,id"
            ),
            {"account": account, "factory_id": factory_id},
        ).mappings().all()
        return [
            {
                "id": str(row["id"]), "completedAt": row["completed_at"].isoformat(),
                "candidatesCount": int(row["candidates_count"] or 0),
                "publishedEans": row["published_eans_json"] if isinstance(row["published_eans_json"], list) else json.loads(row["published_eans_json"] or "[]"),
                "generation": row["generation"],
                "episodeIds": row["episode_ids_json"] if isinstance(row["episode_ids_json"], list) else json.loads(row["episode_ids_json"] or "[]"),
                "operationIds": row["operation_ids_json"] if isinstance(row["operation_ids_json"], list) else json.loads(row["operation_ids_json"] or "[]"),
                "evidence": row["evidence_json"] or {},
            }
            for row in rows
        ]

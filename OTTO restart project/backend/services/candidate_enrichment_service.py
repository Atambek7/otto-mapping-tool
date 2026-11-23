"""Resolve a discovered OTTO candidate to verified Aftercool or Direct OTTO data."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from jobs.service import JobExecutionError, enqueue_job
from services.aftercool_lookup_service import AftercoolLookupService
from services.factory_service import record_routed_candidate
from services.import_pipeline import (
    _json_object,
    _persist_source_product,
    _resolve_import_row,
)
from services.otto_status_service import OttoStatusService


def process_candidate_enrichment(
    engine: Engine,
    *,
    account: str,
    episode_id: str,
    observation_id: str,
    otto_client_factory: Callable[[str], Any],
    aftercool_service_factory: Callable[[], AftercoolLookupService],
    max_attempts: int,
    attempt: int,
) -> dict[str, Any]:
    """Read outside DB transactions, then atomically attach verified source identity."""
    with engine.begin() as connection:
        episode = connection.execute(
            text(
                "SELECT ce.id::text AS id, ce.account, ce.observed_sku, ce.period, ce.episode_state, "
                "ce.source_product_id::text AS source_product_id, ce.last_observation_id::text AS last_observation_id, "
                "obs.id::text AS observation_id, obs.run_id::text AS run_id, obs.response_json, run.run_mode "
                "FROM candidate_episodes ce "
                "JOIN otto_status_observations obs ON obs.id=ce.last_observation_id "
                "AND obs.account=ce.account AND obs.data_scope=ce.data_scope "
                "JOIN import_runs run ON run.id=obs.run_id AND run.account=obs.account AND run.data_scope=obs.data_scope "
                "WHERE ce.id=:episode_id AND ce.account=:account AND ce.data_scope='PRODUCTION' FOR UPDATE OF ce"
            ),
            {"episode_id": episode_id, "account": account},
        ).mappings().first()
        if episode is None or episode["episode_state"] != "OPEN":
            return {"state": "SKIPPED", "reason": "EPISODE_NOT_OPEN"}
        if episode["source_product_id"]:
            connection.execute(
                text(
                    "UPDATE candidate_episodes SET routing_state='ROUTED', routing_error_code=NULL, "
                    "routed_at=COALESCE(routed_at,CURRENT_TIMESTAMP) WHERE id=:episode_id "
                    "AND account=:account AND data_scope='PRODUCTION'"
                ),
                {"episode_id": episode_id, "account": episode["account"]},
            )
            return {"state": "ROUTED", "sourceProductId": episode["source_product_id"]}
        used_observation_id = str(episode["last_observation_id"])
        if used_observation_id != observation_id:
            connection.execute(
                text(
                    "UPDATE candidate_episodes SET routing_state='PENDING', routing_error_code=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                    "AND data_scope='PRODUCTION' AND source_product_id IS NULL"
                ),
                {"episode_id": episode_id, "account": account},
            )
            enqueue_job(
                connection,
                account=account,
                job_type="discovery.enrich",
                idempotency_key=f"candidate-route:{episode_id}:{used_observation_id}",
                payload={"episodeId": episode_id, "observationId": used_observation_id},
                max_attempts=3,
            )
            return {"state": "STALE", "nextObservationId": used_observation_id}
        market_status = _json_object(episode["response_json"])
        connection.execute(
            text(
                "UPDATE candidate_episodes SET routing_state='PROCESSING', routing_error_code=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                "AND data_scope='PRODUCTION' AND source_product_id IS NULL"
            ),
            {"episode_id": episode_id, "account": episode["account"]},
        )

    account = str(episode["account"])
    sku = str(episode["observed_sku"])
    try:
        otto_client = otto_client_factory(account)
        aftercool_service = aftercool_service_factory()
        outcome = _resolve_import_row(
            account,
            {"sku": sku},
            otto_client=otto_client,
            aftercool_service=aftercool_service,
            status_service=OttoStatusService(),
            market_status_response=market_status,
        )
    except Exception as error:
        retryable = bool(getattr(error, "retryable", False))
        code = str(getattr(error, "code", "CANDIDATE_ENRICHMENT_FAILED"))[:64]
        if retryable and attempt < max_attempts:
            raise JobExecutionError(code, retryable=True) from None
        _set_routing_failure(engine, account, episode_id, code, state="ERROR")
        return {"state": "ERROR", "code": code}

    if outcome.get("retryable"):
        code = str(outcome.get("error_code") or "CANDIDATE_ENRICHMENT_RETRYABLE")[:64]
        if attempt < max_attempts:
            raise JobExecutionError(code, retryable=True) from None
        _set_routing_failure(engine, account, episode_id, code, state="ERROR")
        return {"state": "ERROR", "code": code}

    if (
        outcome.get("row_state") != "VALID"
        or not outcome.get("source_record")
        or not outcome.get("source_type")
        or not outcome.get("confirmed_ean")
    ):
        route_status = outcome.get("route_status")
        state = "ERROR" if route_status in {"LOOKUP_ERROR", "NOT_IMPLEMENTED"} else "REVIEW"
        code = str(outcome.get("error_code") or route_status or "SOURCE_IDENTITY_UNRESOLVED")[:64]
        _set_routing_failure(engine, account, episode_id, code, state=state)
        return {"state": state, "code": code}

    with engine.begin() as connection:
        current = connection.execute(
            text(
                "SELECT id::text AS id, source_product_id::text AS source_product_id, last_observation_id::text "
                "AS last_observation_id, episode_state FROM candidate_episodes "
                "WHERE id=:episode_id AND account=:account AND data_scope='PRODUCTION' FOR UPDATE"
            ),
            {"episode_id": episode_id, "account": account},
        ).mappings().first()
        if current is None or current["episode_state"] != "OPEN":
            return {"state": "SKIPPED", "reason": "EPISODE_CHANGED"}
        if current["source_product_id"]:
            return {"state": "ROUTED", "sourceProductId": current["source_product_id"]}
        if str(current["last_observation_id"]) != used_observation_id:
            next_observation_id = str(current["last_observation_id"])
            connection.execute(
                text(
                    "UPDATE candidate_episodes SET routing_state='PENDING', routing_error_code=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                    "AND data_scope='PRODUCTION'"
                ),
                {"episode_id": episode_id, "account": account},
            )
            enqueue_job(
                connection,
                account=account,
                job_type="discovery.enrich",
                idempotency_key=f"candidate-route:{episode_id}:{next_observation_id}",
                payload={"episodeId": episode_id, "observationId": next_observation_id},
                max_attempts=3,
            )
            return {"state": "STALE", "nextObservationId": next_observation_id}

        try:
            with connection.begin_nested():
                source_product_id = _persist_source_product(
                    connection,
                    account=account,
                    run_id=str(episode["run_id"]),
                    run_mode=str(episode["run_mode"] or "INCREMENTAL"),
                    period=str(episode["period"]),
                    sku=sku,
                    ean=str(outcome["confirmed_ean"]),
                    source=outcome["source_record"],
                    source_type=str(outcome["source_type"]),
                )
        except Exception:
            source_product_id = None
        if source_product_id is None:
            connection.execute(
                text(
                    "UPDATE candidate_episodes SET routing_state='REVIEW', routing_error_code='SOURCE_IDENTITY_CONFLICT', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                    "AND data_scope='PRODUCTION'"
                ),
                {"episode_id": episode_id, "account": account},
            )
            return {"state": "REVIEW", "code": "SOURCE_IDENTITY_CONFLICT"}

        connection.execute(
            text(
                "UPDATE candidate_episodes SET source_product_id=:source_id, confirmed_ean=:ean, "
                "routing_state='ROUTED', routing_error_code=NULL, routed_at=CURRENT_TIMESTAMP, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                "AND data_scope='PRODUCTION' AND last_observation_id=:observation_id"
            ),
            {
                "source_id": source_product_id,
                "ean": outcome["confirmed_ean"],
                "episode_id": episode_id,
                "account": account,
                "observation_id": used_observation_id,
            },
        )
        connection.execute(
            text(
                "UPDATE otto_status_observations SET source_product_id=:source_id, confirmed_ean=:ean "
                "WHERE id=:observation_id AND account=:account AND data_scope='PRODUCTION'"
            ),
            {
                "source_id": source_product_id,
                "ean": outcome["confirmed_ean"],
                "observation_id": used_observation_id,
                "account": account,
            },
        )
        connection.execute(
            text(
                "UPDATE import_rows SET row_state='VALID', "
                "row_json=row_json || CAST(:route_patch AS jsonb), errors_json='[]'::jsonb, "
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=:run_id AND account=:account "
                "AND data_scope='PRODUCTION' AND normalized_identifier=:sku "
                "AND row_state IN ('IDENTITY_UNRESOLVED','ERROR')"
            ),
            {
                "route_patch": json.dumps(
                    {
                        "routeStatus": outcome.get("route_status"),
                        "sourceType": outcome.get("source_type"),
                        "sourceProductId": source_product_id,
                        "confirmedEan": outcome.get("confirmed_ean"),
                        "retryable": False,
                    },
                    separators=(",", ":"),
                ),
                "run_id": episode["run_id"],
                "account": account,
                "sku": sku,
            },
        )
        _refresh_import_run_summary(connection, account=account, run_id=str(episode["run_id"]))
        factory_id = outcome["source_record"].get("factory_id")
        if factory_id:
            record_routed_candidate(
                connection,
                account=account,
                factory_id=str(factory_id),
                factory_name=outcome["source_record"].get("factory_name"),
            )
        return {"state": "ROUTED", "sourceProductId": source_product_id, "sourceType": outcome["source_type"]}


def _set_routing_failure(engine: Engine, account: str, episode_id: str, code: str, *, state: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE candidate_episodes SET routing_state=:state, routing_error_code=:code, "
                "routed_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=:episode_id AND account=:account "
                "AND data_scope='PRODUCTION' AND source_product_id IS NULL AND episode_state='OPEN'"
            ),
            {"state": state, "code": code[:64], "episode_id": episode_id, "account": account},
        )


def _refresh_import_run_summary(connection, *, account: str, run_id: str) -> None:
    counts = connection.execute(
        text(
            "SELECT row_state, COUNT(*) AS row_count FROM import_rows WHERE run_id=:run_id "
            "AND account=:account AND data_scope='PRODUCTION' GROUP BY row_state"
        ),
        {"run_id": run_id, "account": account},
    ).mappings().all()
    row_states = {str(row["row_state"]): int(row["row_count"]) for row in counts}
    route_rows = connection.execute(
        text(
            "SELECT row_json->>'routeStatus' AS route, COUNT(*) AS route_count FROM import_rows "
            "WHERE run_id=:run_id AND account=:account AND data_scope='PRODUCTION' "
            "GROUP BY row_json->>'routeStatus'"
        ),
        {"run_id": run_id, "account": account},
    ).mappings().all()
    routes = {str(row["route"]): int(row["route_count"]) for row in route_rows if row["route"]}
    errors = sum(value for key, value in row_states.items() if key in {"INVALID", "DUPLICATE", "IDENTITY_UNRESOLVED", "ERROR"})
    state = "PARTIAL" if errors else "SUCCEEDED"
    summary = {"rowStates": row_states, "routes": routes}
    connection.execute(
        text(
            "UPDATE import_runs SET run_state=:state, status='COMPLETED', processed_count=:processed, "
            "afterbuy_count=:afterbuy, direct_otto_count=:direct, skipped_count=:skipped, error_count=:errors, "
            "summary_json=CAST(:summary AS jsonb), coverage_json=coverage_json || CAST(:summary AS jsonb), "
            "finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP) WHERE id=:run_id AND account=:account "
            "AND data_scope='PRODUCTION' AND run_type='FILE_IMPORT'"
        ),
        {
            "state": state,
            "processed": row_states.get("VALID", 0),
            "afterbuy": routes.get("FOUND", 0),
            "direct": routes.get("NOT_FOUND_CONFIRMED", 0),
            "skipped": row_states.get("DUPLICATE", 0),
            "errors": errors,
            "summary": json.dumps(summary, separators=(",", ":")),
            "run_id": run_id,
            "account": account,
        },
    )


def mark_candidate_enrichment_unavailable(
    engine: Engine, *, account: str, episode_id: str, code: str
) -> None:
    """Expose a disabled integration as a durable route error without network access."""
    _set_routing_failure(engine, account, episode_id, code, state="ERROR")

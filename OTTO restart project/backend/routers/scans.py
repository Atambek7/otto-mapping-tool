"""Authenticated API for explicitly requested, bounded discovery scans."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from schemas.scans import (
    BaselineConfirmRequest,
    CandidateObservationListResponse,
    DiscoveryScheduleDTO,
    DiscoveryScheduleUpdateRequest,
    IncrementalScanRequest,
    KnownSkuReconciliationRequest,
    ScanAcceptedResponse,
    ScanRunDTO,
    ScanRunListResponse,
)
from security import Principal
from services.discovery_scan_service import (
    DiscoveryScanError,
    create_incremental_scan,
    create_known_sku_reconciliation,
    get_scan_run,
    list_scan_runs,
)
from services.discovery_schedule_service import (
    DiscoveryScheduleError,
    confirm_baseline,
    get_schedule,
    update_schedule,
)

router = APIRouter(prefix="/api/restart/scans", tags=["discovery-scans"])


def _scan_write_principal(
    request: Request,
    account: str,
    csrf_token: str | None,
    *,
    require_read_enabled: bool = True,
) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    origins = getattr(request.app.state, "allowed_origins", frozenset())
    config = getattr(request.app.state, "runtime_config", None)
    if request.headers.get("origin") not in origins or not csrf_token:
        raise HTTPException(
            status_code=403,
            detail={"code": "CSRF_REQUIRED", "message": "Verified operator origin and CSRF are required."},
        )
    if not principal or principal.kind != "operator" or not principal.csrf_verified:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "A verified operator is required."})
    if "restart:write" not in principal.permissions or account not in principal.accounts:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "An account-scoped operator is required."})
    if config is None or config.mode.value != "PRODUCTION":
        raise HTTPException(
            status_code=409,
            detail={"code": "PRODUCTION_MODE_REQUIRED", "message": "Discovery run creation is unavailable outside PRODUCTION mode."},
        )
    if require_read_enabled and not config.otto_read_enabled:
        raise HTTPException(
            status_code=409,
            detail={"code": "OTTO_READ_DISABLED", "message": "Enable the explicit OTTO read flag before creating a scan."},
        )
    return principal


def _to_dto(row: dict[str, Any], *, include_parameters: bool) -> dict[str, Any]:
    result = {
        "id": str(row["id"]),
        "account": row["account"],
        "runType": row["run_type"],
        "runState": row["run_state"],
        "windowFrom": row["window_from"],
        "cutoffAt": row["cutoff_at"],
        "totalRows": int(row["total_rows"] or 0),
        "coverage": row["coverage_json"] or {},
        "errors": row["error_json"] or [],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
    }
    if include_parameters:
        result["parameters"] = row.get("parameters_json") or {}
    return result


def _create_response(result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=202, content=result)


@router.post("", response_model=ScanAcceptedResponse, status_code=202)
def start_incremental_scan(
    payload: IncrementalScanRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
) -> JSONResponse:
    principal = _scan_write_principal(request, payload.account, x_csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key must contain 8–128 characters."})
    body = payload.model_dump(mode="json", by_alias=True)
    try:
        with request.app.state.database_engine.begin() as connection:
            result = create_incremental_scan(
                connection,
                account=payload.account,
                actor=principal.subject,
                idempotency_key=idempotency_key,
                from_date=payload.from_date,
                request_body=body,
            )
    except DiscoveryScanError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None
    except IntegrityError:
        raise HTTPException(status_code=409, detail={"code": "SCAN_CONFLICT", "message": "A scan conflicts with current account state."}) from None
    return _create_response(result)


@router.post("/reconcile-known", response_model=ScanAcceptedResponse, status_code=202)
def start_known_sku_reconciliation(
    payload: KnownSkuReconciliationRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
) -> JSONResponse:
    principal = _scan_write_principal(request, payload.account, x_csrf_token)
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key must contain 8–128 characters."})
    body = payload.model_dump(mode="json", by_alias=True)
    try:
        with request.app.state.database_engine.begin() as connection:
            result = create_known_sku_reconciliation(
                connection,
                account=payload.account,
                actor=principal.subject,
                idempotency_key=idempotency_key,
                skus=payload.skus,
                request_body=body,
            )
    except DiscoveryScanError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None
    except IntegrityError:
        raise HTTPException(status_code=409, detail={"code": "SCAN_CONFLICT", "message": "A reconciliation conflicts with current account state."}) from None
    return _create_response(result)


@router.get("", response_model=ScanRunListResponse)
def get_scan_runs(
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with request.app.state.database_engine.connect() as connection:
        rows = list_scan_runs(connection, account=account, limit=limit, offset=offset)
    return {"items": [_to_dto(row, include_parameters=False) for row in rows], "count": len(rows), "limit": limit, "offset": offset}


@router.get("/{run_id}", response_model=ScanRunDTO)
def get_scan_run_detail(
    run_id: str,
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
) -> dict[str, Any]:
    with request.app.state.database_engine.connect() as connection:
        row = get_scan_run(connection, account=account, run_id=run_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "SCAN_NOT_FOUND", "message": "Discovery run was not found."})
    return _to_dto(row, include_parameters=True)


@router.get("/{run_id}/candidates", response_model=CandidateObservationListResponse)
def get_scan_candidates(
    run_id: str,
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500, alias="pageSize"),
) -> dict[str, Any]:
    offset = (page - 1) * page_size
    with request.app.state.database_engine.connect() as connection:
        run_exists = connection.execute(
            text("SELECT 1 FROM import_runs WHERE id=:run_id AND account=:account AND data_scope='PRODUCTION'"),
            {"run_id": run_id, "account": account},
        ).scalar_one_or_none()
        if run_exists is None:
            raise HTTPException(status_code=404, detail={"code": "SCAN_NOT_FOUND", "message": "Discovery run was not found."})
        total = connection.execute(
            text(
                "SELECT COUNT(*) FROM otto_status_observations "
                "WHERE run_id=:run_id AND account=:account AND data_scope='PRODUCTION'"
            ),
            {"run_id": run_id, "account": account},
        ).scalar_one()
        rows = connection.execute(
            text(
                "SELECT obs.requested_sku, obs.classification, obs.period, "
                "obs.reduction_status AS reduction_state, "
                "ce.id::text AS episode_id, ce.episode_state, ce.origin, ce.routing_state, "
                "ce.routing_error_code, ce.source_product_id::text AS source_product_id, "
                "sp.source_type, sp.factory_id, sp.factory_name, sp.original_title AS title "
                "FROM otto_status_observations obs "
                "LEFT JOIN candidate_episodes ce ON ce.last_observation_id=obs.id "
                "AND ce.account=obs.account AND ce.data_scope=obs.data_scope "
                "LEFT JOIN source_products sp ON sp.id=ce.source_product_id "
                "AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
                "WHERE obs.run_id=:run_id AND obs.account=:account AND obs.data_scope='PRODUCTION' "
                "ORDER BY obs.sequence_no, obs.requested_sku LIMIT :limit OFFSET :offset"
            ),
            {"run_id": run_id, "account": account, "limit": page_size, "offset": offset},
        ).mappings().all()
    return {
        "items": [
            {
                "requestedSku": row["requested_sku"],
                "classification": row["classification"],
                "period": row["period"],
                "reductionState": row["reduction_state"],
                "episodeId": row["episode_id"],
                "episodeState": row["episode_state"],
                "origin": row["origin"],
                "routingState": row["routing_state"],
                "routingErrorCode": row["routing_error_code"],
                "sourceProductId": row["source_product_id"],
                "sourceType": row["source_type"],
                "factoryId": row["factory_id"],
                "factoryName": row["factory_name"],
                "title": row["title"],
            }
            for row in rows
        ],
        "count": len(rows),
        "page": page,
        "pageSize": page_size,
        "totalPages": (int(total) + page_size - 1) // page_size,
    }


@router.post("/baseline/confirm")
def confirm_baseline_endpoint(
    payload: BaselineConfirmRequest,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    principal = _scan_write_principal(
        request, payload.account, x_csrf_token, require_read_enabled=False
    )
    try:
        with request.app.state.database_engine.begin() as connection:
            return confirm_baseline(
                connection,
                account=payload.account,
                run_id=payload.run_id,
                actor=principal.subject,
            )
    except DiscoveryScheduleError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None


@router.get("/schedule/config", response_model=DiscoveryScheduleDTO)
def get_discovery_schedule(
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
) -> dict[str, Any]:
    with request.app.state.database_engine.connect() as connection:
        return get_schedule(connection, account=account)


@router.put("/schedule", response_model=DiscoveryScheduleDTO)
def put_discovery_schedule(
    payload: DiscoveryScheduleUpdateRequest,
    request: Request,
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    principal = _scan_write_principal(
        request, payload.account, x_csrf_token, require_read_enabled=payload.enabled
    )
    config = getattr(request.app.state, "runtime_config", None)
    if payload.enabled and (config is None or not config.discovery_schedule_enabled):
        raise HTTPException(
            status_code=409,
            detail={"code": "DISCOVERY_SCHEDULE_DISABLED", "message": "The server-side weekly schedule feature flag is off."},
        )
    try:
        with request.app.state.database_engine.begin() as connection:
            result = update_schedule(
                connection,
                account=payload.account,
                enabled=payload.enabled,
                timezone_name=payload.timezone,
                weekday=payload.weekday,
                local_time=payload.local_time,
            )
            connection.execute(
                text(
                    "INSERT INTO restart_events (id, account, data_scope, event_type, entity_type, entity_id, summary, metadata_json) "
                    "VALUES (:id, :account, 'PRODUCTION', :event_type, 'DISCOVERY_SCHEDULE', :account, :summary, CAST(:metadata AS jsonb))"
                ),
                {
                    "id": str(uuid4()),
                    "account": payload.account,
                    "event_type": "DISCOVERY_SCHEDULE_ENABLED" if payload.enabled else "DISCOVERY_SCHEDULE_DISABLED",
                    "summary": "Недельное обнаружение 3M/12M включено" if payload.enabled else "Недельное обнаружение 3M/12M выключено",
                    "metadata": json.dumps({
                        "actor": principal.subject,
                        "timezone": payload.timezone,
                        "weekday": payload.weekday,
                        "localTime": payload.local_time.isoformat(timespec="minutes"),
                    }, separators=(",", ":")),
                },
            )
            return result
    except DiscoveryScheduleError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None

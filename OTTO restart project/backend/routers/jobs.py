"""Authenticated status and dead-letter operations for background jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from jobs.service import (
    BackgroundJob,
    InvalidJobState,
    JobNotFound,
    list_dead_jobs,
    get_job,
    replay_dead_job,
)

router = APIRouter(prefix="/api/restart/jobs", tags=["background-jobs"])


def _format_time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _job_dto(job: BackgroundJob) -> dict[str, Any]:
    """Expose operational metadata only; never return the payload or key."""
    return {
        "id": job.id,
        "account": job.account,
        "type": job.job_type,
        "state": job.state,
        "priority": job.priority,
        "attemptsInCycle": job.attempts_in_cycle,
        "totalAttempts": job.total_attempts,
        "maxAttempts": job.max_attempts,
        "replayCount": job.replay_count,
        "lastErrorCode": job.last_error_code,
        "dispatchGeneration": job.dispatch_generation,
        "dispatchAttempts": job.dispatch_attempts,
        "dispatchedAt": _format_time(job.dispatched_at),
        "lastDispatchErrorCode": job.last_dispatch_error_code,
        "availableAt": _format_time(job.available_at),
        "createdAt": _format_time(job.created_at),
        "updatedAt": _format_time(job.updated_at),
        "startedAt": _format_time(job.started_at),
        "completedAt": _format_time(job.completed_at),
    }


@router.get("/dead")
def dead_jobs(
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    engine = request.app.state.database_engine
    with engine.connect() as connection:
        jobs = list_dead_jobs(connection, account=account, limit=limit, offset=offset)
    return {"account": account, "items": [_job_dto(job) for job in jobs], "limit": limit, "offset": offset}


@router.get("/{job_id}")
def job_status(
    job_id: str,
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
) -> dict[str, Any]:
    try:
        with request.app.state.database_engine.connect() as connection:
            job = get_job(connection, job_id=job_id, account=account)
    except JobNotFound:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found."}) from None
    return _job_dto(job)


@router.post("/{job_id}/replay")
def replay_job(
    job_id: str,
    request: Request,
    account: str = Query(..., pattern="^(JV|XL)$"),
    x_csrf_token: str | None = Header(None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    allowed_origins = getattr(request.app.state, "allowed_origins", frozenset())
    if (
        not principal
        or principal.kind != "operator"
        or not principal.csrf_verified
        or not x_csrf_token
        or request.headers.get("origin") not in allowed_origins
    ):
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Verified operator origin and CSRF are required."})
    try:
        with request.app.state.database_engine.begin() as connection:
            job = replay_dead_job(connection, job_id=job_id, account=account)
    except JobNotFound:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found."}) from None
    except InvalidJobState:
        raise HTTPException(status_code=409, detail={"code": "JOB_NOT_DEAD", "message": "Only a dead job can be replayed."}) from None
    return _job_dto(job)

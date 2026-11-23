"""Read-only, account-scoped projection of durable OttoRestart jobs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def list_queue_jobs(
    connection: Connection,
    *,
    account: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Return safe job metadata only; payloads and idempotency keys stay private."""
    scope = {"account": account}
    total = int(
        connection.execute(
            text("SELECT COUNT(*) FROM background_jobs WHERE account=:account AND data_scope='PRODUCTION'"),
            scope,
        ).scalar_one()
    )
    summary_row = connection.execute(
        text(
            "SELECT "
            "COUNT(*) FILTER (WHERE state='PENDING') AS pending, "
            "COUNT(*) FILTER (WHERE state='RUNNING') AS running, "
            "COUNT(*) FILTER (WHERE state='RETRY_WAIT') AS retry_wait, "
            "COUNT(*) FILTER (WHERE state='SUCCEEDED') AS succeeded, "
            "COUNT(*) FILTER (WHERE state='DEAD') AS dead, "
            "COUNT(*) FILTER (WHERE state='CANCELLED') AS cancelled, "
            "COUNT(*) FILTER (WHERE last_dispatch_error_code IS NOT NULL) AS dispatch_errors "
            "FROM background_jobs WHERE account=:account AND data_scope='PRODUCTION'"
        ),
        scope,
    ).mappings().one()
    rows = connection.execute(
        text(
            "SELECT id::text AS id, job_type AS \"jobType\", state, priority, "
            "attempts_in_cycle AS \"attemptsInCycle\", total_attempts AS \"totalAttempts\", "
            "max_attempts AS \"maxAttempts\", last_error_code AS \"lastErrorCode\", "
            "last_dispatch_error_code AS \"lastDispatchErrorCode\", available_at AS \"availableAt\", "
            "created_at AS \"createdAt\", started_at AS \"startedAt\", completed_at AS \"completedAt\", "
            "payload_json->>'batchId' AS \"batchId\" "
            "FROM background_jobs WHERE account=:account AND data_scope='PRODUCTION' "
            "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
        ),
        {**scope, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()

    counts = {
        "pending": int(summary_row["pending"]),
        "running": int(summary_row["running"]),
        "retryWait": int(summary_row["retry_wait"]),
        "succeeded": int(summary_row["succeeded"]),
        "dead": int(summary_row["dead"]),
        "cancelled": int(summary_row["cancelled"]),
        "dispatchErrors": int(summary_row["dispatch_errors"]),
    }
    has_active = counts["pending"] + counts["running"] + counts["retryWait"] > 0
    has_attention = counts["dead"] > 0 or counts["dispatchErrors"] > 0
    status = "NEEDS_ATTENTION" if has_attention else "RUNNING" if has_active else "IDLE"
    return {
        "status": status,
        "counts": counts,
        "items": [dict(row) for row in rows],
        "page": {
            "page": page,
            "pageSize": page_size,
            "totalItems": total,
            "totalPages": (total + page_size - 1) // page_size if total else 0,
        },
    }

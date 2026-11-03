"""Factory Lifecycle & Completion Service for OTTO Restart 3/12M.

Controls the lifecycle state machine of Afterbuy factories:
- COMPLETE: all candidates are published, 0 pending, 0 errors, 0 new unreviewed candidates
- NOT_COMPLETE: has unpublished candidates, errors, or newly discovered items
- UNKNOWN: 0 candidates discovered

Preserves full completion history in `factory_completion_history` and guarantees
that `last_completed_at` is preserved when new candidates arrive (status becomes NOT_COMPLETE
with 'Новые N' badge and 'Ранее завершено DD.MM.YYYY' subtitle).
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger("factory_service")


class FactoryService:
    """Manages factory state machine, completion recording and candidate counts."""

    @staticmethod
    def get_factory_metrics(conn: Connection, account: str, factory_id: str) -> dict[str, Any]:
        """Calculates exact live product metrics for a factory."""
        query = text(
            """
            SELECT
                COUNT(*) AS total_candidates,
                COUNT(*) FILTER (WHERE current_status = 'PUBLISHED') AS published_count,
                COUNT(*) FILTER (WHERE current_status = 'READY') AS ready_count,
                COUNT(*) FILTER (WHERE current_status = 'ERROR') AS error_count,
                COUNT(*) FILTER (WHERE is_new_candidate = TRUE) AS new_candidates_count,
                COUNT(*) FILTER (WHERE period = '3M') AS period_3m_count,
                COUNT(*) FILTER (WHERE period = '12M') AS period_12m_count
            FROM source_products
            WHERE account = :account AND factory_id = :factory_id AND period IN ('3M', '12M');
            """
        )
        row = conn.execute(query, {"account": account, "factory_id": factory_id}).fetchone()
        if not row:
            return {
                "total_candidates": 0,
                "published_count": 0,
                "ready_count": 0,
                "error_count": 0,
                "new_candidates_count": 0,
                "period_3m_count": 0,
                "period_12m_count": 0,
            }
        return {
            "total_candidates": row[0] or 0,
            "published_count": row[1] or 0,
            "ready_count": row[2] or 0,
            "error_count": row[3] or 0,
            "new_candidates_count": row[4] or 0,
            "period_3m_count": row[5] or 0,
            "period_12m_count": row[6] or 0,
        }

    @staticmethod
    def evaluate_status(metrics: dict[str, Any]) -> str:
        """Determines COMPLETE, NOT_COMPLETE, or UNKNOWN based on metrics."""
        total = metrics["total_candidates"]
        published = metrics["published_count"]
        errors = metrics["error_count"]
        new_items = metrics["new_candidates_count"]

        if total == 0:
            return "UNKNOWN"
        if published == total and new_items == 0 and errors == 0:
            return "COMPLETE"
        return "NOT_COMPLETE"

    @classmethod
    def sync_factory_status(cls, conn: Connection, account: str, factory_id: str) -> str:
        """Re-evaluates and persists factory status based on actual product state."""
        metrics = cls.get_factory_metrics(conn, account, factory_id)
        computed_status = cls.evaluate_status(metrics)

        conn.execute(
            text(
                """
                UPDATE restart_factories
                SET status = :status, updated_at = CURRENT_TIMESTAMP
                WHERE account = :account AND id = :factory_id;
                """
            ),
            {"status": computed_status, "account": account, "factory_id": factory_id},
        )
        return computed_status

    @classmethod
    def record_completion(cls, conn: Connection, account: str, factory_id: str) -> bool:
        """Explicitly records completion of a factory when all its items are published."""
        metrics = cls.get_factory_metrics(conn, account, factory_id)
        if metrics["total_candidates"] == 0 or metrics["published_count"] != metrics["total_candidates"]:
            logger.warning(
                "Factory %s (account %s) cannot be marked COMPLETE: published %d / total %d",
                factory_id, account, metrics["published_count"], metrics["total_candidates"],
            )
            return False

        # 1. Fetch published EANs
        eans_query = text(
            """
            SELECT COALESCE(aftercool_ean, '')
            FROM source_products
            WHERE account = :account AND factory_id = :factory_id AND current_status = 'PUBLISHED';
            """
        )
        published_eans = [r[0] for r in conn.execute(eans_query, {"account": account, "factory_id": factory_id}).fetchall() if r[0]]

        # 2. Update factory record (preserve first_completed_at, update last_completed_at)
        conn.execute(
            text(
                """
                UPDATE restart_factories
                SET
                    status = 'COMPLETE',
                    last_completed_at = CURRENT_TIMESTAMP,
                    first_completed_at = COALESCE(first_completed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE account = :account AND id = :factory_id;
                """
            ),
            {"account": account, "factory_id": factory_id},
        )

        # 3. Mark all products as no longer new
        conn.execute(
            text(
                """
                UPDATE source_products
                SET is_new_candidate = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE account = :account AND factory_id = :factory_id;
                """
            ),
            {"account": account, "factory_id": factory_id},
        )

        # 4. Record entry in factory_completion_history
        conn.execute(
            text(
                """
                INSERT INTO factory_completion_history (
                    id, account, factory_id, completed_at, candidates_count, published_eans_json, created_at
                ) VALUES (
                    :id, :account, :factory_id, CURRENT_TIMESTAMP, :count, CAST(:eans_json AS jsonb), CURRENT_TIMESTAMP
                );
                """
            ),
            {
                "id": str(uuid4()),
                "account": account,
                "factory_id": factory_id,
                "count": metrics["total_candidates"],
                "eans_json": json.dumps(published_eans),
            },
        )
        return True

    @classmethod
    def list_factories(
        cls,
        conn: Connection,
        account: str,
        status_filter: Optional[str] = None,
        sort_by_new: bool = False,
    ) -> List[dict[str, Any]]:
        """Returns list of factories with computed status, metrics, and completion history."""
        # Query all factories joined with aggregate metrics from source_products
        query = text(
            """
            SELECT
                rf.id,
                rf.account,
                rf.name,
                rf.status AS stored_status,
                rf.first_completed_at,
                rf.last_completed_at,
                rf.last_scanned_at,
                COUNT(sp.id) FILTER (WHERE sp.period IN ('3M', '12M')) AS total_candidates,
                COUNT(sp.id) FILTER (WHERE sp.current_status = 'PUBLISHED') AS published_count,
                COUNT(sp.id) FILTER (WHERE sp.current_status = 'READY') AS ready_count,
                COUNT(sp.id) FILTER (WHERE sp.current_status = 'ERROR') AS error_count,
                COUNT(sp.id) FILTER (WHERE sp.is_new_candidate = TRUE AND sp.period IN ('3M', '12M')) AS new_candidates_count,
                COUNT(sp.id) FILTER (WHERE sp.period = '3M') AS period_3m_count,
                COUNT(sp.id) FILTER (WHERE sp.period = '12M') AS period_12m_count
            FROM restart_factories rf
            LEFT JOIN source_products sp ON sp.account = rf.account AND sp.factory_id = rf.id
            WHERE rf.account = :account
            GROUP BY rf.id, rf.account, rf.name, rf.status, rf.first_completed_at, rf.last_completed_at, rf.last_scanned_at
            ORDER BY rf.name ASC;
            """
        )
        rows = conn.execute(query, {"account": account}).fetchall()

        results: List[dict[str, Any]] = []
        for r in rows:
            factory_id = str(r[0])
            name = str(r[1] if r[2] is None else r[2])
            first_completed = r[4].isoformat() if r[4] else None
            last_completed = r[5].isoformat() if r[5] else None
            last_scanned = r[6].isoformat() if r[6] else None

            metrics = {
                "total_candidates": r[7] or 0,
                "published_count": r[8] or 0,
                "ready_count": r[9] or 0,
                "error_count": r[10] or 0,
                "new_candidates_count": r[11] or 0,
                "period_3m_count": r[12] or 0,
                "period_12m_count": r[13] or 0,
            }

            effective_status = cls.evaluate_status(metrics)

            item = {
                "id": factory_id,
                "account": account,
                "name": name,
                "status": effective_status,
                "newCandidatesCount": metrics["new_candidates_count"],
                "totalCandidates": metrics["total_candidates"],
                "publishedCount": metrics["published_count"],
                "readyCount": metrics["ready_count"],
                "errorCount": metrics["error_count"],
                "period3mCount": metrics["period_3m_count"],
                "period12mCount": metrics["period_12m_count"],
                "firstCompletedAt": first_completed,
                "lastCompletedAt": last_completed,
                "lastScannedAt": last_scanned,
                # Previously completed indicator when new items arrive
                "wasPreviouslyCompleted": bool(last_completed and effective_status == "NOT_COMPLETE"),
            }

            # Filter if requested
            if status_filter and status_filter.upper() != "ALL":
                if item["status"] != status_filter.upper():
                    continue

            results.append(item)

        if sort_by_new:
            # Sort factories with new candidates first, then by total candidates desc
            results.sort(key=lambda x: (x["newCandidatesCount"] > 0, x["newCandidatesCount"], x["totalCandidates"]), reverse=True)

        return results

    @classmethod
    def get_completion_history(cls, conn: Connection, account: str, factory_id: str) -> List[dict[str, Any]]:
        """Retrieves history of past completions for a factory."""
        query = text(
            """
            SELECT id, completed_at, candidates_count, published_eans_json
            FROM factory_completion_history
            WHERE account = :account AND factory_id = :factory_id
            ORDER BY completed_at DESC;
            """
        )
        rows = conn.execute(query, {"account": account, "factory_id": factory_id}).fetchall()
        return [
            {
                "id": str(r[0]),
                "completedAt": r[1].isoformat() if r[1] else None,
                "candidatesCount": r[2] or 0,
                "publishedEans": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
            }
            for r in rows
        ]

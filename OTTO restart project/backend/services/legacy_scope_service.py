"""Read-only diagnostics for records retained outside OttoRestart production."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

LEGACY_SCOPE = "LEGACY_UNVERIFIED"
PRODUCTION_SCOPE = "PRODUCTION"

LEGACY_TABLES = (
    "source_products",
    "ean_pool",
    "ean_links",
    "restart_operations",
    "restart_batches",
    "restart_items",
    "restart_ean_registry",
    "restart_events",
    "otto_library",
    "restart_factories",
    "factory_completion_history",
    "import_runs",
)


class LegacyPromotionRejected(RuntimeError):
    """A legacy row cannot be promoted without the later evidence and auth gates."""


def legacy_summary(conn: Connection, account: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for table in LEGACY_TABLES:
        counts[table] = int(
            conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE account = :account AND data_scope = :scope"),
                {"account": account, "scope": LEGACY_SCOPE},
            ).scalar_one()
        )
    return {"account": account, "dataScope": LEGACY_SCOPE, "counts": counts}


def legacy_rows(
    conn: Connection,
    table: str,
    account: str,
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    if table not in LEGACY_TABLES:
        raise ValueError("Unknown legacy table.")
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("Invalid pagination limits.")
    total = int(
        conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE account = :account AND data_scope = :scope"),
            {"account": account, "scope": LEGACY_SCOPE},
        ).scalar_one()
    )
    rows = conn.execute(
        text(
            f"SELECT * FROM {table} WHERE account = :account AND data_scope = :scope "
            "ORDER BY 1 LIMIT :limit OFFSET :offset"
        ),
        {"account": account, "scope": LEGACY_SCOPE, "limit": limit, "offset": offset},
    ).mappings().all()
    return [dict(row) for row in rows], total


def _conflict_query(
    conn: Connection,
    *,
    account: str,
    count_sql: str,
    sample_sql: str,
) -> dict[str, Any]:
    params = {"account": account, "scope": LEGACY_SCOPE}
    count = int(conn.execute(text(count_sql), params).scalar_one())
    rows = conn.execute(text(sample_sql), params).mappings().all()
    return {"count": count, "sample": [dict(row) for row in rows]}


def legacy_conflict_report(conn: Connection, account: str) -> dict[str, Any]:
    """Report historical identity/lineage conflicts without repairing any row."""
    conflicts = {
        "registry_ean_missing_from_unified_pool": _conflict_query(
            conn,
            account=account,
            count_sql="""
                SELECT COUNT(*)
                FROM restart_ean_registry r
                LEFT JOIN ean_pool ep ON ep.ean = r.ean
                WHERE r.account = :account AND r.data_scope = :scope AND ep.ean IS NULL
            """,
            sample_sql="""
                SELECT r.ean, r.account, r.data_scope, r.status, r.imported_at
                FROM restart_ean_registry r
                LEFT JOIN ean_pool ep ON ep.ean = r.ean
                WHERE r.account = :account AND r.data_scope = :scope AND ep.ean IS NULL
                ORDER BY r.imported_at DESC LIMIT 25
            """,
        ),
        "registry_unified_pool_owner_mismatch": _conflict_query(
            conn,
            account=account,
            count_sql="""
                SELECT COUNT(*)
                FROM restart_ean_registry r
                JOIN ean_pool ep ON ep.ean = r.ean
                WHERE r.account = :account AND r.data_scope = :scope
                  AND (ep.account <> r.account OR ep.data_scope <> r.data_scope)
            """,
            sample_sql="""
                SELECT r.ean, r.account AS registry_account, r.data_scope AS registry_scope,
                       ep.account AS pool_account, ep.data_scope AS pool_scope,
                       r.status AS registry_status, ep.status AS pool_status
                FROM restart_ean_registry r
                JOIN ean_pool ep ON ep.ean = r.ean
                WHERE r.account = :account AND r.data_scope = :scope
                  AND (ep.account <> r.account OR ep.data_scope <> r.data_scope)
                ORDER BY r.imported_at DESC LIMIT 25
            """,
        ),
        "cross_account_or_scope_links": _conflict_query(
            conn,
            account=account,
            count_sql="""
                SELECT COUNT(*)
                FROM ean_links l
                LEFT JOIN source_products p ON p.id = l.source_product_id
                LEFT JOIN ean_pool ep ON ep.ean = l.new_ean
                WHERE l.account = :account AND l.data_scope = :scope
                  AND (p.id IS NULL OR p.account <> l.account OR p.data_scope <> l.data_scope
                       OR ep.ean IS NULL OR ep.account <> l.account OR ep.data_scope <> l.data_scope)
            """,
            sample_sql="""
                SELECT l.id AS link_id, l.account AS link_account, l.data_scope AS link_scope,
                       l.source_product_id, p.account AS source_account, p.data_scope AS source_scope,
                       l.new_ean, ep.account AS pool_account, ep.data_scope AS pool_scope
                FROM ean_links l
                LEFT JOIN source_products p ON p.id = l.source_product_id
                LEFT JOIN ean_pool ep ON ep.ean = l.new_ean
                WHERE l.account = :account AND l.data_scope = :scope
                  AND (p.id IS NULL OR p.account <> l.account OR p.data_scope <> l.data_scope
                       OR ep.ean IS NULL OR ep.account <> l.account OR ep.data_scope <> l.data_scope)
                ORDER BY l.created_at DESC LIMIT 25
            """,
        ),
        "allocated_ean_matches_other_source": _conflict_query(
            conn,
            account=account,
            count_sql="""
                SELECT COUNT(*)
                FROM ean_links l
                JOIN source_products other ON other.aftercool_ean = l.new_ean
                WHERE l.account = :account AND l.data_scope = :scope
                  AND other.data_scope = :scope
                  AND other.id <> l.source_product_id
            """,
            sample_sql="""
                SELECT l.id AS link_id, l.account, l.new_ean AS allocated_ean,
                       l.source_product_id AS linked_product_id, other.id AS conflicting_product_id,
                       other.account AS conflicting_account
                FROM ean_links l
                JOIN source_products other ON other.aftercool_ean = l.new_ean
                WHERE l.account = :account AND l.data_scope = :scope
                  AND other.data_scope = :scope
                  AND other.id <> l.source_product_id
                ORDER BY l.created_at DESC LIMIT 25
            """,
        ),
        "duplicate_source_eans": _conflict_query(
            conn,
            account=account,
            count_sql="""
                SELECT COUNT(*) FROM (
                    SELECT aftercool_ean FROM source_products
                    WHERE account = :account AND data_scope = :scope AND aftercool_ean IS NOT NULL
                    GROUP BY aftercool_ean HAVING COUNT(*) > 1
                ) conflicts
            """,
            sample_sql="""
                SELECT aftercool_ean, COUNT(*) AS row_count
                FROM source_products
                WHERE account = :account AND data_scope = :scope AND aftercool_ean IS NOT NULL
                GROUP BY aftercool_ean HAVING COUNT(*) > 1
                ORDER BY aftercool_ean LIMIT 25
            """,
        ),
        "factory_history_without_matching_scope": _conflict_query(
            conn,
            account=account,
            count_sql="""
                SELECT COUNT(*)
                FROM factory_completion_history h
                LEFT JOIN restart_factories f
                  ON f.account = h.account AND f.id = h.factory_id
                WHERE h.account = :account AND h.data_scope = :scope
                  AND (f.id IS NULL OR f.data_scope <> h.data_scope)
            """,
            sample_sql="""
                SELECT h.id, h.factory_id, h.data_scope AS history_scope,
                       f.data_scope AS factory_scope
                FROM factory_completion_history h
                LEFT JOIN restart_factories f
                  ON f.account = h.account AND f.id = h.factory_id
                WHERE h.account = :account AND h.data_scope = :scope
                  AND (f.id IS NULL OR f.data_scope <> h.data_scope)
                ORDER BY h.completed_at DESC LIMIT 25
            """,
        ),
    }
    return {
        "account": account,
        "dataScope": LEGACY_SCOPE,
        "readOnly": True,
        "conflicts": conflicts,
    }


def require_legacy_promotion(
    *,
    enabled: bool = False,
    actor_id: str | None = None,
    evidence_ref: str | None = None,
    evidence_verified: bool = False,
) -> None:
    """Fail closed until S03 auth and evidence-backed promotion are available."""
    if not enabled:
        raise LegacyPromotionRejected("Legacy promotion is disabled.")
    if not actor_id or not evidence_ref or not evidence_verified:
        raise LegacyPromotionRejected("Verified evidence and an audited actor are required.")
    # S11 owns the evidence ledger and S03 owns trusted actor identity. Until
    # those contracts exist, no flag or caller-supplied assertion can promote.
    raise LegacyPromotionRejected("Evidence-backed promotion is not implemented yet.")

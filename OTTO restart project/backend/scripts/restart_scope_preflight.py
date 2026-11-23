"""Read-only data preflight for OttoRestart migration 0018.

The command reads only OTTORESTART_PREFLIGHT_DATABASE_URL. It never falls back
to the app or migration database URL, never changes schema/data, and emits
aggregate counts only (no EANs, product IDs, credentials, or connection URL).

Run against an explicitly selected PostgreSQL snapshot at schema head 0017
(before the migration) or 0018 (before a guarded rollback):
    OTTORESTART_PREFLIGHT_DATABASE_URL=... python scripts/restart_scope_preflight.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


DATABASE_URL_ENV = "OTTORESTART_PREFLIGHT_DATABASE_URL"
SUPPORTED_SCHEMA_VERSIONS = {17, 18}


class PreflightError(RuntimeError):
    """The selected database is not a suitable migration-0018 preflight target."""


CHECKS: tuple[tuple[str, str, bool, bool, str], ...] = (
    (
        "migration_0018.up.source_products_scoped_identity",
        "SELECT account, data_scope, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, data_scope, source_type, aftercool_product_id FROM source_products"
        " WHERE aftercool_product_id IS NOT NULL GROUP BY account, data_scope, source_type, aftercool_product_id"
        " HAVING COUNT(*) > 1) conflicts GROUP BY account, data_scope ORDER BY account, data_scope",
        True,
        False,
        "New scoped source identity index cannot be created while duplicates exist.",
    ),
    (
        "migration_0018.up.otto_library_scoped_old_ean",
        "SELECT account, data_scope, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, data_scope, old_ean FROM otto_library"
        " GROUP BY account, data_scope, old_ean HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        True,
        False,
        "New scoped library identity index cannot be created while duplicates exist.",
    ),
    (
        "migration_0018.up.active_ean_link_owner",
        "SELECT account, scope_set, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, string_agg(DISTINCT data_scope, ',' ORDER BY data_scope) AS scope_set, new_ean"
        " FROM ean_links WHERE status <> 'DEACTIVATED' GROUP BY account, new_ean HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, scope_set ORDER BY account, scope_set",
        True,
        False,
        "New active EAN-link uniqueness index cannot be created while multiple active owners exist.",
    ),
    (
        "migration_0018.down.global_source_identity",
        "SELECT account, scope_set, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, string_agg(DISTINCT data_scope, ',' ORDER BY data_scope) AS scope_set,"
        " source_type, aftercool_product_id FROM source_products WHERE aftercool_product_id IS NOT NULL"
        " GROUP BY account, source_type, aftercool_product_id HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, scope_set ORDER BY account, scope_set",
        False,
        True,
        "Rollback cannot restore global source identity while scoped duplicates exist.",
    ),
    (
        "migration_0018.down.global_library_old_ean",
        "SELECT account, scope_set, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, string_agg(DISTINCT data_scope, ',' ORDER BY data_scope) AS scope_set, old_ean"
        " FROM otto_library GROUP BY account, old_ean HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, scope_set ORDER BY account, scope_set",
        False,
        True,
        "Rollback cannot restore global old-EAN uniqueness while scoped duplicates exist.",
    ),
    (
        "migration_0018.down.all_ean_link_lineage",
        "SELECT account, scope_set, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, string_agg(DISTINCT data_scope, ',' ORDER BY data_scope) AS scope_set, new_ean"
        " FROM ean_links GROUP BY account, new_ean HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, scope_set ORDER BY account, scope_set",
        False,
        True,
        "Rollback cannot restore permanent EAN-link uniqueness while duplicate lineage is retained.",
    ),
    (
        "operations.duplicate_production_attempt_identity",
        "SELECT account, data_scope, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, data_scope, source_product_id, attempt_number FROM restart_operations"
        " WHERE data_scope='PRODUCTION' AND attempt_number IS NOT NULL"
        " GROUP BY account, data_scope, source_product_id, attempt_number HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "Production operation attempt identities need reconciliation.",
    ),
    (
        "operations.duplicate_production_episode_identity",
        "SELECT account, data_scope, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, data_scope, candidate_episode_id FROM restart_operations"
        " WHERE data_scope='PRODUCTION' AND candidate_episode_id IS NOT NULL"
        " GROUP BY account, data_scope, candidate_episode_id HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "Production episode identities need reconciliation.",
    ),
    (
        "operations.duplicate_open_production_source_identity",
        "SELECT account, data_scope, COUNT(*) AS conflict_groups FROM ("
        " SELECT account, data_scope, source_product_id FROM restart_operations"
        " WHERE data_scope='PRODUCTION' AND status NOT IN ('PUBLISHED','FAILED','CANCELLED')"
        " GROUP BY account, data_scope, source_product_id HAVING COUNT(*) > 1) conflicts"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "Multiple open production operations exist for one source product.",
    ),
    (
        "references.orphan_operation_source_product",
        "SELECT o.account, o.data_scope, COUNT(*) AS affected_rows FROM restart_operations o"
        " LEFT JOIN source_products s ON s.id=o.source_product_id AND s.account=o.account AND s.data_scope=o.data_scope"
        " WHERE s.id IS NULL GROUP BY o.account, o.data_scope ORDER BY o.account, o.data_scope",
        False,
        False,
        "Operation source-product reference is absent or crosses account/scope.",
    ),
    (
        "references.orphan_operation_candidate_episode",
        "SELECT o.account, o.data_scope, COUNT(*) AS affected_rows FROM restart_operations o"
        " LEFT JOIN candidate_episodes c ON c.id=o.candidate_episode_id AND c.account=o.account"
        " AND c.data_scope=o.data_scope AND c.source_product_id=o.source_product_id"
        " WHERE o.candidate_episode_id IS NOT NULL AND c.id IS NULL"
        " GROUP BY o.account, o.data_scope ORDER BY o.account, o.data_scope",
        False,
        False,
        "Operation candidate-episode reference is absent or crosses identity boundaries.",
    ),
    (
        "references.orphan_ean_link_source_product",
        "SELECT l.account, l.data_scope, COUNT(*) AS affected_rows FROM ean_links l"
        " LEFT JOIN source_products s ON s.id=l.source_product_id AND s.account=l.account AND s.data_scope=l.data_scope"
        " WHERE s.id IS NULL GROUP BY l.account, l.data_scope ORDER BY l.account, l.data_scope",
        False,
        False,
        "EAN lineage source-product reference is absent or crosses account/scope.",
    ),
    (
        "references.orphan_ean_link_pool_item",
        "SELECT l.account, l.data_scope, COUNT(*) AS affected_rows FROM ean_links l"
        " LEFT JOIN ean_pool p ON p.ean=l.new_ean AND p.account=l.account AND p.data_scope=l.data_scope"
        " WHERE p.ean IS NULL GROUP BY l.account, l.data_scope ORDER BY l.account, l.data_scope",
        False,
        False,
        "EAN lineage pool reference is absent or crosses account/scope.",
    ),
    (
        "references.orphan_ean_link_operation",
        "SELECT l.account, l.data_scope, COUNT(*) AS affected_rows FROM ean_links l"
        " LEFT JOIN restart_operations o ON o.id=l.operation_id AND o.account=l.account AND o.data_scope=l.data_scope"
        " WHERE l.operation_id IS NOT NULL AND o.id IS NULL"
        " GROUP BY l.account, l.data_scope ORDER BY l.account, l.data_scope",
        False,
        False,
        "EAN lineage operation reference is absent or crosses account/scope.",
    ),
    (
        "references.orphan_pool_reservation_operation",
        "SELECT p.account, p.data_scope, COUNT(*) AS affected_rows FROM ean_pool p"
        " LEFT JOIN restart_operations o ON o.id=p.reservation_operation_id AND o.account=p.account"
        " AND o.data_scope=p.data_scope WHERE p.reservation_operation_id IS NOT NULL AND o.id IS NULL"
        " GROUP BY p.account, p.data_scope ORDER BY p.account, p.data_scope",
        False,
        False,
        "Pool reservation owner is absent or crosses account/scope.",
    ),
    (
        "references.orphan_source_import_run",
        "SELECT s.account, s.data_scope, COUNT(*) AS affected_rows FROM source_products s"
        " LEFT JOIN import_runs r ON r.id=s.import_run_id AND r.account=s.account AND r.data_scope=s.data_scope"
        " WHERE s.import_run_id IS NOT NULL AND r.id IS NULL"
        " GROUP BY s.account, s.data_scope ORDER BY s.account, s.data_scope",
        False,
        False,
        "Source-product import-run reference is absent or crosses account/scope.",
    ),
    (
        "ean_pool.eligible_without_verification",
        "SELECT account, data_scope, COUNT(*) AS affected_rows FROM ean_pool"
        " WHERE eligibility_status='ELIGIBLE' AND (verified_by IS NULL OR verified_at IS NULL)"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "EAN eligibility lacks a verification actor or timestamp.",
    ),
    (
        "ean_pool.production_available_not_eligible",
        "SELECT account, data_scope, COUNT(*) AS affected_rows FROM ean_pool"
        " WHERE data_scope='PRODUCTION' AND status='AVAILABLE' AND eligibility_status <> 'ELIGIBLE'"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "Production AVAILABLE pool entry is not eligible.",
    ),
    (
        "ean_pool.reserved_used_missing_owner",
        "SELECT account, data_scope, COUNT(*) AS affected_rows FROM ean_pool"
        " WHERE status IN ('RESERVED','USED')"
        " AND reservation_operation_id IS NULL GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "RESERVED/USED pool entry has no reservation operation.",
    ),
    (
        "ean_pool.production_reserved_missing_timestamp",
        "SELECT account, data_scope, COUNT(*) AS affected_rows FROM ean_pool"
        " WHERE data_scope='PRODUCTION' AND status='RESERVED' AND reserved_at IS NULL"
        " GROUP BY account, data_scope ORDER BY account, data_scope",
        False,
        False,
        "Production RESERVED pool entry has no reservation timestamp.",
    ),
    (
        "ean_pool.reservation_operation_mismatch",
        "SELECT p.account, p.data_scope, COUNT(*) AS affected_rows FROM ean_pool p"
        " JOIN restart_operations o ON o.id=p.reservation_operation_id AND o.account=p.account"
        " AND o.data_scope=p.data_scope"
        " AND (p.status NOT IN ('RESERVED','USED','QUARANTINED') OR p.eligibility_status <> 'ELIGIBLE'"
        " OR (p.status='RESERVED' AND (o.new_ean IS DISTINCT FROM p.ean OR o.status='CANCELLED')))"
        " GROUP BY p.account, p.data_scope ORDER BY p.account, p.data_scope",
        False,
        False,
        "Pool reservation and owning operation state disagree.",
    ),
)


def _schema_version(connection: Connection) -> int:
    table_exists = connection.execute(text("SELECT to_regclass('public.schema_migrations')")).scalar_one()
    if table_exists is None:
        raise PreflightError("schema_migrations table is missing.")
    history = connection.execute(
        text("SELECT MIN(version) AS first_version, MAX(version) AS last_version, COUNT(*) AS revision_count "
             "FROM public.schema_migrations")
    ).mappings().one()
    if history["last_version"] is None:
        raise PreflightError("schema_migrations has no applied version.")
    first_version = int(history["first_version"])
    version = int(history["last_version"])
    revision_count = int(history["revision_count"])
    if first_version != 1 or revision_count != version:
        raise PreflightError("schema_migrations history is incomplete or has a version gap.")
    return version


def _require_tables(connection: Connection) -> None:
    expected = {
        "schema_migrations",
        "source_products",
        "otto_library",
        "ean_links",
        "ean_pool",
        "restart_operations",
        "candidate_episodes",
        "import_runs",
    }
    present = set(
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        ).scalars()
    )
    missing = sorted(expected - present)
    if missing:
        raise PreflightError("Required OttoRestart schema objects are missing: " + ", ".join(missing))


def _run_checks(connection: Connection, schema_version: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check_id, sql, blocks_migration, blocks_rollback, purpose in CHECKS:
        if check_id.startswith("migration_0018.up.") and schema_version != 17:
            continue
        if check_id.startswith("migration_0018.down.") and schema_version != 18:
            continue
        rows = [dict(row) for row in connection.execute(text(sql)).mappings()]
        for row in rows:
            for key, value in tuple(row.items()):
                if hasattr(value, "item"):
                    row[key] = value.item()
        results.append(
            {
                "id": check_id,
                "blocks_migration": blocks_migration and schema_version == 17,
                "blocks_rollback": blocks_rollback and schema_version == 18,
                "issue_groups": sum(int(row.get("conflict_groups", row.get("affected_rows", 0))) for row in rows),
                "counts": rows,
                "meaning": purpose,
            }
        )
    return results


def main() -> int:
    database_url = os.environ.get(DATABASE_URL_ENV, "").strip()
    if not database_url:
        print(f"Set {DATABASE_URL_ENV} to an explicitly selected PostgreSQL snapshot.", file=sys.stderr)
        return 2

    engine = None
    try:
        engine = create_engine(database_url, pool_pre_ping=True, echo=False)
        if engine.dialect.name != "postgresql":
            raise PreflightError("The preflight supports PostgreSQL only.")
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                version = _schema_version(connection)
                if version not in SUPPORTED_SCHEMA_VERSIONS:
                    raise PreflightError(
                        f"Expected schema head 0017 (before upgrade) or 0018 (before rollback); found {version:04d}."
                    )
                _require_tables(connection)
                checks = _run_checks(connection, version)
        report = {
            "tool": "ottorestart_scope_preflight",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": version,
            "preflight_phase": "BEFORE_MIGRATION_0018" if version == 17 else "BEFORE_ROLLBACK_0018",
            "read_only": True,
            "database_url_emitted": False,
            "identifier_values_emitted": False,
            "checks": checks,
            "migration_blocking_groups": sum(
                check["issue_groups"]
                for check in checks
                if check["blocks_migration"]
            ),
            "rollback_blocking_groups": sum(
                check["issue_groups"]
                for check in checks
                if check["blocks_rollback"]
            ),
            "review_issue_groups": sum(
                check["issue_groups"]
                for check in checks
                if not check["blocks_migration"] and not check["blocks_rollback"]
            ),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if report["migration_blocking_groups"] else 0
    except PreflightError as exc:
        print(f"Preflight refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # do not echo driver errors that may include connection details
        error_type = type(exc).__name__
        print(
            f"Preflight failed ({error_type}); details and connection values were suppressed.",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

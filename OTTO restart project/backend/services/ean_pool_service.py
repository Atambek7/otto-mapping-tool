"""Single-store 13-digit barcode pool operations for OttoRestart.

Legacy records remain in ``LEGACY_UNVERIFIED``. Production allocations only
consume conflict-free entries from the account-scoped production pool.
"""

from __future__ import annotations

import json
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.gtin_service import identity_conflict, valid_ean13, validate_gtin_eligibility

PRODUCTION_SCOPE = "PRODUCTION"
LEGACY_SCOPE = "LEGACY_UNVERIFIED"
class EanPoolError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def is_valid_ean13(value: str) -> bool:
    return valid_ean13(value)


def reserve_operation_ean(
    conn: Connection, *, account: str, operation_id: str, expected_version: int, actor: str,
) -> dict[str, Any]:
    """Operator-selected local reservation with version fencing; no external reads."""
    _require_account(account)
    try:
        operation_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise EanPoolError("INVALID_OPERATION", "Operation ID must be a UUID.") from None
    row = conn.execute(text(
        "SELECT version FROM restart_operations WHERE id=:id AND account=:account "
        "AND data_scope='PRODUCTION' FOR UPDATE"
    ), {"id": operation_id, "account": account}).mappings().first()
    if row is None:
        raise EanPoolError("OPERATION_NOT_FOUND", "Production operation was not found.")
    if type(expected_version) is not int or expected_version != int(row["version"]):
        raise EanPoolError("VERSION_CONFLICT", "Operation changed since it was read.", {"currentVersion": int(row["version"])})
    result = reserve_ean_for_operation(conn, account=account, operation_id=operation_id)
    if result.get("status") not in {"RESERVED", "USED"}:
        raise EanPoolError(str(result.get("code") or "EAN_RESERVATION_FAILED"), "No free eligible EAN could be assigned.")
    if not result.get("reused"):
        conn.execute(text(
            "INSERT INTO restart_events (id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json) "
            "VALUES (:id,:account,'PRODUCTION','EAN_RESERVED','RESTART_OPERATION',:operation_id,"
            "'EAN reserved by operator selection',CAST(:metadata AS jsonb))"
        ), {"id": str(uuid4()), "account": account, "operation_id": operation_id,
            "metadata": json.dumps({"actor": actor, "ean": result["ean"]})})
    version = conn.execute(text("SELECT version FROM restart_operations WHERE id=:id AND account=:account"),
                           {"id": operation_id, "account": account}).scalar_one()
    return {**result, "version": int(version)}


def _require_account(account: str) -> None:
    if account not in {"JV", "XL"}:
        raise EanPoolError("INVALID_ACCOUNT", "Account must be JV or XL.")


def import_pool_entries(
    conn: Connection,
    *,
    account: str,
    eans: list[str],
    actor: str,
    data_scope: str,
) -> dict[str, Any]:
    """Import 13-digit barcodes; identity conflicts remain quarantined."""
    _require_account(account)
    if data_scope not in {PRODUCTION_SCOPE, LEGACY_SCOPE}:
        raise EanPoolError("INVALID_SCOPE", "Unsupported EAN pool data scope.")
    if not eans or any(not is_valid_ean13(ean) for ean in eans):
        raise EanPoolError("INVALID_EAN", "EAN must contain exactly 13 ASCII digits.")
    if len(set(eans)) != len(eans):
        raise EanPoolError("DUPLICATE_EAN", "Duplicate EAN values are not allowed in one import.")

    # Serialize global identity checks by EAN, not by account. This keeps one
    # free identifier from being imported into JV and XL concurrently.
    for ean in sorted(eans):
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:ean, 0))"), {"ean": ean})

    existing: list[str] = []
    for offset in range(0, len(eans), 500):
        chunk = eans[offset : offset + 500]
        existing.extend(
            conn.execute(
                text("SELECT ean FROM ean_pool WHERE ean = ANY(:eans)"), {"eans": chunk}
            ).scalars().all()
        )
        existing.extend(
            conn.execute(
                text("SELECT ean FROM restart_ean_registry WHERE ean = ANY(:eans)"), {"eans": chunk}
            ).scalars().all()
        )
    existing = sorted(set(existing))
    if existing:
        raise EanPoolError(
            "EAN_ALREADY_EXISTS",
            "Some EANs already exist in the unified or retained legacy pool.",
            {"count": len(existing), "samples": existing[:25]},
        )

    rows = []
    for ean in eans:
        conflict = identity_conflict(conn, ean, account) if data_scope == PRODUCTION_SCOPE else False
        eligible = data_scope == PRODUCTION_SCOPE and not conflict
        rows.append(
            {
                "ean": ean,
                "account": account,
                "status": "QUARANTINED" if conflict else "AVAILABLE",
                "data_scope": data_scope,
                "provenance": "MANUAL_IMPORT",
                "eligibility": "INELIGIBLE" if conflict else "ELIGIBLE" if eligible else "UNVERIFIED",
                "reason": "IDENTITY_CONFLICT" if conflict else None,
                "actor": actor[:200],
                "verified": eligible,
            }
        )
    conn.execute(
        text(
            """
            INSERT INTO ean_pool (
                ean, account, status, data_scope, provenance_source, eligibility_status,
                imported_by, imported_at, verified_by, verified_at,
                quarantined_at, quarantine_reason, updated_at
            ) VALUES (
                :ean, :account, :status, :data_scope, :provenance, :eligibility,
                :actor, CURRENT_TIMESTAMP,
                CASE WHEN :verified THEN :actor ELSE NULL END,
                CASE WHEN :verified THEN CURRENT_TIMESTAMP ELSE NULL END,
                CASE WHEN :status = 'QUARANTINED' THEN CURRENT_TIMESTAMP ELSE NULL END,
                :reason, CURRENT_TIMESTAMP
            )
            """
        ),
        rows,
    )
    return {
        "account": account,
        "dataScope": data_scope,
        "importedCount": len(rows),
        "status": "PARTIAL_QUARANTINE" if any(row["status"] == "QUARANTINED" for row in rows) else "AVAILABLE",
        "unverifiedCount": sum(row["eligibility"] == "UNVERIFIED" for row in rows),
        "ineligibleCount": sum(row["eligibility"] == "INELIGIBLE" for row in rows),
    }


def reserve_ean_for_operation(
    conn: Connection,
    *,
    account: str,
    operation_id: str,
    approved_evidence_types: Iterable[str] = (),
) -> dict[str, Any]:
    """Reserve an eligible EAN for one production DRAFT operation, retry-safely."""
    _require_account(account)
    try:
        operation_uuid = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise EanPoolError("INVALID_OPERATION", "Operation ID must be a UUID.") from None

    operation = conn.execute(
        text(
            """
            SELECT o.id, o.status, o.new_ean, o.source_product_id, sp.aftercool_ean
            FROM restart_operations o
            JOIN source_products sp
              ON sp.id = o.source_product_id AND sp.account = o.account AND sp.data_scope = o.data_scope
            WHERE o.id = :operation_id AND o.account = :account AND o.data_scope = 'PRODUCTION'
            FOR UPDATE OF o
            """
        ),
        {"operation_id": operation_uuid, "account": account},
    ).mappings().first()
    if operation is None:
        raise EanPoolError("OPERATION_NOT_FOUND", "Production operation was not found.")

    existing_reservation = conn.execute(
        text(
            """
            SELECT ean, status FROM ean_pool
            WHERE reservation_operation_id = :operation_id AND account = :account
              AND data_scope = 'PRODUCTION'
            FOR UPDATE
            """
        ),
        {"operation_id": operation_uuid, "account": account},
    ).mappings().first()
    if operation["new_ean"] is not None or existing_reservation is not None:
        if (
            operation["new_ean"] is not None
            and existing_reservation is not None
            and operation["new_ean"] == existing_reservation["ean"]
            and existing_reservation["status"] in {"RESERVED", "USED"}
        ):
            if existing_reservation["status"] == "RESERVED":
                eligibility = validate_gtin_eligibility(
                    conn,
                    account=account,
                    ean=existing_reservation["ean"],
                    source_product_id=str(operation["source_product_id"]),
                    approved_evidence_types=approved_evidence_types,
                    operation_id=operation_uuid,
                )
                if not eligibility.eligible:
                    conn.execute(
                        text(
                            "UPDATE ean_pool SET status = 'QUARANTINED', quarantined_at = CURRENT_TIMESTAMP, "
                            "quarantine_reason = :reason, version = version + 1, updated_at = CURRENT_TIMESTAMP "
                            "WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'"
                        ),
                        {"reason": eligibility.code, "ean": existing_reservation["ean"], "account": account},
                    )
                    return {
                        "operationId": operation_uuid,
                        "account": account,
                        "ean": existing_reservation["ean"],
                        "status": "QUARANTINED",
                        "code": eligibility.code,
                        "reused": True,
                    }
            return {
                "operationId": operation_uuid,
                "account": account,
                "ean": existing_reservation["ean"],
                "status": existing_reservation["status"],
                "reused": True,
            }
        raise EanPoolError("RESERVATION_CONFLICT", "Operation and pool reservation do not agree.")
    if operation["status"] != "DRAFT":
        raise EanPoolError("OPERATION_NOT_DRAFT", "A new EAN can only be reserved for a DRAFT operation.")

    quarantined_count = 0
    while True:
        pool_row = conn.execute(
            text(
                """
                SELECT ean FROM ean_pool
                WHERE account = :account AND data_scope = 'PRODUCTION'
                  AND status = 'AVAILABLE' AND eligibility_status = 'ELIGIBLE'
                  AND reservation_operation_id IS NULL
                  AND (owner_reference IS NULL OR owner_reference = :source_product_id)
                ORDER BY created_at, ean
                LIMIT 1 FOR UPDATE SKIP LOCKED
                """
            ),
            {"account": account, "source_product_id": str(operation["source_product_id"])},
        ).mappings().first()
        if pool_row is None:
            # Return a non-success result instead of raising after quarantine
            # writes; the caller can commit those audit changes, then surface
            # the explicit code to its caller.
            return {
                "operationId": operation_uuid,
                "account": account,
                "ean": None,
                "status": "UNAVAILABLE",
                "code": "POOL_EXHAUSTED",
                "reused": False,
                "quarantinedCount": quarantined_count,
            }

        ean = pool_row["ean"]
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:ean, 0))"), {"ean": ean})
        global_pool_rows = conn.execute(text(
            "SELECT account,status,reservation_operation_id FROM ean_pool "
            "WHERE ean=:ean AND data_scope='PRODUCTION' ORDER BY account"
        ), {"ean": ean}).mappings().all()
        if len(global_pool_rows) != 1:
            raise EanPoolError(
                "EAN_GLOBAL_DUPLICATE",
                "This EAN exists more than once in the production pool; quarantine the duplicates before assignment.",
                {"productionPoolRows": len(global_pool_rows)},
            )
        eligibility = validate_gtin_eligibility(
            conn,
            account=account,
            ean=ean,
            source_product_id=str(operation["source_product_id"]),
            approved_evidence_types=approved_evidence_types,
            operation_id=operation_uuid,
        )
        if not eligibility.eligible:
            conn.execute(
                text(
                    """
                    UPDATE ean_pool
                    SET status = 'QUARANTINED', eligibility_status = 'INELIGIBLE',
                        quarantined_at = CURRENT_TIMESTAMP, quarantine_reason = :reason,
                        version = version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'
                    """
                ),
                {"ean": ean, "account": account, "reason": eligibility.code},
            )
            quarantined_count += 1
            continue

        changed = conn.execute(
            text(
                """
                UPDATE ean_pool
                SET status = 'RESERVED', reserved_at = CURRENT_TIMESTAMP,
                    reservation_operation_id = :operation_id, owner_reference = :source_product_id,
                    version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'
                  AND status = 'AVAILABLE' AND eligibility_status = 'ELIGIBLE'
                """
            ),
            {
                "operation_id": operation_uuid,
                "source_product_id": str(operation["source_product_id"]),
                "ean": ean,
                "account": account,
            },
        ).rowcount
        if changed != 1:
            continue
        attached = conn.execute(
            text(
                """
                UPDATE restart_operations
                SET new_ean = :ean, target_sku = :ean, version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = :operation_id AND account = :account AND data_scope = 'PRODUCTION'
                  AND status = 'DRAFT' AND new_ean IS NULL
                """
            ),
            {"ean": ean, "operation_id": operation_uuid, "account": account},
        ).rowcount
        if attached != 1:
            raise EanPoolError("RESERVATION_CONFLICT", "Operation changed while reserving an EAN.")
        # Record into ean_links for audit & UI lineage
        link_insert = conn.execute(
            text(
                """
                INSERT INTO ean_links (
                    id, account, data_scope, source_product_id,
                    operation_id, source_ean, new_ean, status, created_at, updated_at
                )
                SELECT
                    gen_random_uuid(), :account, 'PRODUCTION', o.source_product_id,
                    o.id, ce.confirmed_ean, :ean, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM restart_operations o
                JOIN candidate_episodes ce ON ce.id = o.candidate_episode_id
                WHERE o.id = :operation_id AND o.account = :account
                ON CONFLICT DO NOTHING
                """
            ),
            {"account": account, "operation_id": operation_uuid, "ean": ean},
        )
        if link_insert.rowcount != 1:
            raise EanPoolError(
                "EAN_LINK_CONFLICT",
                "The EAN already has a non-deactivated lineage link.",
            )
        return {
            "operationId": operation_uuid,
            "account": account,
            "ean": ean,
            "status": "RESERVED",
            "reused": False,
        }

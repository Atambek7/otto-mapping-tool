"""13-digit barcode format, pool identity, and optional GTIN evidence records."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_EVIDENCE_BYTES = 10_000_000
ALLOWED_ARTIFACT_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})
_EAN13 = re.compile(r"^[0-9]{13}$")
_EVIDENCE_TYPE = re.compile(r"^[A-Z][A-Z0-9_.-]{1,63}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GtinEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class GtinEligibility:
    eligible: bool
    code: str
    message: str
    evidence_ids: tuple[str, ...] = ()


def valid_ean13(ean: str) -> bool:
    """The approved Restart rule is exactly 13 ASCII digits; no GS1 check-digit test."""
    return isinstance(ean, str) and bool(_EAN13.fullmatch(ean))


def normalize_evidence_types(values: Iterable[str]) -> frozenset[str]:
    normalized = frozenset(value.strip().upper() for value in values if value.strip())
    if any(not _EVIDENCE_TYPE.fullmatch(value) for value in normalized):
        raise GtinEvidenceError("INVALID_EVIDENCE_POLICY", "An approved GTIN evidence type is malformed.")
    return normalized


def identity_conflict(
    conn: Connection,
    ean: str,
    account: str,
    operation_id: str | None = None,
) -> bool:
    """Check retained source, target, registry, and lineage identifiers across accounts/scopes."""
    return bool(
        conn.execute(
            text(
                """
                SELECT
                    EXISTS (SELECT 1 FROM source_products WHERE aftercool_ean = :ean OR aftercool_sku = :ean)
                    OR EXISTS (
                        SELECT 1 FROM ean_links
                        WHERE (new_ean = :ean OR source_ean = :ean)
                          AND (CAST(:operation_id AS uuid) IS NULL OR operation_id IS DISTINCT FROM CAST(:operation_id AS uuid))
                    )
                    OR EXISTS (
                        SELECT 1 FROM restart_operations
                        WHERE new_ean = :ean
                          AND (CAST(:operation_id AS uuid) IS NULL OR id IS DISTINCT FROM CAST(:operation_id AS uuid))
                    )
                    OR EXISTS (
                        SELECT 1 FROM otto_library
                        WHERE old_ean = :ean OR new_ean = :ean OR old_sku = :ean OR new_sku = :ean
                    )
                    OR EXISTS (SELECT 1 FROM restart_ean_registry WHERE ean = :ean AND account <> :account)
                """
            ),
            {"ean": ean, "account": account, "operation_id": operation_id},
        ).scalar_one()
    )


def _artifact_magic_matches(media_type: str, content: bytes) -> bool:
    if media_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    return False


def _safe_filename(filename: str, media_type: str) -> str:
    fallback_extension = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}[media_type]
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    candidate = re.sub(r"[^A-Za-z0-9._-]", "_", leaf)[:100]
    if not candidate or not candidate[0].isalnum() or not _SAFE_FILENAME.fullmatch(candidate):
        candidate = f"evidence{fallback_extension}"
    return candidate


def _validate_artifact(media_type: str, content: bytes) -> str:
    if media_type not in ALLOWED_ARTIFACT_TYPES:
        raise GtinEvidenceError("UNSUPPORTED_EVIDENCE_MEDIA_TYPE", "Use a PDF, PNG, or JPEG evidence artifact.")
    if not content or len(content) > MAX_EVIDENCE_BYTES:
        raise GtinEvidenceError("EVIDENCE_SIZE_INVALID", "Evidence artifact must be between 1 byte and 10 MB.")
    if not _artifact_magic_matches(media_type, content):
        raise GtinEvidenceError("EVIDENCE_CONTENT_TYPE_MISMATCH", "Evidence bytes do not match the declared file type.")
    return hashlib.sha256(content).hexdigest()


def submit_gtin_evidence(
    conn: Connection,
    *,
    account: str,
    ean: str,
    source_product_id: str,
    evidence_type: str,
    source_name: str,
    artifact_filename: str,
    artifact_media_type: str,
    artifact_bytes: bytes,
    claim_basis: str,
    validity_kind: str,
    expires_at: datetime | None,
    actor: str,
) -> dict[str, Any]:
    if account not in {"JV", "XL"}:
        raise GtinEvidenceError("INVALID_ACCOUNT", "Account must be JV or XL.")
    if not valid_ean13(ean):
        raise GtinEvidenceError("INVALID_EAN", "A barcode must contain exactly 13 ASCII digits.")
    try:
        source_id = str(UUID(source_product_id))
    except (TypeError, ValueError, AttributeError):
        raise GtinEvidenceError("INVALID_SOURCE_PRODUCT", "Source product ID must be a UUID.") from None
    evidence_type = evidence_type.strip().upper()
    source_name = source_name.strip()
    claim_basis = claim_basis.strip()
    actor = actor.strip()[:200]
    if not _EVIDENCE_TYPE.fullmatch(evidence_type):
        raise GtinEvidenceError("INVALID_EVIDENCE_TYPE", "Evidence type must be an uppercase stable identifier.")
    if not 1 <= len(source_name) <= 200:
        raise GtinEvidenceError("INVALID_EVIDENCE_SOURCE", "Evidence source name must contain 1–200 characters.")
    if not 20 <= len(claim_basis) <= 2000:
        raise GtinEvidenceError("INVALID_EVIDENCE_BASIS", "Describe the claimed GTIN basis in 20–2000 characters.")
    if not actor:
        raise GtinEvidenceError("ACTOR_REQUIRED", "An authenticated operator is required.", 403)
    if validity_kind not in {"EXPIRING", "PERPETUAL"}:
        raise GtinEvidenceError("INVALID_VALIDITY_KIND", "Validity must be EXPIRING or PERPETUAL.")
    if (validity_kind == "EXPIRING") != (expires_at is not None):
        raise GtinEvidenceError("INVALID_EXPIRY", "Expiring evidence requires an expiry time; perpetual evidence must not have one.")
    if expires_at is not None and expires_at.tzinfo is None:
        raise GtinEvidenceError("INVALID_EXPIRY", "Evidence expiry must include a timezone.")
    artifact_hash = _validate_artifact(artifact_media_type, artifact_bytes)
    safe_filename = _safe_filename(artifact_filename, artifact_media_type)

    pool = conn.execute(
        text(
            "SELECT status, eligibility_status, owner_reference, reservation_operation_id "
            "FROM ean_pool WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION' FOR UPDATE"
        ),
        {"ean": ean, "account": account},
    ).mappings().first()
    if pool is None:
        raise GtinEvidenceError("GTIN_POOL_ENTRY_NOT_FOUND", "The production EAN pool entry was not found.", 404)
    if pool["eligibility_status"] == "INELIGIBLE":
        raise GtinEvidenceError("GTIN_POOL_INELIGIBLE", "An identity-conflicted GTIN cannot be approved through evidence upload.", 409)
    if pool["status"] not in {"QUARANTINED", "AVAILABLE", "RESERVED"}:
        raise GtinEvidenceError("GTIN_POOL_STATE_INVALID", "Evidence cannot change a used GTIN assignment.", 409)
    if pool["owner_reference"] and str(pool["owner_reference"]) != source_id:
        raise GtinEvidenceError("GTIN_PRODUCT_BINDING_CONFLICT", "This GTIN is already bound to a different source product.", 409)
    if pool["reservation_operation_id"] is not None:
        reservation = conn.execute(
            text(
                "SELECT source_product_id FROM restart_operations "
                "WHERE id = :operation_id AND account = :account AND data_scope = 'PRODUCTION'"
            ),
            {"operation_id": str(pool["reservation_operation_id"]), "account": account},
        ).scalar_one_or_none()
        if str(reservation) != source_id:
            raise GtinEvidenceError("GTIN_PRODUCT_BINDING_CONFLICT", "The reserved GTIN belongs to another source product.", 409)

    source = conn.execute(
        text(
            "SELECT id FROM source_products WHERE id = :source_id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"source_id": source_id, "account": account},
    ).scalar_one_or_none()
    if source is None:
        raise GtinEvidenceError("SOURCE_PRODUCT_NOT_FOUND", "Evidence must bind to a production source product in the same account.", 404)
    if identity_conflict(conn, ean, account, str(pool["reservation_operation_id"]) if pool["reservation_operation_id"] else None):
        raise GtinEvidenceError("GTIN_IDENTITY_CONFLICT", "This GTIN conflicts with a retained product or lineage identifier.", 409)

    evidence_id = str(uuid4())
    conn.execute(
        text(
            """
            INSERT INTO gtin_evidence (
                id, account, data_scope, ean, source_product_id, evidence_type, source_name,
                artifact_filename, artifact_media_type, artifact_sha256, artifact_bytes,
                claim_basis, validity_kind, expires_at, submitted_by
            ) VALUES (
                :id, :account, 'PRODUCTION', :ean, :source_id, :evidence_type, :source_name,
                :filename, :media_type, :artifact_sha256, :artifact_bytes,
                :claim_basis, :validity_kind, :expires_at, :actor
            )
            """
        ),
        {
            "id": evidence_id,
            "account": account,
            "ean": ean,
            "source_id": source_id,
            "evidence_type": evidence_type,
            "source_name": source_name,
            "filename": safe_filename,
            "media_type": artifact_media_type,
            "artifact_sha256": artifact_hash,
            "artifact_bytes": artifact_bytes,
            "claim_basis": claim_basis,
            "validity_kind": validity_kind,
            "expires_at": expires_at,
            "actor": actor,
        },
    )
    return {
        "id": evidence_id,
        "account": account,
        "ean": ean,
        "sourceProductId": source_id,
        "evidenceType": evidence_type,
        "sourceName": source_name,
        "artifactFilename": safe_filename,
        "artifactMediaType": artifact_media_type,
        "artifactSha256": artifact_hash,
        "validityKind": validity_kind,
        "expiresAt": expires_at.isoformat() if expires_at else None,
        "reviewStatus": "PENDING",
        "submittedBy": actor,
    }


def list_gtin_evidence(
    conn: Connection,
    *,
    account: str,
    ean: str | None = None,
    source_product_id: str | None = None,
    review_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if account not in {"JV", "XL"} or not 1 <= limit <= 100 or offset < 0:
        raise GtinEvidenceError("INVALID_QUERY", "Invalid account or pagination values.")
    filters = ["account = :account", "data_scope = 'PRODUCTION'"]
    params: dict[str, Any] = {"account": account, "limit": limit, "offset": offset}
    if ean:
        filters.append("ean = :ean")
        params["ean"] = ean
    if source_product_id:
        try:
            params["source_product_id"] = str(UUID(source_product_id))
        except (TypeError, ValueError, AttributeError):
            raise GtinEvidenceError("INVALID_SOURCE_PRODUCT", "Source product ID must be a UUID.") from None
        filters.append("source_product_id = :source_product_id")
    if review_status:
        if review_status not in {"PENDING", "APPROVED", "REJECTED", "QUARANTINED"}:
            raise GtinEvidenceError("INVALID_REVIEW_STATUS", "Unsupported evidence review status.")
        filters.append("review_status = :review_status")
        params["review_status"] = review_status
    where = " AND ".join(filters)
    total = int(conn.execute(text(f"SELECT COUNT(*) FROM gtin_evidence WHERE {where}"), params).scalar_one())
    rows = conn.execute(
        text(
            "SELECT id, account, ean, source_product_id, evidence_type, source_name, artifact_filename, "
            "artifact_media_type, artifact_sha256, claim_basis, validity_kind, expires_at, review_status, "
            "submitted_by, submitted_at, reviewed_by, reviewed_at, review_basis, version "
            f"FROM gtin_evidence WHERE {where} ORDER BY submitted_at DESC, id DESC LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings().all()
    return [_evidence_dict(row) for row in rows], total


def get_gtin_evidence_artifact(conn: Connection, *, account: str, evidence_id: str) -> dict[str, Any]:
    try:
        evidence_uuid = str(UUID(evidence_id))
    except (TypeError, ValueError, AttributeError):
        raise GtinEvidenceError("INVALID_EVIDENCE_ID", "Evidence ID must be a UUID.") from None
    row = conn.execute(
        text(
            "SELECT id, artifact_filename, artifact_media_type, artifact_sha256, artifact_bytes "
            "FROM gtin_evidence WHERE id = :id AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"id": evidence_uuid, "account": account},
    ).mappings().first()
    if row is None:
        raise GtinEvidenceError("GTIN_EVIDENCE_NOT_FOUND", "Evidence artifact was not found in this account.", 404)
    content = bytes(row["artifact_bytes"])
    if hashlib.sha256(content).hexdigest() != row["artifact_sha256"].strip():
        raise GtinEvidenceError("GTIN_EVIDENCE_INTEGRITY_FAILURE", "Stored evidence artifact failed its integrity check.", 409)
    return {
        "id": str(row["id"]),
        "filename": row["artifact_filename"],
        "mediaType": row["artifact_media_type"],
        "sha256": row["artifact_sha256"].strip(),
        "content": content,
    }


def review_gtin_evidence(
    conn: Connection,
    *,
    account: str,
    evidence_id: str,
    decision: str,
    review_basis: str,
    actor: str,
    approved_evidence_types: Iterable[str],
) -> dict[str, Any]:
    try:
        evidence_uuid = str(UUID(evidence_id))
    except (TypeError, ValueError, AttributeError):
        raise GtinEvidenceError("INVALID_EVIDENCE_ID", "Evidence ID must be a UUID.") from None
    if decision not in {"APPROVE", "REJECT_EVIDENCE", "QUARANTINE_GTIN"}:
        raise GtinEvidenceError("INVALID_REVIEW_DECISION", "Unsupported GTIN evidence review decision.")
    basis, actor = review_basis.strip(), actor.strip()[:200]
    if not 20 <= len(basis) <= 2000:
        raise GtinEvidenceError("INVALID_REVIEW_BASIS", "Review basis must contain 20–2000 characters.")
    if not actor:
        raise GtinEvidenceError("ACTOR_REQUIRED", "An authenticated operator is required.", 403)
    # Evidence remains optional audit material. The user-approved production
    # rule does not require an evidence-type allowlist to use a 13-digit code.

    row = conn.execute(
        text(
            """
            SELECT e.*, p.status AS pool_status, p.eligibility_status AS pool_eligibility,
                   p.owner_reference, p.reservation_operation_id
            FROM gtin_evidence e
            JOIN ean_pool p ON p.ean = e.ean AND p.account = e.account AND p.data_scope = e.data_scope
            WHERE e.id = :id AND e.account = :account AND e.data_scope = 'PRODUCTION'
            FOR UPDATE OF e, p
            """
        ),
        {"id": evidence_uuid, "account": account},
    ).mappings().first()
    if row is None:
        raise GtinEvidenceError("GTIN_EVIDENCE_NOT_FOUND", "Evidence was not found in this account.", 404)

    target_status = {
        "APPROVE": "APPROVED",
        "REJECT_EVIDENCE": "REJECTED",
        "QUARANTINE_GTIN": "QUARANTINED",
    }[decision]
    if row["review_status"] != "PENDING":
        if row["review_status"] == target_status and row["reviewed_by"] == actor and row["review_basis"] == basis:
            return {
                "id": evidence_uuid,
                "account": account,
                "ean": row["ean"],
                "sourceProductId": str(row["source_product_id"]),
                "reviewStatus": row["review_status"],
                "code": "REVIEW_ALREADY_APPLIED",
                "reused": True,
            }
        raise GtinEvidenceError("GTIN_REVIEW_ALREADY_FINAL", "Evidence has already received a final review decision.", 409)

    pool_id = str(row["reservation_operation_id"]) if row["reservation_operation_id"] else None
    if decision == "APPROVE":
        if row["validity_kind"] == "EXPIRING" and row["expires_at"] <= datetime.now(timezone.utc):
            return _blocked_review(row, "GTIN_EVIDENCE_EXPIRED", "Expired evidence cannot establish GTIN eligibility.")
        content = bytes(row["artifact_bytes"])
        if hashlib.sha256(content).hexdigest() != row["artifact_sha256"].strip():
            return _blocked_review(row, "GTIN_EVIDENCE_INTEGRITY_FAILURE", "Stored evidence failed its integrity check.")
        if row["pool_eligibility"] == "INELIGIBLE":
            return _blocked_review(row, "GTIN_POOL_INELIGIBLE", "A conflicted GTIN requires an independent resolution workflow.")
        if row["pool_status"] == "USED":
            return _blocked_review(row, "GTIN_ALREADY_USED", "Used GTIN assignments cannot be changed by this review.")
        if row["pool_status"] not in {"QUARANTINED", "AVAILABLE", "RESERVED"}:
            return _blocked_review(row, "GTIN_POOL_STATE_INVALID", "GTIN is not in a reviewable pool state.")
        if row["owner_reference"] and str(row["owner_reference"]) != str(row["source_product_id"]):
            return _quarantine_review_conflict(conn, row, actor, basis, evidence_uuid, "GTIN_PRODUCT_BINDING_CONFLICT")
        other_binding = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM gtin_evidence WHERE ean = :ean AND account = :account "
                "AND data_scope = 'PRODUCTION' AND review_status = 'APPROVED' AND source_product_id <> :source_id)"
            ),
            {"ean": row["ean"], "account": account, "source_id": str(row["source_product_id"])},
        ).scalar_one()
        if other_binding:
            return _quarantine_review_conflict(conn, row, actor, basis, evidence_uuid, "GTIN_PRODUCT_BINDING_CONFLICT")
        if identity_conflict(conn, row["ean"], account, pool_id):
            return _quarantine_review_conflict(conn, row, actor, basis, evidence_uuid, "GTIN_IDENTITY_CONFLICT")

        now = datetime.now(timezone.utc)
        conn.execute(
            text(
                "UPDATE gtin_evidence SET review_status = 'APPROVED', reviewed_by = :actor, "
                "reviewed_at = :now, review_basis = :basis, version = version + 1 WHERE id = :id"
            ),
            {"actor": actor, "now": now, "basis": basis, "id": evidence_uuid},
        )
        pool_status = "RESERVED" if pool_id else "AVAILABLE"
        conn.execute(
            text(
                """
                UPDATE ean_pool
                SET status = :status, eligibility_status = 'ELIGIBLE', owner_reference = :source_id,
                    verified_by = :actor, verified_at = :now, quarantined_at = NULL,
                    quarantine_reason = NULL, version = version + 1, updated_at = :now
                WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'
                """
            ),
            {
                "status": pool_status,
                "source_id": str(row["source_product_id"]),
                "actor": actor,
                "now": now,
                "ean": row["ean"],
                "account": account,
            },
        )
        return {
            "id": evidence_uuid,
            "account": account,
            "ean": row["ean"],
            "sourceProductId": str(row["source_product_id"]),
            "reviewStatus": "APPROVED",
            "poolStatus": pool_status,
            "eligibilityStatus": "ELIGIBLE",
            "code": "GTIN_ELIGIBLE",
            "reused": False,
        }

    if decision == "QUARANTINE_GTIN":
        if pool_id or row["pool_status"] in {"RESERVED", "USED"}:
            return _blocked_review(row, "GTIN_RESERVATION_EXISTS", "A reserved or used GTIN cannot be quarantined by this endpoint.")
        conn.execute(
            text(
                "UPDATE gtin_evidence SET review_status = 'QUARANTINED', reviewed_by = :actor, "
                "reviewed_at = CURRENT_TIMESTAMP, review_basis = :basis, version = version + 1 WHERE id = :id"
            ),
            {"actor": actor, "basis": basis, "id": evidence_uuid},
        )
        conn.execute(
            text(
                "UPDATE ean_pool SET status = 'QUARANTINED', eligibility_status = 'INELIGIBLE', "
                "quarantined_at = CURRENT_TIMESTAMP, quarantine_reason = 'MANUAL_GTIN_QUARANTINE', "
                "version = version + 1, updated_at = CURRENT_TIMESTAMP "
                "WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'"
            ),
            {"ean": row["ean"], "account": account},
        )
        return {
            "id": evidence_uuid,
            "account": account,
            "ean": row["ean"],
            "sourceProductId": str(row["source_product_id"]),
            "reviewStatus": "QUARANTINED",
            "poolStatus": "QUARANTINED",
            "eligibilityStatus": "INELIGIBLE",
            "code": "GTIN_QUARANTINED",
            "reused": False,
        }

    conn.execute(
        text(
            "UPDATE gtin_evidence SET review_status = 'REJECTED', reviewed_by = :actor, "
            "reviewed_at = CURRENT_TIMESTAMP, review_basis = :basis, version = version + 1 WHERE id = :id"
        ),
        {"actor": actor, "basis": basis, "id": evidence_uuid},
    )
    return {
        "id": evidence_uuid,
        "account": account,
        "ean": row["ean"],
        "sourceProductId": str(row["source_product_id"]),
        "reviewStatus": "REJECTED",
        "poolStatus": row["pool_status"],
        "eligibilityStatus": row["pool_eligibility"],
        "code": "GTIN_EVIDENCE_REJECTED",
        "reused": False,
    }


def validate_gtin_eligibility(
    conn: Connection,
    *,
    account: str,
    ean: str,
    source_product_id: str,
    approved_evidence_types: Iterable[str] = (),
    operation_id: str | None = None,
    now: datetime | None = None,
) -> GtinEligibility:
    if account not in {"JV", "XL"}:
        return GtinEligibility(False, "INVALID_ACCOUNT", "Account must be JV or XL.")
    if not valid_ean13(ean):
        return GtinEligibility(False, "INVALID_EAN", "A barcode must contain exactly 13 ASCII digits.")
    try:
        source_id = str(UUID(source_product_id))
    except (TypeError, ValueError, AttributeError):
        return GtinEligibility(False, "INVALID_SOURCE_PRODUCT", "Source product ID must be a UUID.")
    pool = conn.execute(
        text(
            "SELECT status, eligibility_status, owner_reference, verified_by, verified_at, reservation_operation_id, provenance_source "
            "FROM ean_pool WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {"ean": ean, "account": account},
    ).mappings().first()
    if pool is None:
        return GtinEligibility(False, "GTIN_POOL_ENTRY_NOT_FOUND", "GTIN is not in the production pool.")
    if pool["status"] not in {"AVAILABLE", "RESERVED"} or pool["eligibility_status"] != "ELIGIBLE":
        return GtinEligibility(False, "GTIN_NOT_ELIGIBLE", "GTIN is not currently eligible for assignment.")
    if pool["owner_reference"] is not None and str(pool["owner_reference"] or "") != source_id:
        return GtinEligibility(False, "GTIN_PRODUCT_BINDING_MISMATCH", "GTIN is not bound to this source product.")
    if pool["status"] == "RESERVED" and (
        operation_id is None or str(pool["reservation_operation_id"]) != str(operation_id)
    ):
        return GtinEligibility(False, "GTIN_RESERVED_FOR_OTHER_OPERATION", "GTIN is reserved for a different operation.")
    if pool["status"] == "AVAILABLE" and pool["reservation_operation_id"] is not None:
        return GtinEligibility(False, "GTIN_RESERVATION_INCONSISTENT", "Available GTIN unexpectedly has a reservation owner.")

    source_exists = conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM source_products WHERE id = :source_id "
            "AND account = :account AND data_scope = 'PRODUCTION')"
        ),
        {"source_id": source_id, "account": account},
    ).scalar_one()
    if not source_exists:
        return GtinEligibility(False, "SOURCE_PRODUCT_NOT_FOUND", "Source product is not in this production account.")

    if identity_conflict(conn, ean, account, operation_id):
        return GtinEligibility(False, "GTIN_IDENTITY_CONFLICT", "GTIN conflicts with a retained product or lineage identifier.")

    # Keep uniqueness conflicts represented by an already approved pool owner;
    # the evidence itself is not a product-readiness requirement.
    other_binding = conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM gtin_evidence WHERE ean = :ean AND account = :account "
            "AND data_scope = 'PRODUCTION' AND review_status = 'APPROVED' AND source_product_id <> :source_id)"
        ),
        {"ean": ean, "account": account, "source_id": source_id},
    ).scalar_one()
    if other_binding:
        return GtinEligibility(False, "GTIN_PRODUCT_BINDING_CONFLICT", "EAN is already linked to another product identity.")

    # User-approved v1 policy: no GS1 eligibility or check-digit review is
    # required. Production-pool membership, single-operation ownership, source
    # account, and retained-identifier conflict checks above remain mandatory.
    return GtinEligibility(True, "EAN13_FORMAT_ACCEPTED", "13-digit barcode is in the production pool and has no identity conflict.", ())


def _blocked_review(row: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "account": row["account"],
        "ean": row["ean"],
        "sourceProductId": str(row["source_product_id"]),
        "reviewStatus": "PENDING",
        "poolStatus": row["pool_status"],
        "eligibilityStatus": row["pool_eligibility"],
        "code": code,
        "message": message,
        "reused": False,
    }


def _quarantine_review_conflict(
    conn: Connection,
    row: Any,
    actor: str,
    basis: str,
    evidence_id: str,
    code: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            "UPDATE gtin_evidence SET review_status = 'QUARANTINED', reviewed_by = :actor, "
            "reviewed_at = :now, review_basis = :basis, version = version + 1 WHERE id = :id"
        ),
        {"actor": actor, "now": now, "basis": basis, "id": evidence_id},
    )
    has_reservation = row["reservation_operation_id"] is not None
    conn.execute(
        text(
            "UPDATE ean_pool SET status = 'QUARANTINED', "
            "eligibility_status = :eligibility_status, quarantined_at = :now, quarantine_reason = :reason, "
            "version = version + 1, updated_at = :now "
            "WHERE ean = :ean AND account = :account AND data_scope = 'PRODUCTION'"
        ),
        {
            "eligibility_status": "ELIGIBLE" if has_reservation else "INELIGIBLE",
            "now": now,
            "reason": code,
            "ean": row["ean"],
            "account": row["account"],
        },
    )
    return {
        "id": evidence_id,
        "account": row["account"],
        "ean": row["ean"],
        "sourceProductId": str(row["source_product_id"]),
        "reviewStatus": "QUARANTINED",
        "poolStatus": "QUARANTINED",
        "eligibilityStatus": "ELIGIBLE" if has_reservation else "INELIGIBLE",
        "code": code,
        "reused": False,
    }


def _evidence_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "account": row["account"],
        "ean": row["ean"],
        "sourceProductId": str(row["source_product_id"]),
        "evidenceType": row["evidence_type"],
        "sourceName": row["source_name"],
        "artifactFilename": row["artifact_filename"],
        "artifactMediaType": row["artifact_media_type"],
        "artifactSha256": row["artifact_sha256"].strip(),
        "claimBasis": row["claim_basis"],
        "validityKind": row["validity_kind"],
        "expiresAt": row["expires_at"].isoformat() if row["expires_at"] else None,
        "reviewStatus": row["review_status"],
        "submittedBy": row["submitted_by"],
        "submittedAt": row["submitted_at"].isoformat(),
        "reviewedBy": row["reviewed_by"],
        "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "reviewBasis": row["review_basis"],
        "version": row["version"],
    }

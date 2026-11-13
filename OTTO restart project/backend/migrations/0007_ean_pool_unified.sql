-- Unified pool ownership, eligibility, reservation, and migration provenance.
-- Existing rows remain in their current scope and are never promoted here.

ALTER TABLE ean_pool
    ADD COLUMN provenance_source TEXT NOT NULL DEFAULT 'LEGACY_MIGRATION'
        CHECK (provenance_source IN ('LEGACY_MIGRATION', 'LEGACY_REGISTRY_BACKFILL', 'MANUAL_IMPORT', 'OPERATION_RESERVATION')),
    ADD COLUMN owner_reference TEXT,
    ADD COLUMN eligibility_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (eligibility_status IN ('UNVERIFIED', 'ELIGIBLE', 'INELIGIBLE')),
    ADD COLUMN imported_by TEXT,
    ADD COLUMN imported_at TIMESTAMPTZ,
    ADD COLUMN verified_by TEXT,
    ADD COLUMN verified_at TIMESTAMPTZ,
    ADD COLUMN reservation_operation_id UUID,
    ADD COLUMN version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE ean_pool
    ADD CONSTRAINT ck_ean_pool_verified_has_actor_time
        CHECK (eligibility_status <> 'ELIGIBLE' OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)) NOT VALID,
    ADD CONSTRAINT ck_ean_pool_production_reservation_owner
        CHECK (data_scope <> 'PRODUCTION' OR (
            (status NOT IN ('RESERVED', 'USED') OR
                (reservation_operation_id IS NOT NULL AND eligibility_status = 'ELIGIBLE'))
            AND (status <> 'AVAILABLE' OR eligibility_status = 'ELIGIBLE')
            AND (reservation_operation_id IS NULL OR
                (eligibility_status = 'ELIGIBLE' AND status IN ('RESERVED', 'USED', 'QUARANTINED')))
        )) NOT VALID,
    ADD CONSTRAINT fk_ean_pool_reservation_operation
        FOREIGN KEY (reservation_operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope)
        ON DELETE RESTRICT NOT VALID;

CREATE UNIQUE INDEX uq_ean_pool_production_operation
    ON ean_pool (reservation_operation_id)
    WHERE data_scope = 'PRODUCTION' AND reservation_operation_id IS NOT NULL;

CREATE INDEX ix_ean_pool_production_eligible
    ON ean_pool (account, created_at, ean)
    WHERE data_scope = 'PRODUCTION' AND status = 'AVAILABLE' AND eligibility_status = 'ELIGIBLE';

CREATE UNIQUE INDEX uq_restart_operations_production_new_ean
    ON restart_operations (new_ean)
    WHERE data_scope = 'PRODUCTION' AND new_ean IS NOT NULL;

-- Reconcile registry rows inserted after migration 0002 without overwriting
-- an existing global EAN owner in ean_pool. Conflicting rows remain in the
-- legacy registry and can be inspected through the read-only conflict report.
INSERT INTO ean_pool (
    ean, account, status, batch_id, reserved_at, used_at, quarantined_at,
    quarantine_reason, created_at, data_scope, provenance_source,
    eligibility_status, imported_at, updated_at
)
SELECT
    registry.ean,
    registry.account,
    CASE
        WHEN registry.data_scope = 'PRODUCTION' THEN 'QUARANTINED'
        WHEN registry.status = 'SUBMITTED' THEN 'QUARANTINED'
        ELSE registry.status
    END,
    registry.batch_id,
    NULL,
    CASE WHEN registry.status = 'USED' THEN registry.imported_at ELSE NULL END,
    CASE
        WHEN registry.data_scope = 'PRODUCTION' OR registry.status IN ('SUBMITTED', 'QUARANTINED')
        THEN registry.imported_at ELSE NULL
    END,
    CASE
        WHEN registry.data_scope = 'PRODUCTION' THEN 'GTIN_EVIDENCE_REQUIRED'
        WHEN registry.status = 'SUBMITTED' THEN 'LEGACY_SUBMITTED_UNVERIFIED'
        WHEN registry.status = 'QUARANTINED' THEN 'LEGACY_QUARANTINED'
        ELSE NULL
    END,
    registry.imported_at,
    registry.data_scope,
    'LEGACY_REGISTRY_BACKFILL',
    'UNVERIFIED',
    registry.imported_at,
    CURRENT_TIMESTAMP
FROM restart_ean_registry AS registry
ON CONFLICT (ean) DO NOTHING;

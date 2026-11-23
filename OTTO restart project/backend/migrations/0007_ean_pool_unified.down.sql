DROP INDEX IF EXISTS uq_restart_operations_production_new_ean;
DROP INDEX IF EXISTS ix_ean_pool_production_eligible;
DROP INDEX IF EXISTS uq_ean_pool_production_operation;

ALTER TABLE ean_pool
    DROP CONSTRAINT IF EXISTS fk_ean_pool_reservation_operation,
    DROP CONSTRAINT IF EXISTS ck_ean_pool_production_reservation_owner,
    DROP CONSTRAINT IF EXISTS ck_ean_pool_verified_has_actor_time,
    DROP COLUMN IF EXISTS updated_at,
    DROP COLUMN IF EXISTS version,
    DROP COLUMN IF EXISTS reservation_operation_id,
    DROP COLUMN IF EXISTS verified_at,
    DROP COLUMN IF EXISTS verified_by,
    DROP COLUMN IF EXISTS imported_at,
    DROP COLUMN IF EXISTS imported_by,
    DROP COLUMN IF EXISTS eligibility_status,
    DROP COLUMN IF EXISTS owner_reference,
    DROP COLUMN IF EXISTS provenance_source;

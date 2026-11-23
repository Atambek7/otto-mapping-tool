DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM restart_batches WHERE data_scope = 'PRODUCTION') THEN
        RAISE EXCEPTION 'Refusing to remove production Drip Feed schedules and their operation snapshots';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_restart_item_snapshot_immutable ON restart_items;
DROP TRIGGER IF EXISTS trg_restart_batch_schedule_immutable ON restart_batches;
DROP FUNCTION IF EXISTS guard_restart_item_snapshot_update();
DROP FUNCTION IF EXISTS guard_restart_batch_schedule_update();

DROP INDEX IF EXISTS ix_restart_items_production_schedule;
DROP INDEX IF EXISTS ix_restart_batches_production_schedule;
DROP INDEX IF EXISTS uq_restart_items_active_operation_revision;

ALTER TABLE restart_items
    DROP CONSTRAINT IF EXISTS fk_restart_items_drip_qc,
    DROP CONSTRAINT IF EXISTS fk_restart_items_drip_revision,
    DROP CONSTRAINT IF EXISTS fk_restart_items_drip_operation,
    DROP CONSTRAINT IF EXISTS ck_restart_items_production_snapshot,
    DROP CONSTRAINT IF EXISTS ck_restart_items_version,
    DROP CONSTRAINT IF EXISTS ck_restart_items_release_attempts,
    DROP CONSTRAINT IF EXISTS ck_restart_items_drip_status,
    DROP COLUMN IF EXISTS version,
    DROP COLUMN IF EXISTS ready_at,
    DROP COLUMN IF EXISTS last_error_code,
    DROP COLUMN IF EXISTS release_attempts,
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS scheduled_at,
    DROP COLUMN IF EXISTS payload_hash,
    DROP COLUMN IF EXISTS qc_result_id,
    DROP COLUMN IF EXISTS revision_id,
    DROP COLUMN IF EXISTS operation_id;

ALTER TABLE restart_batches
    DROP CONSTRAINT IF EXISTS ck_restart_batches_production_schedule,
    DROP CONSTRAINT IF EXISTS ck_restart_batches_version,
    DROP CONSTRAINT IF EXISTS ck_restart_batches_schedule_generation,
    DROP CONSTRAINT IF EXISTS ck_restart_batches_selected_operations,
    DROP CONSTRAINT IF EXISTS ck_restart_batches_status,
    DROP COLUMN IF EXISTS cancelled_at,
    DROP COLUMN IF EXISTS paused_at,
    DROP COLUMN IF EXISTS started_at,
    DROP COLUMN IF EXISTS created_by,
    DROP COLUMN IF EXISTS schedule_generation,
    DROP COLUMN IF EXISTS selected_operation_ids_json,
    DROP COLUMN IF EXISTS total_days,
    DROP COLUMN IF EXISTS scheduled_start_at,
    DROP COLUMN IF EXISTS timezone_name,
    DROP COLUMN IF EXISTS daily_time,
    DROP COLUMN IF EXISTS local_start_date,
    DROP COLUMN IF EXISTS factory_id,
    DROP COLUMN IF EXISTS version,
    DROP COLUMN IF EXISTS updated_at,
    ADD CONSTRAINT restart_batches_status_check
        CHECK (status IN ('IMPORTED', 'PREVIEWED', 'NEEDS_REVIEW', 'READY', 'CANCELLED'));

ALTER TABLE qc_results DROP CONSTRAINT IF EXISTS uq_qc_results_drip_snapshot;

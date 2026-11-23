DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM restart_items WHERE status='PREPARING' OR (data_scope='PRODUCTION' AND (revision_id IS NULL OR qc_result_id IS NULL))) THEN
 RAISE EXCEPTION 'Resolve or archive preparation items before downgrade'; END IF;
END $$;
ALTER TABLE restart_items DROP CONSTRAINT ck_restart_items_drip_status;
ALTER TABLE restart_items ADD CONSTRAINT ck_restart_items_drip_status CHECK (status IN ('LEGACY_UNVERIFIED','SCHEDULED','READY_TO_SUBMIT','NEEDS_REVIEW','SUBMITTING','OTTO_PROCESSING','AWAITING_ONLINE','PUBLISHED','FAILED','CANCELLED'));
ALTER TABLE restart_items DROP CONSTRAINT ck_restart_items_production_snapshot;
ALTER TABLE restart_items ADD CONSTRAINT ck_restart_items_production_snapshot CHECK (data_scope <> 'PRODUCTION' OR (operation_id IS NOT NULL AND revision_id IS NOT NULL AND qc_result_id IS NOT NULL AND payload_hash ~ '^[0-9a-f]{64}$' AND scheduled_at IS NOT NULL AND status <> 'LEGACY_UNVERIFIED')) NOT VALID;
ALTER TABLE restart_items DROP COLUMN auto_repair_count, DROP COLUMN excluded_attribute_names_json, DROP COLUMN preparation_generation, DROP COLUMN preparation_errors_json;
CREATE OR REPLACE FUNCTION guard_restart_item_snapshot_update() RETURNS trigger AS $$
BEGIN
    IF OLD.data_scope = 'PRODUCTION'
       AND (
           OLD.batch_id IS DISTINCT FROM NEW.batch_id
           OR OLD.account IS DISTINCT FROM NEW.account
           OR OLD.data_scope IS DISTINCT FROM NEW.data_scope
           OR OLD.source_ean IS DISTINCT FROM NEW.source_ean
           OR OLD.row_number IS DISTINCT FROM NEW.row_number
           OR OLD.operation_id IS DISTINCT FROM NEW.operation_id
           OR OLD.revision_id IS DISTINCT FROM NEW.revision_id
           OR OLD.qc_result_id IS DISTINCT FROM NEW.qc_result_id
           OR OLD.payload_hash IS DISTINCT FROM NEW.payload_hash
           OR OLD.scheduled_at IS DISTINCT FROM NEW.scheduled_at
       ) THEN
        RAISE EXCEPTION 'Drip Feed item operation and QC snapshot is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


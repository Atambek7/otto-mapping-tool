-- Preparation is durable and visible before a checked submission snapshot exists.
ALTER TABLE restart_items ADD COLUMN auto_repair_count INTEGER NOT NULL DEFAULT 0 CHECK(auto_repair_count BETWEEN 0 AND 5),
 ADD COLUMN excluded_attribute_names_json JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(excluded_attribute_names_json)='array'),
 ADD COLUMN preparation_generation INTEGER NOT NULL DEFAULT 1 CHECK (preparation_generation > 0),
 ADD COLUMN preparation_errors_json JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(preparation_errors_json)='array');
ALTER TABLE restart_items DROP CONSTRAINT ck_restart_items_drip_status;
ALTER TABLE restart_items ADD CONSTRAINT ck_restart_items_drip_status CHECK (status IN
 ('LEGACY_UNVERIFIED','PREPARING','SCHEDULED','READY_TO_SUBMIT','NEEDS_REVIEW','SUBMITTING','OTTO_PROCESSING','AWAITING_ONLINE','PUBLISHED','FAILED','CANCELLED'));
ALTER TABLE restart_items DROP CONSTRAINT ck_restart_items_production_snapshot;
ALTER TABLE restart_items ADD CONSTRAINT ck_restart_items_production_snapshot CHECK (data_scope <> 'PRODUCTION' OR
 (operation_id IS NOT NULL AND scheduled_at IS NOT NULL AND (status IN ('PREPARING','NEEDS_REVIEW','CANCELLED') OR
 (revision_id IS NOT NULL AND qc_result_id IS NOT NULL AND payload_hash ~ '^[0-9a-f]{64}$' AND status <> 'LEGACY_UNVERIFIED')))) NOT VALID;
CREATE OR REPLACE FUNCTION guard_restart_item_snapshot_update() RETURNS trigger AS $$
BEGIN
 IF OLD.data_scope='PRODUCTION' AND OLD.status IN ('PREPARING','NEEDS_REVIEW')
 AND OLD.batch_id=NEW.batch_id AND OLD.account=NEW.account AND OLD.data_scope=NEW.data_scope
 AND OLD.source_ean=NEW.source_ean AND OLD.row_number=NEW.row_number AND OLD.operation_id=NEW.operation_id
 AND EXISTS (SELECT 1 FROM restart_operations o WHERE o.id=OLD.operation_id AND o.account=OLD.account
 AND o.data_scope='PRODUCTION' AND o.submission_state='NEVER_SUBMITTED') THEN
   RETURN NEW;
 END IF;
 IF OLD.data_scope='PRODUCTION' AND (
 OLD.batch_id IS DISTINCT FROM NEW.batch_id OR OLD.account IS DISTINCT FROM NEW.account OR OLD.data_scope IS DISTINCT FROM NEW.data_scope
 OR OLD.source_ean IS DISTINCT FROM NEW.source_ean OR OLD.row_number IS DISTINCT FROM NEW.row_number
 OR OLD.operation_id IS DISTINCT FROM NEW.operation_id OR OLD.revision_id IS DISTINCT FROM NEW.revision_id
 OR OLD.qc_result_id IS DISTINCT FROM NEW.qc_result_id OR OLD.payload_hash IS DISTINCT FROM NEW.payload_hash
 OR OLD.scheduled_at IS DISTINCT FROM NEW.scheduled_at) THEN
 RAISE EXCEPTION 'Drip Feed item operation and QC snapshot is immutable';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;

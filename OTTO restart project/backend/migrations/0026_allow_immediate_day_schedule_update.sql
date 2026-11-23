-- Allow immediate-day acceleration (trigger-now) to move scheduled_at earlier on SCHEDULED
-- items before submission while keeping the operation, revision, QC, and payload snapshot immutable.
CREATE OR REPLACE FUNCTION guard_restart_item_snapshot_update() RETURNS trigger AS $$
BEGIN
 IF OLD.data_scope='PRODUCTION' AND OLD.status IN ('PREPARING','NEEDS_REVIEW')
 AND OLD.batch_id=NEW.batch_id AND OLD.account=NEW.account AND OLD.data_scope=NEW.data_scope
 AND OLD.source_ean=NEW.source_ean AND OLD.row_number=NEW.row_number AND OLD.operation_id=NEW.operation_id
 AND EXISTS (SELECT 1 FROM restart_operations o WHERE o.id=OLD.operation_id AND o.account=OLD.account
 AND o.data_scope='PRODUCTION' AND o.submission_state='NEVER_SUBMITTED') THEN
   RETURN NEW;
 END IF;
 IF OLD.data_scope='PRODUCTION' AND OLD.status='SCHEDULED' AND NEW.status='SCHEDULED'
 AND OLD.batch_id=NEW.batch_id AND OLD.account=NEW.account AND OLD.data_scope=NEW.data_scope
 AND OLD.source_ean=NEW.source_ean AND OLD.row_number=NEW.row_number AND OLD.operation_id=NEW.operation_id
 AND OLD.revision_id IS NOT DISTINCT FROM NEW.revision_id
 AND OLD.qc_result_id IS NOT DISTINCT FROM NEW.qc_result_id
 AND OLD.payload_hash IS NOT DISTINCT FROM NEW.payload_hash
 AND NEW.scheduled_at <= OLD.scheduled_at
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

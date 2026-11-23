DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM restart_plan_requests) THEN
 RAISE EXCEPTION 'Plan request history exists; downgrade refused'; END IF;
END $$;
DROP INDEX uq_restart_active_operation;
ALTER TABLE restart_batches DROP CONSTRAINT fk_batch_plan_request, DROP COLUMN plan_request_id;
DROP TABLE restart_plan_request_items;
DROP TABLE restart_plan_requests;

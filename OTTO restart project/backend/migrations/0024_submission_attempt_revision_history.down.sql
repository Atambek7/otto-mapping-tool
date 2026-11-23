-- Restoring the old constraint is unsafe after a queue item has a newer revision.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM submission_attempts a JOIN restart_items i ON i.id=a.item_id
        WHERE a.revision_id IS DISTINCT FROM i.revision_id
    ) THEN
        RAISE EXCEPTION 'cannot downgrade: historical attempts reference prior queue revisions';
    END IF;
END $$;
DROP TRIGGER trg_submission_attempt_item_revision ON submission_attempts;
DROP FUNCTION guard_submission_attempt_item_revision();
ALTER TABLE submission_attempts DROP CONSTRAINT fk_submission_attempt_drip_item;
ALTER TABLE submission_attempts ADD CONSTRAINT fk_submission_attempt_drip_item
    FOREIGN KEY (item_id, operation_id, revision_id, account, data_scope)
    REFERENCES restart_items (id, operation_id, revision_id, account, data_scope) ON DELETE RESTRICT;
ALTER TABLE restart_items DROP CONSTRAINT uq_restart_items_attempt_item;

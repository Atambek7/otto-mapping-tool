-- Attempts retain their immutable revision independently of the queue's current revision.
ALTER TABLE restart_items ADD CONSTRAINT uq_restart_items_attempt_item
    UNIQUE (id, operation_id, account, data_scope);
ALTER TABLE submission_attempts DROP CONSTRAINT fk_submission_attempt_drip_item;
ALTER TABLE submission_attempts ADD CONSTRAINT fk_submission_attempt_drip_item
    FOREIGN KEY (item_id, operation_id, account, data_scope)
    REFERENCES restart_items (id, operation_id, account, data_scope) ON DELETE RESTRICT;

-- Preserve the original snapshot-binding check at attempt creation/identity changes.
-- Status/evidence updates to an historical attempt remain valid after preparation.
CREATE FUNCTION guard_submission_attempt_item_revision() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM restart_items i WHERE i.id=NEW.item_id
        AND i.operation_id=NEW.operation_id AND i.account=NEW.account
        AND i.data_scope=NEW.data_scope AND i.revision_id=NEW.revision_id
    ) THEN
        RAISE EXCEPTION 'submission attempt must bind the current queue revision'
            USING ERRCODE='23503';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_submission_attempt_item_revision
    BEFORE INSERT OR UPDATE OF item_id, operation_id, revision_id, account, data_scope
    ON submission_attempts FOR EACH ROW EXECUTE FUNCTION guard_submission_attempt_item_revision();

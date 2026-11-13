DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM submission_attempts) THEN
        RAISE EXCEPTION 'Refusing to remove recorded OTTO submission attempts';
    END IF;
END;
$$;

DROP INDEX IF EXISTS ix_submission_attempt_item_history;
DROP INDEX IF EXISTS uq_submission_attempt_open_operation;
DROP TABLE submission_attempts;
ALTER TABLE restart_items DROP CONSTRAINT IF EXISTS uq_restart_items_submission_identity;

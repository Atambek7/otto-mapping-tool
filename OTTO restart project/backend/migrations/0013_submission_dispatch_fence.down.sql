DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM submission_attempts WHERE dispatch_started_at IS NOT NULL) THEN
        RAISE EXCEPTION 'Refusing to discard durable OTTO dispatch evidence';
    END IF;
END;
$$;

ALTER TABLE submission_attempts DROP CONSTRAINT IF EXISTS submission_attempts_status_check;
ALTER TABLE submission_attempts
    ADD CONSTRAINT submission_attempts_status_check
        CHECK (status IN ('PREPARED', 'ACCEPTED', 'REJECTED', 'UNKNOWN'));

DROP INDEX IF EXISTS uq_submission_attempt_open_operation;
CREATE UNIQUE INDEX uq_submission_attempt_open_operation
    ON submission_attempts (account, operation_id)
    WHERE data_scope = 'PRODUCTION' AND status IN ('PREPARED', 'ACCEPTED', 'UNKNOWN');

ALTER TABLE submission_attempts DROP CONSTRAINT IF EXISTS ck_submission_attempt_dispatch_started;
ALTER TABLE submission_attempts DROP CONSTRAINT IF EXISTS ck_submission_attempt_lifecycle;
ALTER TABLE submission_attempts
    ADD CONSTRAINT ck_submission_attempt_lifecycle CHECK (
        (status = 'PREPARED' AND completed_at IS NULL AND http_status IS NULL)
        OR (status <> 'PREPARED' AND completed_at IS NOT NULL)
    );
ALTER TABLE submission_attempts DROP COLUMN dispatch_started_at;

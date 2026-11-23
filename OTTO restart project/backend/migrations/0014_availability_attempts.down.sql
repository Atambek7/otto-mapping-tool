DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM availability_attempts) THEN
        RAISE EXCEPTION 'Refusing to discard recorded OTTO availability attempts';
    END IF;
END;
$$;

DROP INDEX IF EXISTS ix_availability_attempt_job;
DROP INDEX IF EXISTS ix_availability_attempt_operation;
DROP TABLE availability_attempts;
ALTER TABLE background_jobs DROP CONSTRAINT IF EXISTS uq_background_jobs_scope_identity;

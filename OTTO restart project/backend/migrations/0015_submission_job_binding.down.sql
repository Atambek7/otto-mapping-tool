DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM submission_attempts WHERE background_job_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Refusing to discard submission-to-job history';
    END IF;
END $$;

DROP INDEX uq_submission_attempt_background_job;
ALTER TABLE submission_attempts
    DROP CONSTRAINT ck_submission_attempt_background_job,
    DROP CONSTRAINT fk_submission_attempt_background_job,
    DROP COLUMN background_job_id;

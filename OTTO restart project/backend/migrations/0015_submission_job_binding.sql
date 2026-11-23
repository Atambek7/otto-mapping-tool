-- Bind each durable product submission attempt to its transactional outbox job.

ALTER TABLE submission_attempts
    ADD COLUMN background_job_id UUID,
    ADD CONSTRAINT fk_submission_attempt_background_job
        FOREIGN KEY (background_job_id, account, data_scope)
        REFERENCES background_jobs (id, account, data_scope) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_submission_attempt_background_job
        CHECK (data_scope <> 'PRODUCTION' OR background_job_id IS NOT NULL) NOT VALID;

CREATE UNIQUE INDEX uq_submission_attempt_background_job
    ON submission_attempts (account, background_job_id)
    WHERE background_job_id IS NOT NULL;

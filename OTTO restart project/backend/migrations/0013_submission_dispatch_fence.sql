-- Persist the one-way boundary immediately before the only permitted product POST.
-- A process restart after DISPATCHING requires reconciliation and must never resend.

ALTER TABLE submission_attempts
    ADD COLUMN dispatch_started_at TIMESTAMPTZ;

ALTER TABLE submission_attempts
    DROP CONSTRAINT submission_attempts_status_check;

ALTER TABLE submission_attempts
    ADD CONSTRAINT submission_attempts_status_check
        CHECK (status IN ('PREPARED', 'DISPATCHING', 'ACCEPTED', 'REJECTED', 'UNKNOWN'));

ALTER TABLE submission_attempts
    DROP CONSTRAINT ck_submission_attempt_lifecycle;

ALTER TABLE submission_attempts
    ADD CONSTRAINT ck_submission_attempt_lifecycle CHECK (
        (
            status IN ('PREPARED', 'DISPATCHING')
            AND completed_at IS NULL
            AND http_status IS NULL
        )
        OR (
            status IN ('ACCEPTED', 'REJECTED', 'UNKNOWN')
            AND completed_at IS NOT NULL
        )
    );

ALTER TABLE submission_attempts
    ADD CONSTRAINT ck_submission_attempt_dispatch_started CHECK (
        (status = 'PREPARED' AND dispatch_started_at IS NULL)
        OR (status = 'DISPATCHING' AND dispatch_started_at IS NOT NULL)
        OR status IN ('ACCEPTED', 'REJECTED', 'UNKNOWN')
    );

DROP INDEX uq_submission_attempt_open_operation;
CREATE UNIQUE INDEX uq_submission_attempt_open_operation
    ON submission_attempts (account, operation_id)
    WHERE data_scope = 'PRODUCTION' AND status IN ('PREPARED', 'DISPATCHING', 'ACCEPTED', 'UNKNOWN');

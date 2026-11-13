-- Durable, one-way attempts for new-SKU availability writes.
-- The revision snapshot remains the source of quantity/profile inputs.

ALTER TABLE background_jobs
    ADD CONSTRAINT uq_background_jobs_scope_identity UNIQUE (id, account, data_scope);

CREATE TABLE availability_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    operation_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('QUANTITY', 'DELIVERY_INFORMATION')),
    attempt_key TEXT NOT NULL CHECK (length(attempt_key) BETWEEN 8 AND 160),
    target_sku TEXT NOT NULL CHECK (length(btrim(target_sku)) > 0),
    payload_hash CHAR(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    job_id UUID,
    status TEXT NOT NULL DEFAULT 'PREPARED'
        CHECK (status IN ('PREPARED', 'DISPATCHING', 'ACCEPTED', 'REJECTED', 'UNKNOWN')),
    http_status SMALLINT CHECK (http_status BETWEEN 100 AND 599),
    response_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(response_summary_json) = 'object'),
    error_code TEXT CHECK (error_code IS NULL OR error_code ~ '^[A-Z0-9_.-]{1,64}$'),
    created_by TEXT NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dispatch_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_availability_attempt_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT uq_availability_attempt_action UNIQUE (account, operation_id, revision_id, action),
    CONSTRAINT uq_availability_attempt_key UNIQUE (account, data_scope, attempt_key),
    CONSTRAINT uq_availability_attempt_job UNIQUE (job_id),
    CONSTRAINT fk_availability_attempt_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_availability_attempt_revision
        FOREIGN KEY (revision_id, operation_id, account, data_scope)
        REFERENCES operation_revisions (id, operation_id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_availability_attempt_job
        FOREIGN KEY (job_id, account, data_scope)
        REFERENCES background_jobs (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT ck_availability_attempt_lifecycle CHECK (
        (
            status = 'PREPARED'
            AND dispatch_started_at IS NULL
            AND completed_at IS NULL
            AND http_status IS NULL
        )
        OR (
            status = 'DISPATCHING'
            AND dispatch_started_at IS NOT NULL
            AND completed_at IS NULL
            AND http_status IS NULL
        )
        OR (
            status IN ('ACCEPTED', 'REJECTED', 'UNKNOWN')
            AND dispatch_started_at IS NOT NULL
            AND completed_at IS NOT NULL
        )
    )
);

CREATE INDEX ix_availability_attempt_operation
    ON availability_attempts (account, operation_id, created_at DESC);
CREATE INDEX ix_availability_attempt_job
    ON availability_attempts (job_id)
    WHERE job_id IS NOT NULL;

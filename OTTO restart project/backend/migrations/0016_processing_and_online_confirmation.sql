-- Durable OTTO processing-task polling and post-fulfillment ONLINE confirmation.
-- This migration defines storage only; it does not contact OTTO or start jobs.

ALTER TABLE otto_status_observations
    ALTER COLUMN run_id DROP NOT NULL,
    ADD COLUMN operation_id UUID,
    ADD CONSTRAINT ck_otto_observation_origin CHECK (
        (run_id IS NOT NULL AND operation_id IS NULL)
        OR (run_id IS NULL AND operation_id IS NOT NULL)
    ),
    ADD CONSTRAINT fk_otto_observation_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT;

CREATE TABLE otto_processing_polls (
    attempt_id UUID PRIMARY KEY,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    task_id UUID NOT NULL,
    task_href TEXT NOT NULL CHECK (
        task_href ~* '^/v5/products/update-tasks/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    ),
    state TEXT NOT NULL DEFAULT 'POLLING'
        CHECK (state IN ('POLLING', 'SUCCEEDED', 'FAILED', 'NEEDS_REVIEW', 'DEADLINE')),
    poll_count INTEGER NOT NULL DEFAULT 0 CHECK (poll_count >= 0),
    deadline_at TIMESTAMPTZ NOT NULL,
    current_job_id UUID,
    last_task_state TEXT CHECK (last_task_state IS NULL OR length(last_task_state) <= 100),
    last_error_code TEXT CHECK (last_error_code IS NULL OR last_error_code ~ '^[A-Z0-9_.-]{1,64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_otto_processing_poll_scope_identity UNIQUE (attempt_id, account, data_scope),
    CONSTRAINT fk_otto_processing_poll_attempt
        FOREIGN KEY (attempt_id, account, data_scope)
        REFERENCES submission_attempts (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_otto_processing_poll_job
        FOREIGN KEY (current_job_id, account, data_scope)
        REFERENCES background_jobs (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT ck_otto_processing_poll_lifecycle CHECK (
        (state = 'POLLING' AND completed_at IS NULL AND current_job_id IS NOT NULL)
        OR (state <> 'POLLING' AND completed_at IS NOT NULL AND current_job_id IS NULL)
    )
);

CREATE UNIQUE INDEX uq_otto_processing_poll_task
    ON otto_processing_polls (account, task_id);
CREATE INDEX ix_otto_processing_polls_deadline
    ON otto_processing_polls (account, deadline_at)
    WHERE state = 'POLLING';

CREATE TABLE otto_online_polls (
    operation_id UUID PRIMARY KEY,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    target_sku TEXT NOT NULL CHECK (length(btrim(target_sku)) > 0),
    state TEXT NOT NULL DEFAULT 'POLLING'
        CHECK (state IN ('POLLING', 'CONFIRMED', 'NEEDS_REVIEW', 'DEADLINE')),
    poll_count INTEGER NOT NULL DEFAULT 0 CHECK (poll_count >= 0),
    deadline_at TIMESTAMPTZ NOT NULL,
    current_job_id UUID,
    last_status TEXT CHECK (last_status IS NULL OR length(last_status) <= 100),
    last_error_code TEXT CHECK (last_error_code IS NULL OR last_error_code ~ '^[A-Z0-9_.-]{1,64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_otto_online_poll_scope_identity UNIQUE (operation_id, account, data_scope),
    CONSTRAINT fk_otto_online_poll_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_otto_online_poll_job
        FOREIGN KEY (current_job_id, account, data_scope)
        REFERENCES background_jobs (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT ck_otto_online_poll_lifecycle CHECK (
        (state = 'POLLING' AND completed_at IS NULL AND current_job_id IS NOT NULL)
        OR (state <> 'POLLING' AND completed_at IS NOT NULL AND current_job_id IS NULL)
    )
);

CREATE INDEX ix_otto_online_polls_deadline
    ON otto_online_polls (account, deadline_at)
    WHERE state = 'POLLING';

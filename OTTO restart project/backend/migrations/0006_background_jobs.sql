-- Durable PostgreSQL outbox and attempt history for OttoRestart jobs.
-- Jobs are production scoped; legacy rows are never dispatched.

CREATE TABLE background_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    job_type TEXT NOT NULL CHECK (job_type ~ '^[a-z][a-z0-9_.-]{0,63}$'),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
    payload_json JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'DEAD', 'CANCELLED')),
    priority SMALLINT NOT NULL DEFAULT 0 CHECK (priority BETWEEN -100 AND 100),
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    attempts_in_cycle INTEGER NOT NULL DEFAULT 0 CHECK (attempts_in_cycle >= 0),
    total_attempts INTEGER NOT NULL DEFAULT 0 CHECK (total_attempts >= attempts_in_cycle),
    max_attempts SMALLINT NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
    replay_count INTEGER NOT NULL DEFAULT 0 CHECK (replay_count >= 0),
    last_error_code TEXT,
    dispatch_generation INTEGER NOT NULL DEFAULT 1 CHECK (dispatch_generation > 0),
    dispatch_attempts INTEGER NOT NULL DEFAULT 0 CHECK (dispatch_attempts >= 0),
    dispatched_at TIMESTAMPTZ,
    dispatch_lease_token UUID,
    dispatch_lease_until TIMESTAMPTZ,
    last_dispatch_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_background_jobs_idempotency
        UNIQUE (account, data_scope, job_type, idempotency_key),
    CONSTRAINT ck_background_jobs_lease_state
        CHECK ((state = 'RUNNING' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
            OR (state <> 'RUNNING' AND lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL)),
    CONSTRAINT ck_background_jobs_attempt_limit
        CHECK (attempts_in_cycle <= max_attempts),
    CONSTRAINT ck_background_jobs_dispatch_lease
        CHECK ((dispatch_lease_token IS NULL AND dispatch_lease_until IS NULL)
            OR (dispatch_lease_token IS NOT NULL AND dispatch_lease_until IS NOT NULL)),
    CONSTRAINT ck_background_jobs_completion
        CHECK ((state IN ('SUCCEEDED', 'DEAD', 'CANCELLED') AND completed_at IS NOT NULL)
            OR (state NOT IN ('SUCCEEDED', 'DEAD', 'CANCELLED') AND completed_at IS NULL))
);

CREATE TABLE background_job_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL REFERENCES background_jobs(id) ON DELETE RESTRICT,
    replay_number INTEGER NOT NULL CHECK (replay_number >= 0),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    worker_id TEXT NOT NULL,
    lease_token UUID NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (state IN ('RUNNING', 'SUCCEEDED', 'RETRYABLE_FAILURE', 'FINAL_FAILURE', 'LEASE_EXPIRED')),
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    CONSTRAINT uq_background_job_attempt_cycle UNIQUE (job_id, replay_number, attempt_number),
    CONSTRAINT ck_background_job_attempt_completion
        CHECK ((state = 'RUNNING' AND finished_at IS NULL)
            OR (state <> 'RUNNING' AND finished_at IS NOT NULL))
);

CREATE INDEX ix_background_jobs_claim
    ON background_jobs (priority DESC, available_at, created_at)
    WHERE state IN ('PENDING', 'RETRY_WAIT') AND data_scope = 'PRODUCTION';
CREATE INDEX ix_background_jobs_expired_lease
    ON background_jobs (lease_until)
    WHERE state = 'RUNNING' AND data_scope = 'PRODUCTION';
CREATE INDEX ix_background_jobs_dispatch
    ON background_jobs (priority DESC, available_at, dispatched_at, created_at)
    WHERE state IN ('PENDING', 'RETRY_WAIT') AND data_scope = 'PRODUCTION';
CREATE INDEX ix_background_jobs_expired_dispatch_lease
    ON background_jobs (dispatch_lease_until)
    WHERE dispatch_lease_token IS NOT NULL AND data_scope = 'PRODUCTION';
CREATE INDEX ix_background_jobs_account_state
    ON background_jobs (account, state, updated_at DESC);
CREATE INDEX ix_background_job_attempts_job
    ON background_job_attempts (job_id, replay_number, attempt_number DESC);

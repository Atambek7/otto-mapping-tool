-- Migration 0005: durable, account-scoped import runs and OTTO observations.
-- Existing import history remains present but is explicitly unverified.

ALTER TABLE import_runs
    ALTER COLUMN source_filename DROP NOT NULL,
    ADD COLUMN run_type TEXT NOT NULL DEFAULT 'LEGACY',
    ADD COLUMN run_mode TEXT,
    ADD COLUMN run_state TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    ADD COLUMN started_at TIMESTAMPTZ,
    ADD COLUMN finished_at TIMESTAMPTZ,
    ADD COLUMN window_from TIMESTAMPTZ,
    ADD COLUMN cutoff_at TIMESTAMPTZ,
    ADD COLUMN parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN coverage_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN error_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT ck_import_runs_run_type
        CHECK (run_type IN ('FILE_IMPORT', 'INCREMENTAL_SCAN', 'KNOWN_SKU_RECONCILIATION', 'LEGACY')),
    ADD CONSTRAINT ck_import_runs_run_mode
        CHECK (run_mode IS NULL OR run_mode IN ('BASELINE', 'BASELINE_EXTEND', 'INCREMENTAL')),
    ADD CONSTRAINT ck_import_runs_run_state
        CHECK (run_state IN ('PENDING', 'RUNNING', 'STAGED', 'APPLYING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED', 'LEGACY_UNVERIFIED')),
    ADD CONSTRAINT ck_import_runs_time_order
        CHECK (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at);

CREATE INDEX ix_import_runs_production_state
    ON import_runs (account, run_type, run_state, created_at DESC)
    WHERE data_scope = 'PRODUCTION';

CREATE TABLE import_rows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
        CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION')),
    row_number INTEGER NOT NULL CHECK (row_number > 0),
    raw_identifier TEXT NOT NULL,
    normalized_identifier TEXT,
    period_hint TEXT CHECK (period_hint IS NULL OR period_hint IN ('3M', '12M')),
    row_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (row_state IN ('PENDING', 'VALID', 'INVALID', 'DUPLICATE', 'IDENTITY_UNRESOLVED', 'ERROR')),
    row_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    errors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_import_rows_run_row UNIQUE (run_id, row_number),
    CONSTRAINT uq_import_rows_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT fk_import_rows_scoped_run
        FOREIGN KEY (run_id, account, data_scope)
        REFERENCES import_runs (id, account, data_scope)
        ON DELETE CASCADE
);

CREATE INDEX ix_import_rows_run_state
    ON import_rows (run_id, row_state, row_number);

CREATE TABLE otto_status_observations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
        CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION')),
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    requested_sku TEXT NOT NULL CHECK (requested_sku <> ''),
    response_sku TEXT,
    source_product_id UUID,
    confirmed_ean TEXT,
    moin TEXT,
    http_status SMALLINT,
    classification TEXT NOT NULL
        CHECK (classification IN ('EXACT_CODE', 'TITLE_ONLY', 'AMBIGUOUS', 'NONE', 'UNCHECKABLE')),
    period TEXT CHECK (period IS NULL OR period IN ('3M', '12M')),
    reason TEXT NOT NULL,
    status TEXT,
    market_place_status TEXT,
    active BOOLEAN,
    last_modified TIMESTAMPTZ,
    errors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_codes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_titles_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    unknown_codes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    response_json JSONB NOT NULL,
    response_hash CHAR(64) NOT NULL CHECK (response_hash ~ '^[0-9a-f]{64}$'),
    reduction_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (reduction_status IN ('PENDING', 'OPENED', 'UPDATED', 'CLOSED', 'REPEATED', 'STALE', 'UNORDERED', 'CONFLICT', 'IDENTITY_CONFLICT', 'AMBIGUOUS', 'UNCHECKABLE', 'NONE')),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_otto_observation_period
        CHECK ((classification IN ('EXACT_CODE', 'TITLE_ONLY') AND period IN ('3M', '12M'))
            OR (classification NOT IN ('EXACT_CODE', 'TITLE_ONLY') AND period IS NULL)),
    CONSTRAINT uq_otto_observation_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT uq_otto_observation_run_response UNIQUE (run_id, requested_sku, response_hash),
    CONSTRAINT fk_otto_observation_scoped_run
        FOREIGN KEY (run_id, account, data_scope)
        REFERENCES import_runs (id, account, data_scope)
        ON DELETE RESTRICT,
    CONSTRAINT fk_otto_observation_scoped_source
        FOREIGN KEY (source_product_id, account, data_scope)
        REFERENCES source_products (id, account, data_scope)
        ON DELETE RESTRICT
);

CREATE INDEX ix_otto_observations_production_sku_time
    ON otto_status_observations (account, requested_sku, observed_at DESC)
    WHERE data_scope = 'PRODUCTION';
CREATE INDEX ix_otto_observations_run_reduction
    ON otto_status_observations (run_id, reduction_status, sequence_no);

CREATE TABLE candidate_episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
        CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION')),
    observed_sku TEXT NOT NULL CHECK (observed_sku <> ''),
    canonical_identity TEXT NOT NULL CHECK (canonical_identity <> ''),
    source_product_id UUID,
    confirmed_ean TEXT,
    moin TEXT,
    period TEXT NOT NULL CHECK (period IN ('3M', '12M')),
    classification TEXT NOT NULL CHECK (classification IN ('EXACT_CODE', 'TITLE_ONLY')),
    origin TEXT NOT NULL CHECK (origin IN ('BASELINE', 'NEW')),
    episode_state TEXT NOT NULL DEFAULT 'OPEN' CHECK (episode_state IN ('OPEN', 'CLOSED')),
    first_observation_id UUID NOT NULL,
    last_observation_id UUID NOT NULL,
    closed_by_observation_id UUID,
    last_response_hash CHAR(64) NOT NULL CHECK (last_response_hash ~ '^[0-9a-f]{64}$'),
    last_modified TIMESTAMPTZ,
    first_detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_candidate_episode_close_state
        CHECK ((episode_state = 'OPEN' AND closed_at IS NULL AND closed_by_observation_id IS NULL)
            OR (episode_state = 'CLOSED' AND closed_at IS NOT NULL AND closed_by_observation_id IS NOT NULL)),
    CONSTRAINT uq_candidate_episode_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT fk_candidate_episode_scoped_source
        FOREIGN KEY (source_product_id, account, data_scope)
        REFERENCES source_products (id, account, data_scope)
        ON DELETE RESTRICT,
    CONSTRAINT fk_candidate_episode_first_observation
        FOREIGN KEY (first_observation_id, account, data_scope)
        REFERENCES otto_status_observations (id, account, data_scope)
        ON DELETE RESTRICT,
    CONSTRAINT fk_candidate_episode_last_observation
        FOREIGN KEY (last_observation_id, account, data_scope)
        REFERENCES otto_status_observations (id, account, data_scope)
        ON DELETE RESTRICT,
    CONSTRAINT fk_candidate_episode_closed_observation
        FOREIGN KEY (closed_by_observation_id, account, data_scope)
        REFERENCES otto_status_observations (id, account, data_scope)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_candidate_episode_open_account_sku
    ON candidate_episodes (account, observed_sku)
    WHERE episode_state = 'OPEN' AND data_scope = 'PRODUCTION';
CREATE INDEX ix_candidate_episodes_production_state
    ON candidate_episodes (account, origin, episode_state, period)
    WHERE data_scope = 'PRODUCTION';

CREATE TABLE discovery_cursors (
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION'
        CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION')),
    last_successful_cutoff TIMESTAMPTZ,
    last_successful_run_id UUID,
    overlap_seconds INTEGER NOT NULL DEFAULT 172800 CHECK (overlap_seconds >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account, data_scope),
    CONSTRAINT fk_discovery_cursor_scoped_run
        FOREIGN KEY (last_successful_run_id, account, data_scope)
        REFERENCES import_runs (id, account, data_scope)
        ON DELETE RESTRICT,
    CONSTRAINT ck_discovery_cursor_cutoff_pair
        CHECK ((last_successful_cutoff IS NULL) = (last_successful_run_id IS NULL))
);

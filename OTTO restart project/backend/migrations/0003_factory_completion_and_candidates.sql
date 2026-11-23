-- Migration 0003: Factory completion state machine, candidates tracking, and import history

-- 1. restart_factories: Registry of factories with COMPLETE / NOT_COMPLETE / UNKNOWN status
CREATE TABLE IF NOT EXISTS restart_factories (
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (status IN ('COMPLETE', 'NOT_COMPLETE', 'UNKNOWN')),
    first_completed_at TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    last_scanned_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account, id)
);

CREATE INDEX IF NOT EXISTS ix_restart_factories_account_status
    ON restart_factories(account, status);

-- 2. factory_completion_history: Immutable audit log of each factory completion event
CREATE TABLE IF NOT EXISTS factory_completion_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    factory_id TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    candidates_count INT NOT NULL DEFAULT 0,
    published_eans_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_factory_completion FOREIGN KEY (account, factory_id)
        REFERENCES restart_factories(account, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_factory_completion_history_account_factory
    ON factory_completion_history(account, factory_id, completed_at DESC);

-- 3. import_runs: History of bulk EAN/SKU imports
CREATE TABLE IF NOT EXISTS import_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    source_filename TEXT NOT NULL,
    total_rows INT NOT NULL DEFAULT 0,
    processed_count INT NOT NULL DEFAULT 0,
    afterbuy_count INT NOT NULL DEFAULT 0,
    direct_otto_count INT NOT NULL DEFAULT 0,
    skipped_count INT NOT NULL DEFAULT 0,
    error_count INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'COMPLETED' CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_import_runs_account_created
    ON import_runs(account, created_at DESC);

-- 4. Extend source_products with lifecycle, candidate and cohort attributes
ALTER TABLE source_products ADD COLUMN IF NOT EXISTS import_run_id UUID REFERENCES import_runs(id) ON DELETE SET NULL;
ALTER TABLE source_products ADD COLUMN IF NOT EXISTS is_new_candidate BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE source_products ADD COLUMN IF NOT EXISTS current_status TEXT NOT NULL DEFAULT 'CANDIDATE';
ALTER TABLE source_products ADD COLUMN IF NOT EXISTS period TEXT DEFAULT '3M';
ALTER TABLE source_products ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_source_products_account_factory_new
    ON source_products(account, factory_id, is_new_candidate);

CREATE INDEX IF NOT EXISTS ix_source_products_account_current_status
    ON source_products(account, current_status);

-- 5. Seed initial factories from existing catalog to avoid empty state
INSERT INTO restart_factories (account, id, name, status, created_at, updated_at) VALUES
('JV', '477277', 'GANASI_SOFA_(CN)', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('JV', '504123', 'MEISE_MOEBEL', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('JV', '502007', 'POLIPOL', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('JV', '489912', 'WIMEX', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('JV', '492015', 'FORTE_MOBILIA', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('JV', '479901', 'ACTONA_GROUP', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('XL', '477277', 'GANASI_SOFA_(CN)', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('XL', '504123', 'MEISE_MOEBEL', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('XL', '483385', 'KAPITAN', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('XL', '501144', 'BEGABEST', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('XL', '506780', 'MEBLE_MARS', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
('XL', '508822', 'SIGNAL_MEBLE', 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT (account, id) DO NOTHING;

-- Also seed any factory present in source_products if missing
INSERT INTO restart_factories (account, id, name, status, created_at, updated_at)
SELECT DISTINCT account, factory_id, COALESCE(factory_name, factory_id), 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM source_products
WHERE factory_id IS NOT NULL AND factory_id != ''
ON CONFLICT (account, id) DO NOTHING;


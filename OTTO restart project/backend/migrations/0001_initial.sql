CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS restart_batches (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    period TEXT NOT NULL CHECK (period IN ('3M', '12M')),
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('IMPORTED', 'PREVIEWED', 'NEEDS_REVIEW', 'READY', 'CANCELLED')),
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_restart_batches_account_created
    ON restart_batches(account, created_at DESC);

CREATE TABLE IF NOT EXISTS restart_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES restart_batches(id) ON DELETE CASCADE,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    source_ean TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    UNIQUE(batch_id, row_number)
);

CREATE INDEX IF NOT EXISTS ix_restart_items_batch
    ON restart_items(batch_id, row_number);

CREATE TABLE IF NOT EXISTS restart_ean_registry (
    ean TEXT PRIMARY KEY,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'RESERVED', 'SUBMITTED', 'USED', 'QUARANTINED')),
    batch_id TEXT REFERENCES restart_batches(id),
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_restart_ean_account_status
    ON restart_ean_registry(account, status, imported_at DESC);

CREATE TABLE IF NOT EXISTS restart_idempotency_keys (
    key TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    response_body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS restart_events (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_restart_events_account_created
    ON restart_events(account, created_at DESC);

-- Durable, account-scoped record of one-SKU OTTO submission attempts.
-- This revision adds persistence only; it does not enable or invoke an OTTO writer.

ALTER TABLE restart_items
    ADD CONSTRAINT uq_restart_items_submission_identity
        UNIQUE (id, operation_id, revision_id, account, data_scope);

CREATE TABLE submission_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    operation_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    item_id BIGINT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    source_sku TEXT NOT NULL CHECK (length(btrim(source_sku)) > 0),
    target_sku TEXT NOT NULL CHECK (length(btrim(target_sku)) > 0),
    target_ean TEXT NOT NULL CHECK (target_ean ~ '^[0-9]{13}$'),
    payload_hash CHAR(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    source_price_cents BIGINT NOT NULL CHECK (source_price_cents > 0),
    source_price_currency TEXT NOT NULL CHECK (source_price_currency = 'EUR'),
    source_price_checked_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PREPARED', 'ACCEPTED', 'REJECTED', 'UNKNOWN')),
    http_status SMALLINT CHECK (http_status BETWEEN 100 AND 599),
    task_links_json JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(task_links_json) = 'array'),
    per_sku_results_json JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(per_sku_results_json) = 'array'),
    error_code TEXT CHECK (error_code IS NULL OR error_code ~ '^[A-Z0-9_.-]{1,64}$'),
    created_by TEXT NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_submission_attempt_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT uq_submission_attempt_number UNIQUE (account, operation_id, attempt_number),
    CONSTRAINT fk_submission_attempt_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_submission_attempt_revision
        FOREIGN KEY (revision_id, operation_id, account, data_scope)
        REFERENCES operation_revisions (id, operation_id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_submission_attempt_drip_item
        FOREIGN KEY (item_id, operation_id, revision_id, account, data_scope)
        REFERENCES restart_items (id, operation_id, revision_id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT ck_submission_attempt_lifecycle CHECK (
        (status = 'PREPARED' AND completed_at IS NULL AND http_status IS NULL)
        OR (status <> 'PREPARED' AND completed_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX uq_submission_attempt_open_operation
    ON submission_attempts (account, operation_id)
    WHERE data_scope = 'PRODUCTION' AND status IN ('PREPARED', 'ACCEPTED', 'UNKNOWN');

CREATE INDEX ix_submission_attempt_item_history
    ON submission_attempts (account, item_id, attempt_number DESC);

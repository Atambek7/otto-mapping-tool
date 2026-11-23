-- Migration 0002: Normalized OTTO Library schema for Aftercool integration and atomic EAN management

-- 1. source_products: Master registry of products originating from Aftercool / Afterbuy / OTTO Direct
CREATE TABLE IF NOT EXISTS source_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    source_type TEXT NOT NULL CHECK (source_type IN ('AFTERCOOL', 'AFTERBUY', 'OTTO_DIRECT')),
    aftercool_product_id TEXT,
    aftercool_sku TEXT,
    aftercool_ean TEXT,
    factory_id TEXT,
    factory_name TEXT,
    original_title TEXT NOT NULL,
    original_price_cents BIGINT,
    category TEXT,
    image_url TEXT,
    attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_products_account_type_prod_id
    ON source_products(account, source_type, aftercool_product_id)
    WHERE aftercool_product_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_source_products_account_ean
    ON source_products(account, aftercool_ean);

CREATE INDEX IF NOT EXISTS ix_source_products_account_factory
    ON source_products(account, factory_id);

CREATE INDEX IF NOT EXISTS ix_source_products_account_sku
    ON source_products(account, aftercool_sku);

-- 2. ean_pool: Pooled pool of GS1 EAN-13 barcodes with status tracking and atomic reservation
CREATE TABLE IF NOT EXISTS ean_pool (
    ean TEXT PRIMARY KEY,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE', 'RESERVED', 'USED', 'QUARANTINED')),
    batch_id TEXT,
    reserved_at TIMESTAMPTZ,
    used_at TIMESTAMPTZ,
    quarantined_at TIMESTAMPTZ,
    quarantine_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ean_pool_account_status_created
    ON ean_pool(account, status, created_at ASC);

-- 3. ean_links: Durable lineage mapping between Aftercool source product/EAN and newly assigned OTTO EAN
CREATE TABLE IF NOT EXISTS ean_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    source_product_id UUID NOT NULL REFERENCES source_products(id) ON DELETE RESTRICT,
    source_ean TEXT NOT NULL,
    new_ean TEXT NOT NULL REFERENCES ean_pool(ean) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'PENDING_PUBLISH', 'PUBLISHED', 'DEACTIVATED')),
    operation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deactivated_at TIMESTAMPTZ,
    deactivation_reason TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ean_links_account_new_ean
    ON ean_links(account, new_ean);

CREATE INDEX IF NOT EXISTS ix_ean_links_account_source_ean
    ON ean_links(account, source_ean);

CREATE INDEX IF NOT EXISTS ix_ean_links_account_source_product
    ON ean_links(account, source_product_id);

CREATE INDEX IF NOT EXISTS ix_ean_links_account_status
    ON ean_links(account, status);

-- 4. restart_operations: Individual restart attempts/runs for cohort tracking (3M / 12M)
CREATE TABLE IF NOT EXISTS restart_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    source_product_id UUID NOT NULL REFERENCES source_products(id) ON DELETE RESTRICT,
    period TEXT NOT NULL CHECK (period IN ('3M', '12M')),
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'READY', 'QUEUED', 'PUBLISHED', 'FAILED', 'CANCELLED')),
    new_ean TEXT REFERENCES ean_pool(ean) ON DELETE SET NULL,
    target_title TEXT,
    target_price_cents BIGINT,
    target_category TEXT,
    attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_restart_operations_account_status
    ON restart_operations(account, status, created_at DESC);

-- Migrate barcodes from restart_ean_registry to ean_pool if any
INSERT INTO ean_pool (ean, account, status, batch_id, created_at)
SELECT ean, account, status, batch_id, imported_at
FROM restart_ean_registry
ON CONFLICT (ean) DO NOTHING;

-- Seed initial valid GS1 EAN-13 barcodes for JV account
INSERT INTO ean_pool (ean, account, status, created_at) VALUES
('4069424010015', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010022', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010039', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010046', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010053', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010060', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010077', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010084', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010091', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010107', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010114', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010121', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010138', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010145', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010152', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010169', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010176', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010183', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010190', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP),
('4069424010206', 'JV', 'AVAILABLE', CURRENT_TIMESTAMP)
ON CONFLICT (ean) DO NOTHING;

-- Seed initial valid GS1 EAN-13 barcodes for XL account
INSERT INTO ean_pool (ean, account, status, created_at) VALUES
('4071489010015', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010022', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010039', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010046', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010053', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010060', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010077', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010084', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010091', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010107', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010114', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010121', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010138', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010145', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010152', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010169', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010176', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010183', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010190', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP),
('4071489010206', 'XL', 'AVAILABLE', CURRENT_TIMESTAMP)
ON CONFLICT (ean) DO NOTHING;


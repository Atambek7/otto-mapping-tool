-- Migration 0004: isolate all records that predate evidence-backed production.
-- Existing rows receive LEGACY_UNVERIFIED. Future production services must set
-- data_scope explicitly; the default intentionally remains fail-closed.

ALTER TABLE source_products
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE ean_pool
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE ean_links
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_operations
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_batches
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_items
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_ean_registry
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_idempotency_keys
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_events
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE otto_library
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE restart_factories
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE factory_completion_history
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));
ALTER TABLE import_runs
    ADD COLUMN data_scope TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED'
    CHECK (data_scope IN ('LEGACY_UNVERIFIED', 'PRODUCTION'));

-- Composite unique targets let new rows enforce account and scope lineage.
ALTER TABLE restart_batches
    ADD CONSTRAINT uq_restart_batches_scope_identity UNIQUE (id, account, data_scope);
ALTER TABLE source_products
    ADD CONSTRAINT uq_source_products_scope_identity UNIQUE (id, account, data_scope);
ALTER TABLE ean_pool
    ADD CONSTRAINT uq_ean_pool_scope_identity UNIQUE (ean, account, data_scope);
ALTER TABLE ean_links
    ADD CONSTRAINT uq_ean_links_scope_identity UNIQUE (id, account, data_scope);
ALTER TABLE restart_operations
    ADD CONSTRAINT uq_restart_operations_scope_identity UNIQUE (id, account, data_scope);
ALTER TABLE restart_factories
    ADD CONSTRAINT uq_restart_factories_scope_identity UNIQUE (id, account, data_scope);
ALTER TABLE import_runs
    ADD CONSTRAINT uq_import_runs_scope_identity UNIQUE (id, account, data_scope);

-- NOT VALID preserves inconsistent historical rows while enforcing the same
-- relationship for every new or updated row, including any later promotion.
ALTER TABLE restart_items
    ADD CONSTRAINT fk_restart_items_scoped_batch
    FOREIGN KEY (batch_id, account, data_scope)
    REFERENCES restart_batches (id, account, data_scope)
    ON DELETE CASCADE NOT VALID;

ALTER TABLE restart_ean_registry
    ADD CONSTRAINT fk_restart_ean_registry_scoped_batch
    FOREIGN KEY (batch_id, account, data_scope)
    REFERENCES restart_batches (id, account, data_scope)
    ON DELETE SET NULL (batch_id) NOT VALID;

ALTER TABLE source_products
    ADD CONSTRAINT fk_source_products_scoped_import_run
    FOREIGN KEY (import_run_id, account, data_scope)
    REFERENCES import_runs (id, account, data_scope)
    ON DELETE SET NULL (import_run_id) NOT VALID;

ALTER TABLE ean_links
    ADD CONSTRAINT fk_ean_links_scoped_source_product
    FOREIGN KEY (source_product_id, account, data_scope)
    REFERENCES source_products (id, account, data_scope)
    ON DELETE RESTRICT NOT VALID;
ALTER TABLE ean_links
    ADD CONSTRAINT fk_ean_links_scoped_pool_item
    FOREIGN KEY (new_ean, account, data_scope)
    REFERENCES ean_pool (ean, account, data_scope)
    ON DELETE RESTRICT NOT VALID;
ALTER TABLE ean_links
    ADD CONSTRAINT fk_ean_links_scoped_operation
    FOREIGN KEY (operation_id, account, data_scope)
    REFERENCES restart_operations (id, account, data_scope)
    ON DELETE SET NULL (operation_id) NOT VALID;

ALTER TABLE restart_operations
    ADD CONSTRAINT fk_restart_operations_scoped_source_product
    FOREIGN KEY (source_product_id, account, data_scope)
    REFERENCES source_products (id, account, data_scope)
    ON DELETE RESTRICT NOT VALID;
ALTER TABLE restart_operations
    ADD CONSTRAINT fk_restart_operations_scoped_pool_item
    FOREIGN KEY (new_ean, account, data_scope)
    REFERENCES ean_pool (ean, account, data_scope)
    ON DELETE SET NULL (new_ean) NOT VALID;

ALTER TABLE ean_pool
    ADD CONSTRAINT fk_ean_pool_scoped_batch
    FOREIGN KEY (batch_id, account, data_scope)
    REFERENCES restart_batches (id, account, data_scope)
    NOT VALID;
ALTER TABLE otto_library
    ADD CONSTRAINT fk_otto_library_scoped_batch
    FOREIGN KEY (batch_id, account, data_scope)
    REFERENCES restart_batches (id, account, data_scope)
    ON DELETE SET NULL (batch_id) NOT VALID;
ALTER TABLE factory_completion_history
    ADD CONSTRAINT fk_factory_completion_scoped_factory
    FOREIGN KEY (factory_id, account, data_scope)
    REFERENCES restart_factories (id, account, data_scope)
    ON DELETE CASCADE NOT VALID;

-- Production read paths get indexes scoped to the verified dataset only.
CREATE INDEX ix_source_products_production_factory
    ON source_products (account, factory_id, period)
    WHERE data_scope = 'PRODUCTION';
CREATE UNIQUE INDEX uq_source_products_production_ean
    ON source_products (account, aftercool_ean)
    WHERE data_scope = 'PRODUCTION' AND aftercool_ean IS NOT NULL;
CREATE UNIQUE INDEX uq_source_products_production_direct_sku
    ON source_products (account, aftercool_sku)
    WHERE data_scope = 'PRODUCTION'
      AND source_type = 'OTTO_DIRECT'
      AND aftercool_sku IS NOT NULL;
CREATE INDEX ix_ean_pool_production_account_status
    ON ean_pool (account, status, created_at)
    WHERE data_scope = 'PRODUCTION';
CREATE INDEX ix_ean_links_production_account_source
    ON ean_links (account, source_ean, created_at DESC)
    WHERE data_scope = 'PRODUCTION';
CREATE INDEX ix_restart_batches_production_account_created
    ON restart_batches (account, created_at DESC)
    WHERE data_scope = 'PRODUCTION';
CREATE INDEX ix_restart_factories_production_account_status
    ON restart_factories (account, status)
    WHERE data_scope = 'PRODUCTION';
CREATE INDEX ix_import_runs_production_account_created
    ON import_runs (account, created_at DESC)
    WHERE data_scope = 'PRODUCTION';

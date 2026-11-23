-- Composite production indexes to keep 50k-row import chunking, status
-- observation reduction, and 45k-row catalog pagination fast without full scans.

CREATE INDEX IF NOT EXISTS ix_import_rows_run_scope_order
    ON import_rows (run_id, account, data_scope, row_number);

CREATE INDEX IF NOT EXISTS ix_otto_obs_run_scope_seq
    ON otto_status_observations (run_id, account, data_scope, sequence_no, observed_at, id);

CREATE INDEX IF NOT EXISTS ix_candidate_episodes_catalog_order
    ON candidate_episodes (account, last_detected_at DESC, observed_sku ASC, id ASC)
    WHERE data_scope = 'PRODUCTION' AND episode_state = 'OPEN';

CREATE INDEX IF NOT EXISTS ix_candidate_episodes_production_source
    ON candidate_episodes (account, source_product_id)
    WHERE data_scope = 'PRODUCTION';

CREATE INDEX IF NOT EXISTS ix_source_products_catalog_type
    ON source_products (account, source_type, factory_id)
    WHERE data_scope = 'PRODUCTION';

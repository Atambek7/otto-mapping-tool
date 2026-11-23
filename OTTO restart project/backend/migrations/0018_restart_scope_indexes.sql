-- Make business identity uniqueness respect the legacy/production boundary.
-- Deactivated EAN lineage remains auditable without reserving the identifier
-- forever; at most one non-deactivated link may own an account/EAN pair.

DROP INDEX uq_source_products_account_type_prod_id;
CREATE UNIQUE INDEX uq_source_products_account_scope_type_prod_id
    ON source_products (account, data_scope, source_type, aftercool_product_id)
    WHERE aftercool_product_id IS NOT NULL;

ALTER TABLE otto_library
    DROP CONSTRAINT otto_library_account_old_ean_key;
CREATE UNIQUE INDEX uq_otto_library_account_scope_old_ean
    ON otto_library (account, data_scope, old_ean);

DROP INDEX uq_ean_links_account_new_ean;
CREATE UNIQUE INDEX uq_ean_links_account_new_ean
    ON ean_links (account, new_ean)
    WHERE status <> 'DEACTIVATED';

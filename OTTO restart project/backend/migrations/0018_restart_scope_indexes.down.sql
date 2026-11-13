DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM source_products
        WHERE aftercool_product_id IS NOT NULL
        GROUP BY account, source_type, aftercool_product_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Cannot restore global source product uniqueness while cross-scope duplicates exist';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM ean_links
        GROUP BY account, new_ean
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Cannot restore permanent EAN link uniqueness while deactivated lineage is retained';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM otto_library
        GROUP BY account, old_ean
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Cannot restore global OTTO library old EAN uniqueness while cross-scope rows exist';
    END IF;
END;
$$;

DROP INDEX uq_source_products_account_scope_type_prod_id;
CREATE UNIQUE INDEX uq_source_products_account_type_prod_id
    ON source_products (account, source_type, aftercool_product_id)
    WHERE aftercool_product_id IS NOT NULL;

DROP INDEX uq_otto_library_account_scope_old_ean;
ALTER TABLE otto_library
    ADD CONSTRAINT otto_library_account_old_ean_key UNIQUE (account, old_ean);

DROP INDEX uq_ean_links_account_new_ean;
CREATE UNIQUE INDEX uq_ean_links_account_new_ean
    ON ean_links (account, new_ean);

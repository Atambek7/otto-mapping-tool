-- One production GTIN belongs to exactly one OttoRestart pool row globally,
-- across JV and XL. The migration fails closed if legacy duplicates exist.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM ean_pool
        WHERE data_scope = 'PRODUCTION'
        GROUP BY ean
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Production EAN pool contains duplicate EANs; quarantine duplicates before migration 0020.';
    END IF;
END $$;

CREATE UNIQUE INDEX uq_ean_pool_global_production_ean
    ON ean_pool (ean)
    WHERE data_scope = 'PRODUCTION';

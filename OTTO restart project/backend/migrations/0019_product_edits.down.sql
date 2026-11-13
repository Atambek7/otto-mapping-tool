DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM restart_product_edits) THEN
        RAISE EXCEPTION 'Cannot downgrade while saved product edits exist';
    END IF;
END;
$$;
DROP TABLE restart_product_edits;

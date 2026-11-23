DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM otto_processing_polls)
       OR EXISTS (SELECT 1 FROM otto_online_polls)
       OR EXISTS (SELECT 1 FROM otto_status_observations WHERE operation_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Cannot downgrade populated OTTO processing/ONLINE evidence';
    END IF;
END;
$$;

DROP TABLE otto_online_polls;
DROP TABLE otto_processing_polls;

ALTER TABLE otto_status_observations
    DROP CONSTRAINT fk_otto_observation_operation,
    DROP CONSTRAINT ck_otto_observation_origin,
    DROP COLUMN operation_id,
    ALTER COLUMN run_id SET NOT NULL;

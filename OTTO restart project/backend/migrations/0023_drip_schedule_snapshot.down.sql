DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM restart_plan_request_items
        WHERE scheduled_at IS NOT NULL OR category_snapshot IS NOT NULL
    ) OR EXISTS (
        SELECT 1 FROM restart_plan_requests WHERE processing_started_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Schedule snapshots exist; downgrade refused to preserve the planning manifest';
    END IF;
END $$;

DROP INDEX ix_restart_plan_items_schedule;
ALTER TABLE restart_plan_request_items
    DROP COLUMN category_snapshot,
    DROP COLUMN scheduled_at;

ALTER TABLE restart_plan_requests
    DROP COLUMN processing_started_at;

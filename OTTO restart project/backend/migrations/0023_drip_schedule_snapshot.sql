-- Persist the exact local schedule/category preview for each selected product.
-- Nullable columns allow an existing PENDING request to be backfilled once by
-- its account-scoped planner before any operation/batch is created.
ALTER TABLE restart_plan_request_items
    ADD COLUMN scheduled_at TIMESTAMPTZ,
    ADD COLUMN category_snapshot TEXT;

ALTER TABLE restart_plan_requests
    ADD COLUMN processing_started_at TIMESTAMPTZ;

CREATE INDEX ix_restart_plan_items_schedule
    ON restart_plan_request_items (request_id, account, scheduled_at, row_number);

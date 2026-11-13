-- Durable production Drip Feed schedules over immutable Restart operation revisions.

ALTER TABLE qc_results
    ADD CONSTRAINT uq_qc_results_drip_snapshot
        UNIQUE (id, operation_id, revision_id, account, data_scope);

ALTER TABLE restart_batches
    DROP CONSTRAINT IF EXISTS restart_batches_status_check,
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN factory_id TEXT,
    ADD COLUMN local_start_date DATE,
    ADD COLUMN daily_time TIME WITHOUT TIME ZONE,
    ADD COLUMN timezone_name TEXT,
    ADD COLUMN scheduled_start_at TIMESTAMPTZ,
    ADD COLUMN total_days SMALLINT,
    ADD COLUMN selected_operation_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN schedule_generation INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN version BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN created_by TEXT,
    ADD COLUMN started_at TIMESTAMPTZ,
    ADD COLUMN paused_at TIMESTAMPTZ,
    ADD COLUMN cancelled_at TIMESTAMPTZ,
    ADD CONSTRAINT ck_restart_batches_status CHECK (
        status IN ('IMPORTED', 'PREVIEWED', 'NEEDS_REVIEW', 'READY', 'DRAFT',
                   'SCHEDULED', 'ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED')
    ),
    ADD CONSTRAINT ck_restart_batches_selected_operations
        CHECK (jsonb_typeof(selected_operation_ids_json) = 'array'),
    ADD CONSTRAINT ck_restart_batches_schedule_generation CHECK (schedule_generation > 0),
    ADD CONSTRAINT ck_restart_batches_version CHECK (version > 0),
    ADD CONSTRAINT ck_restart_batches_production_schedule CHECK (
        data_scope <> 'PRODUCTION' OR (
            status IN ('DRAFT', 'SCHEDULED', 'ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED')
            AND local_start_date IS NOT NULL
            AND daily_time IS NOT NULL
            AND timezone_name IS NOT NULL
            AND length(btrim(timezone_name)) BETWEEN 1 AND 100
            AND scheduled_start_at IS NOT NULL
            AND total_days BETWEEN 1 AND 30
            AND created_by IS NOT NULL
            AND length(btrim(created_by)) BETWEEN 1 AND 200
        )
    ) NOT VALID;

ALTER TABLE restart_items
    ADD COLUMN operation_id UUID,
    ADD COLUMN revision_id UUID,
    ADD COLUMN qc_result_id UUID,
    ADD COLUMN payload_hash CHAR(64),
    ADD COLUMN scheduled_at TIMESTAMPTZ,
    ADD COLUMN status TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    ADD COLUMN release_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN last_error_code TEXT,
    ADD COLUMN ready_at TIMESTAMPTZ,
    ADD COLUMN version BIGINT NOT NULL DEFAULT 1,
    ADD CONSTRAINT ck_restart_items_drip_status CHECK (
        status IN ('LEGACY_UNVERIFIED', 'SCHEDULED', 'READY_TO_SUBMIT', 'NEEDS_REVIEW',
                   'SUBMITTING', 'OTTO_PROCESSING', 'AWAITING_ONLINE', 'PUBLISHED',
                   'FAILED', 'CANCELLED')
    ),
    ADD CONSTRAINT ck_restart_items_release_attempts CHECK (release_attempts >= 0),
    ADD CONSTRAINT ck_restart_items_version CHECK (version > 0),
    ADD CONSTRAINT ck_restart_items_production_snapshot CHECK (
        data_scope <> 'PRODUCTION' OR (
            operation_id IS NOT NULL AND revision_id IS NOT NULL AND qc_result_id IS NOT NULL
            AND payload_hash ~ '^[0-9a-f]{64}$' AND scheduled_at IS NOT NULL
            AND status <> 'LEGACY_UNVERIFIED'
        )
    ) NOT VALID,
    ADD CONSTRAINT fk_restart_items_drip_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT fk_restart_items_drip_revision
        FOREIGN KEY (revision_id, operation_id, account, data_scope)
        REFERENCES operation_revisions (id, operation_id, account, data_scope) ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT fk_restart_items_drip_qc
        FOREIGN KEY (qc_result_id, operation_id, revision_id, account, data_scope)
        REFERENCES qc_results (id, operation_id, revision_id, account, data_scope) ON DELETE RESTRICT NOT VALID;

CREATE UNIQUE INDEX uq_restart_items_active_operation_revision
    ON restart_items (account, operation_id, revision_id)
    WHERE data_scope = 'PRODUCTION'
      AND status NOT IN ('FAILED', 'CANCELLED', 'PUBLISHED');

CREATE INDEX ix_restart_batches_production_schedule
    ON restart_batches (account, status, scheduled_start_at)
    WHERE data_scope = 'PRODUCTION';

CREATE INDEX ix_restart_items_production_schedule
    ON restart_items (account, status, scheduled_at, batch_id)
    WHERE data_scope = 'PRODUCTION';

CREATE FUNCTION guard_restart_batch_schedule_update() RETURNS trigger AS $$
BEGIN
    IF OLD.data_scope = 'PRODUCTION'
       AND OLD.status <> 'DRAFT'
       AND (
           OLD.account IS DISTINCT FROM NEW.account
           OR OLD.data_scope IS DISTINCT FROM NEW.data_scope
           OR OLD.period IS DISTINCT FROM NEW.period
           OR OLD.factory_id IS DISTINCT FROM NEW.factory_id
           OR OLD.local_start_date IS DISTINCT FROM NEW.local_start_date
           OR OLD.daily_time IS DISTINCT FROM NEW.daily_time
           OR OLD.timezone_name IS DISTINCT FROM NEW.timezone_name
           OR OLD.scheduled_start_at IS DISTINCT FROM NEW.scheduled_start_at
           OR OLD.total_days IS DISTINCT FROM NEW.total_days
           OR OLD.selected_operation_ids_json IS DISTINCT FROM NEW.selected_operation_ids_json
           OR OLD.created_by IS DISTINCT FROM NEW.created_by
       ) THEN
        RAISE EXCEPTION 'started Drip Feed schedule configuration is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_restart_batch_schedule_immutable
    BEFORE UPDATE ON restart_batches
    FOR EACH ROW EXECUTE FUNCTION guard_restart_batch_schedule_update();

CREATE FUNCTION guard_restart_item_snapshot_update() RETURNS trigger AS $$
BEGIN
    IF OLD.data_scope = 'PRODUCTION'
       AND (
           OLD.batch_id IS DISTINCT FROM NEW.batch_id
           OR OLD.account IS DISTINCT FROM NEW.account
           OR OLD.data_scope IS DISTINCT FROM NEW.data_scope
           OR OLD.source_ean IS DISTINCT FROM NEW.source_ean
           OR OLD.row_number IS DISTINCT FROM NEW.row_number
           OR OLD.operation_id IS DISTINCT FROM NEW.operation_id
           OR OLD.revision_id IS DISTINCT FROM NEW.revision_id
           OR OLD.qc_result_id IS DISTINCT FROM NEW.qc_result_id
           OR OLD.payload_hash IS DISTINCT FROM NEW.payload_hash
           OR OLD.scheduled_at IS DISTINCT FROM NEW.scheduled_at
       ) THEN
        RAISE EXCEPTION 'Drip Feed item operation and QC snapshot is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_restart_item_snapshot_immutable
    BEFORE UPDATE ON restart_items
    FOR EACH ROW EXECUTE FUNCTION guard_restart_item_snapshot_update();

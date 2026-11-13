-- Persist routing progress for discovered candidates and account-scoped weekly scans.
-- This revision is code/schema only; applying it requires the separate DB rollout gate.

ALTER TABLE candidate_episodes
    ADD COLUMN routing_state TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    ADD COLUMN routing_error_code TEXT,
    ADD COLUMN routed_at TIMESTAMPTZ,
    ADD CONSTRAINT ck_candidate_episode_routing_state CHECK (
        routing_state IN ('LEGACY_UNVERIFIED', 'PENDING', 'PROCESSING', 'ROUTED', 'REVIEW', 'ERROR')
    ),
    ADD CONSTRAINT ck_candidate_episode_routing_error CHECK (
        routing_error_code IS NULL OR routing_error_code ~ '^[A-Z0-9_.-]{1,64}$'
    ),
    ADD CONSTRAINT ck_candidate_episode_routed_at CHECK (
        (routing_state = 'ROUTED' AND routed_at IS NOT NULL)
        OR (routing_state <> 'ROUTED' AND routed_at IS NULL)
    );

UPDATE candidate_episodes
SET routing_state = CASE
        WHEN data_scope <> 'PRODUCTION' THEN 'LEGACY_UNVERIFIED'
        WHEN source_product_id IS NOT NULL THEN 'ROUTED'
        ELSE 'PENDING'
    END,
    routed_at = CASE
        WHEN data_scope = 'PRODUCTION' AND source_product_id IS NOT NULL THEN updated_at
        ELSE NULL
    END;

CREATE INDEX ix_candidate_episodes_routing
    ON candidate_episodes (account, routing_state, last_detected_at DESC)
    WHERE data_scope = 'PRODUCTION' AND episode_state = 'OPEN';

ALTER TABLE discovery_cursors
    ADD COLUMN baseline_run_id UUID,
    ADD COLUMN baseline_confirmed_by TEXT,
    ADD COLUMN baseline_confirmed_at TIMESTAMPTZ,
    ADD COLUMN schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN schedule_timezone TEXT NOT NULL DEFAULT 'Europe/Berlin',
    ADD COLUMN schedule_weekday SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN schedule_local_time TIME NOT NULL DEFAULT TIME '03:00',
    ADD COLUMN schedule_next_at TIMESTAMPTZ,
    ADD COLUMN last_scheduled_slot_at TIMESTAMPTZ,
    ADD CONSTRAINT fk_discovery_cursor_baseline_run
        FOREIGN KEY (baseline_run_id, account, data_scope)
        REFERENCES import_runs (id, account, data_scope) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_discovery_cursor_baseline_pair CHECK (
        (baseline_run_id IS NULL AND baseline_confirmed_by IS NULL AND baseline_confirmed_at IS NULL)
        OR (baseline_run_id IS NOT NULL AND baseline_confirmed_by IS NOT NULL AND baseline_confirmed_at IS NOT NULL)
    ),
    ADD CONSTRAINT ck_discovery_cursor_baseline_actor CHECK (
        baseline_confirmed_by IS NULL OR length(btrim(baseline_confirmed_by)) BETWEEN 1 AND 128
    ),
    ADD CONSTRAINT ck_discovery_cursor_schedule_weekday CHECK (schedule_weekday BETWEEN 0 AND 6),
    ADD CONSTRAINT ck_discovery_cursor_schedule_timezone CHECK (
        schedule_timezone ~ '^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$'
    ),
    ADD CONSTRAINT ck_discovery_cursor_schedule_enabled CHECK (
        NOT schedule_enabled OR (
            baseline_confirmed_at IS NOT NULL AND schedule_next_at IS NOT NULL
        )
    );

CREATE INDEX ix_discovery_cursors_due_schedule
    ON discovery_cursors (schedule_next_at, account)
    WHERE data_scope = 'PRODUCTION' AND schedule_enabled;

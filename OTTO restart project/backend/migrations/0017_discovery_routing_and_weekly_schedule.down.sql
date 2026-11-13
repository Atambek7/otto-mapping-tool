DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM candidate_episodes
        WHERE data_scope = 'PRODUCTION' AND routing_state NOT IN ('PENDING', 'LEGACY_UNVERIFIED')
    ) OR EXISTS (
        SELECT 1 FROM discovery_cursors
        WHERE baseline_run_id IS NOT NULL OR schedule_enabled OR last_scheduled_slot_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Cannot downgrade discovery routing or confirmed schedule data';
    END IF;
END;
$$;

DROP INDEX ix_discovery_cursors_due_schedule;
DROP INDEX ix_candidate_episodes_routing;

ALTER TABLE discovery_cursors
    DROP CONSTRAINT ck_discovery_cursor_schedule_enabled,
    DROP CONSTRAINT ck_discovery_cursor_schedule_timezone,
    DROP CONSTRAINT ck_discovery_cursor_schedule_weekday,
    DROP CONSTRAINT ck_discovery_cursor_baseline_actor,
    DROP CONSTRAINT ck_discovery_cursor_baseline_pair,
    DROP CONSTRAINT fk_discovery_cursor_baseline_run,
    DROP COLUMN last_scheduled_slot_at,
    DROP COLUMN schedule_next_at,
    DROP COLUMN schedule_local_time,
    DROP COLUMN schedule_weekday,
    DROP COLUMN schedule_timezone,
    DROP COLUMN schedule_enabled,
    DROP COLUMN baseline_confirmed_at,
    DROP COLUMN baseline_confirmed_by,
    DROP COLUMN baseline_run_id;

ALTER TABLE candidate_episodes
    DROP CONSTRAINT ck_candidate_episode_routed_at,
    DROP CONSTRAINT ck_candidate_episode_routing_error,
    DROP CONSTRAINT ck_candidate_episode_routing_state,
    DROP COLUMN routed_at,
    DROP COLUMN routing_error_code,
    DROP COLUMN routing_state;

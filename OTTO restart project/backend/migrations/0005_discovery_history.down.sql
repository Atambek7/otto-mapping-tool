DROP TABLE discovery_cursors;
DROP TABLE candidate_episodes;
DROP TABLE otto_status_observations;
DROP TABLE import_rows;
DROP INDEX ix_import_runs_production_state;

ALTER TABLE import_runs
    DROP CONSTRAINT ck_import_runs_time_order,
    DROP CONSTRAINT ck_import_runs_run_state,
    DROP CONSTRAINT ck_import_runs_run_mode,
    DROP CONSTRAINT ck_import_runs_run_type,
    DROP COLUMN error_json,
    DROP COLUMN coverage_json,
    DROP COLUMN parameters_json,
    DROP COLUMN cutoff_at,
    DROP COLUMN window_from,
    DROP COLUMN finished_at,
    DROP COLUMN started_at,
    DROP COLUMN run_state,
    DROP COLUMN run_mode,
    DROP COLUMN run_type,
    ALTER COLUMN source_filename SET NOT NULL;

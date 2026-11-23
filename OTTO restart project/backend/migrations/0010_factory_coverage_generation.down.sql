DROP INDEX IF EXISTS uq_factory_completion_production_generation;
DROP INDEX IF EXISTS ix_factory_completion_production_history;

ALTER TABLE factory_completion_history
    DROP CONSTRAINT IF EXISTS ck_factory_completion_generation,
    DROP COLUMN IF EXISTS generation,
    DROP COLUMN IF EXISTS episode_ids_json,
    DROP COLUMN IF EXISTS operation_ids_json,
    DROP COLUMN IF EXISTS evidence_json;

DROP INDEX IF EXISTS ix_restart_factories_production_coverage;
ALTER TABLE restart_factories
    DROP CONSTRAINT IF EXISTS fk_restart_factories_coverage_run,
    DROP CONSTRAINT IF EXISTS ck_restart_factories_coverage_confirmation,
    DROP CONSTRAINT restart_factories_pkey,
    ADD CONSTRAINT restart_factories_pkey PRIMARY KEY (account, id),
    DROP COLUMN IF EXISTS candidate_generation,
    DROP COLUMN IF EXISTS coverage_confirmed_at,
    DROP COLUMN IF EXISTS coverage_generation,
    DROP COLUMN IF EXISTS coverage_run_id,
    DROP COLUMN IF EXISTS coverage_evidence_json,
    DROP COLUMN IF EXISTS projection_json;

ALTER TABLE factory_completion_history
    ADD CONSTRAINT fk_factory_completion FOREIGN KEY (account, factory_id)
        REFERENCES restart_factories (account, id) ON DELETE CASCADE;

-- Factory status is a production projection over verified episodes and
-- operation evidence. Preserve pre-existing factory/history rows as legacy.

-- Migration 0003 used (account, id) as the primary key. S02 must keep those
-- legacy rows while allowing a separate production row for the same factory.
ALTER TABLE factory_completion_history
    DROP CONSTRAINT fk_factory_completion;

ALTER TABLE restart_factories
    DROP CONSTRAINT restart_factories_pkey,
    ADD CONSTRAINT restart_factories_pkey PRIMARY KEY (account, id, data_scope),
    ADD COLUMN candidate_generation BIGINT NOT NULL DEFAULT 0
        CHECK (candidate_generation >= 0),
    ADD COLUMN coverage_confirmed_at TIMESTAMPTZ,
    ADD COLUMN coverage_generation BIGINT
        CHECK (coverage_generation IS NULL OR coverage_generation >= 0),
    ADD COLUMN coverage_run_id UUID,
    ADD COLUMN coverage_evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(coverage_evidence_json) = 'object'),
    ADD COLUMN projection_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(projection_json) = 'object'),
    ADD CONSTRAINT ck_restart_factories_coverage_confirmation CHECK (
        (coverage_confirmed_at IS NULL AND coverage_generation IS NULL AND coverage_run_id IS NULL)
        OR (coverage_confirmed_at IS NOT NULL AND coverage_generation IS NOT NULL
            AND coverage_run_id IS NOT NULL AND coverage_generation <= candidate_generation
            AND coverage_evidence_json <> '{}'::jsonb)
    ),
    ADD CONSTRAINT fk_restart_factories_coverage_run
        FOREIGN KEY (coverage_run_id, account, data_scope)
        REFERENCES import_runs (id, account, data_scope) ON DELETE RESTRICT NOT VALID;

CREATE INDEX ix_restart_factories_production_coverage
    ON restart_factories (account, coverage_confirmed_at, candidate_generation, coverage_generation)
    WHERE data_scope = 'PRODUCTION';

ALTER TABLE factory_completion_history
    ADD COLUMN generation BIGINT,
    ADD COLUMN episode_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(episode_ids_json) = 'array'),
    ADD COLUMN operation_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(operation_ids_json) = 'array'),
    ADD COLUMN evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(evidence_json) = 'object'),
    ADD CONSTRAINT ck_factory_completion_generation
        CHECK (generation IS NULL OR generation >= 0);

CREATE UNIQUE INDEX uq_factory_completion_production_generation
    ON factory_completion_history (account, factory_id, generation)
    WHERE data_scope = 'PRODUCTION' AND generation IS NOT NULL;

CREATE INDEX ix_factory_completion_production_history
    ON factory_completion_history (account, factory_id, completed_at DESC)
    WHERE data_scope = 'PRODUCTION';

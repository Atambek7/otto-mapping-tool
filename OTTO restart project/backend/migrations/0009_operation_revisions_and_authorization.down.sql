DROP TRIGGER IF EXISTS trg_qc_results_append_only ON qc_results;
DROP TRIGGER IF EXISTS trg_operation_revisions_append_only ON operation_revisions;
DROP TRIGGER IF EXISTS trg_restart_authorizations_no_delete ON restart_authorizations;
DROP TRIGGER IF EXISTS trg_restart_authorizations_one_time_consume ON restart_authorizations;

ALTER TABLE restart_operations
    DROP CONSTRAINT IF EXISTS fk_restart_operations_repeat_authorization,
    DROP CONSTRAINT IF EXISTS fk_restart_operations_history_review;

DROP TABLE IF EXISTS qc_results;
DROP TABLE IF EXISTS operation_revisions;
DROP TABLE IF EXISTS restart_authorizations;

DROP FUNCTION IF EXISTS guard_restart_authorization_update();
DROP FUNCTION IF EXISTS reject_operation_evidence_mutation();

ALTER TABLE restart_operations
    DROP CONSTRAINT IF EXISTS fk_restart_operations_candidate_episode,
    DROP CONSTRAINT IF EXISTS fk_restart_operations_processing_event,
    DROP CONSTRAINT IF EXISTS fk_restart_operations_online_observation,
    DROP CONSTRAINT IF EXISTS uq_restart_operations_scoped_source_identity,
    DROP CONSTRAINT IF EXISTS uq_restart_operations_scoped_episode_identity,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_status,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_attempt_number,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_target_sku,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_created_by,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_production_identity,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_submission_state,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_online_evidence,
    DROP CONSTRAINT IF EXISTS ck_restart_operations_published_evidence,
    DROP COLUMN IF EXISTS candidate_episode_id,
    DROP COLUMN IF EXISTS attempt_number,
    DROP COLUMN IF EXISTS target_sku,
    DROP COLUMN IF EXISTS created_by,
    DROP COLUMN IF EXISTS version,
    DROP COLUMN IF EXISTS current_revision_number,
    DROP COLUMN IF EXISTS submission_state,
    DROP COLUMN IF EXISTS repeat_authorization_id,
    DROP COLUMN IF EXISTS history_review_id,
    DROP COLUMN IF EXISTS processing_evidence_event_id,
    DROP COLUMN IF EXISTS online_observation_id,
    ADD CONSTRAINT restart_operations_status_check
        CHECK (status IN ('DRAFT', 'READY', 'QUEUED', 'PUBLISHED', 'FAILED', 'CANCELLED'));

ALTER TABLE candidate_episodes
    DROP CONSTRAINT IF EXISTS uq_candidate_episode_scope_source_identity;
ALTER TABLE restart_events
    DROP CONSTRAINT IF EXISTS uq_restart_events_scope_identity;

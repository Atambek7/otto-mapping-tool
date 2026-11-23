-- Durable operation attempts, immutable payload revisions, QC evidence, and
-- one-time account/source/episode-bound restart authorization.

ALTER TABLE restart_events
    ADD CONSTRAINT uq_restart_events_scope_identity UNIQUE (id, account, data_scope);

ALTER TABLE candidate_episodes
    ADD CONSTRAINT uq_candidate_episode_scope_source_identity
        UNIQUE (id, account, data_scope, source_product_id);

ALTER TABLE restart_operations
    DROP CONSTRAINT IF EXISTS restart_operations_status_check,
    ADD COLUMN candidate_episode_id UUID,
    ADD COLUMN attempt_number INTEGER,
    ADD COLUMN target_sku TEXT,
    ADD COLUMN created_by TEXT,
    ADD COLUMN version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    ADD COLUMN current_revision_number INTEGER NOT NULL DEFAULT 0 CHECK (current_revision_number >= 0),
    ADD COLUMN submission_state TEXT NOT NULL DEFAULT 'NEVER_SUBMITTED'
        CHECK (submission_state IN ('NEVER_SUBMITTED', 'IN_FLIGHT', 'UNKNOWN', 'CONFIRMED')),
    ADD COLUMN repeat_authorization_id UUID,
    ADD COLUMN history_review_id UUID,
    ADD COLUMN processing_evidence_event_id TEXT,
    ADD COLUMN online_observation_id UUID,
    ADD CONSTRAINT ck_restart_operations_status CHECK (
        status IN ('DRAFT', 'PREPARING', 'QC_PENDING', 'READY', 'QUEUED', 'SUBMITTING',
                   'OTTO_PROCESSING', 'AWAITING_ONLINE', 'PUBLISHED', 'NEEDS_REVIEW',
                   'RECONCILIATION_REQUIRED', 'FAILED', 'CANCELLED')
    ),
    ADD CONSTRAINT ck_restart_operations_attempt_number
        CHECK (attempt_number IS NULL OR attempt_number > 0),
    ADD CONSTRAINT ck_restart_operations_target_sku
        CHECK (target_sku IS NULL OR length(btrim(target_sku)) > 0),
    ADD CONSTRAINT ck_restart_operations_created_by
        CHECK (created_by IS NULL OR length(btrim(created_by)) BETWEEN 1 AND 200),
    ADD CONSTRAINT ck_restart_operations_production_identity CHECK (
        data_scope <> 'PRODUCTION' OR
        (candidate_episode_id IS NOT NULL AND attempt_number IS NOT NULL AND created_by IS NOT NULL)
    ) NOT VALID,
    ADD CONSTRAINT ck_restart_operations_submission_state CHECK (
        (submission_state <> 'UNKNOWN' OR status = 'RECONCILIATION_REQUIRED')
        AND (status <> 'RECONCILIATION_REQUIRED' OR submission_state = 'UNKNOWN')
        AND (submission_state <> 'CONFIRMED' OR processing_evidence_event_id IS NOT NULL)
    ) NOT VALID,
    ADD CONSTRAINT ck_restart_operations_online_evidence CHECK (
        online_observation_id IS NULL
        OR (submission_state = 'CONFIRMED' AND processing_evidence_event_id IS NOT NULL)
    ) NOT VALID,
    ADD CONSTRAINT ck_restart_operations_published_evidence CHECK (
        data_scope <> 'PRODUCTION' OR status <> 'PUBLISHED'
        OR (submission_state = 'CONFIRMED' AND target_sku IS NOT NULL
            AND processing_evidence_event_id IS NOT NULL AND online_observation_id IS NOT NULL)
    ) NOT VALID,
    ADD CONSTRAINT fk_restart_operations_candidate_episode
        FOREIGN KEY (candidate_episode_id, account, data_scope, source_product_id)
        REFERENCES candidate_episodes (id, account, data_scope, source_product_id) ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT fk_restart_operations_processing_event
        FOREIGN KEY (processing_evidence_event_id, account, data_scope)
        REFERENCES restart_events (id, account, data_scope) ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT fk_restart_operations_online_observation
        FOREIGN KEY (online_observation_id, account, data_scope)
        REFERENCES otto_status_observations (id, account, data_scope) ON DELETE RESTRICT NOT VALID;

ALTER TABLE restart_operations
    ADD CONSTRAINT uq_restart_operations_scoped_source_identity
        UNIQUE (id, account, data_scope, source_product_id),
    ADD CONSTRAINT uq_restart_operations_scoped_episode_identity
        UNIQUE (id, account, data_scope, source_product_id, candidate_episode_id);

CREATE UNIQUE INDEX uq_restart_operations_production_attempt
    ON restart_operations (account, source_product_id, attempt_number)
    WHERE data_scope = 'PRODUCTION' AND attempt_number IS NOT NULL;
CREATE UNIQUE INDEX uq_restart_operations_production_episode
    ON restart_operations (account, candidate_episode_id)
    WHERE data_scope = 'PRODUCTION' AND candidate_episode_id IS NOT NULL;
CREATE UNIQUE INDEX uq_restart_operations_production_open_source
    ON restart_operations (account, source_product_id)
    WHERE data_scope = 'PRODUCTION' AND status NOT IN ('PUBLISHED', 'FAILED', 'CANCELLED');
CREATE INDEX ix_restart_operations_production_source_history
    ON restart_operations (account, source_product_id, attempt_number DESC)
    WHERE data_scope = 'PRODUCTION';

CREATE TABLE restart_authorizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    source_product_id UUID NOT NULL,
    candidate_episode_id UUID NOT NULL,
    authorization_type TEXT NOT NULL CHECK (authorization_type IN ('HISTORY_REVIEW', 'RESTART_RETRY')),
    decision TEXT NOT NULL CHECK (decision IN ('CONFIRMED_TEST', 'CONFIRMED_PRODUCTION', 'INSUFFICIENT', 'AUTHORIZED')),
    confirmed_prior_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (confirmed_prior_attempt_count >= 0),
    expected_attempt_number INTEGER CHECK (expected_attempt_number IS NULL OR expected_attempt_number > 0),
    reviewed_through_operation_id UUID,
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 20 AND 2000),
    created_by TEXT NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_operation_id UUID,
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT,
    revocation_reason TEXT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    CONSTRAINT uq_restart_authorizations_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT uq_restart_authorizations_source_episode_identity
        UNIQUE (id, account, data_scope, source_product_id, candidate_episode_id),
    CONSTRAINT ck_restart_authorizations_expiry CHECK (expires_at > issued_at),
    CONSTRAINT ck_restart_authorizations_consumption CHECK (
        (consumed_at IS NULL AND consumed_operation_id IS NULL)
        OR (consumed_at IS NOT NULL AND consumed_operation_id IS NOT NULL)
    ),
    CONSTRAINT ck_restart_authorizations_revocation CHECK (
        (revoked_at IS NULL AND revoked_by IS NULL AND revocation_reason IS NULL)
        OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revocation_reason IS NOT NULL
            AND length(btrim(revoked_by)) BETWEEN 1 AND 200
            AND length(btrim(revocation_reason)) BETWEEN 20 AND 2000)
    ),
    CONSTRAINT ck_restart_authorizations_terminal CHECK (consumed_at IS NULL OR revoked_at IS NULL),
    CONSTRAINT ck_restart_authorizations_type_decision CHECK (
        (authorization_type = 'HISTORY_REVIEW'
            AND decision IN ('CONFIRMED_TEST', 'CONFIRMED_PRODUCTION', 'INSUFFICIENT')
            AND expected_attempt_number IS NULL
            AND ((decision = 'CONFIRMED_TEST' AND confirmed_prior_attempt_count = 0)
                OR (decision = 'CONFIRMED_PRODUCTION' AND confirmed_prior_attempt_count > 0)
                OR decision = 'INSUFFICIENT'))
        OR (authorization_type = 'RESTART_RETRY' AND decision = 'AUTHORIZED'
            AND expected_attempt_number IS NOT NULL)
    ),
    CONSTRAINT fk_restart_authorizations_source
        FOREIGN KEY (source_product_id, account, data_scope)
        REFERENCES source_products (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_restart_authorizations_episode
        FOREIGN KEY (candidate_episode_id, account, data_scope, source_product_id)
        REFERENCES candidate_episodes (id, account, data_scope, source_product_id) ON DELETE RESTRICT,
    CONSTRAINT fk_restart_authorizations_reviewed_operation
        FOREIGN KEY (reviewed_through_operation_id, account, data_scope, source_product_id)
        REFERENCES restart_operations (id, account, data_scope, source_product_id) ON DELETE RESTRICT,
    CONSTRAINT fk_restart_authorizations_consumed_operation
        FOREIGN KEY (consumed_operation_id, account, data_scope, source_product_id, candidate_episode_id)
        REFERENCES restart_operations (id, account, data_scope, source_product_id, candidate_episode_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_restart_authorizations_open_retry
    ON restart_authorizations (account, source_product_id, candidate_episode_id)
    WHERE authorization_type = 'RESTART_RETRY' AND decision = 'AUTHORIZED' AND consumed_at IS NULL AND revoked_at IS NULL;
CREATE UNIQUE INDEX uq_restart_authorizations_open_history_review
    ON restart_authorizations (account, source_product_id, candidate_episode_id)
    WHERE authorization_type = 'HISTORY_REVIEW' AND consumed_at IS NULL AND revoked_at IS NULL;

ALTER TABLE restart_operations
    ADD CONSTRAINT fk_restart_operations_repeat_authorization
        FOREIGN KEY (repeat_authorization_id, account, data_scope, source_product_id, candidate_episode_id)
        REFERENCES restart_authorizations (id, account, data_scope, source_product_id, candidate_episode_id) ON DELETE RESTRICT NOT VALID,
    ADD CONSTRAINT fk_restart_operations_history_review
        FOREIGN KEY (history_review_id, account, data_scope, source_product_id, candidate_episode_id)
        REFERENCES restart_authorizations (id, account, data_scope, source_product_id, candidate_episode_id) ON DELETE RESTRICT NOT VALID;

CREATE TABLE operation_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    source_snapshot_json JSONB NOT NULL,
    content_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    price_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    fulfillment_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    gtin_evidence_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(gtin_evidence_ids_json) = 'array'),
    payload_json JSONB NOT NULL,
    payload_hash CHAR(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    created_by TEXT NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_operation_revisions_number UNIQUE (operation_id, revision_number),
    CONSTRAINT uq_operation_revisions_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT uq_operation_revisions_operation_identity UNIQUE (id, operation_id, account, data_scope),
    CONSTRAINT fk_operation_revisions_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT
);

CREATE INDEX ix_operation_revisions_account_operation
    ON operation_revisions (account, operation_id, revision_number DESC);

CREATE TABLE qc_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operation_id UUID NOT NULL,
    revision_id UUID NOT NULL,
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    outcome TEXT NOT NULL CHECK (outcome IN ('PASSED_AUTO', 'MANUAL_REQUIRED', 'BLOCKED')),
    policy_version TEXT NOT NULL CHECK (length(btrim(policy_version)) BETWEEN 1 AND 100),
    payload_hash CHAR(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    result_json JSONB NOT NULL,
    created_by TEXT NOT NULL CHECK (length(btrim(created_by)) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_qc_results_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT fk_qc_results_operation
        FOREIGN KEY (operation_id, account, data_scope)
        REFERENCES restart_operations (id, account, data_scope) ON DELETE RESTRICT,
    CONSTRAINT fk_qc_results_revision
        FOREIGN KEY (revision_id, operation_id, account, data_scope)
        REFERENCES operation_revisions (id, operation_id, account, data_scope) ON DELETE RESTRICT
);

CREATE INDEX ix_qc_results_operation_revision
    ON qc_results (account, operation_id, revision_id, created_at DESC);

CREATE FUNCTION reject_operation_evidence_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'operation evidence records are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_operation_revisions_append_only
    BEFORE UPDATE OR DELETE ON operation_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_operation_evidence_mutation();
CREATE TRIGGER trg_qc_results_append_only
    BEFORE UPDATE OR DELETE ON qc_results
    FOR EACH ROW EXECUTE FUNCTION reject_operation_evidence_mutation();

CREATE FUNCTION guard_restart_authorization_update() RETURNS trigger AS $$
BEGIN
    IF OLD.consumed_at IS NOT NULL OR OLD.revoked_at IS NOT NULL
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.account IS DISTINCT FROM NEW.account
       OR OLD.data_scope IS DISTINCT FROM NEW.data_scope
       OR OLD.source_product_id IS DISTINCT FROM NEW.source_product_id
       OR OLD.candidate_episode_id IS DISTINCT FROM NEW.candidate_episode_id
       OR OLD.authorization_type IS DISTINCT FROM NEW.authorization_type
       OR OLD.decision IS DISTINCT FROM NEW.decision
       OR OLD.confirmed_prior_attempt_count IS DISTINCT FROM NEW.confirmed_prior_attempt_count
       OR OLD.expected_attempt_number IS DISTINCT FROM NEW.expected_attempt_number
       OR OLD.reviewed_through_operation_id IS DISTINCT FROM NEW.reviewed_through_operation_id
       OR OLD.reason IS DISTINCT FROM NEW.reason
       OR OLD.created_by IS DISTINCT FROM NEW.created_by
       OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
       OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
       OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'restart authorization is immutable except for one-time consumption';
    END IF;
    IF NOT (
        (NEW.consumed_at IS NOT NULL AND NEW.consumed_operation_id IS NOT NULL
            AND NEW.revoked_at IS NULL AND NEW.revoked_by IS NULL AND NEW.revocation_reason IS NULL)
        OR (NEW.consumed_at IS NULL AND NEW.consumed_operation_id IS NULL
            AND NEW.revoked_at IS NOT NULL AND NEW.revoked_by IS NOT NULL AND NEW.revocation_reason IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'authorization update must consume or revoke exactly once';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_restart_authorizations_one_time_consume
    BEFORE UPDATE ON restart_authorizations
    FOR EACH ROW EXECUTE FUNCTION guard_restart_authorization_update();
CREATE TRIGGER trg_restart_authorizations_no_delete
    BEFORE DELETE ON restart_authorizations
    FOR EACH ROW EXECUTE FUNCTION reject_operation_evidence_mutation();

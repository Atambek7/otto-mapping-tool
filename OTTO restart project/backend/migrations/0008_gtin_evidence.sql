-- Product/account-bound evidence for manual GTIN eligibility review.
-- Artifacts are private database blobs and are served only through the
-- authenticated account-scoped evidence endpoint.

CREATE TABLE gtin_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    ean TEXT NOT NULL,
    source_product_id UUID NOT NULL,
    evidence_type TEXT NOT NULL CHECK (evidence_type ~ '^[A-Z][A-Z0-9_.-]{1,63}$'),
    source_name TEXT NOT NULL CHECK (length(btrim(source_name)) BETWEEN 1 AND 200),
    artifact_filename TEXT NOT NULL CHECK (artifact_filename ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'),
    artifact_media_type TEXT NOT NULL CHECK (artifact_media_type IN ('application/pdf', 'image/png', 'image/jpeg')),
    artifact_sha256 CHAR(64) NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_bytes BYTEA NOT NULL CHECK (octet_length(artifact_bytes) BETWEEN 1 AND 10000000),
    claim_basis TEXT NOT NULL CHECK (length(btrim(claim_basis)) BETWEEN 20 AND 2000),
    validity_kind TEXT NOT NULL CHECK (validity_kind IN ('EXPIRING', 'PERPETUAL')),
    expires_at TIMESTAMPTZ,
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'APPROVED', 'REJECTED', 'QUARANTINED')),
    submitted_by TEXT NOT NULL CHECK (length(btrim(submitted_by)) BETWEEN 1 AND 200),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_basis TEXT,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    CONSTRAINT uq_gtin_evidence_scope_identity UNIQUE (id, account, data_scope),
    CONSTRAINT uq_gtin_evidence_artifact_product UNIQUE (ean, account, source_product_id, artifact_sha256),
    CONSTRAINT fk_gtin_evidence_pool_item
        FOREIGN KEY (ean, account, data_scope)
        REFERENCES ean_pool (ean, account, data_scope)
        ON DELETE RESTRICT NOT VALID,
    CONSTRAINT fk_gtin_evidence_source_product
        FOREIGN KEY (source_product_id, account, data_scope)
        REFERENCES source_products (id, account, data_scope)
        ON DELETE RESTRICT NOT VALID,
    CONSTRAINT ck_gtin_evidence_validity
        CHECK ((validity_kind = 'PERPETUAL' AND expires_at IS NULL)
            OR (validity_kind = 'EXPIRING' AND expires_at IS NOT NULL)) NOT VALID,
    CONSTRAINT ck_gtin_evidence_review_state
        CHECK ((review_status = 'PENDING' AND reviewed_by IS NULL AND reviewed_at IS NULL AND review_basis IS NULL)
            OR (review_status <> 'PENDING' AND reviewed_by IS NOT NULL
                AND length(btrim(reviewed_by)) BETWEEN 1 AND 200 AND reviewed_at IS NOT NULL
                AND review_basis IS NOT NULL
                AND length(btrim(review_basis)) BETWEEN 20 AND 2000)) NOT VALID
);

CREATE INDEX ix_gtin_evidence_account_binding
    ON gtin_evidence (account, ean, source_product_id, review_status, expires_at);

CREATE INDEX ix_gtin_evidence_submitted
    ON gtin_evidence (account, submitted_at DESC, id DESC);

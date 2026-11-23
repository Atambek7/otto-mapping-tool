CREATE TABLE restart_product_edits (
    account TEXT NOT NULL CHECK (account IN ('JV', 'XL')),
    data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK (data_scope = 'PRODUCTION'),
    candidate_episode_id UUID NOT NULL,
    title TEXT NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 500),
    category TEXT NOT NULL CHECK (length(category) <= 200),
    attributes_json JSONB NOT NULL CHECK (jsonb_typeof(attributes_json) = 'object'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account, candidate_episode_id),
    FOREIGN KEY (candidate_episode_id, account, data_scope)
        REFERENCES candidate_episodes (id, account, data_scope) ON DELETE RESTRICT
);

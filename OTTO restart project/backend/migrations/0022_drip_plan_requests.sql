-- Fixed selection manifests for asynchronous planning, referencing the existing registry.
CREATE TABLE restart_plan_requests (
 id UUID PRIMARY KEY, account TEXT NOT NULL CHECK(account IN ('JV','XL')),
 data_scope TEXT NOT NULL DEFAULT 'PRODUCTION' CHECK(data_scope='PRODUCTION'),
 name TEXT NOT NULL, start_date DATE NOT NULL, daily_time TIME NOT NULL, timezone_name TEXT NOT NULL,
 total_days INTEGER NOT NULL CHECK(total_days BETWEEN 1 AND 30), created_by TEXT NOT NULL,
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 50000), prepared_count INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','BUILDING','COMPLETED','FAILED')),
 last_error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(id,account,data_scope)
);
CREATE TABLE restart_plan_request_items (
 request_id UUID NOT NULL, account TEXT NOT NULL, data_scope TEXT NOT NULL DEFAULT 'PRODUCTION',
 candidate_episode_id UUID NOT NULL, row_number INTEGER NOT NULL, operation_id UUID,
 PRIMARY KEY(request_id,row_number), UNIQUE(request_id,candidate_episode_id),
 FOREIGN KEY(request_id,account,data_scope) REFERENCES restart_plan_requests(id,account,data_scope),
 FOREIGN KEY(candidate_episode_id,account,data_scope) REFERENCES candidate_episodes(id,account,data_scope),
 FOREIGN KEY(operation_id,account,data_scope) REFERENCES restart_operations(id,account,data_scope)
);
ALTER TABLE restart_batches ADD COLUMN plan_request_id UUID,
 ADD CONSTRAINT fk_batch_plan_request FOREIGN KEY(plan_request_id,account,data_scope) REFERENCES restart_plan_requests(id,account,data_scope);

CREATE UNIQUE INDEX uq_restart_active_operation ON restart_items(account,operation_id) WHERE data_scope='PRODUCTION' AND status NOT IN ('FAILED','CANCELLED','PUBLISHED');

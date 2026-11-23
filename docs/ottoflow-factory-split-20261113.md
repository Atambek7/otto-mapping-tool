# NEW_OTTO_2 / NEW_OTTO_3 split — deployed 2026-11-13

User explicitly authorized code transfer, backup, live application and verification.

| Account | NEW_OTTO_3 | Visible unique products | Excluded EANs | NEW_OTTO_2 | Preserved products |
|---|---|---:|---:|---|---:|
| JV | 504487 | 1474 | 1057 | 504466 | 1057 |
| XL | 483563 | 1475 | 1058 | 483554 | 1058 |

## Application

- Transferred the isolated change through a clean three-way merge into the existing dirty checkout; preserved prior application edits and Git index. No commit, push, or Git branch merge.
- Gracefully stopped beat, worker and API after confirming zero active/processing work; retained worker queue configuration. Deployed the newly built frontend and restarted API/worker/beat. No force termination, manual publish, retry-all, or plan recreation.
- Explicitly upgraded Alembic `0006_initial_availability` → `0007_factory_product_exclusions`. The new tables were not created by application startup.
- Applied batches `jv-504487-3f9e974fe6279412` and `xl-483563-adbc9ae8e308760e` with account-scoped audited exclusions. Source ownership was already correct in both accounts; **zero mapping rows required reassignment**.
- Temporarily paused XL plan #92 for application and restored its original ACTIVE state. JV plan #90 stayed ACTIVE; XL plan #91 stayed CANCELLED. Schedules, memberships and results were preserved.

## Evidence

- Full custom PostgreSQL backup: `/home/atambek/.local/share/ottoflow/backups/factory-split-20261113-194113/ottoflow-pre-split.dump` (19,317,100 bytes). Restored successfully in disposable PostgreSQL 15 without network access.
- Applied the actual change to the restored backup first. Both restored-backup rehearsal and live before/after snapshots proved exact preservation of **5064 mappings, 90925 attributes, 3173 queue items and three plan definitions** (plan `updated_at` excluded to allow the explicit pause/resume). Snapshots are stored privately in the same backup directory.
- **10 PostgreSQL tests passed** after adding support for already-canonical XL mappings. Covered migration upgrade/repeat/downgrade/re-upgrade and refusal of destructive downgrade; manifest/account/factory guards; full apply/rollback; conflict rollback; preserved canonical ownership.
- Merged frontend production Docker build passed. API health and frontend page returned HTTP 200. All four authenticated policy requests through the deployed frontend proxy returned expected results; worker ping returned pong, beat resumed its existing schedule, and its first publish/poll jobs returned idle.
- Enumerated every page through the deployed products endpoint with `sync_db=0`. Verified exact visible counts above, absence of all excluded EANs in NEW_OTTO_3, and presence of the full matching EAN set in NEW_OTTO_2. Original source pagination totals and offsets remain intact, including pages containing only excluded products.
- Snapshot comparison after API startup and read verification still matched the baseline exactly. No OTTO write was invoked by this correction. Normal pre-existing scheduling resumed afterward.

## Recovery

Use the reviewed `backend/scripts/manage_factory_exclusions.py rollback` with the exact manifest, expected SHA-256, matching batch confirmation, and explicit actor. Pause/drain plan #92 before XL rollback. Normal rollback deactivates only the selected batch, preserves audit/history, and restores only ownership rows actually changed by that batch (none in this application). Do not downgrade the non-empty audit schema or restore whole product rows. Source-file backups and the prior frontend image tag `ottoflow-before-factory-split-frontend:20261113` are retained for code rollback.

Verification scope: concrete production data/API/proxy/runtime checks above; no claim of a full legacy CI suite or visual browser acceptance. Future EAN overlaps need a separately reviewed manifest.

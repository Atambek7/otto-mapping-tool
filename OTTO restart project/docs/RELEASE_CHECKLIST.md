# OttoRestart — offline release checklist

This checklist records code-level verification only. It does not approve deployment, a live API call, a working-database migration, publication, workers, or schedule activation.

## Current fail-closed preparation status — 2026-11-10

- `POST /operations/{id}/prepare` returns `409 OTTO_TARGET_PAYLOAD_NOT_BUILT` before database/source lookups, OTTO/Aftercool reads, or EAN reservation while `TARGET_PAYLOAD_BUILDER_READY` is false.
- The payload builder is not implemented. The official OTTO product contract and category-specific rules require mandatory fields whose complete account-scoped source mapping and validation have not been established. Keep QC/approval and product publication closed.
- This status update involved no GPT, OTTO, or Aftercool account request, database access, migration, service/container start, worker, Beat, or test suite. The current backend files received syntax compilation only. The historical results below describe prior isolated runs and are not rerun evidence for this tranche.

## Offline verification

The runner must have no egress to OTTO, Aftercool, OpenAI, or other external services. Provide a disposable PostgreSQL 15 server reachable on loopback and a base database name containing `test`; the test suites create and drop uniquely named databases on that server. Do not point `OTTORESTART_TEST_DATABASE_URL` at the application or migration database.

```bash
cd "OTTO restart project"
OTTORESTART_TEST_DATABASE_URL='postgresql+psycopg://restart_test:restart_test@127.0.0.1:5432/otto_restart_test' \
  ./scripts/verify_offline_release.sh
```

The script overrides application/migration DSNs with an unreachable loopback URL, disables external and write flags, compiles backend Python, runs the backend `unittest` suite, typechecks the frontend, and runs its Node tests. It does not start services or migrate the application database. PostgreSQL migration tests run only against per-test databases created from the explicitly validated disposable server. The runtime image's `npm ci` path is a separate packaging check; use the lockfile and a no-egress runner with a populated package cache.

Compose interpolation can be checked without reading `.env` or starting services by supplying throwaway values and suppressing resolved output:

```bash
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
OTTORESTART_ALLOWED_ORIGINS=https://restart.example.invalid \
OTTORESTART_AUTH_USER=offline-check \
OTTORESTART_AUTH_PASSWORD=${OTTORESTART_AUTH_PASSWORD}
OTTORESTART_AUTH_SECRET=${OTTORESTART_AUTH_SECRET}
OTTORESTART_INTERNAL_AUTH_SECRET=${OTTORESTART_INTERNAL_AUTH_SECRET}
OTTORESTART_OPERATOR_ID=offline-check \
docker compose --env-file /dev/null config --quiet
```

## Schema and recovery

- The source tree includes migration `0017_discovery_routing_and_weekly_schedule.sql`; do not infer a running database head from source files. The selected active OttoRestart database was verified at schema head 0018 on 2026-11-10. Apply future migrations only through `backend/migrate.py` against an explicitly selected Restart database after a separate operational review.
- Migration `0018_restart_scope_indexes.sql` was preflighted at schema 0017: zero migration-blocking groups and zero rollback-blocking groups were reported. After explicit operator authorization it was applied to the selected active OttoRestart database; `python /app/migrate.py check` reports `0018/0018` with valid checksums. This does not substitute for disposable-schema upgrade/downgrade or concurrency tests. Five legacy JV RESERVED/USED EAN pool rows without reservation owners were reported and not changed by migration 0018.
- Never-submitted cancellation also fails closed when the operation has no `new_ean` but active lineage or a pool row still points to that operation. This source guard is not yet covered by a PostgreSQL integration result.
- Migration revisions are append-only. Down scripts exist for 0005–0016; they refuse rollback when their data would be lost. Revisions 0001–0004 have no reviewed down scripts. Do not downgrade a populated database; use a forward repair and restore only into a fresh isolated database during recovery rehearsal.
- API startup verifies migration head/checksums and does not apply DDL. The offline release script never invokes `backend/migrate.py` for the application DSN.
- Runtime flags remain off by default: OTTO/Aftercool/OpenAI reads, product/availability writes, Celery, and discovery scheduling. Redis/worker/dispatcher/Beat are opt-in profiles and are not started by verification. The new schedule requires operator-confirmed baseline and both global and per-account enablement.

## Evidence and known limits

- Backend full suite: **294 tests passed** in 106.952s with disposable PostgreSQL 15 and Redis 7 sharing a `--network none` namespace; Redis health check returned `PONG`. This included the S20–S22 downstream chain test. Celery remained disabled and no worker/dispatcher/Beat ran; transport tests use fakes. Application/migration DSNs pointed to an unreachable loopback port.
- Frontend: **35 Node tests passed**; `tsc --noEmit --incremental false` passed using the locally available matching Node dependency tree. A production Next.js build and standalone `/restart/login` smoke are recorded in `docs/S00_CHECKPOINT.md`; `npm ci` from the Restart Dockerfile still depends on an available locked-package cache.
- S20–S22 downstream chain is verified with fake clients from an explicit QC-approved immutable test fixture through product `202`, exact per-SKU task success, both availability actions, and exact-SKU `ONLINE`. No test client reaches a network.
- A single successful end-to-end production path from import through QC is not yet possible: S13 deliberately records `TARGET_SKU_UNVERIFIED`, `FULFILLMENT_TARGET_SCOPE_UNVERIFIED`, and `CATEGORY_SCHEMA_NOT_AVAILABLE_FROM_READ_ADAPTER`. The source-to-target SKU/productReference rule and authority for verified target mapping need a user decision. Do not invent those values in production code.
- An availability `202` has no confirmed task-result contract in this adapter and is fail-closed to review. A confirmed product-task `FAILED` result leaves the same operation/GTIN ready, but no audited technical requeue action currently re-enqueues that released Drip item. No automatic product POST retry is permitted for ambiguous outcomes.
- Poll deadlines/backoff and the S23 16-day coverage cap are code defaults requiring operational approval. Full target identity, category schema, permitted GTIN evidence types, production origin/secret provider, and retry policy remain review items.

## Separate operational gates

- **Current source-only discovery milestone:** 34 OTTO classifier/Aftercool adapter tests, 9 BFF/API client Node tests, frontend TypeScript check, and targeted Python compilation passed. The available host Python lacks the backend dependency set (`SQLAlchemy`, `python-dotenv`), and no disposable PostgreSQL test DSN is configured; PostgreSQL integration suites, including migration `0017`, were not run. No external API, migration, worker, or Beat was started.

- **S26:** limited live read pilot needs explicit permission for account, SKU/window, and credentials. No live read was performed by the offline release suite.
- **S27:** publish pilot needs separate explicit permission for exact operations/account and approved GTIN evidence. No product or availability writes were performed.
- **S28:** discovery schedule requires separate explicit permission after S23/S24/S25R evidence and a confirmed baseline. Beat and the 14-day schedule remain disabled.

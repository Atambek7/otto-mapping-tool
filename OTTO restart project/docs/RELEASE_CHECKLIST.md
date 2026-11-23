# OttoRestart — offline release checklist

This checklist records code-level verification only. It does not approve deployment, a live API call, a working-database migration, publication, workers, or schedule activation.

## Current release status — 2026-11-16

- Corrected four confirmed defects: Drip plan job failures now follow queue retry/terminal semantics and can resume from the persisted manifest; retrying failed items does not unpause a paused batch; category schema filtering no longer deletes operator attributes from the saved editor; and availability HTTP 202 remains accepted/pending until there is separate application evidence.
- Offline verification on this checkout: full backend pytest suite **423 passed, 1 private-fixture diagnostic skipped**; the final focused Drip suite, including committed-cohort recovery, **18 passed**. Frontend TypeScript passed and all **55 offline Node tests passed**. A clean temporary frontend copy installed dependencies from the lockfile and built successfully with Next.js **16.4.6**. Disposable PostgreSQL 15 migration round trip reached head 22, downgraded safely to 20, then upgraded back through 21 to 22.
- The dedicated OttoRestart CI workflow now runs backend pytest with PostgreSQL 15, frontend TypeScript, the offline Node regression suite, and a lockfile-based production build. The local offline release script runs pytest as well, so its backend test command matches CI.
- Synthetic planner/queue/database/UI-pagination scale verification completed on disposable PostgreSQL 15. With 61,000 synthetic XL candidates and 1,000 JV decoys, 1k/10k/50k requests completed with exact manifest, item, and preparation-job counts and no account leakage. The 50k case measured 44.9s preview, 45.3s synchronous request/manifest creation, 851.0s plan construction (103 jobs; longest job 18.6s), and 61.5s preparation-job fanout (104 jobs); peak process RSS was 318.1 MiB and database size 315.3 MB. In this fixture, the 50k plan produced four factory-period batches of 12,500 items each. The planner now commits one factory-period batch per job and resumes from the first unbuilt group. The manifest request remains a long synchronous transaction, and overall 50k plan construction remains slow; a single 50k-item factory-period cohort was not measured. Treat these as synthetic measurements, not a production SLA.
- No real OTTO, Aftercool, or OpenAI requests, product imports, product/availability writes, application-database migrations, worker/Beat activation, or deployment were performed for this verification.
- The submit and availability paths remain closed behind runtime flags. The separate business/GTIN approval required by `AGENTS.md` is not recorded here. Therefore successful OTTO submission, task processing, ONLINE confirmation, and initial availability are not verified and must not be represented as production-ready.

## Synthetic scale run — 2026-11-16

The benchmark used a disposable PostgreSQL 15 database, fake/local-only execution, and synthetic catalog rows. External reads/writes, workers, and live products were not used. The fixture held 61,000 XL candidate rows and 1,000 JV decoys; each plan selected 1,000, 10,000, or 50,000 XL candidates.

| Selected | Preview | Synchronous manifest request | Planner jobs / elapsed | Longest planner job | Preparation fanout | Peak RSS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 2.81 s | 1.28 s | 5 / 7.63 s | 3.54 s | 4 / 1.34 s | 318.1 MiB |
| 10,000 | 24.42 s | 3.50 s | 23 / 82.87 s | 6.17 s | 24 / 12.11 s | 318.1 MiB |
| 50,000 | 44.90 s | 45.26 s | 103 / 851.03 s | 18.60 s | 104 / 61.52 s | 318.1 MiB |

All three plans reached `COMPLETED`; manifest cursor, scheduled items, and pending per-item preparation jobs matched selected counts exactly. No JV candidate was included in XL. The request uses one transaction to persist the immutable manifest, so its 45.26 s duration is also the maximum observed transaction. Operation creation remains the dominant total planning cost. Finalization now creates one factory-period cohort per committed planner job; the fixture's largest cohort was 12,500 rows, so the 50,000-row single-cohort worst case remains unmeasured. The run verifies internal consistency and bounded memory on this machine only; it does not establish production latency, concurrent-worker behavior, provider throughput, AI/QC payload execution, OTTO processing, ONLINE, or availability confirmation.

## Offline verification

The runner must have no egress to OTTO, Aftercool, OpenAI, or other external services. Provide a disposable PostgreSQL 15 server reachable on loopback and a base database name containing `test`; the test suites create and drop uniquely named databases on that server. Do not point `OTTORESTART_TEST_DATABASE_URL` at the application or migration database.

```bash
cd "OTTO restart project"
OTTORESTART_TEST_DATABASE_URL='postgresql+psycopg://restart_test:restart_test@127.0.0.1:5432/otto_restart_test' \
  ./scripts/verify_offline_release.sh
```

The script overrides application/migration DSNs with an unreachable loopback URL, disables external and write flags, compiles backend Python, runs the backend `pytest` suite, typechecks the frontend, and runs its offline Node tests. It does not start services or migrate the application database. PostgreSQL migration tests run only against per-test databases created from the explicitly validated disposable server. The runtime image's `npm ci` path is a separate packaging check; use the lockfile and a no-egress runner with a populated package cache.

Compose interpolation can be checked without reading `.env` or starting services by supplying throwaway values and suppressing resolved output:

```bash
POSTGRES_PASSWORD=offline_check_only \
OTTORESTART_ALLOWED_ORIGINS=https://restart.example.invalid \
OTTORESTART_AUTH_USER=offline-check \
OTTORESTART_AUTH_PASSWORD=offline-check \
OTTORESTART_AUTH_SECRET=offline-check-secret \
OTTORESTART_INTERNAL_AUTH_SECRET=offline-check-internal-secret \
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

## Historical evidence and known limits

The test counts below are earlier checkpoints and are superseded by the 2026-11-16 results at the top of this checklist.

- Backend full suite: **294 tests passed** in 106.952s with disposable PostgreSQL 15 and Redis 7 sharing a `--network none` namespace; Redis health check returned `PONG`. This included the S20–S22 downstream chain test. Celery remained disabled and no worker/dispatcher/Beat ran; transport tests use fakes. Application/migration DSNs pointed to an unreachable loopback port.
- Frontend: **35 Node tests passed**; `tsc --noEmit --incremental false` passed using the locally available matching Node dependency tree. A production Next.js build and standalone `/restart/login` smoke are recorded in `docs/S00_CHECKPOINT.md`; `npm ci` from the Restart Dockerfile still depends on an available locked-package cache.
- S20–S22 downstream chain is verified with fake clients from an explicit QC-approved immutable test fixture through product `202`, exact per-SKU task success, both availability actions, and exact-SKU `ONLINE`. No test client reaches a network.
- A single successful end-to-end production path from import through QC is not yet possible: S13 deliberately records `TARGET_SKU_UNVERIFIED`, `FULFILLMENT_TARGET_SCOPE_UNVERIFIED`, and `CATEGORY_SCHEMA_NOT_AVAILABLE_FROM_READ_ADAPTER`. The source-to-target SKU/productReference rule and authority for verified target mapping need a user decision. Do not invent those values in production code.
- An availability `202` has no confirmed task-result contract in this adapter and is fail-closed to review. A confirmed product-task `FAILED` result leaves the same operation/GTIN ready, but no audited technical requeue action currently re-enqueues that released Drip item. No automatic product POST retry is permitted for ambiguous outcomes.
- Poll deadlines/backoff and the S23 16-day coverage cap are code defaults requiring operational approval. Full target identity, category schema, permitted GTIN evidence types, production origin/secret provider, and retry policy remain review items.

## Separate operational gates

- **Historical source-only discovery milestone:** at that earlier checkpoint, 34 OTTO classifier/Aftercool adapter tests, 9 BFF/API client Node tests, frontend TypeScript check, and targeted Python compilation passed. PostgreSQL integration suites were not run at that time. This is not the current verification result above.

- **S26:** limited live read pilot needs explicit permission for account, SKU/window, and credentials. No live read was performed by the offline release suite.
- **S27:** publish pilot needs separate explicit permission for exact operations/account and approved GTIN evidence. No product or availability writes were performed.
- **S28:** discovery schedule requires separate explicit permission after S23/S24/S25R evidence and a confirmed baseline. Beat and the 14-day schedule remain disabled.

# Phase 5 — queue monitoring in demo mode

Entry point: `/restart/queue`. The queue is an in-memory part of each browser demo session and is partitioned by JV/XL.

## Implemented scope

- Shows only approved Phase 4 demo cards and account-scoped demo queue tasks.
- Adds approved card snapshots to a local dry-run queue after per-ID and version checks. Repeating an enqueue for the same approved payload returns the existing task.
- Supports independent pause/resume of dry-run processing and stop/start of task intake for each account.
- Runs a manual local preflight for a queued task, checking the immutable payload approval and current simulated standard price. Content or price changes block the stale task; changed prices invalidate the approval.
- Supports cancellation of tasks that have not started and displays results, attempts, price snapshots, task version and UTC timestamps.
- Publication is always disabled. A successful dry-run only records a local result; it makes no OTTO/Aftercool request and cannot set an online status.

## Practical limits

There is no background worker, durable queue, distributed lock, authentication/RBAC, external rate limiter, OTTO reconciliation, submit endpoint, or production publish path. Queue controls only change the selected demo account in the current browser session. State expires after one hour of inactivity or a Next.js process restart. The application uses synthetic fixtures only.

The API contract is `contracts/otto-restart-demo-queue-v1.yaml`; endpoints remain under the same-origin `/restart/api/demo/` namespace and require the existing session, origin check, JSON request and demo header for writes.

## Verification

- TypeScript strict typecheck.
- `tests/demo-queue.test.cjs`: approved-only admission, account isolation, idempotency, pause/resume, intake stop/start, version/price invalidation, cancellation and bounded request validation.
- Local HTTP checks for queue reads/writes, same-origin guard and the disabled publication path.

No actual goods, OTTO credentials, Aftercool, SQLite records or OttoFlow services are used.

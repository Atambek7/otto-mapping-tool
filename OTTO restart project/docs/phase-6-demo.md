# Phase 6 — OttoRestart demo prototype

The remaining OttoRestart product screens use an isolated synthetic-data API. A user can explore the end-to-end UI without providing goods or EAN files.

## Included

- Dashboard metrics and attention list computed from demo review fixtures and the session queue. `ONLINE confirmed` is fixed at zero.
- Account-scoped demo batches, search, creation, batch detail tabs, and a read-only preview before the in-memory create action.
- Demo EAN pool that reflects review-stage temporary reservations. Its values are `DEMO-*` strings and never GS1 identifiers.
- Session event history for seeded fixtures, review actions, demo batch/pool actions, and settings changes.
- Account-scoped demo settings for default period, synthetic batch size, and attribute validation mode. The publish switch is hard-coded false and is not writable.
- Header search routes to synthetic review fixtures; the history and help controls lead to OttoRestart-only pages.

## Data boundaries

- State is stored in the Next.js process memory and scoped to an HTTP-only demo session cookie. It expires after one hour idle and resets after a process restart.
- No product file upload is enabled in the current UI. Batch creation produces synthetic rows. The working EAN pool and Phase 3 SQLite database are not read or written.
- No live account credentials, OTTO/Aftercool endpoints, AI services, OttoFlow stores, or OttoFlow queues are accessed.
- Queue actions only validate immutable, approved demo snapshots locally. They never send requests and cannot set ONLINE.
- Synthetic price cases illustrate the agreed JV −10 EUR and XL −15 EUR arithmetic; they are not price quotes.

## Contract and verification

- Demo workspace routes: `contracts/otto-restart-demo-workspace-v1.yaml`.
- Review and queue demo routes are documented in `contracts/otto-restart-demo-review-v1.yaml` and `contracts/otto-restart-demo-queue-v1.yaml`.
- Domain tests: `tests/demo-workspace.test.cjs`, `tests/review-demo.test.cjs`, and `tests/demo-queue.test.cjs`.
- Type checking: `npx tsc --noEmit --incremental false`.
- The prototype is not a production pilot. Real file import, authentication, API authorization, GTIN/OTTO policy approval, a dedicated durable queue, and any permitted publish path remain outside this mock-only implementation.

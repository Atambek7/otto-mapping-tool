# OttoRestart

Standalone OttoRestart workspace for the Restart 3/12M flow. The public base path is `/restart`. **The current UI is a demo prototype and uses only synthetic data in an in-memory browser session.** It does not load actual goods, call OTTO/Aftercool, write to Phase 3 SQLite, or publish anything. The separate FastAPI/SQLite prototype remains available for development experiments, but the current UI does not call it.

## API contract (design only)

- OpenAPI document: `contracts/otto-restart-api-v1.yaml`.
- TypeScript DTOs and client: `features/api/`.
- The client requires an explicit `NEXT_PUBLIC_RESTART_API_URL` pointing to the future isolated OttoRestart API origin. It does not fall back to OttoFlow's `/api` routes.
- Write requests require a CSRF token provider; batch creation also requires an idempotency key.
- The contract covers overview, batch lists/create/Dry-Run, file preview, EAN-pool import/list, and queue reads. No publication endpoint is included.
- `components/api-state.tsx` provides loading, empty, unavailable, error/retry, and permission-denied UI states.
- The API accepts `.xlsx`, `.csv`, and `.txt` input up to 20 MB / 10,000 EAN rows. It validates format and GTIN check digit locally; Dry-Run has no database writes. New EAN import writes only to the OttoRestart pool and marks entries `AVAILABLE`; it does not reserve them.
- The backend is explicitly development-only: it has no user/RBAC authentication, listens on loopback, and uses an isolated SQLite database. Do not expose it publicly or connect it to production until authentication and deployment are designed.
- No external OTTO or Aftercool requests are made, and publication remains disabled.

## Local development

All main screens are usable without supplying test products or starting the local FastAPI service. Open `/restart/review` to try the five built-in synthetic scenarios, or use `/restart/batches` to create a synthetic batch after its no-write preview. The demo EAN pool uses `DEMO-*` strings that are not valid GTINs. Changes live in the browser's server-side session memory and reset after one hour idle or an app restart.

- `/restart` — synthetic dashboard, attention scenarios, recent demo batches and events.
- `/restart/batches` — search/list fixtures and create a synthetic batch with a Dry-Run preview.
- `/restart/batches/demo-jv` / `/restart/batches/demo-xl` — review and preparation flow for five demo scenarios.
- `/restart/ean-pool` — synthetic pool view; the button adds demo codes only.
- `/restart/review` — simulated preparation, standard-price scenarios, validation and approval.
- `/restart/queue` — session-scoped queue with local dry-run checks only.
- `/restart/history` and `/restart/settings` — mock audit events and account-scoped demo settings.

Every page is marked DEMO. Publish remains disabled and ONLINE confirmations remain zero. See [Phase 6 demo prototype](docs/phase-6-demo.md) and [workspace API contract](contracts/otto-restart-demo-workspace-v1.yaml).

### Phase 4 — preparation and review demo

Open `/restart/review` on the existing Next.js preview, or `/restart/batches/demo-jv` / `/restart/batches/demo-xl`.
For the current SSH port forward this is usually `http://localhost:3002/restart/review`.
This demo needs only Next.js: no FastAPI service or API environment variable is needed.

- The DEMO banner identifies all fixtures, mocked prices and local AI title suggestions. The 70-character title limit and attribute rules are illustrative demo rules, not verified OTTO category requirements.
- Tabs cover candidates, preparation, approval, publication (disabled), and audit history. The comparison drawer supports edits, local illustration preview, validation, price refresh, and approval.
- Price is read from the simulated old SKU standardPrice adapter for the selected account, with integer-cent arithmetic: JV minus 1,000 cents, XL minus 1,500 cents. There is no Excel, Aftercool, or sale-price fallback.
- Supplied `DEMO-*` identifiers are deliberately not real GTINs. Reservation is confined to the session's demo pool. Repeated preparation keeps the reservation and never subtracts the discount twice.
- Server version checks reject stale edits. Approval re-reads the simulated price, captures an immutable payload and SHA-256 hash, and is invalidated by material edits or changed prices. The drawer includes a price-change scenario to demonstrate reapproval.
- Each browser gets a separate HTTP-only session in server memory. It expires after one hour of inactivity and resets on process restart. This is a single-process preview, not durable production storage.
- The API is `/restart/api/demo/*`; its separate contract is `contracts/otto-restart-demo-review-v1.yaml`. It does not use Phase 3's SQLite database, real batches, the working EAN pool, or OttoFlow services.
- Live source adapters, real AI, authenticated permissions and actual OTTO category/GTIN checks remain disconnected. `publishAllowed` is always `false`; no submission endpoint exists.

### Phase 5 — isolated queue demo

Open `/restart/queue`. It lists approved demo cards for the selected JV/XL account, lets you add immutable snapshots to a local dry-run queue, pause/resume processing, stop/start task intake, and cancel tasks before they run. A dry-run rechecks the approved version and simulated standard price; stale work is blocked and changed prices require reapproval. Successful completion records only a local demo result.

- Queue state is in-memory, browser-session scoped, and partitioned by account. There is no background worker or durable queue.
- Publication is disabled. Queue tasks do not call OTTO or Aftercool and cannot create online statuses.
- Contract: `contracts/otto-restart-demo-queue-v1.yaml`; details and limits: `docs/phase-5-demo.md`.

Validation: `tsc --noEmit --incremental false`. Phase 4 domain tests are in `tests/review-demo.test.cjs`; Phase 5 queue invariants are in `tests/demo-queue.test.cjs` and run against `demo-queue-service.ts` plus its compiled Phase 4 service dependency.

Requires Node.js 20.9 or newer.

```bash
npm ci
npm run dev
```

Open [http://localhost:3001/restart](http://localhost:3001/restart). The development server uses port 3001 so it does not collide with OttoFlow's port 3000.

### Local API

Use a separate terminal. From this directory, create a Python virtual environment, install the isolated service dependencies, then run:

```bash
cd "OTTO restart project"
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn app:app --host 127.0.0.1 --port 3301
```

Forward port 3301 in the remote IDE if the frontend is opened through a forwarded browser port. Port 3101 is occupied by another application on this host. Copy `.env.example` to `.env.local` in this app directory before starting or restarting Next.js so the browser points at the isolated service. Use the same hostname (`localhost`) for the frontend and API to keep the local CSRF cookie scoped consistently.

To create a production build, run `npm run build`, then `npm start`. Serving this application at `/restart` on the production domain still requires a reverse-proxy/deployment route; that integration has not been added.

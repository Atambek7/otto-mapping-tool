# Phase 4 — preparation and review in demo mode

Entry points: `/restart/review`, `/restart/batches/demo-jv`, `/restart/batches/demo-xl`.
The existing Next.js preview serves the UI and `/restart/api/demo/*`; no extra service is needed.

## Implemented scope

- Account-scoped, browser-scoped fixtures, supplied non-GTIN `DEMO-*` identifier pools, and deterministic simulated old-SKU price/AI adapters.
- Candidate/preparation/approval/publication/history views, server pagination/search/status filters, explicit page-scoped selection and per-ID server checks for bulk approval.
- Two-column comparison drawer with keyboard focus trapping/Escape via native modal dialog, content edits, field differences, local image preview, validation issues, read-only identities and price, timestamp in UTC, and an audit timeline.
- Integer-cent price snapshots, JV −1,000 cents / XL −1,500 cents, version locking, one reservation per prepared item, no duplicate discount on retry, immutable approved payload and SHA-256 fingerprint.
- Fresh price read on approval. Price changes and content edits invalidate approval. A simulated +10 € source-price action demonstrates reapproval.
- No real data imports, Phase 3 database writes, working-pool reservations, external API/AI requests, publication, or ONLINE status changes. Every item has `mode=DEMO`, `publishAllowed=false` and demo-only approval.

## Practical limits

This implements the Phase 4 workflow with fixtures. Real source/AI adapters, actual OTTO category and GTIN rules, authenticated access and durable real preparation/approval storage still need integration. The demo's title limit and attribute schema are illustrative and explicitly labelled as such. State is kept in one Next.js process for up to one hour of inactivity; restarting it resets all demo sessions. This design is intended for preview, not distributed production deployment.

## Files created

- `features/review/types.ts`
- `features/review/demo-service.ts`
- `features/review/client.ts`
- `features/state/restart-account.tsx`
- `app/api/demo/[...segments]/route.ts`
- `components/restart-review/review-workspace.tsx`
- `components/restart-review/item-drawer.tsx`
- `components/restart-review/review-workspace.css`
- `public/demo-chair.svg`
- `contracts/otto-restart-demo-review-v1.yaml`
- `tests/review-demo.test.cjs`
- `docs/phase-4-demo.md`

## OttoRestart files updated

- `app/review/page.tsx` — render the review workspace.
- `app/batches/[batchId]/page.tsx` — support separate demo batch IDs; real batch view remains distinct.
- `components/account-switcher.tsx`, `components/restart-shell.tsx` — use one OttoRestart-only account context.
- `components/api-connection-status.tsx`, `app/globals.css` — distinguish local demo service from the Phase 3 import API.
- `README.md`, `AGENTS.md` — document the demo boundary and operation.
- `.env.example` — correct the independent Phase 3 backend example to free port 3301; port 3101 belongs to another existing application. No running service or actual environment file was changed.

No file outside the OttoRestart workspace was edited. The whole workspace was already untracked before this change, so `git diff --name-status` alone does not enumerate these additions; inspect `git status --untracked-files=all` as well.

## Verification

- TypeScript strict typecheck.
- Production build in a disposable copy, leaving the running preview's `.next` directory intact.
- Ten domain tests for price arithmetic, reservations/retries, blocked scenarios, account isolation, stale versions, validation, immutable approvals, price reapproval, forbidden edits and fixture-session isolation.
- Local HTTP checks on the isolated Next.js preview for prepare → validate → approve, version conflicts, account isolation, origin protection and page/illustration responses.

These checks use synthetic demo sessions only. They do not verify real OTTO/Aftercool behavior or full browser end-to-end interaction.

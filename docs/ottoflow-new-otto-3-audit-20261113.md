# NEW_OTTO_3 JV / XL — functional audit, 2026-11-13

Scope: OttoFlow factory JV 504487 and XL 483563 only. Application fixes, live product edits, live plan edits, deployments and external AI/OTTO writes were not performed. Operational data changed by the operator during the investigation was observed read-only.

## How checked

- Copied current backend code without environment files into `/tmp/ottoflow-new3-audit-20261113/backend`; verified all copied Python application files still match the primary checkout at completion.
- Restored the saved production backup into disposable PostgreSQL 15 with `--network none`. Applied existing migration/exclusion manifests to that copy; cloned transactions and synthetic plans never reached production.
- Ran **119 backend checks: 109 passed, 10 exposed the failures below**. These are targeted audit cases, not the full CI suite. Current production code was executed without applying a fix. Fake AI, source/twin lookup and broker dispatch were used where necessary; real provider contracts and generated content quality were not verified.
- Ran six Playwright scenarios against the deployed frontend bundle, intercepting every API request and blocking external image requests. Verified both JV and XL normal scoped drawer saves, handling of a fully hidden source page, safe blocking when policy is unavailable, and reproduction of N/A/schema failure.
- Read-only production evidence: selected EAN 4072102221917 now has category_id N/A, status Error. Current JV 504487 has 1473 empty categories and one invalid N/A; XL 483563 has 1449 valid and 26 empty categories. This is an observation at audit time, not a change made by the audit.

## Confirmed findings

### P1 — New regression: creating Drip Feed fails

`backend/routers/publish_pipeline.py:create_pipeline_plan` calls `lock_and_require_factory_eans_allowed` without importing it in that function or the module. The import exists only inside preview_pipeline, whose local name is unavailable to create. Both JV and XL eligible create requests reproducibly raise NameError; preview succeeds. The new guard call was introduced by the factory-exclusion change. Existing tests tested policies/migrations but did not execute an eligible plan-creation route. Fix the import and add that route case before deployment.

### P1 — Invalid AI/category IDs accepted and persisted

`services/automapper.py:predict_category` returns an arbitrary nonempty model category_id when taxonomy lookup fails. `/mapping/predict-category` returns HTTP 200 for N/A. `/mapping/category` also accepts and stores N/A directly. Bulk AI persistence lacks a corresponding taxonomy check. Real logs show a successful /mapping/auto-map request followed by schema requests for N/A; the selected production mapping now contains N/A. The AI provider is not called by this audit; malformed results were injected to prove the unchecked application contract. The prediction code predates factory exclusions, and its source was not changed by that work.

Reject unresolved or unknown IDs at prediction and every category write boundary. Treat an absent model category as a partial result; do not persist it as a category. Do not guess a replacement category for the existing affected product.

### P1/P2 — Schema failure masquerades as infinite loading; save remains enabled

ProductDrawer fetches a schema, leaves schema=[] on HTTP 404, then renders “Загрузка схемы атрибутов...” whenever category is nonempty and schema.length===0. It does not use a failure state in that branch. Playwright reproduced this for both factories; Save Mapping remained enabled with N/A. Present failed/unknown/empty/loading states explicitly and block saving an invalid category. The affected logic existed before the exclusion-context edits.

### P2 — Partial synchronization reported as full success

For EAN 4072102221917, Aftercool Produktart Heim-Fitnessstudio and its title do not match the current category resolver. Prior read-only investigation showed 11 stored attributes, null category, status Error and missing_attributes=[Category is not selected]. sync_product_data treats nonempty attributes as success even with no category; the frontend checks “no category” before “Error”, concealing the validation failure from the error counter. All 1474 remaining JV mappings already had no category in the pre-deployment snapshot. Categorized NEW_OTTO_2 mappings were excluded from NEW_OTTO_3, not cleared.

### P2 — Added duplicate-conflict handling hides one allowed product

Source JV catalog has 2533 rows / 2531 unique EANs. After applying the 1057 exclusions, 1474 allowed unique EANs remain. EAN 4072102209861 has two conflicting source products: IDs 84951774 / 84942289 (oval table versus round table plus four chairs). filterAndDedupeFactoryProducts removes that EAN entirely, leaving 1473 visible and only a toast. This is behavior introduced by the exclusion UI change. Preserve a visible review entry for the conflicting identity and block ambiguous actions; do not choose a payload by response order.

### P2/P3 — Existing fallback branches fail because logger is undefined

`routers/mapping.py` references logger without defining it. Both accounts reproduce NameError when twin lookup fails during transfer. A malformed processing-status since parameter triggers the same failure. Matched twin transfers and valid processing-status requests passed. These references were already present before the split. Initialize the module logger and return the intended source/reconciliation error.

### Additional observation — XL hydration warning

All three XL browser scenarios emitted recoverable React #418 hydration warnings; scoped save still worked. useAccount initializes to JV on the server and reads XL from localStorage on the client. This is consistent with an initial render mismatch and predates the exclusion changes. Not established as a product-data loss issue or a regression specific to NEW_OTTO_3.

## Coverage matrix

| Function | Verification / result |
|---|---|
| Account/factory policy and product read | Both scopes passed; missing policy blocks actions |
| Full/hidden-page catalog pagination | Browser continued from an empty filtered page to later allowed products |
| Fresh and cached product sync | Both scopes filter excluded EANs before DB writes; passed |
| Valid category / schema / attributes / title / price | HTTP writes against restored data passed in both scopes |
| Bulk category / attributes / defaults / Mass Edit schema / .99 | Passed in both scopes |
| AI category / attributes / bulk / title persistence | Valid fake outputs passed; unknown category rejection failed; no real provider calls |
| OKB sync persistence | Handler with fake source result passed; real selected-product failure analyzed from saved state/logs |
| Categories/attributes export, async queue args, DB/ZIP worker output | Passed; export worker generated a one-product file per scope with image reads mocked |
| CSV processing and SQLite upload/import | Passed; excluded rows skipped with reasons/counts |
| Account transfer | Matched fake twins passed, absent twin blocked, source-error fallback failed due undefined logger |
| Drip preview / create / queue / pause / resume / cancel / trigger | Preview and controls passed on synthetic plans; create failed; trigger dispatch was mocked |
| Excluded / mixed explicit commands | Rejected before mutation in both scopes |
| NEW_OTTO_2 protection | Mapping and attribute hashes unchanged through the core mutation matrix |

Evidence: `docs/audits/new-otto-3-20261113/`. Temporary runners and read fixtures are under `/tmp/ottoflow-new3-audit-20261113/`; test PostgreSQL was removed after completion. No claim of complete real OTTO publishing, real AI correctness, whole-project CI coverage or production write testing.

## Recommended repair order

1. Fix the confirmed new Drip creation regression and invalid category acceptance; reject N/A throughout category persistence and expose schema errors instead of endless loading. Add eligible plan creation and invalid-category browser/API cases.
2. Distinguish partial synchronization and validation failures; retain conflicted EANs in visible review state. Fix undefined logger fallback paths.
3. Verify both NEW_OTTO_3 workflows on isolated data, deploy the bounded correction, then inspect existing invalid mappings separately without fabricating categories. Keep NEW_OTTO_2 exclusions active.

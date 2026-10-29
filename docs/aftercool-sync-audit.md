# Aftercool synchronization audit — 2026-10-21

Scope: OttoFlow catalog → local mappings, single/bulk category/attribute sync,
OTTO publisher enrichment, scheduled availability, frontend price presentation.
No OTTO product writes, data repairs, retries, or service activation are part of validation.

## Price contract

- A manual `ProductMapping.price` (`has_manual_price=true`) wins.
- Otherwise use only a positive finite Aftercool `Startpreis`. `SofortkaufenPreis`
  is ignored, including when Startpreis is missing/invalid (user clarification, 2026-10-21).
- `price_af` tracks `Startpreis`; it is not proof of the OTTO selling price.
- Missing/invalid source prices do not clear a saved price.
- European thousands/decimal separators, whitespace including NBSP, plain decimal,
  and numeric values are supported consistently. Reject nonfinite/partial values.
- Catalog/single-item refresh updates local prices. It does **not** send prices to OTTO.
- The periodic task sends OTTO quantities and refreshes local prices, only for
  `Published` mappings. It does **not** send OTTO selling prices. Error/Ready and
  "Exists in OTTO" observations are not silently added to this external write set.
- The publisher includes the effective local/manual price when explicitly invoked.
  Automatically republishing old cards to correct a price is not appropriate.

## Confirmed defects and corrections

1. Periodic availability constructed `OttoMarketClient` with unsupported kwargs;
   fixed to the supported constructor. This previously prevented normal execution.
2. Availability fetched one nominal 5000-row page. It now pages at <=500 rows,
   retains only the bounded target set, and fails without applying partial results.
3. Page iterator silently ended on HTTP failure/page cap. It now raises and validates
   envelopes/counts, so metadata sync reports `partial_error` after partial work.
4. Missing/failed Aftercool fetch could turn into default stock=20. Missing products
   now skip external writes; source failure skips the batch. Existing default stock
   policy for a *found* item without a stock field remains.
5. Metadata page HTTP success preceded its DB background write. Requested DB sync
   now finishes before returning, so UI mapping reload sees committed prices.
6. Explicit category/attribute sync trusted a cached client row, never saved prices,
   preferred OKB values, and could silently switch JV to XL when an EAN existed in XL.
   It now fetches fresh exact-EAN/factory data, preserves the selected account,
   prioritizes Aftercool category/attributes, uses OKB for compatible gaps, and saves
   Aftercool prices while retaining manual prices and AI titles. Failures do not
   fall back to the stale client row. Revalidation updates missing attributes.
7. Frontend parseFloat misread values such as `1.399,99`; shared parsing now agrees
   with the backend, and automatic prices use only Startpreis.
8. Failed catalog pages were swallowed and partial results cached as complete.
   Failures now surface, and the advertised total is retained for incomplete caches.
9. Availability dry-run mutated in-session mappings. It now only calculates changes;
   no OTTO client calls, DB writes/commits, or log inserts occur in dry-run.
10. Catalog metadata refresh overwrote AI titles; those titles are now preserved.

## Boundaries and remaining operational decisions

The historical Published flag has separate provenance issues (JV status inheritance,
legacy scripts, batch_file). These are not changed by this sync patch. Adding an
automatic OTTO price-update schedule requires an explicit scope and account/product
eligibility rule; this patch does not introduce it. Existing differences on OTTO
remain until an authorized price operation succeeds.

The backend is bind-mounted in the production containers. No restarts or Celery
commands were run. Python child-process recycling can load edited sources; existing
long-lived processes and frontend image require deliberate activation for a coherent
release. Do not claim this patch is fully deployed merely because source files changed.

## Conflicting source rows

A direct fresh lookup of JV/503916 EAN `4071489040630` returned two rows with
identical EAN/name/factory but Startpreis **839.99** and **799.99**. Both had zero
SofortkaufenPreis. The saved 799.99 is one of these values; it cannot be called an
outdated price solely by comparing against the first source row.

Full metadata sync now first reads all pages to collect compact EAN/price
signatures, then persists metadata in page-sized batches, excluding conflicting
prices. The result exposes `price_conflicts`, `catalog_complete` and a partial
status. The UI uses this full-factory path before loading raw catalog pages with
`sync_db=0`, and allows up to 180s without retrying the mutating request. It flags
conflicts and avoids presenting a raw conflicting row as a fallback effective price.
Page-only API sync also skips within-page price conflicts. A page-only call cannot
prove absence of duplicates on unseen pages; the UI intentionally uses full sync.

Single-item synchronization rejects conflicting exact-EAN results. Availability
reads all necessary factory pages before acting and skips conflicting/missing
products. The publisher checks the full catalog for conflicting automatic prices,
retains only target batch data, and uses an exact fresh lookup if catalog retrieval
was incomplete. It refuses to send a stale/ambiguous automatic price. A valid manual
price remains authoritative for publication.

The two-pass metadata operation is not an atomic snapshot of the external service.
A source price change between passes is treated as a conflict and skipped when
observed. Earlier page commits remain if a later persistence/network operation fails;
this is reported as incomplete, rather than as successful full synchronization.

## Read-only production observations

- Streamed **40,010** local mappings without loading the whole catalog in memory.
- JV: 25,719 mappings; 110 missing/invalid prices (99 Not Mapped, 10 Error, 1 Ready).
- XL: 14,291 mappings; 843 missing/invalid prices (758 Error, 29 Not Mapped, 56 Ready).
- No missing prices observed under Published, and no invalid manual prices observed.
- Source sample: JV/503916 **627 rows**, XL/483385 **762 rows**; all 1,389 rows had a
  positive parsed source price. XL prices matched locally. JV contained the conflicting
  duplicate described above. Counts are rows, not a claim of 1,389 distinct EANs.
- A read-only availability preview for JV/503916 saw 604 local Published mappings:
  506 matched source products, 98 were absent from that factory response. This does
  not prove deletion from Aftercool or absence from OTTO; the safe action is to skip
  external updates and record the missing source.
- No local data repair or OTTO product/price/quantity write was executed for this audit.
  Existing missing prices require successful source synchronization, not fabricated values.

## Validation

Backend validation uses a private Docker `--internal` network, disposable PostgreSQL
15 and Redis 7, source mounted read-only, and tmpfs for image cache. Upstream APIs
are mocked. The suite includes account isolation, manual overrides, page 2 prices,
partial-page failure, cache freshness, duplicate conflicts across pages, fresh
publisher price, rejection of incomplete/ambiguous automatic prices, and dry-run
non-mutation even if the caller commits later.

Frontend validation includes TypeScript, Node price regressions and a Node 20
webpack production build in a disposable copy. The normal Google font download
proved unstable (network/Next loader errors); for deterministic verification the
build uses real downloaded Montserrat files through Next's test font transport.
No font configuration or installed dependencies were changed in the repository.
An existing unsupported named export from the Next page was made local so Next's
route type validation succeeds.

Final automated checks: **45 backend discovery tests**, **4 standalone availability
tests**, furniture/category rule scripts, **3 frontend price tests**, TypeScript and
the production build passed. Mocked Playwright checks passed for 501-row pagination
after metadata persistence, price refresh in drawer and table, second-page error
without a false complete cache, and source-conflict warning with no arbitrary
fallback price. No browser page errors. Browser API calls were intercepted fixtures.

Final live diagnostic used a PostgreSQL **READ ONLY** transaction and the repaired
dry-run: JV/503916 had604 Published mappings, **505 would_sync**, **99 skipped**
(98 missing source +1 conflicting source EAN4071489040630), **0 synced_ok** and
**0 price_updated**. No OTTO client was constructed in this preview. Temporary
test PostgreSQL/Redis/preview containers and the internal test network were removed.

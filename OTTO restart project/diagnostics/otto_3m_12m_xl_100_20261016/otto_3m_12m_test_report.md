# OTTO Market API 3M/12M check — XL sample of 100 SKUs

Run time (UTC): 2026-10-16T10:54:55.292241+00:00

## Scope and source

- Account: XL only.
- Requests: exactly one `GET /v5/products/{sku}/marketplace-status` per unique SKU, with retries only for transient/API authorization failures.
- Input: the 50 SKU labels under `[3M]` and 50 under `[12M]` in the user message. The referenced CSV was not provided; expected-period comparisons below use those user-provided group labels, not CSV fields.
- No full catalog scan, JV request, OTTO write, publish, price/availability/activity change, Restart, scheduler, or candidate creation was performed.
- OAuth access token was acquired with the existing project `OttoMarketClient`; token and credentials were not saved in result files.
- The request rate was limited to approximately 2 GET/second. Every SKU completed on its first attempt; no 429 or retry occurred. The runner includes bounded `Retry-After` handling for 429 and transient retry handling for 5xx/network errors.

## Summary

| Metric | Result |
|---|---:|
| Input SKUs | 100 |
| Expected 3M / 12M (user list) | 50 / 50 |
| HTTP 200 | 100 |
| API `status=REJECTED` | 100 |
| `lastModified` present | 100 |
| MOIN present / missing | 99 / 1 (`4067282904347`) |
| Individual error objects returned | 101 |
| API/transport errors or uncheckable responses | 0 |
| Other non-200 HTTP outcomes | 0 |
| Confirmed 3M by code/title | 50 |
| Confirmed 12M by code/title | 50 |
| Code-only exact classification matches | 100 / 100 |
| Title-only exact classification matches | 100 / 100 |
| Matches to provided period labels (code + title rules) | 100 |
| Recognized opposite-period classifications | 0 |
| Current state has no recognized 3M/12M evidence | 0 |
| Ambiguous / contradictory | 0 |
| Codes outside the tested 3M/12M mapping (occurrences / distinct values) | 1 / 1 |
| Accuracy on current, recognized, unambiguous comparable records | 100.00% (100 records) |

## Classification rules

- Primary evidence: complete normalized error codes `602016 - Nonseller` → 3M and `602016 - LOW_PERFORMER` → 12M. The numeric prefix `602016` alone is not sufficient.
- Fallback: normalized German title phrases for no net sales over three or twelve months.
- Every `errors[]` item is examined. Code/title disagreement or evidence for both periods makes the result ambiguous.
- `REJECTED` status alone is not classified as 3M/12M. Missing `active` is not treated as negative evidence.
- HTTP errors, missing/mismatched response SKU, and transport errors are uncheckable; they are not counted as proof that a restriction is absent.

## Interpretation

- Match rate is computed only on 100 HTTP-200 records whose returned SKU matches the request and whose errors produce one unambiguous 3M/12M classification. State-changed/no-known-limit and uncheckable cases are reported separately. On this sample, both the full-code-only mapping and the title-phrase-only fallback independently matched all 100 user-provided period labels.
- Because the source CSV is absent, this run cannot compare export Marketplace Status, export reason text, export error code, or distinguish a post-export state change from an incomplete export row. The prompt labels are the only expected-period reference.
- One 3M SKU (`4067282904347`) returned two errors: its recognized 3M error and the additional code `301006 - Dimensionierung Werte identisch` (variant-defining values are identical). This extra code does not contradict 3M. Its response omitted `moin`; all other 99 responses included MOIN. This code is outside the tested 3M/12M mapping, not asserted to be unknown to OTTO.
- Raw per-SKU API response bodies are preserved in `otto_3m_12m_api_responses.json`. Post-run cases needing investigation are in `otto_3m_12m_unknown_errors.json`; all 100 parsed rows are in the CSV.

## Files

- `otto_3m_12m_test_results.csv` — one row per requested SKU with expected group, returned fields, all codes/titles, detected class, and comparison outcome.
- `otto_3m_12m_unknown_errors.json` — codes outside the tested 3M/12M mapping, contradictions, ambiguous cases, possible state changes, uncheckable responses, and missing response fields.
- `otto_3m_12m_api_responses.json` — sanitized raw response bodies and HTTP statuses; no Authorization header or token.

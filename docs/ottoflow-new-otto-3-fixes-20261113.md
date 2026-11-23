# NEW_OTTO_3 regression fixes — applied 2026-11-13

Fixed missing Drip creation guard import and mapping logger; reject invalid category IDs in prediction, single/bulk assignment and bulk AI persistence. Unknown AI category responses do not create mappings or change product attributes. Source sync with attributes but no resolved category now returns partial, which both UI consumers handle explicitly.

ProductDrawer shows schema failure/empty-schema errors and disables Save until a schema is loaded. Cached conflict EANs remain listed in a persistent warning after reload; no conflicting source payload is selected automatically. Existing exclusions, account/factory context and identifier ownership remain unchanged. Existing stored N/A was not automatically reassigned to a guessed category.

Validation: 119/119 isolated backend audit cases, 3 regression unit tests, TypeScript, production Docker build and 8 browser scenarios succeeded. Tests cover JV/XL allowed/excluded CRUD, bulk/defaults/mass edit/price endings, imports/exports and export workers, Drip create/preview/controls, invalid category failures and conflict-cache reload. External providers/images/broker dispatch are faked in isolated checks. Existing recoverable XL React hydration warning is still observed; the shared account hook was not modified by this correction.

Deployment: source and full DB backed up to `/home/atambek/.local/share/ottoflow/backups/new3-fix-20261113-183550`; old frontend image retained as `ottoflow-before-new3-fix:20261113`. Graceful API/worker/beat stop; no forced termination. Updated eight files, deployed rebuilt frontend, resumed existing services. No migrations, manual publication, retry-all, commit or push.

Exact live snapshots across application/restart preserved 5064 mappings, 102564 attributes, 3173 queue items and three plan definitions. Live negative requests returned 400 for invalid categories in both factories, malformed polling since returned 200, worker ping returned pong. Root data category choices were not changed. User can refresh the browser; the existing N/A product requires choosing a verified OTTO category manually.

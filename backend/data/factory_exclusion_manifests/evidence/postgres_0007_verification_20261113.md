# PostgreSQL 15 verification — factory exclusions, 2026-11-13

Result: **9 tests passed in 22.581 seconds** using `python -m unittest -v tests.test_factory_exclusions_postgres`.

Isolation: fresh PostgreSQL 15 container with `--network none`, tmpfs storage, no host ports or production volumes. The disposable test runner shared only this container network namespace and mounted the isolated backend read-only. No production DB, Redis, API, worker, deployment, or migration was accessed.

Verified:
- Upgrade 0006_initial_availability → 0007_factory_product_exclusions; repeated upgrade; empty-table downgrade; re-upgrade.
- Complete preservation of synthetic JV/XL legacy mapping/attribute rows through the schema transitions.
- Refusal of a destructive schema downgrade with non-empty audit tables, preserving the schema revision and data.
- Full 1057-EAN JV and 1058-EAN XL manifest integrity; account/factory isolation and missing-context rejection.
- Idempotent JV apply, safe data rollback, and atomic rejection of a conflicting mapping.
- XL ownership apply/rollback preserves mapping content and all 1058 queue items.
- After a subsequent product edit, XL rollback stops atomically and retains the entire applied batch and operator edit.

Scope: a synthetic pre-0007 schema was created from existing ORM tables excluding the three new tables, then stamped at 0006 on the disposable database. The old 0001–0006 migration chain was not tested; 0001 expects pre-existing legacy tables. This is not a production snapshot or a proof of production schema readiness.

The initial attempt found missing PostgreSQL enum types in the test fixture; the fixture now explicitly installs the legacy enum types before creating its tables. No application or migration change was needed.

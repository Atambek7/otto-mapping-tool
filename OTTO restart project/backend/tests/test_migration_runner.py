from __future__ import annotations

import os
import asyncio
import contextlib
import io
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from services.factory_service import FactoryService
from services.legacy_scope_service import legacy_conflict_report, legacy_summary
from services.discovery_service import (
    StatusResponseInput,
    apply_staged_run,
    create_import_run,
    stage_status_responses,
)
from services.otto_status_service import CODE_12M, CODE_3M
from migration_runner import (
    SQLITE_BASELINE,
    MigrationChecksumMismatch,
    MigrationError,
    SchemaNotReady,
    UnsafeDowngrade,
    check_schema_head,
    discover_migrations,
    downgrade,
    upgrade,
)


class MigrationDiscoveryTests(unittest.TestCase):
    def test_discovery_uses_only_contiguous_postgresql_migrations(self) -> None:
        migrations = discover_migrations()
        self.assertEqual([migration.version for migration in migrations], list(range(1, 20)))
        self.assertTrue(all(migration.name != SQLITE_BASELINE for migration in migrations))
        self.assertEqual(migrations[0].name, "0001_postgres_initial.sql")

    def test_runner_rejects_sqlite_without_schema_changes(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        try:
            with self.assertRaisesRegex(MigrationError, "PostgreSQL only"):
                upgrade(engine)
        finally:
            engine.dispose()

    def test_migration_cli_does_not_fall_back_to_api_database_url(self) -> None:
        from migrate import main

        output = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "OTTORESTART_DATABASE_URL": "postgresql://ignored:ignored@127.0.0.1:1/otto_restart_test",
                "OTTORESTART_MIGRATION_DATABASE_URL": "",
            },
        ), contextlib.redirect_stderr(output):
            result = main(["upgrade"])

        self.assertEqual(result, 2)
        self.assertIn("OTTORESTART_MIGRATION_DATABASE_URL", output.getvalue())
        self.assertNotIn("ignored", output.getvalue())

    def test_api_lifespan_checks_schema_but_does_not_apply_migrations(self) -> None:
        import app

        async def exercise_lifespan():
            with patch.object(app, "check_schema_head") as check_schema:
                async with app.lifespan(app.app):
                    pass
            check_schema.assert_called_once_with(app.engine)

        asyncio.run(exercise_lifespan())
        self.assertFalse(hasattr(app, "migrate"))

    def test_openapi_exposes_import_run_and_product_read_contracts(self) -> None:
        import app

        schema = app.app.openapi()
        run_response = schema["paths"]["/api/restart/imports/runs"]["get"]["responses"]["200"]
        self.assertEqual(
            run_response["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ImportRunListResponse",
        )
        product_schema = schema["components"]["schemas"]["ProductReadDTO"]
        self.assertIn("account", product_schema["properties"])
        self.assertIn("factoryId", product_schema["properties"])
        self.assertIn("imageUrl", product_schema["properties"])

        factory_recalculation = schema["paths"]["/api/restart/factories/{factory_id}/complete"]["post"]
        factory_headers = {parameter["name"] for parameter in factory_recalculation.get("parameters", [])}
        self.assertIn("Idempotency-Key", factory_headers)
        self.assertIn("X-CSRF-Token", factory_headers)

        jobs_paths = schema["paths"]
        self.assertIn("get", jobs_paths["/api/restart/jobs/{job_id}"])
        self.assertIn("get", jobs_paths["/api/restart/jobs/dead"])
        self.assertIn("post", jobs_paths["/api/restart/jobs/{job_id}/replay"])
        self.assertIn("post", jobs_paths["/api/restart/scans"])
        self.assertIn("get", jobs_paths["/api/restart/scans"])
        self.assertIn("post", jobs_paths["/api/restart/scans/reconcile-known"])
        self.assertIn("get", jobs_paths["/api/restart/scans/{run_id}"])
        scan_headers = {parameter["name"] for parameter in jobs_paths["/api/restart/scans"]["post"].get("parameters", [])}
        self.assertIn("Idempotency-Key", scan_headers)
        self.assertIn("X-CSRF-Token", scan_headers)

    def test_openapi_exposes_only_scoped_operation_controls_for_s12(self) -> None:
        import app

        paths = app.app.openapi()["paths"]
        self.assertIn("post", paths["/api/restart/operations"])
        self.assertIn("post", paths["/api/restart/operations/history-reviews"])
        self.assertIn("post", paths["/api/restart/operations/retry-authorizations"])
        self.assertIn("post", paths["/api/restart/operations/{operation_id}/cancel"])

    def test_openapi_exposes_persistent_drip_schedule_contract(self) -> None:
        import app

        schema = app.app.openapi()
        paths = schema["paths"]
        self.assertIn("get", paths["/api/restart/batches"])
        self.assertIn("post", paths["/api/restart/batches/preview"])
        self.assertIn("post", paths["/api/restart/batches"])
        for action in ("start", "pause", "resume", "cancel", "retry"):
            operation = paths[f"/api/restart/batches/{{batch_id}}/{action}"]["post"]
            header_names = {parameter["name"] for parameter in operation.get("parameters", [])}
            self.assertIn("Idempotency-Key", header_names)
            self.assertIn("X-CSRF-Token", header_names)
        preview = schema["components"]["schemas"]["DripSchedulePreview"]["properties"]
        self.assertEqual(preview["publicationEnabled"]["const"], False)


class MigrationRunnerPostgresTests(unittest.TestCase):
    """Integration tests use uniquely named throwaway databases on local PG15 only."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL to run PostgreSQL migration tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql":
            raise RuntimeError("OTTORESTART_TEST_DATABASE_URL must use PostgreSQL.")
        if cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Migration integration tests accept a loopback PostgreSQL server only.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        app_url = os.environ.get("OTTORESTART_DATABASE_URL", "").strip()
        if app_url and make_url(app_url) == cls.base_url:
            raise RuntimeError("Test and application database URLs must be different.")

        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        try:
            with cls.admin_engine.connect() as connection:
                version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
            if not 150000 <= version < 160000:
                raise RuntimeError("Migration integration tests require PostgreSQL 15.")
        except Exception:
            cls.admin_engine.dispose()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        admin_engine = getattr(cls, "admin_engine", None)
        if admin_engine is not None:
            admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_s01_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.database_url: URL = self.base_url.set(database=self.database_name)
        self.engines = []

    def tearDown(self) -> None:
        for engine in self.engines:
            engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def make_engine(self):
        engine = create_engine(self.database_url, pool_pre_ping=True)
        self.engines.append(engine)
        return engine

    def test_fresh_upgrade_and_repeat_are_transactionally_idempotent(self) -> None:
        engine = self.make_engine()
        first = upgrade(engine)
        self.assertEqual(first.applied_versions, tuple(range(1, 20)))
        self.assertEqual(first.adopted_legacy_versions, ())
        self.assertEqual(first.head_version, 19)
        self.assertEqual(check_schema_head(engine).current_version, 19)

        second = upgrade(engine)
        self.assertEqual(second.applied_versions, ())
        self.assertEqual(second.adopted_legacy_versions, ())
        self.assertEqual(second.head_version, 19)

    def test_failed_revision_rolls_back_its_ddl_and_history_row(self) -> None:
        engine = self.make_engine()
        migrations = list(discover_migrations())
        migrations[1] = replace(
            migrations[1],
            sql="CREATE TABLE transaction_probe (id INTEGER); INVALID SQL;",
        )

        with self.assertRaises(SQLAlchemyError):
            upgrade(engine, migrations)

        with engine.connect() as connection:
            versions = connection.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars().all()
            probe = connection.execute(text("SELECT to_regclass('public.transaction_probe')")).scalar_one()
        self.assertEqual(versions, [1])
        self.assertIsNone(probe)
        self.assertEqual(upgrade(engine).applied_versions, tuple(range(2, 20)))

    def test_startup_schema_check_does_not_create_metadata(self) -> None:
        engine = self.make_engine()
        with self.assertRaises(SchemaNotReady):
            check_schema_head(engine)
        with engine.connect() as connection:
            self.assertIsNone(
                connection.execute(text("SELECT to_regclass('public.schema_migrations')")).scalar_one()
            )

    def test_old_migration_history_is_adopted_without_losing_rows(self) -> None:
        migrations = discover_migrations()
        engine = self.make_engine()
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        for migration in migrations:
            with engine.begin() as connection:
                connection.exec_driver_sql(migration.sql)
                connection.execute(
                    text("INSERT INTO schema_migrations(version, applied_at) VALUES (:version, CURRENT_TIMESTAMP)"),
                    {"version": migration.version},
                )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO restart_batches "
                    "(id, account, period, name, status, item_count) "
                    "VALUES ('legacy-sentinel', 'JV', '3M', 'preserve me', 'IMPORTED', 0)"
                )
            )

        report = upgrade(engine)
        self.assertEqual(report.applied_versions, ())
        self.assertEqual(report.adopted_legacy_versions, tuple(range(1, 20)))
        self.assertEqual(check_schema_head(engine).current_version, 19)
        with engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT name FROM restart_batches WHERE id = 'legacy-sentinel'")
                ).scalar_one(),
                "preserve me",
            )
            sources = connection.execute(
                text("SELECT DISTINCT checksum_source FROM schema_migrations ORDER BY checksum_source")
            ).scalars().all()
        self.assertEqual(sources, ["legacy_adopted"])

    def test_concurrent_upgrade_calls_serialize_with_advisory_lock(self) -> None:
        database_url = self.database_url

        def run_upgrade():
            engine = create_engine(database_url, pool_pre_ping=True)
            try:
                return upgrade(engine)
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=2) as executor:
            reports = list(executor.map(lambda _: run_upgrade(), range(2)))
        self.assertEqual(sorted(len(report.applied_versions) for report in reports), [0, 19])
        self.assertTrue(all(report.head_version == 19 for report in reports))

    def test_changed_checksum_is_rejected(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        with engine.begin() as connection:
            connection.execute(text("UPDATE schema_migrations SET checksum_sha256 = 'bad' WHERE version = 2"))
        with self.assertRaises(MigrationChecksumMismatch):
            check_schema_head(engine)

    def test_downgrade_refuses_to_drop_nonempty_application_schema(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        with self.assertRaises(UnsafeDowngrade):
            downgrade(engine, 0)
        self.assertEqual(check_schema_head(engine).current_version, 19)

    def test_scope_migration_preserves_legacy_rows_and_reports_conflicts(self) -> None:
        engine = self.make_engine()
        revisions = discover_migrations()
        upgrade(engine, revisions[:3])
        xl_product_id = "11111111-1111-4111-8111-111111111111"
        jv_product_id = "22222222-2222-4222-8222-222222222222"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products "
                    "(id, account, source_type, aftercool_ean, original_title) "
                    "VALUES (:id, 'XL', 'AFTERCOOL', '4069424320000', 'XL legacy source'), "
                    "(:jv_id, 'JV', 'AFTERCOOL', '4069424010015', 'JV conflicting source')"
                ),
                {"id": xl_product_id, "jv_id": jv_product_id},
            )
            connection.execute(
                text(
                    "INSERT INTO ean_links "
                    "(id, account, source_product_id, source_ean, new_ean, status) "
                    "VALUES ('33333333-3333-4333-8333-333333333333', 'XL', :source_id, "
                    "'4069424320000', '4069424010015', 'ACTIVE')"
                ),
                {"source_id": xl_product_id},
            )
            connection.execute(
                text(
                    "INSERT INTO restart_ean_registry (ean, account, status) VALUES "
                    "('4069424999999', 'XL', 'AVAILABLE'), ('4069424010015', 'XL', 'AVAILABLE')"
                )
            )
            before_products = connection.execute(text("SELECT COUNT(*) FROM source_products")).scalar_one()
            before_links = connection.execute(text("SELECT COUNT(*) FROM ean_links")).scalar_one()

        report = upgrade(engine)
        self.assertEqual(report.applied_versions, tuple(range(4, 20)))
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM source_products")).scalar_one(), before_products)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM ean_links")).scalar_one(), before_links)
            legacy_count = connection.execute(
                text("SELECT COUNT(*) FROM source_products WHERE data_scope = 'LEGACY_UNVERIFIED'")
            ).scalar_one()
            production_count = connection.execute(
                text("SELECT COUNT(*) FROM source_products WHERE data_scope = 'PRODUCTION'")
            ).scalar_one()
            preserved_scope = connection.execute(
                text("SELECT data_scope FROM ean_links WHERE account = 'XL'")
            ).scalar_one()
            legacy_report = legacy_conflict_report(connection, "XL")
            summary = legacy_summary(connection, "XL")
            production_factories = FactoryService.list_factories(connection, "XL")

        self.assertEqual(legacy_count, before_products)
        self.assertEqual(production_count, 0)
        self.assertEqual(preserved_scope, "LEGACY_UNVERIFIED")
        self.assertEqual(legacy_report["conflicts"]["cross_account_or_scope_links"]["count"], 1)
        self.assertEqual(legacy_report["conflicts"]["allocated_ean_matches_other_source"]["count"], 1)
        self.assertEqual(legacy_report["conflicts"]["registry_ean_missing_from_unified_pool"]["count"], 0)
        self.assertEqual(legacy_report["conflicts"]["registry_unified_pool_owner_mismatch"]["count"], 1)
        with engine.connect() as connection:
            backfilled = connection.execute(
                text("SELECT account, data_scope, eligibility_status FROM ean_pool WHERE ean = '4069424999999'")
            ).one()
            unchanged_registry = connection.execute(
                text("SELECT account, status, data_scope FROM restart_ean_registry WHERE ean = '4069424999999'")
            ).one()
        self.assertEqual(tuple(backfilled), ("XL", "LEGACY_UNVERIFIED", "UNVERIFIED"))
        self.assertEqual(tuple(unchanged_registry), ("XL", "AVAILABLE", "LEGACY_UNVERIFIED"))
        self.assertEqual(summary["counts"]["source_products"], 1)
        self.assertEqual(production_factories, [])

    def test_scope_aware_business_indexes_allow_only_safe_reuse(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        legacy_source_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        production_source_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        ean = "4990000000006"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products "
                    "(id, account, source_type, aftercool_product_id, aftercool_ean, original_title, data_scope) "
                    "VALUES (:id, 'JV', 'AFTERCOOL', 'ac-42', '4990000000013', 'legacy source', 'LEGACY_UNVERIFIED'), "
                    "(:production_id, 'JV', 'AFTERCOOL', 'ac-42', '4990000000020', 'production source', 'PRODUCTION')"
                ),
                {"id": legacy_source_id, "production_id": production_source_id},
            )
            connection.execute(
                text(
                    "INSERT INTO ean_pool "
                    "(ean, account, status, data_scope, eligibility_status, verified_by, verified_at) "
                    "VALUES (:ean, 'JV', 'AVAILABLE', 'PRODUCTION', 'ELIGIBLE', 'fixture', CURRENT_TIMESTAMP)"
                ),
                {"ean": ean},
            )
            connection.execute(
                text(
                    "INSERT INTO ean_links "
                    "(account, source_product_id, source_ean, new_ean, status, data_scope, deactivated_at) "
                    "VALUES ('JV', :source_id, '4990000000020', :ean, 'DEACTIVATED', 'PRODUCTION', CURRENT_TIMESTAMP)"
                ),
                {"source_id": production_source_id, "ean": ean},
            )
            connection.execute(
                text(
                    "INSERT INTO ean_links "
                    "(account, source_product_id, source_ean, new_ean, status, data_scope) "
                    "VALUES ('JV', :source_id, '4990000000020', :ean, 'ACTIVE', 'PRODUCTION')"
                ),
                {"source_id": production_source_id, "ean": ean},
            )

        with self.assertRaises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ean_links "
                        "(account, source_product_id, source_ean, new_ean, status, data_scope) "
                        "VALUES ('JV', :source_id, '4990000000020', :ean, 'ACTIVE', 'PRODUCTION')"
                    ),
                    {"source_id": production_source_id, "ean": ean},
                )

    def test_composite_foreign_keys_reject_new_cross_account_lineage(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ean_pool (ean, account, status, data_scope) "
                    "VALUES ('4069424999999', 'JV', 'QUARANTINED', 'PRODUCTION')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO source_products "
                    "(id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES ('44444444-4444-4444-8444-444444444444', 'XL', 'AFTERCOOL', "
                    "'4069424999998', 'XL production source', 'PRODUCTION')"
                )
            )

        with self.assertRaises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ean_links "
                        "(id, account, source_product_id, source_ean, new_ean, status, data_scope) "
                        "VALUES ('55555555-5555-4555-8555-555555555555', 'JV', "
                        "'44444444-4444-4444-8444-444444444444', '4069424999998', "
                        "'4069424999999', 'ACTIVE', 'PRODUCTION')"
                    )
                )

    def _apply_status_run(self, engine, account, mode, body):
        with engine.begin() as connection:
            run_id = create_import_run(connection, account, "FILE_IMPORT", mode)
            staged = stage_status_responses(
                connection,
                account,
                run_id,
                [StatusResponseInput(requested_sku=body["sku"], http_status=200, body=body)],
            )
            applied = apply_staged_run(connection, account, run_id)
        return run_id, staged, applied

    def test_discovery_episode_lifecycle_is_idempotent_and_preserves_history(self) -> None:
        engine = self.make_engine()
        upgrade(engine)

        restriction_3m = {
            "sku": "xl-sku-1", "lastModified": "2026-10-01T10:00:00Z",
            "moin": "MOIN-1", "errors": [{"code": CODE_3M, "title": "3M restriction"}],
        }
        baseline_run_id, staged, first = self._apply_status_run(engine, "XL", "BASELINE", restriction_3m)
        self.assertEqual(staged["inserted"], 1)
        self.assertEqual(first["opened"], 1)
        with engine.begin() as connection:
            replay = apply_staged_run(connection, "XL", baseline_run_id)
        self.assertTrue(replay["already_applied"])

        _, _, repeated = self._apply_status_run(engine, "XL", "INCREMENTAL", restriction_3m)
        self.assertEqual(repeated["repeated"], 1)

        restriction_12m = {
            "sku": "xl-sku-1", "lastModified": "2026-10-02T10:00:00Z",
            "moin": "MOIN-1", "errors": [{"code": CODE_12M, "title": "12M restriction"}],
        }
        _, _, changed_period = self._apply_status_run(engine, "XL", "INCREMENTAL", restriction_12m)
        self.assertEqual(changed_period["updated"], 1)

        clear = {"sku": "xl-sku-1", "lastModified": "2026-10-03T10:00:00Z", "errors": []}
        _, _, cleared = self._apply_status_run(engine, "XL", "INCREMENTAL", clear)
        self.assertEqual(cleared["closed"], 1)

        restriction_again = {
            "sku": "xl-sku-1", "lastModified": "2026-10-04T10:00:00Z",
            "errors": [{"code": CODE_3M, "title": "3M again"}],
        }
        _, _, reopened = self._apply_status_run(engine, "XL", "INCREMENTAL", restriction_again)
        self.assertEqual(reopened["opened"], 1)

        with engine.connect() as connection:
            episodes = connection.execute(
                text(
                    "SELECT origin, period, episode_state FROM candidate_episodes "
                    "WHERE account = 'XL' AND observed_sku = 'xl-sku-1' "
                    "AND data_scope = 'PRODUCTION' ORDER BY created_at, id"
                )
            ).mappings().all()
            observation_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM otto_status_observations "
                    "WHERE account = 'XL' AND requested_sku = 'xl-sku-1' AND data_scope = 'PRODUCTION'"
                )
            ).scalar_one()
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["origin"], "BASELINE")
        self.assertEqual(episodes[0]["period"], "12M")
        self.assertEqual(episodes[0]["episode_state"], "CLOSED")
        self.assertEqual(episodes[1]["origin"], "NEW")
        self.assertEqual(episodes[1]["period"], "3M")
        self.assertEqual(episodes[1]["episode_state"], "OPEN")
        self.assertEqual(observation_count, 5)

    def test_duplicate_staging_is_idempotent_within_a_run(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        body = {
            "sku": "xl-sku-duplicate", "lastModified": "2026-10-05T10:00:00Z",
            "errors": [{"code": CODE_3M, "title": "3M"}],
        }
        with engine.begin() as connection:
            run_id = create_import_run(connection, "XL", "FILE_IMPORT", "BASELINE")
            duplicate = StatusResponseInput(body["sku"], 200, body)
            staged = stage_status_responses(connection, "XL", run_id, [duplicate, duplicate])
            applied = apply_staged_run(connection, "XL", run_id)
        self.assertEqual(staged["inserted"], 1)
        self.assertEqual(staged["duplicates"], 1)
        self.assertEqual(applied["opened"], 1)
        with engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM otto_status_observations "
                    "WHERE run_id = :run_id AND data_scope = 'PRODUCTION'"
                ),
                {"run_id": run_id},
            ).scalar_one()
        self.assertEqual(count, 1)

    def test_partial_incremental_scan_does_not_apply_any_episode(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        with engine.begin() as connection:
            run_id = create_import_run(connection, "XL", "INCREMENTAL_SCAN", "INCREMENTAL")
            stage_status_responses(
                connection,
                "XL",
                run_id,
                [
                    StatusResponseInput(
                        "xl-sku-partial", 200,
                        {"sku": "xl-sku-partial", "errors": [{"code": CODE_3M}]},
                    ),
                    StatusResponseInput("xl-sku-error", 503, {"message": "temporary"}),
                ],
            )
            result = apply_staged_run(connection, "XL", run_id)
            run_state = connection.execute(
                text("SELECT run_state FROM import_runs WHERE id = :id"), {"id": run_id}
            ).scalar_one()
            episode_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM candidate_episodes "
                    "WHERE account = 'XL' AND data_scope = 'PRODUCTION'"
                )
            ).scalar_one()
        self.assertFalse(result["applied"])
        self.assertEqual(run_state, "PARTIAL")
        self.assertEqual(episode_count, 0)

    def test_stale_status_response_does_not_roll_back_episode(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        newer = {
            "sku": "xl-sku-2", "lastModified": "2026-10-10T10:00:00Z",
            "errors": [{"code": CODE_12M, "title": "12M"}],
        }
        self._apply_status_run(engine, "XL", "BASELINE", newer)
        stale = {
            "sku": "xl-sku-2", "lastModified": "2026-10-09T10:00:00Z",
            "errors": [{"code": CODE_3M, "title": "3M"}],
        }
        stale_run_id, _, result = self._apply_status_run(engine, "XL", "INCREMENTAL", stale)

        with engine.connect() as connection:
            period = connection.execute(
                text(
                    "SELECT period FROM candidate_episodes WHERE account = 'XL' "
                    "AND observed_sku = 'xl-sku-2' AND episode_state = 'OPEN'"
                )
            ).scalar_one()
            reduction = connection.execute(
                text(
                    "SELECT reduction_status FROM otto_status_observations "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": stale_run_id},
            ).scalar_one()
        self.assertEqual(result["stale_or_conflicting"], 1)
        self.assertEqual(period, "12M")
        self.assertEqual(reduction, "STALE")

    def test_account_scope_is_required_for_run_and_confirmed_source_identity(self) -> None:
        engine = self.make_engine()
        upgrade(engine)
        with engine.begin() as connection:
            xl_run = create_import_run(connection, "XL", "FILE_IMPORT", "BASELINE")
            with self.assertRaisesRegex(ValueError, "outside the production account scope"):
                stage_status_responses(
                    connection,
                    "JV",
                    xl_run,
                    [StatusResponseInput("sku", 200, {"sku": "sku", "errors": []})],
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_products "
                    "(id, account, source_type, aftercool_ean, original_title, data_scope) "
                    "VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'XL', 'AFTERCOOL', "
                    "'4069424320999', 'XL product', 'PRODUCTION')"
                )
            )
            jv_run = create_import_run(connection, "JV", "FILE_IMPORT", "BASELINE")
            with self.assertRaisesRegex(ValueError, "outside the production account scope"):
                stage_status_responses(
                    connection,
                    "JV",
                    jv_run,
                    [StatusResponseInput(
                        "sku", 200, {"sku": "sku", "errors": []},
                        source_product_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    )],
                )


if __name__ == "__main__":
    unittest.main()

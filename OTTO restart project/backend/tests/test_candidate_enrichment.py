from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from migration_runner import upgrade
from services.candidate_enrichment_service import process_candidate_enrichment
from services.discovery_service import StatusResponseInput, apply_staged_run, create_import_run, stage_status_responses


class FakeOttoClient:
    def __init__(self, sku: str, ean: str):
        self.sku = sku
        self.ean = ean
        self.status_reads = 0

    def get_marketplace_status(self, _sku: str):
        self.status_reads += 1
        raise AssertionError("A staged marketplace status must be reused.")

    def get_product(self, sku: str):
        return {
            "sku": sku,
            "ean": self.ean,
            "productDescription": {"productName": "Verified OTTO title", "attributes": []},
            "mediaAssets": [{"url": "https://images.otto.example/product.jpg"}],
        }

    def get_product_prices(self, sku: str):
        return {"sku": sku, "standardPrice": {"amount": "49.99", "currency": "EUR"}}


class FakeAftercoolService:
    def __init__(self, sku: str, ean: str):
        self.sku = sku
        self.ean = ean
        self.calls = []

    def lookup_product(self, account: str, ean: str, factory_hint=None):
        self.calls.append((account, ean, factory_hint))
        return {
            "account": account,
            "status": "FOUND",
            "verified": True,
            "data_scope": "PRODUCTION",
            "source_type": "AFTERBUY",
            "aftercool_product_id": "aftercool-product-1",
            "sku": self.sku,
            "ean": self.ean,
            "factory_id": "factory-1",
            "factory_name": "Verified Factory",
        }


class CandidateEnrichmentPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for candidate enrichment tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Candidate enrichment tests require a loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("Candidate enrichment tests require a disposable database name containing 'test'.")
        configured_app_url = os.environ.get("OTTORESTART_DATABASE_URL", "").strip()
        if configured_app_url and make_url(configured_app_url) == cls.base_url:
            raise RuntimeError("Application and candidate test databases must be different.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Candidate enrichment tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        engine = getattr(cls, "admin_engine", None)
        if engine is not None:
            engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_route_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.engine = create_engine(self.base_url.set(database=self.database_name), pool_pre_ping=True)
        upgrade(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    def test_confirmed_candidate_is_routed_only_after_exact_account_source_lookup(self) -> None:
        now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        observed_sku = "4066381333931"
        aftercool_sku = "internal-aftercool-sku"
        ean = "4066381333931"
        status = {
            "sku": observed_sku,
            "status": "REJECTED",
            "marketPlaceStatus": "REJECTED",
            "lastModified": "2026-11-09T10:00:00Z",
            "errors": [{"code": "602016 - Nonseller", "title": "No Nettoumsatz for at least three months."}],
        }
        with self.engine.begin() as connection:
            run_id = create_import_run(
                connection,
                "XL",
                "INCREMENTAL_SCAN",
                "INCREMENTAL",
                window_from=now,
                cutoff_at=now,
                parameters={"test": True},
            )
            stage_status_responses(
                connection,
                "XL",
                run_id,
                [StatusResponseInput(requested_sku=observed_sku, http_status=200, body=status)],
            )
            apply_staged_run(connection, "XL", run_id)
            episode = connection.execute(
                text("SELECT id::text AS id,last_observation_id::text AS observation_id,routing_state "
                     "FROM candidate_episodes WHERE account='XL' AND data_scope='PRODUCTION'")
            ).mappings().one()
            job = connection.execute(
                text("SELECT COUNT(*) FROM background_jobs WHERE job_type='discovery.enrich' AND account='XL'")
            ).scalar_one()
        self.assertEqual(episode["routing_state"], "PENDING")
        self.assertEqual(job, 1)

        otto = FakeOttoClient(observed_sku, ean)
        aftercool = FakeAftercoolService(aftercool_sku, ean)
        result = process_candidate_enrichment(
            self.engine,
            account="XL",
            episode_id=episode["id"],
            observation_id=episode["observation_id"],
            otto_client_factory=lambda account: otto,
            aftercool_service_factory=lambda: aftercool,
            max_attempts=3,
            attempt=1,
        )

        self.assertEqual(result["state"], "ROUTED")
        self.assertEqual(otto.status_reads, 0)
        self.assertEqual(aftercool.calls, [("XL", ean, None)])
        with self.engine.connect() as connection:
            episode_after = connection.execute(
                text("SELECT ce.routing_state,ce.confirmed_ean,sp.source_type,sp.aftercool_sku,sp.aftercool_ean,sp.factory_id "
                     "FROM candidate_episodes ce JOIN source_products sp ON sp.id=ce.source_product_id "
                     "WHERE ce.id=:episode_id AND ce.account='XL' AND ce.data_scope='PRODUCTION'"),
                {"episode_id": episode["id"]},
            ).mappings().one()
            factory = connection.execute(
                text("SELECT candidate_generation FROM restart_factories WHERE account='XL' AND id='factory-1' "
                     "AND data_scope='PRODUCTION'")
            ).scalar_one()
        self.assertEqual(episode_after["routing_state"], "ROUTED")
        self.assertEqual(episode_after["confirmed_ean"], ean)
        self.assertEqual(episode_after["source_type"], "AFTERBUY")
        self.assertEqual(episode_after["aftercool_sku"], aftercool_sku)
        self.assertEqual(episode_after["aftercool_ean"], ean)
        self.assertEqual(episode_after["factory_id"], "factory-1")
        self.assertEqual(factory, 1)


if __name__ == "__main__":
    unittest.main()

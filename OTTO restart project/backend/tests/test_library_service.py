from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from migration_runner import upgrade
from services.discovery_service import StatusResponseInput, create_import_run, stage_status_responses
from services.library_service import LibraryQueryError, list_links, lookup_aftercool
from services.otto_status_service import CODE_3M


class LibraryServicePostgresTests(unittest.TestCase):
    """Production Library checks run against a disposable loopback PostgreSQL 15 database."""

    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for isolated Library tests.")
        cls.base_url = make_url(raw_url)
        if cls.base_url.get_backend_name() != "postgresql" or cls.base_url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("Library tests require an explicit loopback PostgreSQL test server.")
        if "test" not in (cls.base_url.database or "").lower():
            raise RuntimeError("The explicit test database name must contain 'test'.")
        cls.admin_engine = create_engine(cls.base_url.set(database="postgres"), pool_pre_ping=True)
        with cls.admin_engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
        if not 150000 <= version < 160000:
            raise RuntimeError("Library tests require PostgreSQL 15.")

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "admin_engine", None):
            cls.admin_engine.dispose()

    def setUp(self) -> None:
        self.database_name = f"ottorestart_library_test_{uuid.uuid4().hex}"
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quote(self.database_name)} TEMPLATE template0")
        self.database_url: URL = self.base_url.set(database=self.database_name)
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        upgrade(self.engine)
        self.products: list[dict[str, str]] = []

    def tearDown(self) -> None:
        self.engine.dispose()
        quote = self.admin_engine.dialect.identifier_preparer.quote_identifier
        with self.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(self.database_name)}")

    @staticmethod
    def _ean(seed: int) -> str:
        body = f"499{seed % 1_000_000_000:09d}"
        digits = [int(char) for char in body]
        checksum = (10 - sum(digit * (3 if i % 2 == 0 else 1) for i, digit in enumerate(reversed(digits))) % 10) % 10
        return body + str(checksum)

    def _create_product(self, index: int, *, scope: str = "PRODUCTION") -> dict[str, str]:
        source_id = str(uuid.uuid4())
        source_ean = self._ean(1000 + index)
        new_ean = self._ean(2000 + index)
        sku = f"SOURCE-SKU-{index}"
        factory_id = "factory-library"
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO source_products (
                  id,account,source_type,aftercool_product_id,aftercool_sku,aftercool_ean,
                  factory_id,factory_name,original_title,data_scope,period
                ) VALUES (
                  :id,'XL','AFTERCOOL',:product_id,:sku,:ean,:factory_id,'Library Factory',
                  :title,:scope,'3M'
                )
            """), {
                "id": source_id, "product_id": f"AC-{index:03d}", "sku": sku,
                "ean": source_ean, "factory_id": factory_id,
                "title": f"Library product {index}", "scope": scope,
            })
        product = {
            "sourceId": source_id,
            "sourceEan": source_ean,
            "newEan": new_ean,
            "sku": sku,
            "factoryId": factory_id,
            "productId": f"AC-{index:03d}",
            "scope": scope,
        }
        self.products.append(product)
        return product

    def _create_candidate(self, product: dict[str, str], index: int) -> str:
        run_id = str(uuid.uuid4())
        with self.engine.begin() as connection:
            run_id = create_import_run(connection, "XL", "FILE_IMPORT", "BASELINE", source_filename="library-fixture.txt")
            stage_status_responses(connection, "XL", run_id, [StatusResponseInput(
                requested_sku=product["sku"],
                http_status=200,
                body={
                    "sku": product["sku"],
                    "status": "REJECTED",
                    "marketPlaceStatus": "REJECTED",
                    "lastModified": datetime.now(timezone.utc).isoformat(),
                    "errors": [{"code": CODE_3M, "title": "Nettoumsatz fehlt"}],
                },
                source_product_id=product["sourceId"],
                confirmed_ean=product["sourceEan"],
            )])
            connection.execute(text("UPDATE import_runs SET run_state='SUCCEEDED',status='COMPLETED',finished_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": run_id})
            episode_id = connection.execute(text("""
                INSERT INTO candidate_episodes (
                  account,data_scope,observed_sku,canonical_identity,source_product_id,confirmed_ean,
                  period,classification,origin,episode_state,first_observation_id,last_observation_id,
                  last_response_hash,last_modified
                ) SELECT 'XL','PRODUCTION',:sku,'OTTO_SKU:' || :sku,:source_id,:ean,
                         '3M','EXACT_CODE','BASELINE','OPEN',id,id,response_hash,last_modified
                  FROM otto_status_observations WHERE run_id=:run_id
                RETURNING id
            """), {
                "sku": product["sku"], "source_id": product["sourceId"], "ean": product["sourceEan"], "run_id": run_id,
            }).scalar_one()
        return str(episode_id)

    def _link_product(self, product: dict[str, str], episode_id: str, index: int, *, online: bool) -> None:
        operation_id = str(uuid.uuid4()) if online else None
        target_sku = f"TARGET-SKU-{index}" if online else None
        processing_event_id = str(uuid.uuid4()) if online else None
        online_observation_id = None
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO ean_pool (
                  ean,account,status,data_scope,provenance_source,eligibility_status,
                  verified_by,verified_at,created_at
                ) VALUES (:ean,'XL','AVAILABLE','PRODUCTION','MANUAL_IMPORT','ELIGIBLE',
                          'offline-test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """), {"ean": product["newEan"]})
            if online:
                event_metadata = {"targetSku": target_sku, "result": "SUCCEEDED"}
                connection.execute(text("""
                    INSERT INTO restart_events (
                      id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json
                    ) VALUES (:id,'XL','PRODUCTION','OTTO_PRODUCT_PROCESSED','OTTO_PROCESSING_RESULT',
                              :operation_id,'Per-SKU processing succeeded',CAST(:metadata AS jsonb))
                """), {
                    "id": processing_event_id, "operation_id": operation_id,
                    "metadata": __import__("json").dumps(event_metadata),
                })
                online_run = create_import_run(connection, "XL", "KNOWN_SKU_RECONCILIATION", "INCREMENTAL")
                stage_status_responses(connection, "XL", online_run, [StatusResponseInput(
                    requested_sku=target_sku,
                    http_status=200,
                    body={"sku": target_sku, "status": "ONLINE", "marketPlaceStatus": "ONLINE", "errors": []},
                )])
                online_observation_id = connection.execute(text(
                    "SELECT id FROM otto_status_observations WHERE run_id=:run_id"
                ), {"run_id": online_run}).scalar_one()
                connection.execute(text("UPDATE import_runs SET run_state='SUCCEEDED',status='COMPLETED',finished_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": online_run})
                connection.execute(text("""
                    INSERT INTO restart_operations (
                      id,account,data_scope,source_product_id,candidate_episode_id,attempt_number,
                      target_sku,created_by,period,status,new_ean,submission_state,
                      processing_evidence_event_id,online_observation_id
                    ) VALUES (:id,'XL','PRODUCTION',:source_id,:episode_id,1,:target_sku,'offline-test',
                              '3M','PUBLISHED',:ean,'CONFIRMED',:event_id,:observation_id)
                """), {
                    "id": operation_id, "source_id": product["sourceId"], "episode_id": episode_id,
                    "target_sku": target_sku, "ean": product["newEan"],
                    "event_id": processing_event_id, "observation_id": online_observation_id,
                })
                connection.execute(text("""
                    UPDATE ean_pool SET status='USED',reservation_operation_id=:operation_id,used_at=CURRENT_TIMESTAMP
                    WHERE ean=:ean AND account='XL' AND data_scope='PRODUCTION'
                """), {"operation_id": operation_id, "ean": product["newEan"]})
            connection.execute(text("""
                INSERT INTO ean_links (
                  id,account,source_product_id,source_ean,new_ean,status,operation_id,data_scope
                ) VALUES (:id,'XL',:source_id,:source_ean,:new_ean,'ACTIVE',:operation_id,'PRODUCTION')
            """), {
                "id": str(uuid.uuid4()), "source_id": product["sourceId"],
                "source_ean": product["sourceEan"], "new_ean": product["newEan"],
                "operation_id": operation_id,
            })

    def test_link_state_is_not_online_and_verified_processing_plus_online_is_separate(self) -> None:
        product = self._create_product(1)
        episode_id = self._create_candidate(product, 1)
        self._link_product(product, episode_id, 1, online=True)

        with self.engine.connect() as connection:
            result = list_links(connection, account="XL", page=1, page_size=10)

        self.assertEqual(result["page"]["totalItems"], 1)
        item = result["items"][0]
        self.assertEqual(item["linkStatus"], "ACTIVE")
        self.assertEqual(item["linkedIdentifier"]["ean"], product["newEan"])
        self.assertTrue(item["processingConfirmed"])
        self.assertTrue(item["onlineConfirmed"])
        self.assertEqual(item["confirmedOnlineIdentifiers"][0]["status"], "ONLINE")
        self.assertEqual(item["confirmedOnlineIdentifiers"][0]["sku"], "TARGET-SKU-1")

    def test_aftercool_factory_lookup_is_paginated_and_keeps_lineage_history(self) -> None:
        for index in (1, 2):
            product = self._create_product(index)
            episode_id = self._create_candidate(product, index)
            self._link_product(product, episode_id, index, online=index == 1)
        legacy = self._create_product(3, scope="LEGACY_UNVERIFIED")
        # Give the legacy row a distinguishable pool/link record; it must never enter the production lookup.
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO ean_pool(ean,account,status,data_scope) VALUES (:ean,'XL','AVAILABLE','LEGACY_UNVERIFIED')"), {"ean": legacy["newEan"]})
            connection.execute(text("""
                INSERT INTO ean_links(account,source_product_id,source_ean,new_ean,status,data_scope)
                VALUES ('XL',:source_id,:source_ean,:new_ean,'ACTIVE','LEGACY_UNVERIFIED')
            """), {"source_id": legacy["sourceId"], "source_ean": legacy["sourceEan"], "new_ean": legacy["newEan"]})

        with self.engine.connect() as connection:
            first = lookup_aftercool(connection, account="XL", factory_id="factory-library", page=1, page_size=1)
            second = lookup_aftercool(connection, account="XL", factory_id="factory-library", page=2, page_size=1)
            all_links = list_links(connection, account="XL", page=1, page_size=1)

        self.assertEqual(first["page"]["totalItems"], 2)
        self.assertEqual(first["page"]["totalPages"], 2)
        self.assertEqual(second["page"]["totalItems"], 2)
        self.assertEqual(len(first["items"]), 1)
        online_item = first["items"][0]
        self.assertEqual(online_item["sourceProduct"]["factoryId"], "factory-library")
        self.assertEqual(len(online_item["linkedIdentifiers"]), 1)
        self.assertEqual(len(online_item["confirmedOnlineIdentifiers"]), 1)
        self.assertTrue(online_item["history"])
        self.assertTrue(online_item["events"])
        self.assertIsNotNone(online_item["checkedAt"])
        self.assertEqual(all_links["page"]["totalItems"], 2)
        with self.engine.connect() as connection:
            second_link_page = list_links(connection, account="XL", page=2, page_size=1)
        self.assertEqual(second_link_page["page"]["totalItems"], 2)
        self.assertNotEqual(all_links["items"][0]["id"], second_link_page["items"][0]["id"])
        self.assertEqual(len(second["items"][0]["confirmedOnlineIdentifiers"]), 0)
        with self.assertRaises(LibraryQueryError):
            lookup_aftercool(connection, account="XL", page=1, page_size=101, factory_id="factory-library")

    def test_aftercool_lookup_requires_a_selector(self) -> None:
        with self.engine.connect() as connection:
            with self.assertRaisesRegex(LibraryQueryError, "selector"):
                lookup_aftercool(connection, account="XL")

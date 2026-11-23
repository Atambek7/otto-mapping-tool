from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from migration_runner import upgrade
from services.import_pipeline import _persist_source_product
from services.ean_pool_service import import_pool_entries, reserve_ean_for_operation
from services.operation_service import create_operation
from services.portal_media_service import (
    ACCOUNT_BRAND_IDS,
    PORTAL_MEDIA_SOURCE_REF,
    PortalMediaError,
    canonical_portal_image_url,
    list_missing_portal_media,
    parse_portal_media_csv,
    portal_gallery_urls_from_attributes,
    save_portal_media_assets,
)
from services.preparation_service import (
    load_preparation_context,
    prepare_operation_revision,
    select_target_image_candidates,
)
from services.qc_service import evaluate_qc


class PortalMediaUnitTests(unittest.TestCase):
    def test_canonical_portal_url_accepts_only_i_otto_de_assets(self) -> None:
        asset_id, url = canonical_portal_image_url(asset_id="abc123-def456")
        self.assertEqual(asset_id, "abc123-def456")
        self.assertEqual(url, "https://i.otto.de/i/otto/abc123-def456")

        parsed_id, parsed_url = canonical_portal_image_url(url="https://i.otto.de/i/otto/0f1e2d3c4b5a")
        self.assertEqual(parsed_id, "0f1e2d3c4b5a")
        self.assertEqual(parsed_url, "https://i.otto.de/i/otto/0f1e2d3c4b5a")

        for bad in (
            {"url": "http://i.otto.de/i/otto/abc12345"},
            {"url": "https://evil.example/i/otto/abc12345"},
            {"url": "https://127.0.0.1/i/otto/abc12345"},
            {"url": "https://i.otto.de/other/abc12345"},
            {"asset_id": "../escape"},
        ):
            with self.subTest(bad=bad), self.assertRaises(PortalMediaError):
                canonical_portal_image_url(**bad)

    def test_parse_portal_media_csv_groups_and_orders_assets(self) -> None:
        csv_text = (
            "\ufeffean,productUuid,position,filename,assetId,url,status\n"
            "4062292451157,uuid-1,1,2.jpg,asset-two,https://i.otto.de/i/otto/asset-two,ASSET_FOUND\n"
            "4062292451157,uuid-1,0,0.jpg,asset-one,https://i.otto.de/i/otto/asset-one,ASSET_FOUND\n"
        )
        items = parse_portal_media_csv(csv_text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sku"], "4062292451157")
        self.assertEqual(len(items[0]["assets"]), 2)

    def test_select_target_image_candidates_uses_portal_gallery_when_otto_v5_lacks_locations(self) -> None:
        portal_gallery = portal_gallery_urls_from_attributes({
            "portalMediaGallery": [
                {"position": 1, "assetId": "asset-two-123", "url": "https://i.otto.de/i/otto/asset-two-123"},
                {"position": 0, "assetId": "asset-one-123", "url": "https://i.otto.de/i/otto/asset-one-123"},
            ]
        })
        candidates = select_target_image_candidates(
            {"mediaAssets": [{"type": "IMAGE", "filename": "only-name.jpg"}]},
            source_type="OTTO_DIRECT",
            aftercool=None,
            otto_source_ref="OTTO:/v5/products/4062292451157",
            aftercool_gallery=None,
            catalog_image_url="https://i.otto.de/i/otto/asset-one-123",
            portal_gallery=portal_gallery,
        )
        self.assertEqual(
            candidates,
            [
                ("https://i.otto.de/i/otto/asset-one-123", "OttoRestart:catalog-image"),
                ("https://i.otto.de/i/otto/asset-two-123", PORTAL_MEDIA_SOURCE_REF),
            ],
        )


class PortalMediaPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw_url = os.environ.get("OTTORESTART_TEST_DATABASE_URL", "").strip()
        if not raw_url:
            raise unittest.SkipTest("Set OTTORESTART_TEST_DATABASE_URL for PostgreSQL portal media tests.")
        base_url = make_url(raw_url)
        cls.db_name = f"otto_portal_media_test_{uuid4().hex[:8]}"
        cls.admin_engine = create_engine(base_url.set(database="postgres"), pool_pre_ping=True, isolation_level="AUTOCOMMIT")
        with cls.admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{cls.db_name}"'))
        cls.engine = create_engine(base_url.set(database=cls.db_name), pool_pre_ping=True)
        upgrade(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        if hasattr(cls, "admin_engine"):
            with cls.admin_engine.connect() as conn:
                conn.execute(text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:db AND pid<>pg_backend_pid()"
                ), {"db": cls.db_name})
                conn.execute(text(f'DROP DATABASE IF EXISTS "{cls.db_name}"'))
            cls.admin_engine.dispose()

    def test_portal_media_save_brand_guard_import_preservation_and_qc_pass(self) -> None:
        from services.gtin_service import review_gtin_evidence, submit_gtin_evidence

        source_id = str(uuid4())
        episode_id = str(uuid4())
        operation_id = str(uuid4())
        run_id = str(uuid4())
        obs_id = str(uuid4())
        source_sku = "4062292451157"
        target_ean = "4990000000154"

        otto_v5_product = {
            "sku": source_sku,
            "ean": source_sku,
            "productReference": "4062292020544",
            "productDescription": {
                "category": "Dekosäule",
                "brandId": ACCOUNT_BRAND_IDS["JV"],
                "productLine": "Antik Stil XXL Griechische Säule Design Luxus Stützen 300cm Groß",
                "description": "Handgefertigte Säule für den Innenbereich.",
                "bulletPoints": ["Made in Europa"],
                "attributes": [
                    {"name": "Relevanz Gefahrgut", "values": ["Produkt fällt nicht unter die Gefahrgutvorschriften."]},
                ],
            },
            "mediaAssets": [{"type": "IMAGE", "filename": "be3d6522-19c9-4c32-90c3-5f6df3c7.jpg"}],
            "pricing": {"standardPrice": {"amount": 2929, "currency": "EUR"}},
        }
        otto_prices = {"sku": source_sku, "standardPrice": {"amount": 2929, "currency": "EUR"}}
        otto_delivery = {"sku": source_sku, "type": "FORWARDER_PREFERREDLOCATION", "deliveryTime": 30}

        with self.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO import_runs(id,account,source_filename,total_rows,status,data_scope,run_type,run_mode,run_state,started_at,finished_at) "
                "VALUES (:id,'JV','scan.json',1,'COMPLETED','PRODUCTION','FILE_IMPORT','INCREMENTAL','SUCCEEDED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ), {"id": run_id})
            conn.execute(text(
                "INSERT INTO source_products(id,account,data_scope,source_type,aftercool_sku,aftercool_ean,original_title,original_price_cents,category,image_url,attributes_json) "
                "VALUES (:id,'JV','PRODUCTION','OTTO_DIRECT',:sku,:sku,:title,292900,'Dekosäule',NULL,CAST(:attrs AS jsonb))"
            ), {"id": source_id, "sku": source_sku, "title": otto_v5_product["productDescription"]["productLine"],
                "attrs": json.dumps({"ottoProductData": otto_v5_product})})
            conn.execute(text(
                "INSERT INTO otto_status_observations(id,run_id,account,data_scope,sequence_no,requested_sku,response_sku,source_product_id,confirmed_ean,http_status,classification,period,reason,status,market_place_status,active,errors_json,error_codes_json,error_titles_json,unknown_codes_json,response_json,response_hash,reduction_status) "
                "VALUES (:id,:run,'JV','PRODUCTION',1,:sku,:sku,:sp,:sku,200,'EXACT_CODE','12M','602016 - LOW_PERFORMER','REJECTED','INACTIVE',FALSE,'[]'::jsonb,'[\"602016 - LOW_PERFORMER\"]'::jsonb,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','OPENED')"
            ), {"id": obs_id, "run": run_id, "sku": source_sku, "sp": source_id})
            conn.execute(text(
                "INSERT INTO candidate_episodes(id,account,data_scope,observed_sku,canonical_identity,source_product_id,confirmed_ean,period,classification,origin,first_observation_id,last_observation_id,last_response_hash) "
                "VALUES (:id,'JV','PRODUCTION',:sku,:sku,:sp,:sku,'12M','EXACT_CODE','NEW',:obs,:obs,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
            ), {"id": episode_id, "sku": source_sku, "sp": source_id, "obs": obs_id})
            import_pool_entries(conn, account="JV", eans=[target_ean], actor="operator", data_scope="PRODUCTION")
            op = create_operation(conn, account="JV", candidate_episode_id=episode_id, actor="operator")
            operation_id = str(op["id"])

            missing = list_missing_portal_media(conn, account="JV")
            self.assertEqual(len(missing["items"]), 1)
            self.assertEqual(missing["items"][0]["sku"], source_sku)
            self.assertEqual(missing["items"][0]["expectedBrandId"], ACCOUNT_BRAND_IDS["JV"])

            # Cross-account XL brandId must fail closed in JV
            with self.assertRaises(PortalMediaError) as brand_err:
                save_portal_media_assets(
                    conn, account="JV", actor="operator",
                    items=[{
                        "sku": source_sku,
                        "brandId": ACCOUNT_BRAND_IDS["XL"],
                        "assets": [{"position": 0, "assetId": "asset-wrong-brand-1"}],
                    }],
                )
            self.assertEqual(brand_err.exception.code, "BRAND_ACCOUNT_MISMATCH")

        with self.engine.begin() as conn:
            saved = save_portal_media_assets(
                conn,
                account="JV",
                actor="operator",
                items=[{
                    "sku": source_sku,
                    "productUuid": "portal-uuid-123",
                    "brandId": ACCOUNT_BRAND_IDS["JV"],
                    "assets": [
                        {"position": 1, "assetId": "asset-col-02", "filename": "2.jpg"},
                        {"position": 0, "assetId": "asset-col-01", "filename": "0.jpg"},
                    ],
                }],
            )
            self.assertEqual(saved["updatedCount"], 1)
            self.assertEqual(saved["savedAssetsCount"], 2)
            self.assertEqual(saved["results"][0]["heroUrl"], "https://i.otto.de/i/otto/asset-col-01")
            self.assertEqual(list_missing_portal_media(conn, account="JV")["total"], 0)

            # Re-importing OTTO_DIRECT without image_url preserves portal gallery and hero image_url
            _persist_source_product(
                conn,
                account="JV",
                run_id=run_id,
                run_mode="INCREMENTAL",
                period="12M",
                sku=source_sku,
                ean=source_sku,
                source={
                    "title": otto_v5_product["productDescription"]["productLine"],
                    "price_cents": 292900,
                    "category": "Dekosäule",
                    "image_url": None,
                    "otto_product_data": otto_v5_product,
                },
                source_type="OTTO_DIRECT",
            )
            sp_row = conn.execute(
                text("SELECT image_url, attributes_json FROM source_products WHERE id=:id"),
                {"id": source_id},
            ).mappings().one()
            self.assertEqual(sp_row["image_url"], "https://i.otto.de/i/otto/asset-col-01")
            self.assertEqual(len(portal_gallery_urls_from_attributes(sp_row["attributes_json"])), 2)

        with self.engine.begin() as conn:
            ev = submit_gtin_evidence(
                conn, account="JV", ean=target_ean, source_product_id=source_id,
                evidence_type="TEST_GS1_EVIDENCE", source_name="fixture",
                artifact_filename="gtin.pdf", artifact_media_type="application/pdf",
                artifact_bytes=b"%PDF-1.4\ntest\n%%EOF",
                claim_basis="Test GTIN authorization bound to JV candidate source.",
                validity_kind="PERPETUAL", expires_at=None, actor="operator",
            )
            review_gtin_evidence(
                conn, account="JV", evidence_id=ev["id"], decision="APPROVE",
                review_basis="Approved only inside isolated disposable test database.",
                actor="reviewer", approved_evidence_types=frozenset({"TEST_GS1_EVIDENCE"}),
            )
            reserve_ean_for_operation(
                conn,
                account="JV",
                operation_id=operation_id,
                approved_evidence_types=frozenset({"TEST_GS1_EVIDENCE"}),
            )
            current_version = int(conn.execute(
                text("SELECT version FROM restart_operations WHERE id=:id"),
                {"id": operation_id},
            ).scalar_one())
            preflight = load_preparation_context(conn, account="JV", operation_id=operation_id, expected_version=current_version)
            prepared = prepare_operation_revision(
                conn,
                account="JV",
                operation_id=operation_id,
                expected_version=current_version,
                preflight_context=preflight,
                otto_product=otto_v5_product,
                otto_prices=otto_prices,
                fulfillment=otto_delivery,
                aftercool=None,
                aftercool_gallery={"account": "JV", "ean": source_sku, "verified": True, "data_scope": "PRODUCTION", "status": "NOT_FOUND_CONFIRMED", "image_urls": []},
                captured_at=datetime.now(timezone.utc),
                actor="operator",
                approved_evidence_types=frozenset({"TEST_GS1_EVIDENCE"}),
            )
            self.assertNotIn("IMAGES_MISSING", prepared["readinessGaps"])
            rev = conn.execute(text(
                "SELECT source_snapshot_json, content_json, price_snapshot_json, fulfillment_snapshot_json, payload_json, payload_hash "
                "FROM operation_revisions WHERE operation_id=:op ORDER BY revision_number DESC LIMIT 1"
            ), {"op": operation_id}).mappings().one()
            qc = evaluate_qc({
                "operation": {"account": "JV", "targetSku": target_ean},
                "source": {"originalTitle": otto_v5_product["productDescription"]["productLine"]},
                "revision": {
                    "sourceSnapshot": rev["source_snapshot_json"],
                    "content": rev["content_json"],
                    "priceSnapshot": rev["price_snapshot_json"],
                    "fulfillmentSnapshot": rev["fulfillment_snapshot_json"],
                    "payload": rev["payload_json"],
                    "payloadHash": rev["payload_hash"],
                },
            })
            issue_codes = {i.code for i in qc.issues}
            self.assertNotIn("IMAGES_MISSING", issue_codes)
            self.assertNotIn("IMAGE_SOURCE_UNVERIFIED", issue_codes)


if __name__ == "__main__":
    unittest.main()

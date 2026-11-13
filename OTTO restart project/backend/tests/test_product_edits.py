from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Barrier
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services.operation_service import OperationError, create_operation
from services.product_catalog_service import get_product, list_products
from services.product_edit_service import get_product_edit, save_product_edit
from services.qc_service import QCError, record_operation_qc
from tests.test_factory_service import FactoryServicePostgresTests
from tests.test_security_auth import SECRET, current_claims, encode_token


class ProductEditPostgresTests(FactoryServicePostgresTests):
    def test_edit_invalidates_approval_and_blocks_unapplied_content_from_qc(self):
        _, candidate, _, _ = self.stage_candidate(account="JV", run_mode="BASELINE")
        with self.engine.begin() as conn:
            operation = create_operation(conn, account="JV", candidate_episode_id=candidate, actor="operator")
            conn.execute(text("UPDATE restart_operations SET status='READY' WHERE id=:id"), {"id": operation["id"]})
            saved = save_product_edit(conn, account="JV", candidate_id=candidate, expected_version=0, title="Edited", category="Sofa", attributes={}, actor="operator")
            self.assertEqual(saved["edit"]["version"], 1)
            state = conn.execute(text("SELECT status,version FROM restart_operations WHERE id=:id"), {"id": operation["id"]}).one()
            self.assertEqual(tuple(state), ("NEEDS_REVIEW", 2))
            with self.assertRaises(QCError) as qc:
                record_operation_qc(conn, account="JV", operation_id=operation["id"], expected_version=2, actor="operator", approved_evidence_types=())
            self.assertEqual(qc.exception.code, "PRODUCT_EDITS_NOT_APPLIED")
            conn.execute(text("UPDATE restart_operations SET status='QUEUED' WHERE id=:id"), {"id": operation["id"]})
            with self.assertRaises(OperationError) as queued:
                save_product_edit(conn, account="JV", candidate_id=candidate, expected_version=1, title="No", category="", attributes={}, actor="operator")
            self.assertEqual(queued.exception.code, "OPERATION_NOT_EDITABLE")
            self.assertEqual(get_product_edit(conn, account="JV", candidate_id=candidate)["edit"]["title"], "Edited")

    def test_catalog_price_preview_uses_verified_sources_and_current_xl_policy(self):
        source_id, candidate, _, sku = self.stage_candidate(account="XL", run_mode="BASELINE")
        source2, candidate2, _, sku2 = self.stage_candidate(account="XL", run_mode="BASELINE")
        _, unknown_price_candidate, _, _ = self.stage_candidate(account="XL", run_mode="BASELINE")
        with self.engine.begin() as conn:
            ean = conn.execute(text("SELECT confirmed_ean FROM candidate_episodes WHERE id=:id"), {"id": candidate}).scalar_one()
            evidence = {"ottoPricesData": {"sku": sku, "standardPrice": {"amount": "100.00", "currency": "EUR"}},
                        "aftercoolLookup": {"status": "FOUND", "verified": True, "data_scope": "PRODUCTION", "account": "XL", "ean": ean,
                                            "factory_id": "factory-s17", "aftercool_product_id": "123", "price_cents": 15000}}
            conn.execute(text("UPDATE source_products SET aftercool_product_id='123', attributes_json=CAST(:data AS jsonb) WHERE id=:id"), {"id": source_id, "data": json.dumps(evidence)})
            preview = get_product(conn, account="XL", candidate_episode_id=candidate).price_preview
            self.assertEqual((preview["baseCents"], preview["targetCents"], preview["selectedSource"]), (15000, 13099, "AFTERCOOL_START_PRICE"))
            evidence["aftercoolLookup"]["account"] = "JV"
            conn.execute(text("UPDATE source_products SET attributes_json=CAST(:data AS jsonb) WHERE id=:id"), {"id": source_id, "data": json.dumps(evidence)})
            self.assertIsNone(get_product(conn, account="XL", candidate_episode_id=candidate).price_preview)
            evidence["syncSnapshot"] = {"sourceType": "AFTERCOOL", "sourceSku": sku, "sourceEan": ean,
                                        "ottoStandardPriceCents": 10000, "aftercoolStartPriceCents": 15000,
                                        "targetPriceCents": 13599, "capturedAt": "2026-11-10T00:00:00+00:00"}
            conn.execute(text("UPDATE source_products SET attributes_json=CAST(:data AS jsonb) WHERE id=:id"), {"id": source_id, "data": json.dumps(evidence)})
            self.assertEqual(get_product(conn, account="XL", candidate_episode_id=candidate).price_preview["targetCents"], 13099)
            ean2 = conn.execute(text("SELECT confirmed_ean FROM candidate_episodes WHERE id=:id"), {"id": candidate2}).scalar_one()
            evidence2 = {"ottoPricesData": {"sku": sku2, "standardPrice": {"amount": "120.00", "currency": "EUR"}},
                         "aftercoolLookup": {"status": "FOUND", "verified": True, "data_scope": "PRODUCTION", "account": "XL", "ean": ean2,
                                             "factory_id": "factory-s17", "aftercool_product_id": "124", "price_cents": 9000}}
            conn.execute(text("UPDATE source_products SET aftercool_product_id='124', attributes_json=CAST(:data AS jsonb) WHERE id=:id"), {"id": source2, "data": json.dumps(evidence2)})
            sorted_items = list_products(conn, account="XL", sort_by="price", sort_dir="asc").items
            self.assertEqual([item.id for item in sorted_items], [candidate2, candidate, unknown_price_candidate])
            self.assertEqual([item.price_preview["baseCents"] if item.price_preview else None for item in sorted_items], [12000, 15000, None])

    def test_catalog_pagination_and_filters_cover_complete_factory_and_account(self):
        _, first, _, _ = self.stage_candidate(account="JV", factory_id="a", run_mode="BASELINE")
        _, second, _, _ = self.stage_candidate(account="JV", factory_id="a", run_mode="BASELINE")
        self.stage_candidate(account="JV", factory_id="b", run_mode="BASELINE")
        self.stage_candidate(account="XL", factory_id="a", run_mode="BASELINE")
        with self.engine.begin() as conn:
            save_product_edit(conn, account="JV", candidate_id=first, expected_version=0, title="A " + "x" * 80, category="Sofa", attributes={}, actor="operator")
            save_product_edit(conn, account="JV", candidate_id=second, expected_version=0, title="B unique", category="Bett", attributes={}, actor="operator")
            first_page = list_products(conn, account="JV", source="AFTERBUY", factory_id="a", page=1, page_size=1, sort_by="name")
            second_page = list_products(conn, account="JV", source="AFTERBUY", factory_id="a", page=2, page_size=1, sort_by="name")
            long_titles = list_products(conn, account="JV", factory_id="a", title_over_70=True)
            searched = list_products(conn, account="JV", factory_id="a", query="B unique")
            sofas = list_products(conn, account="JV", factory_id="a", category="Sofa")
        self.assertEqual((first_page.page.total, first_page.page.total_pages), (2, 2))
        self.assertEqual((first_page.items[0].id, second_page.items[0].id), (first, second))
        self.assertEqual(first_page.metrics.scope, "CATALOG_OPEN_CANDIDATES")
        self.assertEqual((first_page.metrics.total, first_page.metrics.over_70_count), (2, 1))
        self.assertEqual(set(first_page.metrics.categories), {"Sofa", "Bett"})
        self.assertEqual([item.id for item in long_titles.items], [first])
        self.assertEqual([item.id for item in searched.items], [second])
        self.assertEqual([item.id for item in sofas.items], [first])

    def test_catalog_list_can_skip_aggregates_and_omit_raw_source_data(self):
        source_id, candidate, _, _ = self.stage_candidate(account="XL", run_mode="BASELINE")
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE source_products SET attributes_json=CAST(:data AS jsonb) WHERE id=:id"),
                {"data": json.dumps({"diagnosticFixture": "detail-only"}), "id": source_id},
            )
            listed = list_products(conn, account="XL", include_metrics=False)
            detail = get_product(conn, account="XL", candidate_episode_id=candidate)

        self.assertIsNone(listed.metrics)
        self.assertEqual([item.id for item in listed.items], [candidate])
        self.assertEqual(listed.items[0].source_data, {})
        self.assertEqual(detail.source_data["diagnosticFixture"], "detail-only")

    def test_down_migration_refuses_to_drop_saved_edits(self):
        _, candidate, _, _ = self.stage_candidate(account="JV", run_mode="BASELINE")
        with self.engine.begin() as conn:
            save_product_edit(conn, account="JV", candidate_id=candidate, expected_version=0, title="Saved", category="", attributes={}, actor="operator")
        down = (Path(__file__).resolve().parents[1] / "migrations" / "0019_product_edits.down.sql").read_text()
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.exec_driver_sql(down)
        with self.engine.begin() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM restart_product_edits")).scalar_one(), 1)
            conn.execute(text("DELETE FROM restart_product_edits"))
            conn.exec_driver_sql(down)
            self.assertIsNone(conn.execute(text("SELECT to_regclass('public.restart_product_edits')")).scalar_one())

    def test_http_permissions_csrf_idempotency_and_conflict(self):
        _, jv, _, _ = self.stage_candidate(account="JV", run_mode="BASELINE")
        _, xl, _, _ = self.stage_candidate(account="XL", run_mode="BASELINE")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        env.update({"OTTORESTART_DATABASE_URL": self.database_url.render_as_string(hide_password=False),
                    "OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_INTERNAL_AUTH_SECRET": SECRET,
                    "OTTORESTART_OPERATOR_ID": "operator-1", "OTTORESTART_OPERATOR_ACCOUNTS": "JV,XL",
                    "OTTORESTART_ALLOWED_ORIGINS": "http://testserver"})
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}/api/restart"
        def call(method, path, *, token=None, body=None, key="edit-test-0001"):
            headers = {"Origin": "http://testserver", "X-CSRF-Token": "offline-csrf", "Idempotency-Key": key}
            if token: headers["Authorization"] = f"Bearer {token}"
            data = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode()
            try:
                with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=5) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                return error.code, json.loads(error.read())
        try:
            for _ in range(100):
                try:
                    if call("GET", "/health")[0] == 200: break
                except (URLError, ConnectionError):
                    if process.poll() is not None: self.fail("Isolated API failed to start")
                    time.sleep(.1)
            else:
                self.fail("Isolated API did not become healthy")
            path = f"/products/{jv}/edit?account=JV"
            token = encode_token(current_claims(accounts=["JV"]))
            self.assertEqual(call("GET", path)[0], 401)
            self.assertEqual(call("GET", path, token=encode_token(current_claims(accounts=["XL"])))[0], 403)
            self.assertEqual(call("GET", path, token=token)[1], {"edit": None})
            catalog_status, catalog = call("GET", "/products?account=JV&includeMetrics=false", token=token)
            self.assertEqual(catalog_status, 200)
            self.assertIsNone(catalog["metrics"])
            self.assertEqual(catalog["items"][0]["sourceData"], {})
            body = {"account": "JV", "expectedVersion": 0, "title": "Revised", "category": "Sofa", "attributes": {"Farbe": "Blau"}}
            self.assertEqual(call("PUT", path, token=encode_token(current_claims(accounts=["JV"], csrf=False)), body=body)[0], 403)
            first = call("PUT", path, token=token, body=body)
            self.assertEqual(first[0], 200)
            self.assertEqual(first[1]["edit"]["version"], 1)
            self.assertEqual(call("PUT", path, token=token, body=body)[1], first[1])
            conflict = call("PUT", path, token=token, body={**body, "title": "Other"}, key="edit-test-0002")
            self.assertEqual((conflict[0], conflict[1]["code"]), (409, "VERSION_CONFLICT"))
            self.assertEqual(call("GET", path, token=token)[1]["edit"]["title"], "Revised")
            xl_token = encode_token(current_claims(accounts=["XL"]))
            xl_path = f"/products/{xl}/edit?account=XL"
            self.assertEqual(call("PUT", xl_path, token=xl_token, body={**body, "account": "XL", "title": "XL"}, key="edit-test-0003")[0], 200)
            self.assertEqual(call("GET", xl_path, token=xl_token)[1]["edit"]["title"], "XL")
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def test_scoped_roundtrip_preserves_source_and_catalog_status(self):
        source_jv, jv, _, _ = self.stage_candidate(account="JV", run_mode="BASELINE")
        source_xl, xl, _, _ = self.stage_candidate(account="XL", run_mode="BASELINE")
        with self.engine.begin() as conn:
            before = conn.execute(text("SELECT id, original_title, category, attributes_json FROM source_products WHERE id IN (:jv,:xl) ORDER BY id"), {"jv": source_jv, "xl": source_xl}).all()
            self.assertIsNone(get_product_edit(conn, account="JV", candidate_id=jv)["edit"])
            first = save_product_edit(conn, account="JV", candidate_id=jv, expected_version=0, title="JV title", category="Sofa", attributes={"Farbe": "Blau"}, actor="operator")
            self.assertEqual(first["edit"]["version"], 1)
            save_product_edit(conn, account="XL", candidate_id=xl, expected_version=0, title="XL title", category="Bett", attributes={}, actor="operator")
            self.assertEqual(get_product_edit(conn, account="JV", candidate_id=jv)["edit"]["title"], "JV title")
            self.assertEqual(get_product(conn, account="JV", candidate_episode_id=jv).saved_edit["title"], "JV title")
            self.assertEqual(get_product(conn, account="XL", candidate_episode_id=xl).saved_edit["title"], "XL title")
            with self.assertRaises(OperationError) as forbidden:
                get_product_edit(conn, account="XL", candidate_id=jv)
            self.assertEqual(forbidden.exception.code, "PRODUCT_NOT_FOUND")
            after = conn.execute(text("SELECT id, original_title, category, attributes_json FROM source_products WHERE id IN (:jv,:xl) ORDER BY id"), {"jv": source_jv, "xl": source_xl}).all()
            self.assertEqual(before, after)
            self.assertEqual(conn.execute(text("SELECT count(*) FROM restart_operations")).scalar_one(), 0)

    def test_two_writers_with_same_version_cannot_overwrite_each_other(self):
        _, candidate, _, _ = self.stage_candidate(account="JV", run_mode="BASELINE")
        with self.engine.begin() as conn:
            save_product_edit(conn, account="JV", candidate_id=candidate, expected_version=0, title="Initial", category="", attributes={}, actor="operator")
        barrier = Barrier(2)
        def write(title):
            barrier.wait()
            try:
                with self.engine.begin() as conn:
                    save_product_edit(conn, account="JV", candidate_id=candidate, expected_version=1, title=title, category="", attributes={}, actor="operator")
                return "saved"
            except OperationError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ["First", "Second"]))
        self.assertCountEqual(results, ["saved", "VERSION_CONFLICT"])
        with self.engine.connect() as conn:
            self.assertEqual(get_product_edit(conn, account="JV", candidate_id=candidate)["edit"]["version"], 2)

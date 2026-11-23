from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.aftercool_client import AftercoolClient, AftercoolCredentials, AftercoolHttpResponse
from integrations.otto_client import HttpResponse, OttoApiError, OttoCredentials, OttoMarketClient
from integrations.otto_sqlite_snapshot import (
    EXPECTED_ACCOUNT_BRAND_IDS,
    SqliteSnapshotOttoClient,
    resolve_snapshot_db_path,
)
from jobs.celery_transport import DEFAULT_QUEUE, IMPORT_QUEUE, CeleryJobTransport
from jobs.service import DispatchClaim
from runtime_mode import RuntimeConfig, RuntimeMode


class SqliteSnapshotAndResilienceTests(unittest.TestCase):
    def _create_sample_sqlite(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE products (sku TEXT PRIMARY KEY, ean TEXT, raw_json TEXT)")
        conn.execute("CREATE TABLE product_marketplace_status (sku TEXT PRIMARY KEY, status TEXT, moin TEXT, errors_json TEXT, raw_json TEXT)")
        conn.execute("CREATE TABLE product_prices (sku TEXT, source TEXT, standard_amount TEXT, currency TEXT, vat TEXT, raw_json TEXT)")

        jv_product = {
            "sku": "4062292451157",
            "ean": "4062292451157",
            "productDescription": {
                "brandId": EXPECTED_ACCOUNT_BRAND_IDS["JV"],
                "category": "Dekosäule",
                "productLine": "Antik Stil XXL Säule",
            },
            "pricing": {"standardPrice": {"amount": 2929, "currency": "EUR"}},
        }
        wrong_brand_product = {
            "sku": "4067282937215",
            "ean": "4067282937215",
            "productDescription": {
                "brandId": EXPECTED_ACCOUNT_BRAND_IDS["XL"],
                "category": "Sofa",
                "productLine": "XL Item",
            },
        }
        conn.execute("INSERT INTO products VALUES (?, ?, ?)", ("4062292451157", "4062292451157", json.dumps(jv_product)))
        conn.execute("INSERT INTO products VALUES (?, ?, ?)", ("4067282937215", "4067282937215", json.dumps(wrong_brand_product)))
        conn.execute(
            "INSERT INTO product_marketplace_status VALUES (?, ?, ?, ?, ?)",
            (
                "4062292451157",
                "REJECTED",
                "M2DV9TEST1",
                json.dumps([{"code": "602016 - LOW_PERFORMER", "title": "Low"}]),
                json.dumps({
                    "sku": "4062292451157",
                    "moin": "M2DV9TEST1",
                    "status": "REJECTED",
                    "errors": [{"code": "602016 - LOW_PERFORMER", "title": "Low"}],
                }),
            ),
        )
        conn.execute(
            "INSERT INTO product_prices VALUES (?, ?, ?, ?, ?, ?)",
            ("4062292451157", "products_v5", "2929.0", "EUR", "FULL", json.dumps({"sku": "4062292451157", "standardPrice": {"amount": 2929.0, "currency": "EUR"}})),
        )
        conn.commit()
        conn.close()

    def test_sqlite_snapshot_reads_product_status_prices_enforces_brand_and_falls_back(self) -> None:
        class FallbackClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get_marketplace_status(self, sku: str):
                self.calls.append(("status", sku))
                return {"sku": sku, "status": "ONLINE", "errors": []}

            def get_product(self, sku: str):
                self.calls.append(("product", sku))
                return {"sku": sku, "ean": sku, "productDescription": {"productLine": "Live Fallback"}}

            def get_product_prices(self, sku: str):
                self.calls.append(("prices", sku))
                return {"sku": sku, "standardPrice": {"amount": 99.0, "currency": "EUR"}}

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "otto_jv_subset.db"
            self._create_sample_sqlite(db_path)

            resolved = resolve_snapshot_db_path("JV", {"OTTORESTART_OTTO_SNAPSHOT_DIR": tmp})
            self.assertEqual(resolved, db_path)

            fallback = FallbackClient()
            client = SqliteSnapshotOttoClient("JV", db_path, fallback_client=fallback)
            try:
                status = client.get_marketplace_status("4062292451157")
                product = client.get_product("4062292451157")
                prices = client.get_product_prices("4062292451157")
                self.assertEqual(status["status"], "REJECTED")
                self.assertEqual(product["productDescription"]["productLine"], "Antik Stil XXL Säule")
                self.assertEqual(prices["standardPrice"]["amount"], 2929.0)
                self.assertEqual(fallback.calls, [])

                # Cross-account XL product stored in a JV DB must fail closed
                with self.assertRaises(OttoApiError) as brand_err:
                    client.get_product("4067282937215")
                self.assertEqual(brand_err.exception.code, "ACCOUNT_CREDENTIAL_MISMATCH")

                # Missing SKU falls back to live OTTO client transparently
                fb_prod = client.get_product("9999999999999")
                self.assertEqual(fb_prod["productDescription"]["productLine"], "Live Fallback")
                self.assertEqual(fallback.calls, [("product", "9999999999999")])
            finally:
                client.close()

    def test_extract_subset_script_builds_compact_sqlite(self) -> None:
        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            import extract_otto_sqlite_subset as subset_tool
        finally:
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            full_db = tmp_path / "otto_jv_full.db"
            out_db = tmp_path / "otto_jv_subset.db"
            csv_file = tmp_path / "import.csv"
            csv_file.write_text("sku;period\n4062292451157;12M\n0000000000000;3M\n", encoding="utf-8")
            self._create_sample_sqlite(full_db)

            identifiers = subset_tool.collect_identifiers([csv_file])
            stats = subset_tool.extract_subset(full_db, out_db, identifiers, compress=False)
            self.assertEqual(stats["matched_products"], 1)
            self.assertEqual(stats["matched_status"], 1)
            self.assertEqual(stats["matched_prices"], 1)
            self.assertTrue(out_db.is_file())

    def test_otto_and_aftercool_env_clients_retry_429_with_cooldown(self) -> None:
        class FakeOttoTransport:
            def __init__(self, responses):
                self._responses = list(responses)
                self.requests = []

            def request(self, method, url, headers, body, timeout_seconds):
                self.requests.append((method, url))
                return self._responses.pop(0)

        class RecordingLimiter:
            def __init__(self):
                self.acquired = 0
                self.cooldowns = []

            def acquire(self):
                self.acquired += 1

            def cooldown(self, seconds):
                self.cooldowns.append(seconds)

        otto_transport = FakeOttoTransport([
            HttpResponse(200, {}, json.dumps({"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"}).encode()),
            HttpResponse(429, {"Retry-After": "1.5"}, b""),
            HttpResponse(200, {}, json.dumps({"sku": "sku-1", "status": "ONLINE"}).encode()),
        ])
        limiter = RecordingLimiter()
        otto = OttoMarketClient(
            "JV",
            OttoCredentials(account="JV", client_id="id", client_secret="sec"),
            RuntimeConfig(mode=RuntimeMode.PRODUCTION, otto_read_enabled=True),
            transport=otto_transport,
            rate_limiter=limiter,
            max_429_retries=2,
            sleep=lambda _s: None,
        )
        res = otto.get_marketplace_status("sku-1")
        self.assertEqual(res["status"], "ONLINE")
        self.assertEqual(limiter.cooldowns, [1.5])
        self.assertEqual(len(otto_transport.requests), 3)

        class FakeAcTransport:
            def __init__(self, responses):
                self._responses = list(responses)
                self.requests = []

            def request(self, method, url, headers, body, timeout_seconds):
                self.requests.append((method, url))
                return self._responses.pop(0)

        ac_transport = FakeAcTransport([
            AftercoolHttpResponse(200, {"Set-Cookie": "session=s1"}, b""),
            AftercoolHttpResponse(429, {"Retry-After": "2.0"}, b""),
            AftercoolHttpResponse(200, {}, json.dumps({"items": [], "total": 0}).encode()),
        ])
        ac_limiter = RecordingLimiter()
        ac = AftercoolClient(
            AftercoolCredentials(username="u", password="p"),
            transport=ac_transport,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            rate_limiter=ac_limiter,
            max_429_retries=2,
            sleep=lambda _s: None,
        )
        out = ac.lookup_product("XL", "4006381333931")
        self.assertEqual(out["status"], "NOT_FOUND_CONFIRMED")
        self.assertEqual(ac_limiter.cooldowns, [2.0])

    def test_celery_transport_routes_import_process_to_dedicated_import_queue(self) -> None:
        calls = []

        class RecordingApp:
            def send_task(self, *args, **kwargs):
                calls.append((args, kwargs))

        transport = CeleryJobTransport(RecordingApp())
        transport.publish_claimed_job(DispatchClaim(job_id="00000000-0000-0000-0000-000000000011", lease_token="l1", generation=0, job_type="import.process"))
        transport.publish_claimed_job(DispatchClaim(job_id="00000000-0000-0000-0000-000000000012", lease_token="l2", generation=0, job_type="drip.release_item"))
        self.assertEqual(calls[0][1]["queue"], IMPORT_QUEUE)
        self.assertEqual(calls[1][1]["queue"], DEFAULT_QUEUE)


if __name__ == "__main__":
    unittest.main()

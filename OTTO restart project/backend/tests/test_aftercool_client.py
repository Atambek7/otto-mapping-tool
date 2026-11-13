from __future__ import annotations

import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from integrations.aftercool_client import (
    AftercoolApiError,
    AftercoolClient,
    AftercoolCredentials,
    AftercoolHttpResponse,
)
from runtime_mode import RuntimeConfig, RuntimeMode
from services.aftercool_lookup_service import AftercoolLookupService


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, headers, body, timeout_seconds):
        self.requests.append((method, url, dict(headers), body, timeout_seconds))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(status=200, payload=None, headers=None):
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return AftercoolHttpResponse(status, headers or {}, body)


def login(session="test-session", status=200):
    return response(status, headers={"Set-Cookie": f"session={session}; Path=/; HttpOnly"})


def product(ean="4006381333931", *, product_id="ac-1", account="XL", factory_id="f-1", sku="sku-1", source_file=None):
    result = {
        "id": product_id,
        "ean": ean,
        "sku": sku,
        "account": account,
        "factory_id": factory_id,
        "factory_name": "Factory One",
        "name": "Verified item",
        "row": {
            "EAN": ean,
            "Startpreis": "12,34 €",
            "PictureURLs": ["https://images.example/a.jpg", "https://images.example/b.jpg"],
            "category": "Furniture",
        },
    }
    if source_file is not None:
        result["source_file"] = source_file
    return result


def client(
    transport, *, account_mode=RuntimeMode.PRODUCTION, enabled=True, sleep=None, max_pages=20,
    factory_exclusion_ids=None, factory_exclusion_path=None,
):
    return AftercoolClient(
        AftercoolCredentials("aftercool-user", "aftercool-password"),
        runtime_config=RuntimeConfig(mode=account_mode, aftercool_read_enabled=enabled),
        transport=transport,
        max_pages=max_pages,
        sleep=sleep or (lambda _: None),
        factory_exclusion_ids=factory_exclusion_ids,
        factory_exclusion_path=factory_exclusion_path,
    )


class AftercoolClientTests(unittest.TestCase):
    def test_read_requires_production_mode_and_flag_before_http(self):
        for mode, enabled, expected in (
            (RuntimeMode.LEGACY_READ_ONLY, True, "AFTERCOOL_PRODUCTION_MODE_REQUIRED"),
            (RuntimeMode.PRODUCTION, False, "AFTERCOOL_READ_DISABLED"),
        ):
            transport = FakeTransport()
            with self.subTest(mode=mode, enabled=enabled):
                with self.assertRaises(AftercoolApiError) as raised:
                    client(transport, account_mode=mode, enabled=enabled).lookup_product("XL", "4006381333931")
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(transport.requests, [])

    def test_credentials_are_redacted_and_required(self):
        credentials = AftercoolCredentials.from_environment({
            "OTTORESTART_AFTERCOOL_USERNAME": "private-user",
            "OTTORESTART_AFTERCOOL_PASSWORD": "private-password",
        })
        self.assertNotIn("private-user", repr(credentials))
        self.assertNotIn("private-password", repr(credentials))
        with self.assertRaises(AftercoolApiError) as raised:
            AftercoolCredentials.from_environment({})
        self.assertEqual(raised.exception.code, "CREDENTIALS_MISSING")
        self.assertNotIn("private", str(raised.exception))

    def test_found_product_uses_exact_identity_start_price_and_images(self):
        transport = FakeTransport(login(), response(payload={"items": [product()], "total": 1}))
        result = client(transport).lookup_product("XL", "4006381333931")

        self.assertEqual(result["status"], "FOUND")
        self.assertTrue(result["verified"])
        self.assertEqual(result["source_type"], "AFTERCOOL")
        self.assertEqual(result["product_id"], "ac-1")
        self.assertEqual(result["ean"], "4006381333931")
        self.assertEqual(result["sku"], "sku-1")
        self.assertEqual(result["factory_id"], "f-1")
        self.assertEqual(result["factory_name"], "Factory One")
        self.assertEqual(result["name"], "Verified item")
        self.assertEqual(result["title"], "Verified item")
        self.assertEqual(result["price_cents"], 1234)
        self.assertEqual(result["image_url"], "https://images.example/a.jpg")
        self.assertEqual(result["image_urls"], [
            "https://images.example/a.jpg",
            "https://images.example/b.jpg",
        ])
        self.assertEqual(result["category"], "Furniture")
        method, url, headers, body, _ = transport.requests[0]
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(method, "POST")
        self.assertEqual(urlsplit(url).path, "/auth/login")
        self.assertNotIn(b"private", body or b"")
        method, url, headers, body, _ = transport.requests[1]
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(method, "GET")
        self.assertEqual(urlsplit(url).path, "/api/products")
        self.assertEqual(query["account"], ["XL"])
        self.assertEqual(query["dataset"], ["lister"])
        self.assertEqual(query["q"], ["4006381333931"])
        self.assertEqual(query["include_row"], ["1"])
        self.assertEqual(query["limit"], ["500"])
        self.assertEqual(query["offset"], ["0"])
        self.assertEqual(headers["Cookie"], "session=test-session")
        self.assertIsNone(body)

    def test_aftercool_name_field_is_preserved_and_preferred_to_legacy_title_fields(self):
        record = AftercoolClient._normalize_product(
            {
                **product(),
                "name": "Esstisch Esszimmertisch Tisch Esszimmer Holz Schwarz Esszimmermöbel",
                "Artikelbeschreibung": "Legacy title must not win",
                "title": "Another fallback title",
            },
            "XL",
        )
        expected = "Esstisch Esszimmertisch Tisch Esszimmer Holz Schwarz Esszimmermöbel"
        self.assertEqual(record["name"], expected)
        self.assertEqual(record["title"], expected)

    def test_jv_lookup_preserves_jv_account_scope(self):
        transport = FakeTransport(
            login(),
            response(payload={"items": [product(account="JV")], "total": 1}),
        )
        result = client(transport).lookup_product("JV", "4006381333931")
        self.assertEqual(result["status"], "FOUND")
        query = parse_qs(urlsplit(transport.requests[1][1]).query)
        self.assertEqual(query["account"], ["JV"])

    def test_only_startpreis_is_a_price_source(self):
        raw = product()
        raw["price"] = 999.99
        raw["row"].pop("Startpreis")
        transport = FakeTransport(login(), response(payload={"items": [raw], "total": 1}))
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertIsNone(result["price_cents"])

    def test_complete_empty_search_is_confirmed_not_found(self):
        transport = FakeTransport(login(), response(payload={"items": [], "total": 0}))
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "NOT_FOUND_CONFIRMED")
        self.assertFalse(result["found"])
        self.assertEqual(result["source_type"], "OTTO_DIRECT")
        self.assertIsNone(result["ean"])
        self.assertEqual(result["lookup_identifier"], "4006381333931")
        self.assertIsNone(result["title"])
        self.assertIsNone(result["category"])
        self.assertIsNone(result["price_cents"])

    def test_sku_or_product_id_match_cannot_substitute_for_the_requested_ean(self):
        transport = FakeTransport(login(), response(payload={
            "items": [product(ean="4006381333932", product_id="4006381333931", sku="4006381333931")],
            "total": 1,
        }))
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "NOT_FOUND_CONFIRMED")
        self.assertIsNone(result["ean"])

    def test_candidate_without_complete_identity_is_ambiguous_not_absent(self):
        incomplete = {"name": "Possible result", "account": "XL", "row": {"Startpreis": "8.00"}}
        transport = FakeTransport(login(), response(payload={"items": [incomplete], "total": 1}))
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "IDENTITY_INCOMPLETE")
        self.assertIsNone(result["source_type"])

    def test_duplicate_conflicting_identity_is_ambiguous(self):
        transport = FakeTransport(login(), response(payload={
            "items": [product(product_id="ac-1"), product(product_id="ac-2")],
            "total": 2,
        }))
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "DUPLICATE_IDENTIFIER")

    def test_protected_factory_duplicate_is_skipped_by_source_file(self):
        protected = product(product_id="protected-product", factory_id="not-present", source_file=r"UK\source__501456.csv")
        protected.pop("factory_id")
        eligible = product(product_id="eligible-product", factory_id="409886", source_file="SKORZANYSWIAT__PL__409886.csv")
        transport = FakeTransport(login(), response(payload={"items": [protected, eligible], "total": 2}))

        result = client(transport).lookup_product("JV", "4006381333931")

        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["product_id"], "eligible-product")
        self.assertEqual(result["factory_id"], "409886")
        self.assertEqual(result["source_file"], "SKORZANYSWIAT__PL__409886.csv")
        self.assertEqual(result["excluded_factory_match_count"], 1)
        self.assertEqual(result["duplicate_count"], 2)
        self.assertEqual(len(result["factory_exclusion_policy_sha256"]), 64)

    def test_all_exact_matches_from_protected_factories_remain_ambiguous(self):
        protected = product(product_id="protected-product", factory_id="501456")
        transport = FakeTransport(login(), response(payload={"items": [protected], "total": 1}))

        result = client(transport).lookup_product("JV", "4006381333931")

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "PROTECTED_FACTORY_MATCH")
        self.assertEqual(result["excluded_factory_ids"], ["501456"])
        self.assertEqual(len(result["factory_exclusion_policy_sha256"]), 64)
        self.assertIsNone(result["source_type"])

    def test_factory_exclusions_are_account_scoped(self):
        candidate = product(product_id="xl-product", account="XL", factory_id="501456")
        transport = FakeTransport(login(), response(payload={"items": [candidate], "total": 1}))
        exclusions = {"JV": {"501456"}, "XL": set()}

        result = client(transport, factory_exclusion_ids=exclusions).lookup_product("XL", "4006381333931")

        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["factory_id"], "501456")

    def test_unavailable_factory_exclusion_policy_fails_before_http(self):
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            missing_policy = f"{directory}/missing.json"
            with self.assertRaises(AftercoolApiError) as raised:
                client(transport, factory_exclusion_path=missing_policy).lookup_product("JV", "4006381333931")

        self.assertEqual(raised.exception.code, "FACTORY_EXCLUSION_POLICY_UNAVAILABLE")
        self.assertEqual(transport.requests, [])

    def test_reported_row_account_is_diagnostic_while_lookup_scope_stays_selected_account(self):
        transport = FakeTransport(login(), response(payload={"items": [product(account="JV")], "total": 1}))
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["account"], "XL")
        self.assertEqual(result["reported_account"], "JV")

    def test_factory_hint_mismatch_does_not_create_direct_route(self):
        transport = FakeTransport(login(), response(payload={"items": [product()], "total": 1}))
        result = client(transport).lookup_product("XL", "4006381333931", factory_hint="other-factory")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "FACTORY_HINT_MISMATCH")
        self.assertIsNone(result["source_type"])

    def test_factory_hint_filters_exact_ean_candidates_but_does_not_supply_identity(self):
        transport = FakeTransport(login(), response(payload={
            "items": [
                product(product_id="other-factory-product", factory_id="f-2"),
                product(product_id="selected-product", factory_id="f-1"),
            ],
            "total": 2,
        }))
        result = client(transport).lookup_product("XL", "4006381333931", factory_hint="f-1")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["product_id"], "selected-product")
        self.assertEqual(result["factory_id"], "f-1")

    def test_factory_name_lookup_is_account_scoped(self):
        item = product()
        item.pop("factory_name")
        transport = FakeTransport(
            login(),
            response(payload={"items": [item], "total": 1}),
            response(payload=[{"id": "f-1", "name": "Factory One", "account": "XL"}]),
        )
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["factory_name"], "Factory One")
        factory_query = parse_qs(urlsplit(transport.requests[2][1]).query)
        self.assertEqual(factory_query["account"], ["XL"])

    def test_pagination_is_complete_and_keeps_account_filter(self):
        first_page = [product(ean=f"4006381333{i:03d}", product_id=f"id-{i}") for i in range(500)]
        second_page = [product(ean="4006381333931", product_id="target")]
        transport = FakeTransport(
            login(),
            response(payload={"items": first_page, "total": 501}),
            response(payload={"items": second_page, "total": 501}),
        )
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["product_id"], "target")
        page_query = parse_qs(urlsplit(transport.requests[-1][1]).query)
        self.assertEqual(page_query["offset"], ["500"])
        self.assertEqual(page_query["account"], ["XL"])

    def test_partial_or_changing_pages_are_not_treated_as_absence(self):
        partial = FakeTransport(login(), response(payload={"items": [], "total": 1}))
        with self.assertRaises(AftercoolApiError) as raised:
            client(partial).lookup_product("XL", "4006381333931")
        self.assertEqual(raised.exception.code, "PARTIAL_RESPONSE")

        changing = FakeTransport(
            login(),
            response(payload={
                "items": [product(ean=f"4006381333{i:03d}", product_id=f"id-{i}") for i in range(500)],
                "total": 501,
            }),
            response(payload={"items": [product(ean="4006381333932")], "total": 502}),
        )
        with self.assertRaises(AftercoolApiError) as raised:
            client(changing).lookup_product("XL", "4006381333931")
        self.assertEqual(raised.exception.code, "PAGINATION_INCONSISTENT")

    def test_401_refreshes_session_once(self):
        transport = FakeTransport(
            login("expired"),
            response(401),
            login("fresh"),
            response(payload={"items": [product()], "total": 1}),
        )
        result = client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual([request[0] for request in transport.requests], ["POST", "GET", "POST", "GET"])
        self.assertEqual(transport.requests[1][2]["Cookie"], "session=expired")
        self.assertEqual(transport.requests[3][2]["Cookie"], "session=fresh")

    def test_timeout_is_bounded_and_typed(self):
        waits = []
        transport = FakeTransport(login(), TimeoutError(), TimeoutError(), TimeoutError())
        with self.assertRaises(AftercoolApiError) as raised:
            client(transport, sleep=waits.append).lookup_product("XL", "4006381333931")
        self.assertEqual(raised.exception.code, "NETWORK_ERROR")
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(waits, [0.25, 0.5])

    def test_login_authentication_failure_is_typed(self):
        transport = FakeTransport(response(401))
        with self.assertRaises(AftercoolApiError) as raised:
            client(transport).lookup_product("XL", "4006381333931")
        self.assertEqual(raised.exception.code, "AUTH_HTTP_401")
        self.assertEqual(len(transport.requests), 1)


class AftercoolLookupServiceTests(unittest.TestCase):
    def test_production_service_returns_verified_adapter_data(self):
        transport = FakeTransport(login(), response(payload={"items": [product()], "total": 1}))
        adapter = client(transport)
        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=adapter,
        )
        result = service.lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["account"], "XL")
        self.assertEqual(result["factory_id"], "f-1")
        self.assertEqual(result["image_url"], "https://images.example/a.jpg")

    def test_protected_factory_ambiguity_keeps_safe_exclusion_diagnostics(self):
        policy_hash = "a" * 64

        class ProtectedFactoryClient:
            def lookup_product(self, *args, **kwargs):
                return {
                    "status": "AMBIGUOUS", "reason_code": "PROTECTED_FACTORY_MATCH",
                    "excluded_factory_ids": ["501456"], "factory_exclusion_policy_sha256": policy_hash,
                }

        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=ProtectedFactoryClient(),
        )

        result = service.lookup_product("JV", "4006381333931")

        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "PROTECTED_FACTORY_MATCH")
        self.assertEqual(result["excluded_factory_ids"], ["501456"])
        self.assertEqual(result["factory_exclusion_policy_sha256"], policy_hash)
        self.assertIn("protected factories", result["reason"])

    def test_demo_fixture_miss_never_uses_injected_live_client(self):
        class NeverCallClient:
            def lookup_product(self, *args, **kwargs):
                raise AssertionError("DEMO miss must not use live client")

        service = AftercoolLookupService(
            live_enabled=True,
            runtime_mode=RuntimeMode.DEMO,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=NeverCallClient(),
        )
        result = service.lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "NOT_IMPLEMENTED")
        self.assertIsNone(result["source_type"])

    def test_legacy_live_enabled_flag_cannot_bypass_disabled_read_flag(self):
        class NeverCallClient:
            def lookup_product(self, *args, **kwargs):
                raise AssertionError("disabled integration must not use client")

        service = AftercoolLookupService(
            live_enabled=True,
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=False),
            client=NeverCallClient(),
        )
        result = service.lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "NOT_IMPLEMENTED")
        self.assertEqual(result["reason_code"], "AFTERCOOL_READ_DISABLED")

    def test_missing_credentials_and_http_failures_become_lookup_error_without_details(self):
        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            environment={},
        )
        missing = service.lookup_product("XL", "4006381333931")
        self.assertEqual(missing["status"], "LOOKUP_ERROR")
        self.assertEqual(missing["reason_code"], "CREDENTIALS_MISSING")
        self.assertNotIn("password", repr(missing).lower())

        class FailedClient:
            def lookup_product(self, *args, **kwargs):
                raise AftercoolApiError("HTTP_503", status_code=503, retryable=True)

        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=FailedClient(),
        )
        failed = service.lookup_product("XL", "4006381333931")
        self.assertEqual(failed["status"], "LOOKUP_ERROR")
        self.assertEqual(failed["reason_code"], "HTTP_503")

    def test_enabled_production_result_must_have_verified_identity(self):
        class UnverifiedClient:
            def lookup_product(self, *args, **kwargs):
                return {
                    "status": "FOUND",
                    "found": True,
                    "source_type": "AFTERCOOL",
                    "verified": False,
                    "data_scope": "UNVERIFIED",
                    "product_id": None,
                    "ean": "4006381333931",
                    "factory_id": "f-1",
                }

        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=UnverifiedClient(),
        )
        result = service.lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "IDENTITY_INCOMPLETE")

    def test_ean_mismatch_in_found_adapter_response_is_not_accepted(self):
        class WrongEanClient:
            def lookup_product(self, *args, **kwargs):
                return {
                    "status": "FOUND",
                    "found": True,
                    "source_type": "AFTERCOOL",
                    "verified": True,
                    "data_scope": "PRODUCTION",
                    "product_id": "ac-1",
                    "ean": "4006381333932",
                    "factory_id": "f-1",
                }

        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=WrongEanClient(),
        )
        result = service.lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["reason_code"], "IDENTITY_INCOMPLETE")

    def test_conflicting_account_evidence_is_ambiguous(self):
        class WrongAccountClient:
            def lookup_product(self, *args, **kwargs):
                raise AftercoolApiError("ACCOUNT_MISMATCH")

        service = AftercoolLookupService(
            runtime_mode=RuntimeMode.PRODUCTION,
            runtime_config=RuntimeConfig(mode=RuntimeMode.PRODUCTION, aftercool_read_enabled=True),
            client=WrongAccountClient(),
        )
        result = service.lookup_product("XL", "4006381333931")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertIsNone(result["source_type"])


if __name__ == "__main__":
    unittest.main()

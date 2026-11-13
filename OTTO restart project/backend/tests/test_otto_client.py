from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from integrations.otto_client import (
    HttpResponse,
    OttoApiError,
    OttoCredentials,
    OttoMarketClient,
)
from runtime_mode import RuntimeConfig, RuntimeMode


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


class NoWaitLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


def response(status=200, payload=None, headers=None):
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return HttpResponse(status, headers or {}, body)


def token(value="access-token", expires_in=1800):
    return response(payload={"access_token": value, "expires_in": expires_in, "token_type": "Bearer"})


def client(transport, *, account="XL", enabled=True, sleep=None, credentials=None):
    return OttoMarketClient(
        account,
        credentials or OttoCredentials(account, "client-id-" + account, "secret-" + account),
        RuntimeConfig(mode=RuntimeMode.LEGACY_READ_ONLY, otto_read_enabled=enabled),
        transport=transport,
        rate_limiter=NoWaitLimiter(),
        sleep=sleep or (lambda _: None),
    )


def write_client(transport, *, account="XL", mode=RuntimeMode.PRODUCTION, enabled=True, read_enabled=True):
    return OttoMarketClient(
        account,
        OttoCredentials(account, "client-id-" + account, "secret-" + account),
        RuntimeConfig(
            mode=mode,
            otto_read_enabled=read_enabled,
            product_write_enabled=enabled,
        ),
        transport=transport,
        rate_limiter=NoWaitLimiter(),
    )


def availability_write_client(
    transport, *, account="XL", mode=RuntimeMode.PRODUCTION, enabled=True, read_enabled=True
):
    return OttoMarketClient(
        account,
        OttoCredentials(account, "client-id-" + account, "secret-" + account),
        RuntimeConfig(
            mode=mode,
            otto_read_enabled=read_enabled,
            availability_write_enabled=enabled,
        ),
        transport=transport,
        rate_limiter=NoWaitLimiter(),
    )


def product_payload(*, account="XL", sku="target-sku-1", ean="4006381333931"):
    return {
        "account": account,
        "productReference": "reference-1",
        "sku": sku,
        "ean": ean,
        "productDescription": {"category": "confirmed-category"},
    }


class OttoMarketClientTests(unittest.TestCase):
    def test_read_disabled_fails_before_token_or_http(self):
        transport = FakeTransport()
        with self.assertRaises(OttoApiError) as raised:
            client(transport, enabled=False).get_product("sku-1")
        self.assertEqual(raised.exception.code, "OTTO_READ_DISABLED")
        self.assertEqual(transport.requests, [])

    def test_product_write_requires_production_mode_and_dedicated_flag_before_http(self):
        cases = (
            (RuntimeMode.LEGACY_READ_ONLY, True, True, "OTTO_PRODUCT_WRITE_MODE_DISABLED"),
            (RuntimeMode.TEST, True, True, "OTTO_PRODUCT_WRITE_MODE_DISABLED"),
            (RuntimeMode.PRODUCTION, False, True, "OTTO_PRODUCT_WRITE_DISABLED"),
            (RuntimeMode.PRODUCTION, True, False, "OTTO_READ_DISABLED"),
        )
        for mode, enabled, read_enabled, expected_code in cases:
            transport = FakeTransport()
            with self.subTest(mode=mode, enabled=enabled, read_enabled=read_enabled):
                with self.assertRaises(OttoApiError) as raised:
                    write_client(
                        transport, mode=mode, enabled=enabled, read_enabled=read_enabled
                    ).submit_product_payload(
                        product_payload(), expected_sku="target-sku-1", expected_ean="4006381333931"
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(transport.requests, [])

    def test_product_write_checks_account_and_exact_target_identity_before_http(self):
        for payload, account, sku, ean, expected_code in (
            (product_payload(account="JV"), "XL", "target-sku-1", "4006381333931", "ACCOUNT_CREDENTIAL_MISMATCH"),
            (product_payload(), "XL", "other-sku", "4006381333931", "PRODUCT_IDENTITY_MISMATCH"),
            (product_payload(), "XL", "target-sku-1", "4006381333999", "PRODUCT_IDENTITY_MISMATCH"),
        ):
            transport = FakeTransport()
            with self.subTest(expected_code=expected_code, sku=sku, ean=ean):
                with self.assertRaises(OttoApiError) as raised:
                    write_client(transport, account=account).submit_product_payload(
                        payload, expected_sku=sku, expected_ean=ean
                    )
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(transport.requests, [])

    def test_product_write_posts_one_exact_sku_once_and_parses_async_links(self):
        transport = FakeTransport(
            token("write-token"),
            response(202, {"links": [{"rel": "self", "href": "/v5/products/update-tasks/01234567-89ab-4cde-8fab-0123456789ab"}]}),
        )
        result = write_client(transport).submit_product_payload(
            product_payload(), expected_sku="target-sku-1", expected_ean="4006381333931"
        )

        self.assertEqual(result.status_code, 202)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.response["links"][0]["rel"], "self")
        self.assertEqual([request[0] for request in transport.requests], ["POST", "POST"])
        self.assertEqual(urlsplit(transport.requests[0][1]).path, "/v1/token")
        method, url, headers, body, _timeout = transport.requests[1]
        self.assertEqual(method, "POST")
        self.assertEqual(urlsplit(url).path, "/v5/products")
        self.assertEqual(headers["Authorization"], "Bearer write-token")
        submitted = json.loads(body)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["sku"], "target-sku-1")
        self.assertEqual(submitted[0]["ean"], "4006381333931")

    def test_product_write_429_is_returned_without_retry(self):
        transport = FakeTransport(token(), response(429, headers={"Retry-After": "17"}))
        result = write_client(transport).submit_product_payload(
            product_payload(), expected_sku="target-sku-1", expected_ean="4006381333931"
        )
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.retry_after_seconds, 17.0)
        self.assertEqual([request[0] for request in transport.requests], ["POST", "POST"])

    def test_product_write_transport_failure_is_unknown_and_never_retried(self):
        transport = FakeTransport(token(), TimeoutError("sensitive transport detail"))
        result = write_client(transport).submit_product_payload(
            product_payload(), expected_sku="target-sku-1", expected_ean="4006381333931"
        )
        self.assertIsNone(result.status_code)
        self.assertEqual(result.error_code, "PRODUCT_WRITE_OUTCOME_UNKNOWN")
        self.assertEqual(result.response, None)
        self.assertEqual([request[0] for request in transport.requests], ["POST", "POST"])
        self.assertNotIn("sensitive transport detail", repr(result))

    def test_availability_write_requires_production_mode_and_separate_flag(self):
        cases = (
            (RuntimeMode.LEGACY_READ_ONLY, True, True, "OTTO_AVAILABILITY_WRITE_MODE_DISABLED"),
            (RuntimeMode.TEST, True, True, "OTTO_AVAILABILITY_WRITE_MODE_DISABLED"),
            (RuntimeMode.PRODUCTION, False, True, "OTTO_AVAILABILITY_WRITE_DISABLED"),
            (RuntimeMode.PRODUCTION, True, False, "OTTO_READ_DISABLED"),
        )
        for mode, enabled, read_enabled, code in cases:
            transport = FakeTransport()
            with self.subTest(mode=mode, enabled=enabled, read_enabled=read_enabled):
                with self.assertRaises(OttoApiError) as raised:
                    availability_write_client(
                        transport, mode=mode, enabled=enabled, read_enabled=read_enabled
                    ).update_quantity("target-sku-1", 3)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(transport.requests, [])

    def test_availability_writes_validate_explicit_single_sku_inputs_before_http(self):
        transport = FakeTransport()
        otto = availability_write_client(transport)
        for quantity in (-1, True, 2.5):
            with self.subTest(quantity=quantity), self.assertRaises(OttoApiError) as raised:
                otto.update_quantity("target-sku-1", quantity)
            self.assertEqual(raised.exception.code, "AVAILABILITY_QUANTITY_INVALID")
        with self.assertRaises(OttoApiError) as raised:
            otto.update_delivery_information(
                "target-sku-1", shipping_profile_id="invalid", processing_time="DEFAULT"
            )
        self.assertEqual(raised.exception.code, "INVALID_SHIPPING_PROFILE_ID")
        with self.assertRaises(OttoApiError) as raised:
            otto.update_delivery_information(
                "target-sku-1", shipping_profile_id="38427776-7383-4d65-82c9-dbe949d0573a",
                processing_time="",
            )
        self.assertEqual(raised.exception.code, "PROCESSING_TIME_REQUIRED")
        for invalid_processing_time in ("0", "100", "01", "FAST"):
            with self.subTest(processing_time=invalid_processing_time):
                with self.assertRaises(OttoApiError) as raised:
                    otto.update_delivery_information(
                        "target-sku-1", shipping_profile_id="38427776-7383-4d65-82c9-dbe949d0573a",
                        processing_time=invalid_processing_time,
                    )
                self.assertEqual(raised.exception.code, "PROCESSING_TIME_REQUIRED")
        self.assertEqual(transport.requests, [])

    def test_quantity_update_is_one_sku_post_and_does_not_invent_stock(self):
        transport = FakeTransport(
            token(), response(204),
        )
        result = availability_write_client(transport).update_quantity("target-sku-1", 0)
        self.assertEqual(result.status_code, 204)
        self.assertIsNone(result.error_code)
        self.assertEqual([request[0] for request in transport.requests], ["POST", "POST"])
        method, url, headers, body, _timeout = transport.requests[1]
        self.assertEqual(method, "POST")
        self.assertEqual(urlsplit(url).path, "/v1/availability/quantities")
        self.assertEqual(headers["Authorization"], "Bearer access-token")
        self.assertEqual(json.loads(body), [{"sku": "target-sku-1", "quantity": 0}])

    def test_delivery_update_uses_only_explicit_profile_and_processing_time(self):
        profile_id = "38427776-7383-4d65-82c9-dbe949d0573a"
        transport = FakeTransport(token(), response(200, {"accepted": True}))
        result = availability_write_client(transport).update_delivery_information(
            "target-sku-1", shipping_profile_id=profile_id, processing_time="DEFAULT"
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.response, {"accepted": True})
        method, url, _headers, body, _timeout = transport.requests[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(urlsplit(url).path, "/v1/availability/product-delivery-information")
        self.assertEqual(json.loads(body), [{
            "sku": "target-sku-1", "shippingProfileId": profile_id, "processingTime": "DEFAULT",
        }])

    def test_availability_429_and_transport_failure_are_not_retried(self):
        limited = FakeTransport(token(), response(429, headers={"Retry-After": "23"}))
        result = availability_write_client(limited).update_quantity("target-sku-1", 4)
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.retry_after_seconds, 23.0)
        self.assertEqual([request[0] for request in limited.requests], ["POST", "POST"])

        failed = FakeTransport(token(), TimeoutError("private transport details"))
        result = availability_write_client(failed).update_quantity("target-sku-1", 4)
        self.assertIsNone(result.status_code)
        self.assertEqual(result.error_code, "AVAILABILITY_WRITE_OUTCOME_UNKNOWN")
        self.assertNotIn("private transport details", repr(result))
        self.assertEqual([request[0] for request in failed.requests], ["POST", "POST"])

    def test_credentials_are_loaded_by_account_without_echoing_values(self):
        settings = {
            "OTTORESTART_OTTO_XL_CLIENT_ID": "xl-id",
            "OTTORESTART_OTTO_XL_CLIENT_SECRET": "xl-secret-value",
        }
        credentials = OttoCredentials.from_environment("XL", settings)
        self.assertEqual(credentials.client_id, "xl-id")
        self.assertNotIn("xl-id", repr(credentials))
        self.assertNotIn("xl-secret-value", repr(credentials))
        with self.assertRaises(OttoApiError) as raised:
            OttoCredentials.from_environment("JV", settings)
        self.assertEqual(raised.exception.code, "CREDENTIALS_MISSING")
        self.assertNotIn("xl-secret-value", str(raised.exception))

    def test_credentials_cannot_be_reused_for_another_account(self):
        with self.assertRaises(OttoApiError) as raised:
            client(FakeTransport(), account="JV", credentials=OttoCredentials("XL", "id", "secret"))
        self.assertEqual(raised.exception.code, "ACCOUNT_CREDENTIAL_MISMATCH")

    def test_token_uses_official_form_fields_and_minimum_read_scopes(self):
        transport = FakeTransport(token(), response(payload={"sku": "sku-1", "ean": "ean-1"}))
        result = client(transport).get_product("sku-1")
        self.assertEqual(result["sku"], "sku-1")
        method, url, headers, body, timeout = transport.requests[0]
        form = parse_qs(body.decode("utf-8"))
        self.assertEqual(method, "POST")
        self.assertEqual(urlsplit(url).path, "/v1/token")
        self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")
        self.assertEqual(form["grant_type"], ["client_credentials"])
        self.assertEqual(form["client_id"], ["client-id-XL"])
        self.assertEqual(form["client_secret"], ["secret-XL"])
        self.assertEqual(form["scope"], ["products availability shipping-profiles"])
        self.assertEqual(timeout, 15.0)

    def test_account_tokens_and_cache_are_isolated(self):
        transport = FakeTransport(
            token("token-xl"), response(payload={"sku": "same-sku"}),
            token("token-jv"), response(payload={"sku": "same-sku"}),
            response(payload={"sku": "same-sku"}),
        )
        xl = client(transport, account="XL")
        jv = client(transport, account="JV")
        xl.get_product("same-sku")
        jv.get_product("same-sku")
        xl.get_product("same-sku")
        get_auth = [request[2]["Authorization"] for request in transport.requests if request[0] == "GET"]
        self.assertEqual(get_auth, ["Bearer token-xl", "Bearer token-jv", "Bearer token-xl"])

    def test_one_401_refreshes_token_and_retries_get_once(self):
        transport = FakeTransport(
            token("old-token"), response(401), token("new-token"), response(payload={"sku": "sku-1"})
        )
        result = client(transport).get_product("sku-1")
        self.assertEqual(result["sku"], "sku-1")
        self.assertEqual([r[0] for r in transport.requests], ["POST", "GET", "POST", "GET"])
        self.assertEqual(transport.requests[1][2]["Authorization"], "Bearer old-token")
        self.assertEqual(transport.requests[3][2]["Authorization"], "Bearer new-token")

    def test_repeated_401_is_typed_and_not_retried_forever(self):
        transport = FakeTransport(token("first"), response(401), token("second"), response(401))
        with self.assertRaises(OttoApiError) as raised:
            client(transport).get_product("sku-1")
        self.assertEqual(raised.exception.code, "HTTP_401")
        self.assertEqual([r[0] for r in transport.requests], ["POST", "GET", "POST", "GET"])

    def test_429_exposes_retry_after_without_immediate_retry(self):
        transport = FakeTransport(token(), response(429, headers={"Retry-After": "17"}))
        with self.assertRaises(OttoApiError) as raised:
            client(transport).get_product("sku-1")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after_seconds, 17.0)
        self.assertEqual(len(transport.requests), 2)

    def test_5xx_get_retries_are_bounded_and_typed(self):
        sleeps = []
        transport = FakeTransport(token(), response(503), response(503), response(503))
        with self.assertRaises(OttoApiError) as raised:
            client(transport, sleep=sleeps.append).get_product("sku-1")
        self.assertEqual(raised.exception.code, "HTTP_503")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(sleeps, [0.25, 0.5])

    def test_marketplace_status_pagination_stays_on_same_endpoint(self):
        transport = FakeTransport(
            token(),
            response(payload={
                "marketPlaceStatus": [{"sku": "sku-1", "status": "RESTRICTED"}],
                "links": [{"rel": "next", "href": "/v5/products/marketplace-status?sku=sku-1&page=1"}],
            }),
            response(payload={"marketPlaceStatus": [{"sku": "sku-1", "status": "ONLINE"}], "links": []}),
        )
        rows = client(transport).list_marketplace_status({"sku": "sku-1"}, max_pages=2)
        self.assertEqual([row["status"] for row in rows], ["RESTRICTED", "ONLINE"])
        self.assertEqual(urlsplit(transport.requests[-1][1]).path, "/v5/products/marketplace-status")

    def test_marketplace_status_page_iterator_is_lazy_and_keeps_all_statuses(self):
        transport = FakeTransport(
            token(),
            response(payload={
                "marketPlaceStatus": [{"sku": "sku-1", "status": "REJECTED"}],
                "links": [{"rel": "next", "href": "/v5/products/marketplace-status?fromDate=2026-11-01T00%3A00%3A00Z&limit=1000&page=1"}],
            }),
            response(payload={"marketPlaceStatus": [{"sku": "sku-2", "status": "ONLINE"}], "links": []}),
        )
        pages = client(transport).iter_marketplace_status_pages(
            {"fromDate": "2026-11-01T00:00:00Z", "limit": 1000}, max_pages=2
        )
        first = next(pages)
        self.assertEqual(first, [{"sku": "sku-1", "status": "REJECTED"}])
        self.assertEqual(len(transport.requests), 2)  # token plus one GET; page 2 is not buffered
        self.assertNotIn("marketPlaceStatus", urlsplit(transport.requests[1][1]).query)
        self.assertEqual(next(pages), [{"sku": "sku-2", "status": "ONLINE"}])
        with self.assertRaises(StopIteration):
            next(pages)

    def test_marketplace_status_iterator_detects_pagination_loop_and_page_cap(self):
        loop_transport = FakeTransport(
            token(),
            response(payload={"marketPlaceStatus": [], "links": [{"rel": "next", "href": "/v5/products/marketplace-status?fromDate=x&page=1"}]}),
            response(payload={"marketPlaceStatus": [], "links": [{"rel": "next", "href": "/v5/products/marketplace-status?fromDate=x&page=1"}]}),
        )
        with self.assertRaises(OttoApiError) as raised:
            list(client(loop_transport).iter_marketplace_status_pages({"fromDate": "x"}, max_pages=3))
        self.assertEqual(raised.exception.code, "PAGINATION_LOOP")

        cap_transport = FakeTransport(
            token(),
            response(payload={"marketPlaceStatus": [], "links": [{"rel": "next", "href": "/v5/products/marketplace-status?fromDate=x&page=1"}]}),
        )
        with self.assertRaises(OttoApiError) as raised:
            list(client(cap_transport).iter_marketplace_status_pages({"fromDate": "x"}, max_pages=1))
        self.assertEqual(raised.exception.code, "PAGINATION_LIMIT_REACHED")

    def test_marketplace_status_rejects_wrong_sku(self):
        transport = FakeTransport(token(), response(payload={"sku": "other-sku", "status": "ONLINE"}))
        with self.assertRaises(OttoApiError) as raised:
            client(transport).get_marketplace_status("requested-sku")
        self.assertEqual(raised.exception.code, "SKU_MISMATCH")

    def test_pagination_cannot_drop_the_requested_sku_filter(self):
        transport = FakeTransport(
            token(),
            response(payload={
                "marketPlaceStatus": [],
                "links": [{"rel": "next", "href": "/v5/products/marketplace-status?page=1"}],
            }),
        )
        with self.assertRaises(OttoApiError) as raised:
            client(transport).list_marketplace_status({"sku": "sku-1"})
        self.assertEqual(raised.exception.code, "PAGINATION_SCOPE_CHANGED")
        self.assertEqual(len(transport.requests), 2)

    def test_filtered_list_rejects_unexpected_sku_and_unbounded_calls(self):
        with self.assertRaises(OttoApiError) as raised:
            client(FakeTransport()).list_products({})
        self.assertEqual(raised.exception.code, "FILTER_REQUIRED")

        transport = FakeTransport(token(), response(payload={"productVariations": [{"sku": "other"}], "links": []}))
        with self.assertRaises(OttoApiError) as raised:
            client(transport).list_products({"sku": "requested"})
        self.assertEqual(raised.exception.code, "SKU_MISMATCH")

    def test_missing_or_wrong_product_price_is_never_silently_fallback(self):
        transport = FakeTransport(token(), response(payload={"sku": "sku-1", "sale": {"salePrice": {"amount": 9.0}}}))
        with self.assertRaises(OttoApiError) as raised:
            client(transport).get_product_prices("sku-1")
        self.assertEqual(raised.exception.code, "MISSING_STANDARD_PRICE")

        transport = FakeTransport(token(), response(payload={"sku": "other", "standardPrice": {"amount": 12.0, "currency": "EUR"}}))
        with self.assertRaises(OttoApiError) as raised:
            client(transport).get_product_prices("sku-1")
        self.assertEqual(raised.exception.code, "SKU_MISMATCH")

    def test_product_price_accepts_standard_price_only(self):
        transport = FakeTransport(token(), response(payload={
            "sku": "sku-1", "standardPrice": {"amount": 12.34, "currency": "EUR"},
            "sale": {"salePrice": {"amount": 8.99, "currency": "EUR"}},
        }))
        result = client(transport).get_product_prices("sku-1")
        self.assertEqual(result["standardPrice"]["amount"], 12.34)
        self.assertEqual(urlsplit(transport.requests[-1][1]).path, "/v5/products/sku-1/prices")

    def test_task_href_rejects_other_host_and_non_task_path_before_network(self):
        transport = FakeTransport()
        otto = client(transport)
        for href in (
            "https://evil.example/v5/products/update-tasks/123/succeeded",
            "https://api.otto.market:444/v5/products/update-tasks/00000000-0000-4000-8000-000000000000",
            "/v5/products/active-status",
            "//evil.example/v5/products/update-tasks/00000000-0000-4000-8000-000000000000",
        ):
            with self.subTest(href=href), self.assertRaises(OttoApiError) as raised:
                otto.read_update_task_href(href)
            self.assertEqual(raised.exception.code, "UNTRUSTED_OTTO_URL")
        self.assertEqual(transport.requests, [])

    def test_update_task_results_use_only_known_result_paths(self):
        task_id = "09abe6ca-9c36-45bb-9cd3-a7de7f93284b"
        transport = FakeTransport(token(), response(payload={"results": [{"variation": "sku-1", "errors": []}]}))
        rows = client(transport).get_update_task_results(task_id, "succeeded")
        self.assertEqual(rows[0]["variation"], "sku-1")
        self.assertTrue(urlsplit(transport.requests[-1][1]).path.endswith(f"/{task_id}/succeeded"))
        with self.assertRaises(OttoApiError) as raised:
            client(FakeTransport()).get_update_task_results(task_id, "delete")
        self.assertEqual(raised.exception.code, "INVALID_TASK_RESULT_TYPE")

    def test_product_and_shipping_read_endpoints_are_account_scoped_gets(self):
        profile_id = "38427776-7383-4d65-82c9-dbe949d0573a"
        transport = FakeTransport(
            token(),
            response(payload={"sku": "sku-1", "shippingProfileId": profile_id, "processingTime": "2"}),
            response(payload={"shippingProfileId": profile_id, "shippingProfileName": "Parcel"}),
            response(payload={"categoryGroups": [], "links": []}),
        )
        otto = client(transport)
        self.assertEqual(otto.get_product_delivery_information("sku-1")["sku"], "sku-1")
        self.assertEqual(otto.get_shipping_profile(profile_id)["shippingProfileId"], profile_id)
        self.assertEqual(otto.list_categories(), [])
        get_requests = [request for request in transport.requests if request[0] == "GET"]
        self.assertEqual([urlsplit(request[1]).path for request in get_requests], [
            "/v1/availability/product-delivery-information/sku-1",
            f"/v1/shipping-profiles/{profile_id}",
            "/v5/products/categories",
        ])
        self.assertTrue(all(request[2]["Authorization"] == "Bearer access-token" for request in get_requests))

    def test_task_id_and_page_limit_are_validated(self):
        with self.assertRaises(OttoApiError) as raised:
            client(FakeTransport()).get_update_task("not-a-uuid")
        self.assertEqual(raised.exception.code, "INVALID_TASK_ID")
        with self.assertRaises(OttoApiError) as raised:
            client(FakeTransport()).list_marketplace_status({"sku": "sku-1"}, max_pages=101)
        self.assertEqual(raised.exception.code, "INVALID_PAGE_LIMIT")


if __name__ == "__main__":
    unittest.main()

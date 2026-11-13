"""Fail-closed OttoRestart client for OTTO Market.

Reads, product submission, and new-SKU availability updates share one
account-scoped OAuth client. Writes require explicit PRODUCTION mode and
dedicated feature flags; each POST is single-SKU and never retried.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from runtime_mode import RuntimeConfig, RuntimeMode

OTTO_API_BASE_URL = "https://api.otto.market"
OTTO_TOKEN_PATH = "/v1/token"
OTTO_READ_SCOPES = "products availability shipping-profiles"
ALLOWED_OTTO_HOSTS = frozenset({"api.otto.market"})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PAGINATION_PAGES = 100
MAX_GET_RETRIES = 2
_TASK_PATH = re.compile(
    r"^/v5/products/update-tasks/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"(?:/(?:succeeded|failed|unchanged))?$"
)


class OttoApiError(RuntimeError):
    """Safe typed API failure; never includes credentials or response bodies."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


@dataclass(frozen=True)
class OttoCredentials:
    account: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        _validate_account(self.account)
        if not self.client_id.strip() or not self.client_secret.strip():
            raise OttoApiError("CREDENTIALS_MISSING")

    @classmethod
    def from_environment(
        cls,
        account: str,
        environment: Mapping[str, str] | None = None,
    ) -> OttoCredentials:
        _validate_account(account)
        source = os.environ if environment is None else environment
        prefix = f"OTTORESTART_OTTO_{account}_"
        client_id = source.get(prefix + "CLIENT_ID", "").strip()
        client_secret = source.get(prefix + "CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise OttoApiError("CREDENTIALS_MISSING")
        return cls(account=account, client_id=client_id, client_secret=client_secret)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class OttoProductWriteResult:
    status_code: int | None
    response: Mapping[str, Any] | None
    error_code: str | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class OttoAvailabilityWriteResult:
    status_code: int | None
    response: Mapping[str, Any] | None
    error_code: str | None = None
    retry_after_seconds: float | None = None


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class RateLimiter(Protocol):
    def acquire(self) -> None: ...


class _ProcessRateLimiter:
    """A shared 2-GET/sec limiter for all account clients in this process."""

    def __init__(self, interval_seconds: float = 0.5) -> None:
        self._interval = interval_seconds
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._interval
        if wait:
            time.sleep(wait)


_GLOBAL_RATE_LIMITER = _ProcessRateLimiter()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibTransport:
    """Small stdlib transport with redirects disabled and bounded responses."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        opener = build_opener(_NoRedirectHandler())
        try:
            response = opener.open(request, timeout=timeout_seconds)
        except HTTPError as error:
            # Preserve the status/headers but never parse or surface OTTO's body.
            return HttpResponse(
                status_code=error.code,
                headers={str(key): str(value) for key, value in error.headers.items()},
                body=b"",
            )
        except (URLError, TimeoutError, OSError) as error:
            raise _TransportFailure from error

        try:
            body_bytes = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body_bytes) > MAX_RESPONSE_BYTES:
                raise _OversizedResponse
            return HttpResponse(
                status_code=response.status,
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=body_bytes,
            )
        finally:
            response.close()


class _TransportFailure(Exception):
    pass


class _OversizedResponse(Exception):
    pass


def _validate_account(account: str) -> None:
    if account not in {"JV", "XL"}:
        raise OttoApiError("INVALID_ACCOUNT")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def _retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (retry_at - current).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_json(response: HttpResponse) -> Any:
    if not response.body:
        raise OttoApiError("INVALID_JSON_RESPONSE", status_code=response.status_code)
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OttoApiError("INVALID_JSON_RESPONSE", status_code=response.status_code) from error


def _next_href(payload: Mapping[str, Any]) -> str | None:
    links = payload.get("links")
    candidates: list[Any]
    if isinstance(links, list):
        candidates = links
    elif isinstance(links, Mapping):
        candidates = [links]
    else:
        return None
    for link in candidates:
        if isinstance(link, Mapping) and link.get("rel") == "next":
            href = link.get("href")
            if not isinstance(href, str) or not href.strip():
                raise OttoApiError("INVALID_PAGINATION_LINK")
            return href
    return None


def _normalize_task_id(process_uuid: str) -> str:
    try:
        return str(UUID(process_uuid))
    except (ValueError, TypeError, AttributeError) as error:
        raise OttoApiError("INVALID_TASK_ID") from error


def _require_filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
    cleaned = {key: value for key, value in (filters or {}).items() if value is not None and value != ""}
    if not cleaned:
        raise OttoApiError("FILTER_REQUIRED")
    return cleaned


class OttoMarketClient:
    """Account-scoped read-only OTTO client with explicit live-read gating."""

    def __init__(
        self,
        account: str,
        credentials: OttoCredentials,
        runtime_config: RuntimeConfig,
        *,
        transport: HttpTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        _validate_account(account)
        if credentials.account != account:
            raise OttoApiError("ACCOUNT_CREDENTIAL_MISMATCH")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.account = account
        self._credentials = credentials
        self._runtime_config = runtime_config
        self._transport = transport or UrllibTransport()
        self._rate_limiter = rate_limiter or _GLOBAL_RATE_LIMITER
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @classmethod
    def from_environment(
        cls,
        account: str,
        *,
        environment: Mapping[str, str] | None = None,
        runtime_config: RuntimeConfig | None = None,
        **kwargs: Any,
    ) -> OttoMarketClient:
        source = os.environ if environment is None else environment
        credentials = OttoCredentials.from_environment(account, source)
        config = runtime_config or RuntimeConfig.from_environment(source)
        return cls(account, credentials, config, **kwargs)

    def _assert_read_enabled(self) -> None:
        if not self._runtime_config.otto_read_enabled:
            raise OttoApiError("OTTO_READ_DISABLED")

    def _assert_product_write_enabled(self) -> None:
        if self._runtime_config.mode is not RuntimeMode.PRODUCTION:
            raise OttoApiError("OTTO_PRODUCT_WRITE_MODE_DISABLED")
        if not self._runtime_config.otto_read_enabled:
            raise OttoApiError("OTTO_READ_DISABLED")
        if not self._runtime_config.product_write_enabled:
            raise OttoApiError("OTTO_PRODUCT_WRITE_DISABLED")

    def _assert_availability_write_enabled(self) -> None:
        if self._runtime_config.mode is not RuntimeMode.PRODUCTION:
            raise OttoApiError("OTTO_AVAILABILITY_WRITE_MODE_DISABLED")
        if not self._runtime_config.otto_read_enabled:
            raise OttoApiError("OTTO_READ_DISABLED")
        if not self._runtime_config.availability_write_enabled:
            raise OttoApiError("OTTO_AVAILABILITY_WRITE_DISABLED")

    @staticmethod
    def _validate_url(url: str, expected_path: str | None = None) -> str:
        parts = urlsplit(url)
        try:
            port = parts.port
        except ValueError as error:
            raise OttoApiError("UNTRUSTED_OTTO_URL") from error
        if (
            parts.scheme != "https"
            or parts.hostname not in ALLOWED_OTTO_HOSTS
            or port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or "\\" in parts.path
            or any(segment == ".." for segment in parts.path.split("/"))
        ):
            raise OttoApiError("UNTRUSTED_OTTO_URL")
        if expected_path is not None and parts.path != expected_path:
            raise OttoApiError("UNTRUSTED_OTTO_URL")
        return url

    def _api_url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if not path.startswith("/") or path.startswith("//"):
            raise OttoApiError("UNTRUSTED_OTTO_URL")
        encoded = urlencode(params or {}, doseq=True)
        url = OTTO_API_BASE_URL + path + (f"?{encoded}" if encoded else "")
        return self._validate_url(url, expected_path=path)

    def _resolve_paging_href(self, href: str, expected_path: str) -> str:
        if href.startswith("//"):
            raise OttoApiError("UNTRUSTED_OTTO_URL")
        resolved = urljoin(OTTO_API_BASE_URL + expected_path, href)
        return self._validate_url(resolved, expected_path=expected_path)

    @staticmethod
    def _verify_paging_scope(initial_url: str, next_url: str) -> None:
        paging_keys = {"page", "limit", "cursor"}
        initial = parse_qsl(urlsplit(initial_url).query, keep_blank_values=True)
        actual = parse_qsl(urlsplit(next_url).query, keep_blank_values=True)
        required = [pair for pair in initial if pair[0] not in paging_keys]
        actual_pairs = set(actual)
        if any(pair not in actual_pairs for pair in required):
            raise OttoApiError("PAGINATION_SCOPE_CHANGED")

    def _resolve_task_href(self, href: str) -> tuple[str, str]:
        if not isinstance(href, str) or not href.strip() or href.startswith("//"):
            raise OttoApiError("UNTRUSTED_OTTO_URL")
        resolved = urljoin(OTTO_API_BASE_URL, href)
        validated = self._validate_url(resolved)
        path = urlsplit(validated).path
        if not _TASK_PATH.fullmatch(path):
            raise OttoApiError("UNTRUSTED_OTTO_URL")
        return validated, path

    def _get_access_token(self) -> str:
        self._assert_read_enabled()
        with self._token_lock:
            now = self._clock()
            if self._token and now < self._token_expires_at:
                return self._token
            form = urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.client_secret,
                    "scope": OTTO_READ_SCOPES,
                }
            ).encode("utf-8")
            url = self._api_url(OTTO_TOKEN_PATH)
            try:
                response = self._transport.request(
                    "POST",
                    url,
                    {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                    form,
                    self._timeout_seconds,
                )
            except (_TransportFailure, TimeoutError, OSError) as error:
                raise OttoApiError("TOKEN_NETWORK_ERROR", retryable=True) from error
            except _OversizedResponse as error:
                raise OttoApiError("TOKEN_RESPONSE_TOO_LARGE") from error
            if not 200 <= response.status_code < 300:
                retryable = response.status_code == 429 or response.status_code >= 500
                retry_after = _retry_after_seconds(_header(response.headers, "Retry-After"))
                raise OttoApiError(
                    f"TOKEN_HTTP_{response.status_code}",
                    status_code=response.status_code,
                    retryable=retryable,
                    retry_after_seconds=retry_after,
                )
            payload = _parse_json(response)
            if not isinstance(payload, Mapping):
                raise OttoApiError("INVALID_TOKEN_RESPONSE")
            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            token_type = payload.get("token_type", "Bearer")
            if (
                not isinstance(access_token, str)
                or not access_token.strip()
                or isinstance(expires_in, bool)
                or not isinstance(expires_in, (int, float))
                or not math.isfinite(float(expires_in))
                or expires_in <= 0
                or not isinstance(token_type, str)
                or token_type.casefold() != "bearer"
            ):
                raise OttoApiError("INVALID_TOKEN_RESPONSE")
            ttl = float(expires_in)
            skew = min(60.0, ttl * 0.1)
            self._token = access_token
            self._token_expires_at = self._clock() + max(0.0, ttl - skew)
            return access_token

    def _invalidate_token(self, previous: str) -> None:
        with self._token_lock:
            if self._token == previous:
                self._token = None
                self._token_expires_at = 0.0

    def _request_get(self, url: str, expected_path: str) -> Any:
        self._assert_read_enabled()
        self._validate_url(url, expected_path)
        auth_retried = False
        transient_retries = 0
        while True:
            token = self._get_access_token()
            self._rate_limiter.acquire()
            try:
                response = self._transport.request(
                    "GET",
                    url,
                    {"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    None,
                    self._timeout_seconds,
                )
            except (_TransportFailure, TimeoutError, OSError) as error:
                if transient_retries < MAX_GET_RETRIES:
                    self._sleep(0.25 * (2**transient_retries))
                    transient_retries += 1
                    continue
                raise OttoApiError("NETWORK_ERROR", retryable=True) from error
            except _OversizedResponse as error:
                raise OttoApiError("RESPONSE_TOO_LARGE") from error

            if response.status_code == 401 and not auth_retried:
                self._invalidate_token(token)
                auth_retried = True
                continue
            if response.status_code == 429:
                raise OttoApiError(
                    "HTTP_429",
                    status_code=429,
                    retryable=True,
                    retry_after_seconds=_retry_after_seconds(_header(response.headers, "Retry-After")),
                )
            if response.status_code >= 500 and transient_retries < MAX_GET_RETRIES:
                self._sleep(0.25 * (2**transient_retries))
                transient_retries += 1
                continue
            if not 200 <= response.status_code < 300:
                raise OttoApiError(
                    f"HTTP_{response.status_code}",
                    status_code=response.status_code,
                    retryable=response.status_code >= 500,
                )
            return _parse_json(response)

    def _get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self._request_get(self._api_url(path, params), path)

    def _paginate(
        self,
        path: str,
        params: Mapping[str, Any],
        collection_key: str,
        *,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        return [row for page in self._iter_paginated_pages(
            path, params, collection_key, max_pages=max_pages
        ) for row in page]

    def _iter_paginated_pages(
        self,
        path: str,
        params: Mapping[str, Any],
        collection_key: str,
        *,
        max_pages: int,
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield validated pages without retaining the complete catalog in memory."""
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGINATION_PAGES:
            raise OttoApiError("INVALID_PAGE_LIMIT")
        page_url = self._api_url(path, params)
        visited: set[str] = set()
        for _ in range(max_pages):
            if page_url in visited:
                raise OttoApiError("PAGINATION_LOOP", retryable=False)
            visited.add(page_url)
            payload = self._request_get(page_url, path)
            if not isinstance(payload, Mapping) or not isinstance(payload.get(collection_key), list):
                raise OttoApiError("INVALID_PAGINATION_RESPONSE")
            page_rows = payload[collection_key]
            if any(not isinstance(item, Mapping) for item in page_rows):
                raise OttoApiError("INVALID_PAGINATION_RESPONSE")
            yield [dict(item) for item in page_rows]
            href = _next_href(payload)
            if href is None:
                return
            page_url = self._resolve_paging_href(href, path)
            self._verify_paging_scope(self._api_url(path, params), page_url)
            if page_url in visited:
                raise OttoApiError("PAGINATION_LOOP", retryable=False)
        raise OttoApiError("PAGINATION_LIMIT_REACHED", retryable=True)

    @staticmethod
    def _validate_sku(payload: Any, expected_sku: str) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or payload.get("sku") != expected_sku:
            raise OttoApiError("SKU_MISMATCH")
        return dict(payload)

    @staticmethod
    def _validate_price(payload: Mapping[str, Any]) -> dict[str, Any]:
        standard_price = payload.get("standardPrice")
        if not isinstance(standard_price, Mapping):
            raise OttoApiError("MISSING_STANDARD_PRICE")
        amount = standard_price.get("amount")
        currency = standard_price.get("currency")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(float(amount))
            or not isinstance(currency, str)
            or not currency.strip()
        ):
            raise OttoApiError("MISSING_STANDARD_PRICE")
        return dict(payload)

    def get_product(self, sku: str) -> dict[str, Any]:
        if not sku:
            raise OttoApiError("SKU_REQUIRED")
        path = f"/v5/products/{quote(sku, safe='')}"
        return self._validate_sku(self._get_json(path), sku)

    def submit_product_payload(
        self,
        payload: Mapping[str, Any],
        *,
        expected_sku: str,
        expected_ean: str,
    ) -> OttoProductWriteResult:
        """Submit one previously approved product exactly once.

        The caller must persist its attempt as in-flight before calling this
        method. A transport exception after dispatch is reported as unknown;
        this method never retries or follows redirects.
        """
        self._assert_product_write_enabled()
        sku = str(expected_sku or "").strip()
        ean = str(expected_ean or "").strip()
        if not sku or not ean or not isinstance(payload, Mapping):
            raise OttoApiError("PRODUCT_PAYLOAD_INVALID")
        if (
            payload.get("sku") != sku
            or payload.get("ean") != ean
            or not isinstance(payload.get("productReference"), str)
            or not str(payload.get("productReference")).strip()
        ):
            raise OttoApiError("PRODUCT_IDENTITY_MISMATCH")
        if payload.get("account") is not None and str(payload.get("account")).strip().upper() != self.account:
            raise OttoApiError("ACCOUNT_CREDENTIAL_MISMATCH")
        try:
            body = json.dumps(
                [dict(payload)], ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise OttoApiError("PRODUCT_PAYLOAD_INVALID") from None

        token = self._get_access_token()
        url = self._api_url("/v5/products")
        try:
            response = self._transport.request(
                "POST",
                url,
                {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body,
                self._timeout_seconds,
            )
        except Exception:
            # Once the product request is handed to the transport, even an
            # unexpected exception cannot prove that OTTO did not accept it.
            return OttoProductWriteResult(None, None, "PRODUCT_WRITE_OUTCOME_UNKNOWN")

        parsed: Mapping[str, Any] | None = None
        error_code = None
        if response.body:
            try:
                decoded = json.loads(response.body.decode("utf-8"))
                if isinstance(decoded, Mapping):
                    parsed = dict(decoded)
                else:
                    error_code = "INVALID_PRODUCT_WRITE_RESPONSE"
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_code = "INVALID_PRODUCT_WRITE_RESPONSE"
        elif 200 <= response.status_code < 300:
            error_code = "PRODUCT_WRITE_RESPONSE_EMPTY"
        return OttoProductWriteResult(
            status_code=response.status_code,
            response=parsed,
            error_code=error_code,
            retry_after_seconds=_retry_after_seconds(_header(response.headers, "Retry-After")),
        )

    def _submit_availability_payload(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        expected_sku: str,
    ) -> OttoAvailabilityWriteResult:
        self._assert_availability_write_enabled()
        sku = str(expected_sku or "").strip()
        if not sku or not isinstance(payload, Mapping) or payload.get("sku") != sku:
            raise OttoApiError("AVAILABILITY_IDENTITY_MISMATCH")
        try:
            body = json.dumps(
                [dict(payload)], ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise OttoApiError("AVAILABILITY_PAYLOAD_INVALID") from None

        token = self._get_access_token()
        url = self._api_url(path)
        try:
            response = self._transport.request(
                "POST",
                url,
                {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body,
                self._timeout_seconds,
            )
        except Exception:
            # A timeout after handing the request to the transport cannot prove
            # that OTTO did not apply the update. Never retry from this client.
            return OttoAvailabilityWriteResult(None, None, "AVAILABILITY_WRITE_OUTCOME_UNKNOWN")

        parsed: Mapping[str, Any] | None = None
        error_code = None
        if response.body:
            try:
                decoded = json.loads(response.body.decode("utf-8"))
                if isinstance(decoded, Mapping):
                    parsed = dict(decoded)
                else:
                    error_code = "AVAILABILITY_WRITE_RESPONSE_INVALID"
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_code = "AVAILABILITY_WRITE_RESPONSE_INVALID"
        elif response.status_code not in {202, 204}:
            error_code = "AVAILABILITY_WRITE_RESPONSE_EMPTY"
        return OttoAvailabilityWriteResult(
            status_code=response.status_code,
            response=parsed,
            error_code=error_code,
            retry_after_seconds=_retry_after_seconds(_header(response.headers, "Retry-After")),
        )

    def update_quantity(self, sku: str, quantity: int) -> OttoAvailabilityWriteResult:
        """Update one explicitly chosen new SKU; no stock default or retry is applied."""
        normalized_sku = str(sku or "").strip()
        if not normalized_sku:
            raise OttoApiError("SKU_REQUIRED")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise OttoApiError("AVAILABILITY_QUANTITY_INVALID")
        return self._submit_availability_payload(
            "/v1/availability/quantities",
            {"sku": normalized_sku, "quantity": quantity},
            expected_sku=normalized_sku,
        )

    def update_delivery_information(
        self,
        sku: str,
        *,
        shipping_profile_id: str,
        processing_time: str,
    ) -> OttoAvailabilityWriteResult:
        """Set explicit target delivery values without account/stock defaults."""
        normalized_sku = str(sku or "").strip()
        if not normalized_sku:
            raise OttoApiError("SKU_REQUIRED")
        try:
            profile_id = str(UUID(str(shipping_profile_id)))
        except (ValueError, TypeError, AttributeError) as error:
            raise OttoApiError("INVALID_SHIPPING_PROFILE_ID") from error
        normalized_processing_time = processing_time.strip().upper() if isinstance(processing_time, str) else ""
        if normalized_processing_time != "DEFAULT" and not (
            normalized_processing_time.isdecimal()
            and 1 <= int(normalized_processing_time) <= 99
            and str(int(normalized_processing_time)) == normalized_processing_time
        ):
            raise OttoApiError("PROCESSING_TIME_REQUIRED")
        return self._submit_availability_payload(
            "/v1/availability/product-delivery-information",
            {
                "sku": normalized_sku,
                "shippingProfileId": profile_id,
                "processingTime": normalized_processing_time,
            },
            expected_sku=normalized_sku,
        )

    def get_product_prices(self, sku: str) -> dict[str, Any]:
        if not sku:
            raise OttoApiError("SKU_REQUIRED")
        path = f"/v5/products/{quote(sku, safe='')}/prices"
        return self._validate_price(self._validate_sku(self._get_json(path), sku))

    def get_marketplace_status(self, sku: str) -> dict[str, Any]:
        if not sku:
            raise OttoApiError("SKU_REQUIRED")
        path = f"/v5/products/{quote(sku, safe='')}/marketplace-status"
        return self._validate_sku(self._get_json(path), sku)

    def get_active_status(self, sku: str) -> dict[str, Any]:
        if not sku:
            raise OttoApiError("SKU_REQUIRED")
        path = f"/v5/products/{quote(sku, safe='')}/active-status"
        return self._validate_sku(self._get_json(path), sku)

    def list_products(self, filters: Mapping[str, Any], *, max_pages: int = 50) -> list[dict[str, Any]]:
        params = _require_filters(filters)
        rows = self._paginate("/v5/products", params, "productVariations", max_pages=max_pages)
        self._verify_filtered_sku(rows, params)
        return rows

    def list_product_prices(self, filters: Mapping[str, Any], *, max_pages: int = 50) -> list[dict[str, Any]]:
        params = _require_filters(filters)
        rows = self._paginate("/v5/products/prices", params, "variationPrices", max_pages=max_pages)
        self._verify_filtered_sku(rows, params)
        return [self._validate_price(row) for row in rows]

    def list_marketplace_status(
        self,
        filters: Mapping[str, Any],
        *,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        params = _require_filters(filters)
        rows = self._paginate(
            "/v5/products/marketplace-status", params, "marketPlaceStatus", max_pages=max_pages
        )
        self._verify_filtered_sku(rows, params)
        return rows

    def iter_marketplace_status_pages(
        self,
        filters: Mapping[str, Any],
        *,
        max_pages: int = 50,
    ) -> Iterator[list[dict[str, Any]]]:
        """Stream validated marketplace-status pages for bounded scanners.

        No status filter is added implicitly: callers can observe both active
        restrictions and explicit clear statuses. SKU-filtered callers retain
        the same exact-SKU validation as ``list_marketplace_status``.
        """
        params = _require_filters(filters)
        for page in self._iter_paginated_pages(
            "/v5/products/marketplace-status", params, "marketPlaceStatus", max_pages=max_pages
        ):
            self._verify_filtered_sku(page, params)
            yield page

    def list_categories(self, *, category: str | None = None, max_pages: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": 2000}
        if category:
            params["category"] = category
        return self._paginate("/v5/products/categories", params, "categoryGroups", max_pages=max_pages)

    def get_update_task(self, process_uuid: str) -> dict[str, Any]:
        task_id = _normalize_task_id(process_uuid)
        path = f"/v5/products/update-tasks/{task_id}"
        payload = self._get_json(path)
        if not isinstance(payload, Mapping):
            raise OttoApiError("INVALID_TASK_RESPONSE")
        return dict(payload)

    def get_update_task_results(
        self,
        process_uuid: str,
        result_type: str,
        *,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        task_id = _normalize_task_id(process_uuid)
        if result_type not in {"succeeded", "failed", "unchanged"}:
            raise OttoApiError("INVALID_TASK_RESULT_TYPE")
        path = f"/v5/products/update-tasks/{task_id}/{result_type}"
        return self._paginate(path, {}, "results", max_pages=max_pages)

    def read_update_task_href(self, href: str) -> dict[str, Any]:
        """Read only a documented update-task self/result link from OTTO."""
        url, path = self._resolve_task_href(href)
        payload = self._request_get(url, path)
        if not isinstance(payload, Mapping):
            raise OttoApiError("INVALID_TASK_RESPONSE")
        return dict(payload)

    def get_product_delivery_information(self, sku: str) -> dict[str, Any]:
        if not sku:
            raise OttoApiError("SKU_REQUIRED")
        path = f"/v1/availability/product-delivery-information/{quote(sku, safe='')}"
        return self._validate_sku(self._get_json(path), sku)

    def get_shipping_profile(self, shipping_profile_id: str) -> dict[str, Any]:
        try:
            normalized_id = str(UUID(shipping_profile_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise OttoApiError("INVALID_SHIPPING_PROFILE_ID") from error
        path = f"/v1/shipping-profiles/{normalized_id}"
        payload = self._get_json(path)
        if not isinstance(payload, Mapping) or payload.get("shippingProfileId") != normalized_id:
            raise OttoApiError("INVALID_SHIPPING_PROFILE_RESPONSE")
        return dict(payload)

    def list_shipping_profiles(self, *, max_pages: int = 50) -> list[dict[str, Any]]:
        return self._paginate("/v1/shipping-profiles", {}, "results", max_pages=max_pages)

    @staticmethod
    def _verify_filtered_sku(rows: list[dict[str, Any]], filters: Mapping[str, Any]) -> None:
        expected = filters.get("sku")
        if expected is None:
            return
        if not isinstance(expected, str) or any(row.get("sku") != expected for row in rows):
            raise OttoApiError("SKU_MISMATCH")

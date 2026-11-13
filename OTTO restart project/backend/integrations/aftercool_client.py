"""Account-scoped, read-only adapter for the Aftercool HTTP API.

Product identity is accepted only after complete pagination and exact matching
of a returned EAN, SKU, or Aftercool product ID. The only POST is the login
request that obtains the Aftercool session cookie.
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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from runtime_mode import RuntimeConfig, RuntimeMode

AFTERCOOL_BASE_URL = "https://aftercool.de"
AFTERCOOL_ALLOWED_HOSTS = frozenset({"aftercool.de"})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PRODUCT_PAGE_SIZE = 500
MAX_PRODUCT_PAGES = 20
MAX_GET_RETRIES = 2
SESSION_CACHE_SECONDS = 15 * 60
FACTORY_EXCLUSION_POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "aftercool_factory_exclusions.json"


class AftercoolApiError(RuntimeError):
    """Safe integration failure with a stable code and no response body."""

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
class AftercoolCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.username.strip() or not self.password.strip():
            raise AftercoolApiError("CREDENTIALS_MISSING")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> AftercoolCredentials:
        source = os.environ if environment is None else environment
        username = source.get("OTTORESTART_AFTERCOOL_USERNAME", "").strip()
        password = source.get("OTTORESTART_AFTERCOOL_PASSWORD", "").strip()
        if not username or not password:
            raise AftercoolApiError("CREDENTIALS_MISSING")
        return cls(username=username, password=password)


@dataclass(frozen=True)
class AftercoolHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class AftercoolHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> AftercoolHttpResponse: ...


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class _TransportFailure(Exception):
    pass


class _OversizedResponse(Exception):
    pass


class UrllibAftercoolTransport:
    """Stdlib HTTP transport with redirects disabled and bounded response size."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> AftercoolHttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        opener = build_opener(_NoRedirectHandler())
        try:
            response = opener.open(request, timeout=timeout_seconds)
        except HTTPError as error:
            return AftercoolHttpResponse(
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
            return AftercoolHttpResponse(
                status_code=response.status,
                headers={str(key): str(value) for key, value in response.headers.items()},
                body=body_bytes,
            )
        finally:
            response.close()


def _validate_account(account: str) -> str:
    normalized = str(account or "").strip().upper()
    if normalized not in {"JV", "XL"}:
        raise AftercoolApiError("INVALID_ACCOUNT")
    return normalized


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value.strip())
        return max(0.0, parsed) if math.isfinite(parsed) else None
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _decode_json(response: AftercoolHttpResponse) -> Any:
    if not response.body:
        raise AftercoolApiError("INVALID_JSON_RESPONSE", status_code=response.status_code)
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AftercoolApiError("INVALID_JSON_RESPONSE", status_code=response.status_code) from error


def _as_items(payload: Any, *, code: str) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(payload, list):
        items = payload
        total = None
    elif isinstance(payload, Mapping):
        items = payload.get("items")
        total_value = payload.get("total")
        if total_value is None:
            total = None
        elif isinstance(total_value, bool) or not isinstance(total_value, int) or total_value < 0:
            raise AftercoolApiError(code)
        else:
            total = total_value
    else:
        raise AftercoolApiError(code)
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise AftercoolApiError(code)
    return [dict(item) for item in items], total


def _first_value(sources: tuple[Mapping[str, Any], ...], keys: tuple[str, ...]) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip() not in {"", "None", "0", "—", "-"}:
                return value
    return None


def _normalize_identifier(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if normalized in {"", "None", "0", "—", "-"}:
        return None
    return normalized


def _parse_start_price_cents(value: Any) -> int | None:
    """Parse only Aftercool Startpreis; never fall back to other price fields."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        raw = str(value)
    else:
        raw = re.sub(r"\s+", "", re.sub(r"EUR|€", "", str(value), flags=re.IGNORECASE))
    if not raw:
        return None
    if "." in raw and "," in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw):
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= Decimal("0.0001"):
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _extract_images(sources: tuple[Mapping[str, Any], ...]) -> list[str]:
    result: list[str] = []
    for source in sources:
        for key in ("PictureURLs", "PictureURL", "GalleryURL", "galleryUrl", "imageUrl"):
            value = source.get(key)
            values = value if isinstance(value, list) else [value]
            for entry in values:
                if not isinstance(entry, str):
                    continue
                for candidate in re.split(r"[|,]", entry):
                    image = candidate.strip()
                    if image.startswith(("https://", "http://")) and image not in result:
                        result.append(image)
    return result


def _extract_product_attributes(sources: tuple[Mapping[str, Any], ...]) -> list[dict[str, str]]:
    """Keep only explicitly structured Aftercool attributes, never arbitrary columns."""
    result: list[dict[str, str]] = []
    positions: dict[str, int] = {}

    def add(name: Any, raw_value: Any) -> None:
        if len(result) >= 200 or not isinstance(name, str):
            return
        clean_name = " ".join(name.split())[:120]
        if not clean_name:
            return
        values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
        parts = [" ".join(str(value).split())[:500] for value in values
                 if isinstance(value, (str, int, float)) and not isinstance(value, bool)]
        parts = [value for value in parts if value and value not in {"0", "—", "-"}]
        if not parts:
            return
        normalized_name = clean_name.casefold()
        if normalized_name in positions:
            existing = result[positions[normalized_name]]
            existing_parts = [part.strip() for part in existing["value"].split(";") if part.strip()]
            for part in parts:
                if part not in existing_parts:
                    existing_parts.append(part)
            existing["value"] = "; ".join(existing_parts)[:1000]
            return
        positions[normalized_name] = len(result)
        result.append({"name": clean_name, "value": "; ".join(dict.fromkeys(parts))[:1000]})

    for source in sources:
        raw = next((source.get(key) for key in (
            "attributes", "productAttributes", "product_attributes", "Eigenschaften",
        ) if source.get(key) not in (None, {}, [])), None)
        if isinstance(raw, Mapping):
            for name, value in raw.items():
                add(name, value)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                name = next((item.get(key) for key in ("name", "attribute", "attributeName", "key")
                             if isinstance(item.get(key), str)), None)
                value = next((item.get(key) for key in ("value", "values", "attributeValue")
                              if item.get(key) is not None), None)
                add(name, value)
        if len(result) >= 200:
            break
    return result


def _canonical_signature(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("product_id"),
        record.get("ean"),
        record.get("sku"),
        record.get("factory_id"),
        record.get("title"),
        record.get("category"),
        record.get("price_cents"),
        tuple(record.get("image_urls", [])),
        tuple(sorted((item.get("name"), item.get("value")) for item in record.get("attributes", []))),
    )


def _source_file_factory_id(value: Any) -> str | None:
    """Read a factory ID only from the established ``...__<id>.csv`` suffix."""
    if not isinstance(value, str) or not value.strip():
        return None
    filename = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.[^.]+$", "", filename)
    match = re.search(r"__(\d{3,})$", stem)
    return match.group(1) if match else None


def _load_factory_exclusion_policy(path: Path) -> tuple[dict[str, frozenset[str]], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AftercoolApiError("FACTORY_EXCLUSION_POLICY_UNAVAILABLE") from error
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1 or payload.get("source_kind") != "lister":
        raise AftercoolApiError("FACTORY_EXCLUSION_POLICY_INVALID")
    raw_ids = payload.get("excluded_factory_ids")
    digest = payload.get("source_report_sha256")
    if not isinstance(raw_ids, Mapping) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AftercoolApiError("FACTORY_EXCLUSION_POLICY_INVALID")

    normalized: dict[str, frozenset[str]] = {}
    for account in ("JV", "XL"):
        values = raw_ids.get(account)
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not re.fullmatch(r"\d+", value) for value in values
        ):
            raise AftercoolApiError("FACTORY_EXCLUSION_POLICY_INVALID")
        normalized[account] = frozenset(values)
    return normalized, digest


def _normalize_factory_exclusion_ids(value: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    normalized: dict[str, frozenset[str]] = {}
    for account in ("JV", "XL"):
        raw_values = value.get(account)
        if not isinstance(raw_values, (list, tuple, set, frozenset)):
            raise ValueError("factory exclusion IDs must be supplied for JV and XL")
        values = frozenset(str(item).strip() for item in raw_values)
        if any(not re.fullmatch(r"\d+", item) for item in values):
            raise ValueError("factory exclusion IDs must be numeric strings")
        normalized[account] = values
    return normalized


class AftercoolClient:
    """Read Aftercool product/factory data with account scope and bounded pages."""

    _request_slots = threading.BoundedSemaphore(3)

    def __init__(
        self,
        credentials: AftercoolCredentials,
        *,
        transport: AftercoolHttpTransport | None = None,
        base_url: str = AFTERCOOL_BASE_URL,
        runtime_config: RuntimeConfig | None = None,
        timeout_seconds: float = 30.0,
        max_pages: int = MAX_PRODUCT_PAGES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        factory_exclusion_ids: Mapping[str, Any] | None = None,
        factory_exclusion_path: Path | str | None = None,
    ) -> None:
        self._validate_base_url(base_url)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PRODUCT_PAGES:
            raise ValueError(f"max_pages must be between 1 and {MAX_PRODUCT_PAGES}")
        self._credentials = credentials
        self._runtime_config = runtime_config or RuntimeConfig.from_environment(os.environ)
        self._transport = transport or UrllibAftercoolTransport()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_pages = max_pages
        self._clock = clock
        self._sleep = sleep
        self._session_lock = threading.Lock()
        self._sessions: dict[str, tuple[str, float]] = {}
        self._factory_cache_lock = threading.Lock()
        self._factory_cache: dict[tuple[str, str], tuple[str | None, float]] = {}
        self._factory_exclusion_policy_hash: str | None = None
        self._factory_exclusion_policy_error: str | None = None
        if factory_exclusion_ids is not None:
            self._factory_exclusion_ids = _normalize_factory_exclusion_ids(factory_exclusion_ids)
        else:
            policy_path = Path(factory_exclusion_path) if factory_exclusion_path is not None else FACTORY_EXCLUSION_POLICY_PATH
            try:
                self._factory_exclusion_ids, self._factory_exclusion_policy_hash = _load_factory_exclusion_policy(policy_path)
            except AftercoolApiError as error:
                # A missing or invalid policy must stop lookup before HTTP.
                self._factory_exclusion_ids = None
                self._factory_exclusion_policy_error = error.code

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        runtime_config: RuntimeConfig | None = None,
        **kwargs: Any,
    ) -> AftercoolClient:
        source = os.environ if environment is None else environment
        credentials = AftercoolCredentials.from_environment(source)
        base_url = source.get("OTTORESTART_AFTERCOOL_BASE_URL", AFTERCOOL_BASE_URL).strip()
        config = runtime_config or RuntimeConfig.from_environment(source)
        return cls(credentials, base_url=base_url, runtime_config=config, **kwargs)

    def _assert_read_enabled(self) -> None:
        if self._runtime_config.mode != RuntimeMode.PRODUCTION:
            raise AftercoolApiError("AFTERCOOL_PRODUCTION_MODE_REQUIRED")
        if not self._runtime_config.aftercool_read_enabled:
            raise AftercoolApiError("AFTERCOOL_READ_DISABLED")

    @staticmethod
    def _validate_base_url(base_url: str) -> None:
        parts = urlsplit(base_url)
        try:
            port = parts.port
        except ValueError as error:
            raise AftercoolApiError("UNTRUSTED_AFTERCOOL_URL") from error
        if (
            parts.scheme != "https"
            or parts.hostname not in AFTERCOOL_ALLOWED_HOSTS
            or port not in {None, 443}
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise AftercoolApiError("UNTRUSTED_AFTERCOOL_URL")

    def _url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        if path not in {"/auth/login", "/api/products", "/api/factories"}:
            raise AftercoolApiError("UNTRUSTED_AFTERCOOL_PATH")
        query = urlencode(params or {}, doseq=True)
        return self.base_url + path + (f"?{query}" if query else "")

    def _login(self, account: str) -> str:
        self._assert_read_enabled()
        body = json.dumps(
            {"username": self._credentials.username, "password": self._credentials.password}
        ).encode("utf-8")
        try:
            response = self._transport.request(
                "POST",
                self._url("/auth/login"),
                {"Content-Type": "application/json", "Accept": "application/json"},
                body,
                min(self.timeout_seconds, 15.0),
            )
        except (_TransportFailure, TimeoutError, OSError) as error:
            raise AftercoolApiError("AUTH_NETWORK_ERROR", retryable=True) from error
        except _OversizedResponse as error:
            raise AftercoolApiError("AUTH_RESPONSE_TOO_LARGE") from error
        if response.status_code != 200:
            raise AftercoolApiError(
                f"AUTH_HTTP_{response.status_code}",
                status_code=response.status_code,
                retryable=response.status_code == 429 or response.status_code >= 500,
                retry_after_seconds=_retry_after_seconds(_header(response.headers, "Retry-After")),
            )
        cookie_header = _header(response.headers, "Set-Cookie")
        if not cookie_header:
            raise AftercoolApiError("SESSION_COOKIE_MISSING")
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
        except Exception as error:
            raise AftercoolApiError("SESSION_COOKIE_INVALID") from error
        session_cookie = cookies.get("session")
        if session_cookie is None or not session_cookie.value:
            raise AftercoolApiError("SESSION_COOKIE_MISSING")
        return session_cookie.value

    def _get_session(self, account: str, *, force_refresh: bool = False) -> str:
        now = self._clock()
        with self._session_lock:
            cached = self._sessions.get(account)
            if not force_refresh and cached and now < cached[1]:
                return cached[0]
            session = self._login(account)
            self._sessions[account] = (session, self._clock() + SESSION_CACHE_SECONDS)
            return session

    def _invalidate_session(self, account: str, previous: str) -> None:
        with self._session_lock:
            cached = self._sessions.get(account)
            if cached and cached[0] == previous:
                self._sessions.pop(account, None)

    def _request_get(
        self,
        account: str,
        path: str,
        params: Mapping[str, Any],
    ) -> Any:
        self._assert_read_enabled()
        normalized_account = _validate_account(account)
        url = self._url(path, params)
        auth_retried = False
        transient_retries = 0
        while True:
            session = self._get_session(normalized_account)
            try:
                with self._request_slots:
                    response = self._transport.request(
                        "GET",
                        url,
                        {"Cookie": f"session={session}", "Accept": "application/json"},
                        None,
                        self.timeout_seconds,
                    )
            except (_TransportFailure, TimeoutError, OSError) as error:
                if transient_retries < MAX_GET_RETRIES:
                    self._sleep(0.25 * (2**transient_retries))
                    transient_retries += 1
                    continue
                raise AftercoolApiError("NETWORK_ERROR", retryable=True) from error
            except _OversizedResponse as error:
                raise AftercoolApiError("RESPONSE_TOO_LARGE") from error

            if response.status_code in {401, 403} and not auth_retried:
                self._invalidate_session(normalized_account, session)
                self._get_session(normalized_account, force_refresh=True)
                auth_retried = True
                continue
            if response.status_code in {401, 403}:
                raise AftercoolApiError("AUTHENTICATION_FAILED", status_code=response.status_code)
            if response.status_code == 429:
                raise AftercoolApiError(
                    "HTTP_429",
                    status_code=429,
                    retryable=True,
                    retry_after_seconds=_retry_after_seconds(_header(response.headers, "Retry-After")),
                )
            if response.status_code >= 500 and transient_retries < MAX_GET_RETRIES:
                self._sleep(0.25 * (2**transient_retries))
                transient_retries += 1
                continue
            if response.status_code != 200:
                raise AftercoolApiError(
                    f"HTTP_{response.status_code}",
                    status_code=response.status_code,
                    retryable=response.status_code >= 500,
                )
            return _decode_json(response)

    def _read_product_pages(self, account: str, identifier: str) -> list[dict[str, Any]]:
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128:
            raise AftercoolApiError("IDENTIFIER_INVALID")
        query = identifier.strip()
        all_items: list[dict[str, Any]] = []
        expected_total: int | None = None

        for page in range(self.max_pages):
            offset = page * PRODUCT_PAGE_SIZE
            payload = self._request_get(
                account,
                "/api/products",
                {
                    "account": _validate_account(account),
                    "dataset": "lister",
                    "q": query,
                    "include_row": 1,
                    "limit": PRODUCT_PAGE_SIZE,
                    "offset": offset,
                },
            )
            items, total = _as_items(payload, code="INVALID_PRODUCTS_RESPONSE")
            if total is not None:
                if expected_total is not None and total != expected_total:
                    raise AftercoolApiError("PAGINATION_INCONSISTENT")
                expected_total = total
                if total > self.max_pages * PRODUCT_PAGE_SIZE:
                    raise AftercoolApiError("PAGINATION_LIMIT_REACHED", retryable=False)
            if len(items) > PRODUCT_PAGE_SIZE:
                raise AftercoolApiError("INVALID_PRODUCTS_RESPONSE")
            if expected_total is not None and offset + len(items) > expected_total:
                raise AftercoolApiError("PAGINATION_INCONSISTENT")
            if not items:
                if expected_total is not None and offset < expected_total:
                    raise AftercoolApiError("PARTIAL_RESPONSE", retryable=True)
                return all_items
            all_items.extend(items)
            next_offset = offset + len(items)
            if expected_total is not None:
                if next_offset >= expected_total:
                    return all_items
                if len(items) < PRODUCT_PAGE_SIZE:
                    raise AftercoolApiError("PARTIAL_RESPONSE", retryable=True)
            elif len(items) < PRODUCT_PAGE_SIZE:
                return all_items

        if expected_total is not None and len(all_items) < expected_total:
            raise AftercoolApiError("PAGINATION_LIMIT_REACHED", retryable=False)
        if len(all_items) >= self.max_pages * PRODUCT_PAGE_SIZE:
            raise AftercoolApiError("PAGINATION_LIMIT_REACHED", retryable=False)
        return all_items

    @staticmethod
    def _normalize_product(item: Mapping[str, Any], account: str) -> dict[str, Any]:
        row_value = item.get("row")
        row = row_value if isinstance(row_value, Mapping) else {}
        sources = (item, row)
        reported_account_value = _first_value(sources, ("account", "Account", "shop_account"))
        reported_account = (
            str(reported_account_value).strip().upper()
            if reported_account_value is not None and str(reported_account_value).strip()
            else None
        )

        item_ean = _normalize_identifier(_first_value((item,), ("ean", "EAN")))
        row_ean = _normalize_identifier(_first_value((row,), ("EAN", "ean")))
        if item_ean and row_ean and item_ean != row_ean:
            raise AftercoolApiError("IDENTITY_CONFLICT")
        ean = item_ean or row_ean
        product_id = _normalize_identifier(_first_value(sources, (
            "aftercool_product_id", "aftercoolProductId", "product_id", "productId", "ProduktID", "ProductID", "id",
        )))
        sku = _normalize_identifier(_first_value(sources, ("sku", "SKU", "Artikelnummer")))
        factory_id = _normalize_identifier(_first_value(sources, ("factory_id", "factoryId")))
        source_file_value = _first_value(sources, ("source_file", "sourceFile", "filename"))
        source_file = str(source_file_value).strip() if source_file_value is not None and str(source_file_value).strip() else None
        source_factory_id = _source_file_factory_id(source_file)
        if factory_id and source_factory_id and factory_id != source_factory_id:
            raise AftercoolApiError("IDENTITY_CONFLICT")
        name_value = _first_value(sources, ("name",))
        name = str(name_value).strip() if name_value is not None and str(name_value).strip() else None
        title_value = name or _first_value(sources, ("Artikelbeschreibung", "product_name", "title"))
        title = str(title_value).strip() if title_value is not None and str(title_value).strip() else None
        category_value = _first_value(sources, ("category", "category_name", "Kategorie", "Category"))
        category = str(category_value).strip() if category_value is not None and str(category_value).strip() else None
        factory_name_value = _first_value((item, row), ("factory_name", "factoryName", "Fabrikname", "FactoryName"))
        factory_name = str(factory_name_value).strip() if factory_name_value is not None and str(factory_name_value).strip() else None
        images = _extract_images(sources)
        attributes = _extract_product_attributes(sources)

        return {
            "product_id": product_id,
            "ean": ean,
            "sku": sku,
            # The selected request account scopes the lookup. Aftercool's row
            # account field is retained as diagnostic metadata because the
            # upstream dataset may contain mixed labels.
            "reported_account": reported_account,
            "factory_id": factory_id,
            "source_file": source_file,
            "source_factory_id": source_factory_id,
            "factory_name": factory_name,
            "name": name,
            "title": title,
            "category": category,
            "price_cents": _parse_start_price_cents(row.get("Startpreis")),
            "image_url": images[0] if images else None,
            "image_urls": images,
            "attributes": attributes,
        }

    def _is_excluded_factory(self, account: str, record: Mapping[str, Any]) -> bool:
        if self._factory_exclusion_ids is None:
            raise AftercoolApiError(self._factory_exclusion_policy_error or "FACTORY_EXCLUSION_POLICY_UNAVAILABLE")
        excluded = self._factory_exclusion_ids[account]
        return bool(
            (record.get("factory_id") and record["factory_id"] in excluded)
            or (record.get("source_factory_id") and record["source_factory_id"] in excluded)
        )

    def _lookup_factory_name(self, account: str, factory_id: str) -> str | None:
        key = (account, factory_id)
        now = self._clock()
        with self._factory_cache_lock:
            cached = self._factory_cache.get(key)
            if cached and now < cached[1]:
                return cached[0]

        payload = self._request_get(account, "/api/factories", {"account": account})
        factories, total = _as_items(payload, code="INVALID_FACTORIES_RESPONSE")
        if total is not None and total != len(factories):
            raise AftercoolApiError("PARTIAL_FACTORIES_RESPONSE", retryable=True)
        if isinstance(payload, Mapping):
            links = payload.get("links")
            if links:
                raise AftercoolApiError("PARTIAL_FACTORIES_RESPONSE", retryable=True)

        names: set[str] = set()
        for factory in factories:
            row_account = factory.get("account")
            row_id = _normalize_identifier(factory.get("id") or factory.get("factory_id"))
            if row_account is None or str(row_account).strip().upper() != account or row_id != factory_id:
                continue
            name = factory.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
        if len(names) > 1:
            raise AftercoolApiError("FACTORY_AMBIGUOUS")
        factory_name = next(iter(names)) if names else None
        with self._factory_cache_lock:
            self._factory_cache[key] = (factory_name, self._clock() + 120.0)
        return factory_name

    def lookup_product(
        self,
        account: str,
        identifier: str,
        factory_hint: str | None = None,
    ) -> dict[str, Any]:
        self._assert_read_enabled()
        normalized_account = _validate_account(account)
        normalized_identifier = _normalize_identifier(identifier)
        if not normalized_identifier or not re.fullmatch(r"\d{8,14}", normalized_identifier):
            raise AftercoolApiError("EAN_INVALID")
        if self._factory_exclusion_ids is None:
            raise AftercoolApiError(self._factory_exclusion_policy_error or "FACTORY_EXCLUSION_POLICY_UNAVAILABLE")

        raw_items = self._read_product_pages(normalized_account, normalized_identifier)
        exact_items: list[dict[str, Any]] = []
        excluded_exact_items: list[dict[str, Any]] = []
        incomplete_identity = False
        for item in raw_items:
            record = self._normalize_product(item, normalized_account)
            if record["ean"] == normalized_identifier and self._is_excluded_factory(normalized_account, record):
                excluded_exact_items.append(record)
                continue
            if not record["product_id"] or not record["ean"] or not record["factory_id"]:
                incomplete_identity = True
            if normalized_identifier != record["ean"]:
                continue
            exact_items.append(record)

        if incomplete_identity:
            return {
                "status": "AMBIGUOUS",
                "found": None,
                "source_type": None,
                "product_id": None,
                "aftercool_product_id": None,
                "ean": None,
                "lookup_identifier": normalized_identifier,
                "sku": None,
                "factory_id": None,
                "factory_name": None,
                "name": None,
                "title": None,
                "category": None,
                "price_cents": None,
                "image_url": None,
                "image_urls": [],
                "verified": False,
                "data_scope": "UNVERIFIED",
                "reason": "Aftercool returned candidate rows without enough identity fields to rule out a match.",
                "reason_code": "IDENTITY_INCOMPLETE",
            }

        if not exact_items:
            if excluded_exact_items:
                excluded_ids = sorted({
                    str(record.get("factory_id") or record.get("source_factory_id"))
                    for record in excluded_exact_items
                })
                return {
                    "status": "AMBIGUOUS",
                    "found": None,
                    "source_type": None,
                    "product_id": None,
                    "aftercool_product_id": None,
                    "ean": None,
                    "lookup_identifier": normalized_identifier,
                    "sku": None,
                    "factory_id": None,
                    "factory_name": None,
                    "name": None,
                    "title": None,
                    "category": None,
                    "price_cents": None,
                    "image_url": None,
                    "image_urls": [],
                    "verified": False,
                    "data_scope": "UNVERIFIED",
                    "reason": "All exact Aftercool EAN matches belong to protected factories; absence is not confirmed.",
                    "reason_code": "PROTECTED_FACTORY_MATCH",
                    "excluded_factory_ids": excluded_ids,
                    "factory_exclusion_policy_sha256": self._factory_exclusion_policy_hash,
                }
            return {
                "status": "NOT_FOUND_CONFIRMED",
                "found": False,
                "source_type": "OTTO_DIRECT",
                "product_id": None,
                "aftercool_product_id": None,
                "ean": None,
                "lookup_identifier": normalized_identifier,
                "sku": None,
                "factory_id": None,
                "factory_name": None,
                "name": None,
                "title": None,
                "category": None,
                "price_cents": None,
                "image_url": None,
                "image_urls": [],
                "verified": True,
                "data_scope": "PRODUCTION",
                "reason": "Complete account-scoped EAN search found no exact Aftercool product.",
            }

        if factory_hint:
            normalized_hint = str(factory_hint).strip()
            if not normalized_hint:
                normalized_hint = None
            if normalized_hint:
                exact_items = [item for item in exact_items if item.get("factory_id") == normalized_hint]
            if normalized_hint and not exact_items:
                return {
                    "status": "AMBIGUOUS",
                    "found": None,
                    "source_type": None,
                    "product_id": None,
                    "aftercool_product_id": None,
                    "ean": None,
                    "lookup_identifier": normalized_identifier,
                    "sku": None,
                    "factory_id": None,
                    "factory_name": None,
                    "name": None,
                    "title": None,
                    "category": None,
                    "price_cents": None,
                    "image_url": None,
                    "image_urls": [],
                    "verified": False,
                    "data_scope": "UNVERIFIED",
                    "reason": "No exact-EAN Aftercool result matched the caller-supplied factory filter.",
                    "reason_code": "FACTORY_HINT_MISMATCH",
                }

        signatures: dict[tuple[Any, ...], dict[str, Any]] = {}
        for record in exact_items:
            signatures[_canonical_signature(record)] = record
        identities = {(record.get("product_id"), record.get("ean"), record.get("factory_id")) for record in signatures.values()}
        if len(signatures) > 1 or len(identities) > 1:
            return {
                "status": "AMBIGUOUS",
                "found": None,
                "source_type": None,
                "product_id": None,
                "aftercool_product_id": None,
                "ean": None,
                "lookup_identifier": normalized_identifier,
                "sku": None,
                "factory_id": None,
                "factory_name": None,
                "name": None,
                "title": None,
                "category": None,
                "price_cents": None,
                "image_url": None,
                "image_urls": [],
                "verified": False,
                "data_scope": "UNVERIFIED",
                "reason": "Multiple conflicting exact Aftercool records matched the identifier.",
                "reason_code": "DUPLICATE_IDENTIFIER",
            }

        record = next(iter(signatures.values()))
        if not record["ean"] or not record["product_id"] or not record["factory_id"]:
            return {
                "status": "AMBIGUOUS",
                "found": None,
                "source_type": None,
                "product_id": record["product_id"],
                "aftercool_product_id": record["product_id"],
                "ean": record["ean"],
                "sku": record["sku"],
                "factory_id": record["factory_id"],
                "factory_name": record["factory_name"],
                "name": record["name"],
                "title": record["title"],
                "category": record["category"],
                "price_cents": record["price_cents"],
                "image_url": record["image_url"],
                "image_urls": record["image_urls"],
                "attributes": record["attributes"],
                "verified": False,
                "data_scope": "UNVERIFIED",
                "reason": "Aftercool response lacks product ID, EAN, or factory ID evidence.",
                "reason_code": "IDENTITY_INCOMPLETE",
            }

        metadata_errors: list[str] = []
        factory_name = record["factory_name"]
        if not factory_name:
            try:
                factory_name = self._lookup_factory_name(normalized_account, record["factory_id"])
            except AftercoolApiError as error:
                metadata_errors.append(error.code)

        return {
            "account": normalized_account,
            "status": "FOUND",
            "found": True,
            "source_type": "AFTERCOOL",
            "product_id": record["product_id"],
            "aftercool_product_id": record["product_id"],
            "ean": record["ean"],
            "sku": record["sku"],
            "factory_id": record["factory_id"],
            "source_file": record["source_file"],
            "source_factory_id": record["source_factory_id"],
            "factory_name": factory_name,
            "reported_account": record["reported_account"],
            "name": record["name"],
            "title": record["title"],
            "category": record["category"],
            "price_cents": record["price_cents"],
            "image_url": record["image_url"],
            "image_urls": record["image_urls"],
            "attributes": record["attributes"],
            "verified": True,
            "data_scope": "PRODUCTION",
            "metadata_status": "PARTIAL" if metadata_errors else "COMPLETE",
            "metadata_errors": metadata_errors,
            "duplicate_count": len(exact_items) + len(excluded_exact_items),
            "excluded_factory_match_count": len(excluded_exact_items),
            "factory_exclusion_policy_sha256": self._factory_exclusion_policy_hash,
            "matched_by": "ean",
        }

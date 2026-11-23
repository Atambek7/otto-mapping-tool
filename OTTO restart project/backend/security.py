"""Restart API authentication contracts for signed BFF and read-only M2M calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

ALLOWED_ACCOUNTS = frozenset({"JV", "XL"})
INTERNAL_ISSUER = "ottorestart-bff"
INTERNAL_AUDIENCE = "otto-restart-api"
LOOKUP_PATH = "/api/restart/library/lookup"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class SecurityConfigurationError(RuntimeError):
    """Security settings are absent or unsafe for this runtime mode."""


class AuthenticationFailure(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SecuritySettings:
    mode: str
    internal_secret: str | None
    operator_id: str | None
    operator_ids: frozenset[str]
    operator_accounts: frozenset[str]
    aftercool_key_hash: str | None
    aftercool_accounts: frozenset[str]

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "SecuritySettings":
        mode = environment.get("OTTORESTART_MODE", "LEGACY_READ_ONLY").strip().upper()
        internal_secret = environment.get("OTTORESTART_INTERNAL_AUTH_SECRET", "").strip() or None
        operator_id = environment.get("OTTORESTART_OPERATOR_ID", "").strip() or None
        operator_ids = _parse_operator_ids(environment.get("OTTORESTART_OPERATOR_IDS", ""))
        operator_accounts = _parse_accounts(environment.get("OTTORESTART_OPERATOR_ACCOUNTS", "JV,XL"))
        aftercool_hash = environment.get("OTTORESTART_AFTERCOOL_M2M_KEY_HASH", "").strip().lower() or None
        aftercool_accounts_raw = environment.get("OTTORESTART_AFTERCOOL_ALLOWED_ACCOUNTS", "").strip()
        if aftercool_hash and not aftercool_accounts_raw:
            raise SecurityConfigurationError(
                "Aftercool M2M access requires explicit OTTORESTART_AFTERCOOL_ALLOWED_ACCOUNTS."
            )
        aftercool_accounts = _parse_accounts(aftercool_accounts_raw) if aftercool_accounts_raw else frozenset()

        if aftercool_hash and not _SHA256_HEX.fullmatch(aftercool_hash):
            raise SecurityConfigurationError("OTTORESTART_AFTERCOOL_M2M_KEY_HASH must be a SHA-256 hex digest.")

        if mode == "PRODUCTION":
            if not internal_secret or len(internal_secret) < 32 or _looks_like_placeholder(internal_secret):
                raise SecurityConfigurationError("Production requires a non-default 32+ character internal auth secret.")
            if not operator_id and not operator_ids:
                raise SecurityConfigurationError("Production requires an explicit OttoRestart operator identity allow-list.")
            if not environment.get("OTTORESTART_OPERATOR_ACCOUNTS", "").strip():
                raise SecurityConfigurationError("Production requires explicit OTTORESTART_OPERATOR_ACCOUNTS.")

        return cls(
            mode=mode,
            internal_secret=internal_secret,
            operator_id=operator_id,
            operator_ids=operator_ids,
            operator_accounts=operator_accounts,
            aftercool_key_hash=aftercool_hash,
            aftercool_accounts=aftercool_accounts,
        )


@dataclass(frozen=True)
class Principal:
    kind: str
    subject: str
    accounts: frozenset[str]
    permissions: frozenset[str]
    request_id: str
    csrf_verified: bool = False


def _parse_accounts(raw: str) -> frozenset[str]:
    accounts = frozenset(part.strip().upper() for part in raw.split(",") if part.strip())
    if not accounts or not accounts <= ALLOWED_ACCOUNTS:
        raise SecurityConfigurationError("Account permissions must be a non-empty subset of JV and XL.")
    return accounts


def _parse_operator_ids(raw: str) -> frozenset[str]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if any(len(part) > 128 for part in parts) or len(parts) != len(set(parts)):
        raise SecurityConfigurationError("Operator identities must be unique values no longer than 128 characters.")
    return frozenset(parts)


def _looks_like_placeholder(secret: str) -> bool:
    lowered = secret.lower()
    return any(marker in lowered for marker in ("change-this", "replace-with", "example", "default-secret", "dev-secret"))


def _decode_base64url(value: str) -> bytes:
    if not value or len(value) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url value")
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def verify_internal_principal(
    token: str | None,
    secret: str | None,
    *,
    now: int | None = None,
) -> Principal:
    if not token or not secret or len(token) > 12_000:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    parts = token.split(".")
    if len(parts) != 2:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    payload_part, signature_part = parts
    try:
        signature = _decode_base64url(signature_part)
        payload_bytes = _decode_base64url(payload_part)
        expected_signature = hmac.new(secret.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Signature mismatch")
        claims = json.loads(payload_bytes)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.") from None

    current_time = int(time.time()) if now is None else now
    if not isinstance(claims, dict):
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")
    if (
        claims.get("iss") != INTERNAL_ISSUER
        or claims.get("aud") != INTERNAL_AUDIENCE
        or claims.get("kind") != "operator"
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"].strip()
        or not isinstance(claims.get("rid"), str)
        or not claims["rid"].strip()
        or type(issued_at) is not int
        or type(expires_at) is not int
        or issued_at > current_time + 30
        or expires_at <= current_time
        or expires_at <= issued_at
        or expires_at - issued_at > 60
    ):
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")

    accounts = _claim_set(claims.get("accounts"), ALLOWED_ACCOUNTS)
    permissions = _claim_set(claims.get("permissions"), {"restart:read", "restart:write"})
    if not accounts or "restart:read" not in permissions:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    if "csrf" in claims and type(claims["csrf"]) is not bool:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")

    return Principal(
        kind="operator",
        subject=claims["sub"],
        accounts=accounts,
        permissions=permissions,
        request_id=claims["rid"],
        csrf_verified=claims.get("csrf", False),
    )


def _claim_set(value: Any, allowed: set[str] | frozenset[str]) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    result = frozenset(value)
    if not result <= allowed:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    return result


def authenticate_request(
    bearer_token: str | None,
    settings: SecuritySettings,
    *,
    method: str,
    path: str,
    now: int | None = None,
) -> Principal:
    try:
        principal = verify_internal_principal(bearer_token, settings.internal_secret, now=now)
    except AuthenticationFailure:
        principal = _verify_aftercool_key(bearer_token, settings)

    if principal.kind == "m2m":
        if method.upper() != "GET" or path != LOOKUP_PATH or principal.permissions != frozenset({"library:read"}):
            raise AuthenticationFailure(403, "FORBIDDEN", "This credential is limited to read-only Library lookup.")
        return principal

    if settings.operator_ids and principal.subject not in settings.operator_ids:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    if not settings.operator_ids and settings.operator_id and principal.subject != settings.operator_id:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    if not principal.accounts <= settings.operator_accounts:
        raise AuthenticationFailure(403, "ACCOUNT_FORBIDDEN", "The principal is not allowed to access these accounts.")

    permission = "restart:read" if method.upper() in {"GET", "HEAD", "OPTIONS"} else "restart:write"
    if permission not in principal.permissions:
        raise AuthenticationFailure(403, "FORBIDDEN", "The Restart principal lacks permission for this action.")
    if method.upper() not in {"GET", "HEAD", "OPTIONS"} and not principal.csrf_verified:
        raise AuthenticationFailure(403, "CSRF_REQUIRED", "A BFF-verified CSRF token is required for mutations.")
    return principal


def _verify_aftercool_key(token: str | None, settings: SecuritySettings) -> Principal:
    if not token or len(token) > 4096 or not settings.aftercool_key_hash:
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(candidate, settings.aftercool_key_hash):
        raise AuthenticationFailure(401, "UNAUTHORIZED", "A valid Restart principal is required.")
    return Principal(
        kind="m2m",
        subject="aftercool-readonly",
        accounts=settings.aftercool_accounts,
        permissions=frozenset({"library:read"}),
        request_id="",
    )


def authorize_account(principal: Principal, account: str) -> None:
    if account not in principal.accounts:
        raise AuthenticationFailure(403, "ACCOUNT_FORBIDDEN", "The principal is not allowed to access this account.")

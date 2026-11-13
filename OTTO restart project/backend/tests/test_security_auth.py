from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
from pathlib import Path

from security import (
    AuthenticationFailure,
    SecurityConfigurationError,
    SecuritySettings,
    authenticate_request,
    authorize_account,
    verify_internal_principal,
)


SECRET = "restart-test-signing-secret-with-adequate-length"


def encode_token(claims: dict, secret: str = SECRET) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def claims(**overrides):
    value = {
        "iss": "ottorestart-bff",
        "aud": "otto-restart-api",
        "kind": "operator",
        "sub": "operator-1",
        "accounts": ["JV"],
        "permissions": ["restart:read", "restart:write"],
        "rid": "test-request-id",
        "iat": 100,
        "exp": 160,
        "csrf": True,
    }
    value.update(overrides)
    return value


def current_claims(**overrides):
    now = int(time.time())
    return claims(iat=now, exp=now + 60, **overrides)


class InternalPrincipalTests(unittest.TestCase):
    def test_accepts_valid_signed_principal(self):
        principal = verify_internal_principal(encode_token(claims()), SECRET, now=120)
        self.assertEqual(principal.subject, "operator-1")
        self.assertEqual(principal.accounts, frozenset({"JV"}))
        self.assertTrue(principal.csrf_verified)

    def test_rejects_forged_signature_and_expired_token(self):
        token = encode_token(claims())
        forged = token.rsplit(".", 1)[0] + ".invalid"
        for candidate, now in ((forged, 120), (token, 160)):
            with self.subTest(candidate=candidate[:8], now=now), self.assertRaises(AuthenticationFailure):
                verify_internal_principal(candidate, SECRET, now=now)

    def test_rejects_wrong_audience_and_excessive_ttl(self):
        for invalid_claims in (claims(aud="ottoflow-api"), claims(exp=161)):
            with self.subTest(claims=invalid_claims), self.assertRaises(AuthenticationFailure):
                verify_internal_principal(encode_token(invalid_claims), SECRET, now=120)

    def test_rejects_claims_with_unknown_account_or_permission(self):
        for invalid_claims in (claims(accounts=["JV", "OTHER"]), claims(permissions=["restart:admin"])):
            with self.subTest(claims=invalid_claims), self.assertRaises(AuthenticationFailure):
                verify_internal_principal(encode_token(invalid_claims), SECRET, now=120)

    def test_enforces_operator_account_scope(self):
        settings = SecuritySettings.from_environment({
            "OTTORESTART_MODE": "LEGACY_READ_ONLY",
            "OTTORESTART_INTERNAL_AUTH_SECRET": SECRET,
            "OTTORESTART_OPERATOR_ID": "operator-1",
            "OTTORESTART_OPERATOR_ACCOUNTS": "JV",
        })
        principal = authenticate_request(encode_token(claims()), settings, method="GET", path="/api/restart/overview", now=120)
        authorize_account(principal, "JV")
        with self.assertRaises(AuthenticationFailure) as raised:
            authorize_account(principal, "XL")
        self.assertEqual(raised.exception.code, "ACCOUNT_FORBIDDEN")

    def test_rejects_forged_operator_identity(self):
        settings = SecuritySettings.from_environment({
            "OTTORESTART_MODE": "LEGACY_READ_ONLY",
            "OTTORESTART_INTERNAL_AUTH_SECRET": SECRET,
            "OTTORESTART_OPERATOR_ID": "configured-operator",
            "OTTORESTART_OPERATOR_ACCOUNTS": "JV",
        })
        with self.assertRaises(AuthenticationFailure):
            authenticate_request(encode_token(claims()), settings, method="GET", path="/api/restart/overview", now=120)

    def test_requires_csrf_claim_for_operator_mutations(self):
        settings = SecuritySettings.from_environment({
            "OTTORESTART_MODE": "LEGACY_READ_ONLY",
            "OTTORESTART_INTERNAL_AUTH_SECRET": SECRET,
            "OTTORESTART_OPERATOR_ACCOUNTS": "JV",
        })
        token = encode_token(claims(csrf=False))
        with self.assertRaises(AuthenticationFailure) as raised:
            authenticate_request(token, settings, method="POST", path="/api/restart/batches", now=120)
        self.assertEqual(raised.exception.code, "CSRF_REQUIRED")

    def test_m2m_is_limited_to_get_library_lookup_and_scoped_accounts(self):
        raw_key = "aftercool-test-key"
        settings = SecuritySettings.from_environment({
            "OTTORESTART_MODE": "LEGACY_READ_ONLY",
            "OTTORESTART_AFTERCOOL_M2M_KEY_HASH": hashlib.sha256(raw_key.encode()).hexdigest(),
            "OTTORESTART_AFTERCOOL_ALLOWED_ACCOUNTS": "XL",
        })
        principal = authenticate_request(raw_key, settings, method="GET", path="/api/restart/library/lookup", now=120)
        self.assertEqual(principal.kind, "m2m")
        authorize_account(principal, "XL")
        for method, path in (
            ("POST", "/api/restart/library/lookup"),
            ("GET", "/api/restart/library/links"),
            ("GET", "/api/restart/library/ean-pool"),
            ("GET", "/api/restart/events"),
            ("GET", "/api/restart/library/lookup/extra"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(AuthenticationFailure):
                authenticate_request(raw_key, settings, method=method, path=path, now=120)
        with self.assertRaises(AuthenticationFailure):
            authorize_account(principal, "JV")

    def test_m2m_key_requires_explicit_allowed_accounts(self):
        raw_key = "aftercool-test-key"
        with self.assertRaisesRegex(SecurityConfigurationError, "explicit OTTORESTART_AFTERCOOL_ALLOWED_ACCOUNTS"):
            SecuritySettings.from_environment({
                "OTTORESTART_MODE": "LEGACY_READ_ONLY",
                "OTTORESTART_AFTERCOOL_M2M_KEY_HASH": hashlib.sha256(raw_key.encode()).hexdigest(),
            })

    def test_production_requires_non_placeholder_secret_operator_and_accounts(self):
        cases = (
            {"OTTORESTART_MODE": "PRODUCTION"},
            {"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_INTERNAL_AUTH_SECRET": "change-this-to-a-long-placeholder-value", "OTTORESTART_OPERATOR_ID": "op", "OTTORESTART_OPERATOR_ACCOUNTS": "JV"},
            {"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_INTERNAL_AUTH_SECRET": SECRET},
        )
        for env in cases:
            with self.subTest(config_keys=sorted(env)), self.assertRaises(SecurityConfigurationError):
                SecuritySettings.from_environment(env)


class RestartHttpAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Importing the module builds an engine but does not connect or start
        # the lifespan. Every request below must be rejected before DB access.
        os.environ["OTTORESTART_DATABASE_URL"] = "postgresql+psycopg://restart_test:restart_test@127.0.0.1:65432/restart_test"
        os.environ["OTTORESTART_INTERNAL_AUTH_SECRET"] = SECRET
        os.environ["OTTORESTART_OPERATOR_ID"] = "operator-1"
        os.environ["OTTORESTART_OPERATOR_ACCOUNTS"] = "JV"
        os.environ["OTTORESTART_MODE"] = "TEST"
        os.environ["OTTORESTART_ALLOWED_ORIGINS"] = "http://testserver"
        os.environ["OTTORESTART_AFTERCOOL_M2M_KEY_HASH"] = ""
        os.environ["OTTORESTART_AFTERCOOL_ALLOWED_ACCOUNTS"] = "JV,XL"
        backend_dir = str(Path(__file__).resolve().parents[1])
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        import app as restart_app
        from fastapi.testclient import TestClient

        restart_app.SECURITY_SETTINGS = SecuritySettings.from_environment(os.environ)

        cls.client = TestClient(restart_app.app)

    def test_forged_actor_and_account_headers_do_not_authenticate(self):
        response = self.client.get(
            "/api/restart/overview?account=JV",
            headers={"X-Actor-ID": "operator-1", "X-Account": "JV", "Idempotency-Key": "replay-key-001"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "UNAUTHORIZED")
        self.assertTrue(response.json()["requestId"])

    def test_operator_token_cannot_access_another_account(self):
        token = encode_token(current_claims())
        response = self.client.get(
            "/api/restart/overview?account=XL",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "ACCOUNT_FORBIDDEN")

    def test_mutation_without_bff_verified_csrf_is_rejected(self):
        token = encode_token(current_claims(csrf=False))
        response = self.client.post(
            "/api/restart/batches/preview",
            json={"account": "JV", "period": "3M", "name": "offline", "sourceEans": ["4006381333931"]},
            headers={"Authorization": f"Bearer {token}", "Origin": "http://testserver", "X-CSRF-Token": "forged"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "CSRF_REQUIRED")

    def test_authorized_multipart_request_account_is_checked_before_handler(self):
        token = encode_token(current_claims())
        response = self.client.post(
            "/api/restart/imports/upload?account=XL",
            files={"file": ("sample.txt", b"\x00\xffbinary", "application/octet-stream")},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://testserver",
                "X-CSRF-Token": "csrf-token",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "ACCOUNT_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()

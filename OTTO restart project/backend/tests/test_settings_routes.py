from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from routers.settings import content_defaults, runtime_settings
from runtime_mode import RuntimeConfig, RuntimeMode
from services.categories_repo import CATEGORIES


class RuntimeSettingsUnitTests(unittest.TestCase):
    def request(self, accounts=("JV", "XL")):
        return SimpleNamespace(
            state=SimpleNamespace(principal=SimpleNamespace(subject="offline-operator", accounts=frozenset(accounts))),
            app=SimpleNamespace(state=SimpleNamespace(runtime_config=RuntimeConfig(
                mode=RuntimeMode.PRODUCTION, otto_read_enabled=True, openai_enabled=True,
                product_write_enabled=True,
            ))),
        )

    def test_settings_expose_flags_and_account_price_rules_without_credentials(self):
        env = {
            "OTTORESTART_OTTO_XL_CLIENT_ID": "offline-id",
            "OTTORESTART_OTTO_XL_CLIENT_SECRET": "never-return-this-secret",
            "OTTORESTART_OPENAI_API_KEY": "never-return-this-key",
            "OTTORESTART_OPENAI_MODEL": "offline-model",
        }
        with patch.dict("os.environ", env, clear=True):
            result = runtime_settings(self.request(), "XL")
            jv = runtime_settings(self.request(), "JV")
        self.assertTrue(result["otto"]["configured"])
        self.assertFalse(jv["otto"]["configured"])
        self.assertEqual(result["price"]["discountCents"], 2000)
        self.assertEqual(jv["price"]["discountCents"], 1000)
        self.assertTrue(result["ai"]["available"])
        self.assertNotIn("never-return", json.dumps(result))
        self.assertFalse(result["publication"]["availabilityWriteEnabled"])

    def test_settings_and_defaults_reject_inaccessible_account(self):
        with self.assertRaises(HTTPException) as error:
            runtime_settings(self.request(("JV",)), "XL")
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException):
            content_defaults(self.request(("JV",)), "XL", "Sofa")

    def test_default_suggestions_require_real_category_and_match_selected_account(self):
        with self.assertRaises(HTTPException) as error:
            content_defaults(self.request(), "XL", "NOT_A_REAL_CATEGORY_987")
        self.assertEqual(error.exception.status_code, 422)
        category = next(item for item in CATEGORIES if item.get("name") == "Skulptur")
        result = content_defaults(self.request(), "XL", category["name"])
        self.assertEqual(result["account"], "XL")
        self.assertTrue(result["attributes"])
        self.assertNotIn("JVMOEBEL", json.dumps(result["attributes"]).upper())

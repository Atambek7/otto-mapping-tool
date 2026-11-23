from __future__ import annotations

import unittest
import contextlib
import io
import os
from unittest.mock import patch

from services.legacy_scope_service import (
    LEGACY_TABLES,
    LegacyPromotionRejected,
    legacy_rows,
    legacy_summary,
    require_legacy_promotion,
)


class ScalarResult:
    def scalar_one(self):
        return 7


class EmptyMappings:
    def all(self):
        return []


class LegacyReadConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, _params=None):
        sql = str(statement).strip()
        self.statements.append(sql)
        if not sql.lower().startswith("select"):
            raise AssertionError("legacy diagnostics must be read-only")
        if "SELECT *" in sql:
            class Result:
                def mappings(self):
                    return EmptyMappings()

            return Result()
        return ScalarResult()


class LegacyScopeServiceTests(unittest.TestCase):
    def test_summary_is_account_scoped_and_only_counts_legacy(self):
        conn = LegacyReadConnection()

        result = legacy_summary(conn, "XL")

        self.assertEqual(result["dataScope"], "LEGACY_UNVERIFIED")
        self.assertEqual(set(result["counts"]), set(LEGACY_TABLES))
        self.assertTrue(all("account = :account" in sql and "data_scope = :scope" in sql for sql in conn.statements))

    def test_row_reader_allowlists_table_and_applies_account_scope_and_limit(self):
        conn = LegacyReadConnection()

        rows, count = legacy_rows(conn, "source_products", "JV", limit=25, offset=50)

        self.assertEqual(rows, [])
        self.assertEqual(count, 7)
        self.assertTrue(all("data_scope = :scope" in sql and "account = :account" in sql for sql in conn.statements))
        self.assertIn("ORDER BY 1 LIMIT :limit OFFSET :offset", conn.statements[-1])
        with self.assertRaises(ValueError):
            legacy_rows(conn, "schema_migrations", "JV", limit=25, offset=0)

    def test_legacy_promotion_fails_closed_without_flag_and_verified_evidence(self):
        with self.assertRaises(LegacyPromotionRejected):
            require_legacy_promotion()
        with self.assertRaises(LegacyPromotionRejected):
            require_legacy_promotion(enabled=True, actor_id="operator", evidence_ref="doc-1")
        with self.assertRaises(LegacyPromotionRejected):
            require_legacy_promotion(
                enabled=True,
                actor_id="operator",
                evidence_ref="doc-1",
                evidence_verified=True,
            )

    def test_promotion_cli_is_disabled_by_default_and_never_accepts_unverified_evidence(self):
        from promote_legacy import main

        output = io.StringIO()
        args = ["--account", "XL", "--table", "source_products", "--row-id", "sku-1", "--evidence-ref", "doc-1"]
        with patch.dict(os.environ, {"OTTORESTART_LEGACY_PROMOTION_ENABLED": "false"}, clear=False):
            with contextlib.redirect_stderr(output):
                self.assertEqual(main(args), 2)
        self.assertIn("disabled", output.getvalue())

        output = io.StringIO()
        with patch.dict(os.environ, {"OTTORESTART_LEGACY_PROMOTION_ENABLED": "true"}, clear=False):
            with contextlib.redirect_stderr(output):
                self.assertEqual(main(args), 2)
        self.assertIn("Verified evidence", output.getvalue())

if __name__ == "__main__":
    unittest.main()

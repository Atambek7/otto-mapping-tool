from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from services.otto_status_service import (
    CODE_12M,
    CODE_3M,
    classify_errors,
    classify_marketplace_status_response,
)


class OttoStatusClassifierTests(unittest.TestCase):
    def test_exact_codes_classify_3m_and_12m(self):
        three = classify_errors([{"code": CODE_3M, "title": ""}])
        twelve = classify_errors([{"code": CODE_12M, "title": ""}])

        self.assertEqual((three.classification, three.period), ("EXACT_CODE", "3M"))
        self.assertEqual((twelve.classification, twelve.period), ("EXACT_CODE", "12M"))

    def test_additional_unrelated_code_does_not_mask_3m_and_is_preserved(self):
        errors = [
            {"code": CODE_3M, "title": ""},
            {"code": "301006 - Dimensionierung Werte identisch", "title": "variant error"},
        ]

        result = classify_errors(errors)

        self.assertEqual((result.classification, result.period), ("EXACT_CODE", "3M"))
        self.assertEqual(result.unknown_codes, ("301006 - Dimensionierung Werte identisch",))
        self.assertEqual(result.errors, tuple(errors))

    def test_normalizes_unicode_case_and_whitespace_but_requires_full_code(self):
        result = classify_errors([{"code": "  602016   -   nonseller  "}])

        self.assertEqual((result.classification, result.period), ("EXACT_CODE", "3M"))
        self.assertEqual(classify_errors([{"code": "602016"}]).classification, "NONE")
        self.assertEqual(classify_errors([{"code": "602016 - Nonseller extra"}]).classification, "NONE")

    def test_title_only_requires_period_and_no_net_sales_semantics(self):
        three = classify_errors([{"title": "Über drei Monaten wurde kein Nettoumsatz erzeugt."}])
        twelve = classify_errors([{"title": "In zwölf Monaten gab es keinen Nettoumsatz."}])
        period_only = classify_errors([{"title": "Über drei Monaten wurde etwas geändert."}])
        net_sales_only = classify_errors([{"title": "Es wurde kein Nettoumsatz erzeugt."}])

        self.assertEqual((three.classification, three.period), ("TITLE_ONLY", "3M"))
        self.assertEqual((twelve.classification, twelve.period), ("TITLE_ONLY", "12M"))
        self.assertEqual(period_only.classification, "NONE")
        self.assertEqual(net_sales_only.classification, "NONE")

    def test_opposite_code_title_or_multiple_periods_are_ambiguous(self):
        code_title_conflict = classify_errors([
            {"code": CODE_3M, "title": "Über zwölf Monaten gab es keinen Nettoumsatz."}
        ])
        multiple_codes = classify_errors([
            {"code": CODE_3M}, {"code": CODE_12M}, {"code": "unknown"}
        ])
        both_periods_in_title = classify_errors([
            {"title": "Drei Monaten und zwölf Monaten wurde kein Nettoumsatz erzeugt."}
        ])

        for result in (code_title_conflict, multiple_codes, both_periods_in_title):
            self.assertEqual(result.classification, "AMBIGUOUS")
            self.assertIsNone(result.period)

    def test_empty_unrelated_and_malformed_errors(self):
        self.assertEqual(classify_errors([]).classification, "NONE")
        unrelated = classify_errors([{"code": "301006 - other", "title": "unrelated"}])
        self.assertEqual(unrelated.classification, "NONE")
        self.assertEqual(unrelated.unknown_codes, ("301006 - other",))
        self.assertEqual(classify_errors(None).classification, "UNCHECKABLE")
        malformed = classify_errors([{"code": CODE_3M}, "bad-entry"])
        self.assertEqual(malformed.classification, "UNCHECKABLE")
        self.assertEqual(len(malformed.errors), 2)

    def test_response_requires_http_200_matching_sku_and_error_array(self):
        good_body = {"sku": "sku-1", "errors": [{"code": CODE_3M}]}
        self.assertEqual(
            classify_marketplace_status_response(good_body, "sku-1", 200).classification,
            "EXACT_CODE",
        )
        mismatch = classify_marketplace_status_response(good_body, "sku-2", 200)
        self.assertEqual(mismatch.classification, "UNCHECKABLE")
        self.assertIsNone(mismatch.period)
        self.assertEqual(mismatch.errors, tuple(good_body["errors"]))
        self.assertEqual(
            classify_marketplace_status_response(good_body, "sku-1", 503).classification,
            "UNCHECKABLE",
        )
        self.assertEqual(
            classify_marketplace_status_response({"sku": "sku-1"}, "sku-1", 200).classification,
            "UNCHECKABLE",
        )
        self.assertEqual(
            classify_marketplace_status_response({"sku": "sku-1", "errors": []}, "sku-2", 200).classification,
            "UNCHECKABLE",
        )

    def test_response_fields_are_not_invented_or_inferred(self):
        body = {
            "sku": "sku-1",
            "status": "INACTIVE",
            "lastModified": "2026-10-15T10:00:00Z",
            "errors": [{"code": CODE_3M}],
        }

        result = classify_marketplace_status_response(body, "sku-1", 200)

        self.assertEqual(result.status, "INACTIVE")
        self.assertEqual(result.last_modified, "2026-10-15T10:00:00Z")
        self.assertIsNone(result.marketplace_status)
        self.assertIsNone(result.active)


class SavedOttoResponsesTests(unittest.TestCase):
    def test_saved_100_sku_responses_match_expected_periods(self):
        project_root = Path(__file__).resolve().parents[2]
        diagnostic_dir = project_root / "diagnostics" / "otto_3m_12m_xl_100_20261016"
        responses_data = json.loads(
            (diagnostic_dir / "otto_3m_12m_api_responses.json").read_text(encoding="utf-8")
        )
        with (diagnostic_dir / "otto_3m_12m_test_results.csv").open(
            newline="", encoding="utf-8-sig"
        ) as csv_file:
            expected_rows = list(csv.DictReader(csv_file))

        expected = {row["api_sku"]: row["expected_period_from_user_list"] for row in expected_rows}
        records = responses_data["responses"]
        self.assertEqual(len(expected), 100)
        self.assertEqual(len(records), 100)

        counts = {"3M": 0, "12M": 0}
        additional_error_count = 0
        for record in records:
            body = record["body"]
            request = record["request"]
            requested_sku = request["requested_sku"]
            self.assertEqual(record["http_status"], 200)
            self.assertEqual(body.get("sku"), requested_sku)
            result = classify_marketplace_status_response(body, requested_sku, record["http_status"])
            self.assertEqual(result.classification, "EXACT_CODE", requested_sku)
            self.assertEqual(result.period, expected[requested_sku], requested_sku)
            counts[result.period] += 1
            if any(error.get("code", "").startswith("301006") for error in body.get("errors", [])):
                additional_error_count += 1
                self.assertEqual(result.period, "3M")
                self.assertIn("301006 - Dimensionierung Werte identisch", result.unknown_codes)

        self.assertEqual(counts, {"3M": 50, "12M": 50})
        self.assertEqual(additional_error_count, 1)


if __name__ == "__main__":
    unittest.main()

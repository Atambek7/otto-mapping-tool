from __future__ import annotations

import unittest
from types import SimpleNamespace

from jobs.processing_handler import _task_result, create_processing_executor
from jobs.service import JobExecutionError


class ProcessingHandlerTests(unittest.TestCase):
    def test_task_summary_is_not_success_without_exact_per_sku_result(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "done", "succeeded": 1, "failed": 0, "unchanged": 0}

            def get_update_task_results(self, _task_id, result_type, *, max_pages):
                return [{"sku": "other-sku"}] if result_type == "succeeded" else []

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("NEEDS_REVIEW", "OTTO_TASK_TARGET_SKU_MISMATCH", "done"))

    def test_task_counts_and_result_pages_must_agree(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "done", "succeeded": 0, "failed": 0, "unchanged": 0}

            def get_update_task_results(self, _task_id, result_type, *, max_pages):
                return [{"sku": "target-sku"}] if result_type == "succeeded" else []

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("NEEDS_REVIEW", "OTTO_TASK_COUNT_MISMATCH", "done"))

    def test_only_unique_exact_succeeded_result_is_processing_proof(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "done", "succeeded": 1, "failed": 0, "unchanged": 0}

            def get_update_task_results(self, _task_id, result_type, *, max_pages):
                return [{"sku": "target-sku"}] if result_type == "succeeded" else []

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("SUCCEEDED", None, "done"))

    def test_unchanged_is_not_publication_proof(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "done", "succeeded": 0, "failed": 0, "unchanged": 1}

            def get_update_task_results(self, _task_id, result_type, *, max_pages):
                return [{"sku": "target-sku"}] if result_type == "unchanged" else []

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("NEEDS_REVIEW", "OTTO_TASK_UNCHANGED_NOT_SUCCESS_PROOF", "done"))

    def test_exact_failed_result_is_distinct_from_processing_success(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "done", "succeeded": 0, "failed": 1, "unchanged": 0}

            def get_update_task_results(self, _task_id, result_type, *, max_pages):
                return [{"sku": "target-sku"}] if result_type == "failed" else []

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("FAILED", "OTTO_TASK_SKU_FAILED", "done"))

    def test_mixed_per_sku_results_are_ambiguous(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "done", "succeeded": 1, "failed": 1, "unchanged": 0}

            def get_update_task_results(self, _task_id, result_type, *, max_pages):
                return [{"sku": "target-sku"}] if result_type in {"succeeded", "failed"} else []

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("NEEDS_REVIEW", "OTTO_TASK_TARGET_SKU_RESULT_MISSING_OR_DUPLICATE", "done"))

    def test_pending_task_never_uses_summary_counters_as_success(self):
        class Client:
            def read_update_task_href(self, _href):
                return {"state": "processing", "succeeded": 1, "failed": 0, "unchanged": 0}

        outcome = _task_result(Client(), {
            "task_href": "/v5/products/update-tasks/00000000-0000-4000-8000-000000000001",
            "task_id": "00000000-0000-4000-8000-000000000001",
            "target_sku": "target-sku",
        })
        self.assertEqual(outcome, ("POLLING", None, "processing"))

    def test_variation_resource_results_and_rejections_are_preserved(self):
        for result_type in ("succeeded", "failed", "unchanged"):
            with self.subTest(result_type=result_type):
                class Client:
                    def read_update_task_href(self, _href):
                        return {"state": "done", "succeeded": int(result_type == "succeeded"),
                                "failed": int(result_type == "failed"), "unchanged": int(result_type == "unchanged")}

                    def get_update_task_results(self, _task_id, kind, **_kwargs):
                        return [{"variation": "/v5/products/4072102511612", "errors": [{
                            "code": "100005 - INVALID_VALUE_CATEGORY", "title": "'25927' ist keine gültige Produktkategorie.",
                        }]}] if kind == result_type else []

                evidence = []
                outcome = _task_result(Client(), {"task_href": "/task", "task_id": "id",
                                       "target_sku": "4072102511612"}, results_out=evidence)
                self.assertEqual(outcome[0], {"succeeded": "SUCCEEDED", "failed": "FAILED", "unchanged": "NEEDS_REVIEW"}[result_type])
                self.assertEqual(evidence[0]["sku"], "4072102511612")
                self.assertEqual(evidence[0]["errors"][0]["code"], "100005_INVALID_VALUE_CATEGORY")

    def test_variation_must_exactly_match_target_without_conflicting_sku(self):
        rows = [
            {"variation": "/v5/products/other"},
            {"variation": "https://attacker.invalid/v5/products/target-sku"},
            {"variation": "/v5/products/target-sku?other=1"},
            {"variation": "/v5/products/target-sku", "sku": "other"},
            {"variation": None, "sku": "target-sku"},
            {"variation": "/v5/products/target-sku", "sku": None},
        ]
        for row in rows:
            with self.subTest(row=row):
                class Client:
                    def read_update_task_href(self, _href):
                        return {"state": "done", "succeeded": 1, "failed": 0, "unchanged": 0}
                    def get_update_task_results(self, _id, kind, **_kwargs):
                        return [row] if kind == "succeeded" else []
                evidence = []
                outcome = _task_result(Client(), {"task_href": "/task", "task_id": "id",
                                       "target_sku": "target-sku"}, results_out=evidence)
                self.assertEqual(outcome, ("NEEDS_REVIEW", "OTTO_TASK_TARGET_SKU_MISMATCH", "done"))
                self.assertEqual(evidence, [])

    def test_marketplace_state_uses_endpoint_status_and_rejects_conflicts(self):
        from services.otto_processing_service import _marketplace_state
        self.assertEqual(_marketplace_state({"status": "ONLINE"}), "ONLINE")
        self.assertEqual(_marketplace_state({"marketPlaceStatus": "ONLINE"}), "ONLINE")
        self.assertIsNone(_marketplace_state({"status": "REJECTED", "marketPlaceStatus": "ONLINE"}))
        self.assertIsNone(_marketplace_state({"status": "SUCCEEDED"}))
        self.assertIsNone(_marketplace_state({"status": None}))

    def test_disabled_read_flag_stops_before_database_or_client(self):
        calls: list[str] = []
        executor = create_processing_executor(
            lambda: calls.append("database"),
            environment={"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_OTTO_READ_ENABLED": "false"},
            client_factory=lambda *_args: calls.append("client"),
        )
        with self.assertRaises(JobExecutionError) as raised:
            executor(SimpleNamespace(job_type="otto.poll_product_task", payload={"attemptId": "x"}, account="XL"))
        self.assertEqual(raised.exception.code, "OTTO_READ_DISABLED")
        self.assertEqual(calls, [])

    def test_invalid_job_stops_before_database_access(self):
        executor = create_processing_executor(lambda: self.fail("invalid job must not access database"), environment={})
        with self.assertRaises(JobExecutionError) as raised:
            executor(SimpleNamespace(job_type="otto.submit_product", payload={}, account="XL"))
        self.assertEqual(raised.exception.code, "INVALID_OTTO_POLL_JOB")


if __name__ == "__main__":
    unittest.main()

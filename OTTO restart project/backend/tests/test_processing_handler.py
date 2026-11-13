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

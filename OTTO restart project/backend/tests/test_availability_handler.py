from __future__ import annotations

import unittest
from types import SimpleNamespace

from jobs.availability_handler import create_availability_executor
from jobs.service import JobExecutionError


class AvailabilityHandlerTests(unittest.TestCase):
    def test_disabled_flag_stops_before_database_or_client_construction(self) -> None:
        calls: list[str] = []

        def forbidden_engine_provider():
            calls.append("database")
            raise AssertionError("disabled availability write must not access the database")

        def forbidden_client_factory(account, config):
            calls.append("client")
            raise AssertionError("disabled availability write must not construct an OTTO client")

        executor = create_availability_executor(
            forbidden_engine_provider,
            environment={
                "OTTORESTART_MODE": "PRODUCTION",
                "OTTORESTART_OTTO_READ_ENABLED": "true",
                "OTTORESTART_AVAILABILITY_WRITE_ENABLED": "false",
            },
            client_factory=forbidden_client_factory,
        )
        job = SimpleNamespace(
            job_type="otto.availability.quantity",
            account="XL",
            id="00000000-0000-4000-8000-000000000001",
            payload={
                "action": "QUANTITY",
                "availabilityAttemptId": "00000000-0000-4000-8000-000000000002",
            },
        )
        with self.assertRaises(JobExecutionError) as raised:
            executor(job)
        self.assertEqual(raised.exception.code, "OTTO_AVAILABILITY_WRITE_DISABLED")
        self.assertEqual(calls, [])

    def test_wrong_job_type_is_rejected_without_runtime_access(self) -> None:
        executor = create_availability_executor(
            lambda: self.fail("invalid job must stop before database access"), environment={}
        )
        job = SimpleNamespace(job_type="otto.product.submit", account="XL", id="x", payload={})
        with self.assertRaises(JobExecutionError) as raised:
            executor(job)
        self.assertEqual(raised.exception.code, "INVALID_AVAILABILITY_JOB")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from sys import modules
from unittest.mock import patch

from jobs.import_handler import create_import_executor
from jobs.service import JobExecutionError
from runtime_mode import RuntimeMode


class ImportHandlerTests(unittest.TestCase):
    def test_executor_passes_run_chunk_and_retry_metadata_without_network(self):
        environment = {
            "OTTORESTART_MODE": "PRODUCTION",
            "OTTORESTART_OTTO_READ_ENABLED": "true",
            "OTTORESTART_AFTERCOOL_READ_ENABLED": "true",
            "OTTORESTART_OPENAI_ENABLED": "false",
            "OTTORESTART_PRODUCT_WRITE_ENABLED": "false",
            "OTTORESTART_AVAILABILITY_WRITE_ENABLED": "false",
            "OTTORESTART_DISCOVERY_SCHEDULE_ENABLED": "false",
            "OTTORESTART_CELERY_ENABLED": "false",
        }
        job = SimpleNamespace(
            id="job-2", account="JV", payload={"runId": "run-1", "chunk": 2},
            lease_token="lease-token", attempts_in_cycle=3, max_attempts=5,
        )

        with patch("jobs.import_handler.process_import_run") as process:
            executor = create_import_executor(lambda: object(), environment=environment)
            executor(job)

        process.assert_called_once()
        args, kwargs = process.call_args
        self.assertEqual(args[1], "run-1")
        self.assertEqual(kwargs["chunk_number"], 2)
        self.assertEqual(kwargs["job_attempt"], 3)
        self.assertEqual(kwargs["max_attempts"], 5)
        self.assertEqual(kwargs["runtime_config"].mode, RuntimeMode.PRODUCTION)

    def test_invalid_chunk_is_rejected_before_processing(self):
        job = SimpleNamespace(
            id="job-invalid", account="JV", payload={"runId": "run-1", "chunk": True},
            lease_token="lease-token", attempts_in_cycle=1, max_attempts=5,
        )
        with patch("jobs.import_handler.process_import_run") as process:
            executor = create_import_executor(lambda: object(), environment={})
            with self.assertRaises(JobExecutionError) as raised:
                executor(job)
        self.assertEqual(raised.exception.code, "INVALID_IMPORT_CHUNK")
        process.assert_not_called()

    def test_explicit_celery_app_registers_import_executor_without_connecting(self):
        from jobs.celery_app import EXECUTE_JOB_TASK, create_celery_app

        class FakeCelery:
            def __init__(self, _name, *, broker, backend):
                self.broker = broker
                self.backend = backend
                self.conf = {}
                self.tasks = {}

            def task(self, *, name, **_options):
                def register(function):
                    self.tasks[name] = function
                    return function
                return register

        factory = SimpleNamespace(Celery=FakeCelery)
        environment = {
            "OTTORESTART_CELERY_ENABLED": "true",
            "OTTORESTART_MODE": "TEST",
            "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0",
        }
        engine_provider = lambda: object()
        import_executor = lambda _job: None

        with patch.dict(modules, {"celery": factory}):
            with patch("jobs.import_handler.create_import_executor", return_value=import_executor) as create_executor:
                celery_app = create_celery_app(environment, engine_provider=engine_provider)

        create_executor.assert_called_once_with(engine_provider)
        self.assertIn(EXECUTE_JOB_TASK, celery_app.tasks)
        with patch("jobs.celery_app.run_job_by_id") as run_job:
            celery_app.tasks[EXECUTE_JOB_TASK]("job-id")
        self.assertIs(run_job.call_args.kwargs["executors"]["import.process"], import_executor)


if __name__ == "__main__":
    unittest.main()

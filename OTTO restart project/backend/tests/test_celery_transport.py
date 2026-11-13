from __future__ import annotations

import inspect
import unittest
from uuid import uuid4
from unittest.mock import patch

from jobs.celery_app import (
    DISCOVERY_SCHEDULE_TASK,
    DISPATCH_OUTBOX_TASK,
    CeleryConfigurationError,
    app as runtime_app,
    create_celery_app,
)
from jobs.celery_transport import DEFAULT_QUEUE, EXECUTE_JOB_TASK, CeleryJobTransport
from jobs.dispatcher import AVAILABILITY_WRITE_JOB_TYPES, FIRST_RELEASE_JOB_ALLOWLIST, job_allowlist
from jobs.transport import JobPublishError


class CeleryTransportTests(unittest.TestCase):
    def test_disabled_or_ambiguous_runtime_fails_closed(self):
        self.assertIsNone(runtime_app)
        with self.assertRaises(CeleryConfigurationError):
            create_celery_app(
                {"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_CELERY_ENABLED": "false"},
                engine_provider=lambda: None,
            )
        with self.assertRaises(CeleryConfigurationError):
            create_celery_app(
                {"OTTORESTART_MODE": "LEGACY_READ_ONLY", "OTTORESTART_CELERY_ENABLED": "true",
                 "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0"},
                engine_provider=lambda: None,
            )
        with self.assertRaises(CeleryConfigurationError):
            create_celery_app(
                {"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_CELERY_ENABLED": "true",
                 "OTTORESTART_CELERY_BROKER_URL": ""},
                engine_provider=lambda: None,
            )
        with self.assertRaises(CeleryConfigurationError):
            create_celery_app(
                {"OTTORESTART_MODE": "PRODUCTION", "OTTORESTART_CELERY_ENABLED": "true",
                 "OTTORESTART_CELERY_BROKER_URL": "amqp://localhost"},
                engine_provider=lambda: None,
            )

    def test_celery_app_requires_explicit_redis_and_registers_id_only_task(self):
        engine = object()
        app = create_celery_app(
            {
                "OTTORESTART_MODE": "TEST",
                "OTTORESTART_CELERY_ENABLED": "true",
                "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0",
            },
            engine_provider=lambda: engine,
            executors={},
        )
        task = app.tasks[EXECUTE_JOB_TASK]
        self.assertEqual(list(inspect.signature(task.run).parameters), ["job_id"])
        self.assertTrue(task.acks_late)
        self.assertTrue(task.reject_on_worker_lost)
        self.assertTrue(app.conf.task_ignore_result)
        self.assertEqual(app.conf.task_default_queue, DEFAULT_QUEUE)
        self.assertEqual(app.conf.accept_content, ["json"])
        job_id = str(uuid4())
        with patch("jobs.celery_app.run_job_by_id") as run_job:
            app.tasks[EXECUTE_JOB_TASK].apply(kwargs={"job_id": job_id})
        run_job.assert_called_once()
        self.assertIs(run_job.call_args.args[0], engine)
        self.assertEqual(run_job.call_args.kwargs["job_id"], job_id)
        self.assertEqual(run_job.call_args.kwargs["executors"], {})
        self.assertEqual(run_job.call_args.kwargs["allowed_job_types"], FIRST_RELEASE_JOB_ALLOWLIST)
        self.assertIsNone(run_job.call_args.kwargs["created_after"])

        with patch("jobs.celery_app.dispatch_once") as dispatch:
            app.tasks[DISPATCH_OUTBOX_TASK].apply()
        dispatch.assert_called_once()
        self.assertIs(dispatch.call_args.args[0], engine)
        self.assertIsInstance(dispatch.call_args.args[1], CeleryJobTransport)
        self.assertEqual(dispatch.call_args.kwargs["allowed_job_types"], FIRST_RELEASE_JOB_ALLOWLIST)

    def test_enabled_app_registers_complete_read_and_write_gated_executor_map(self):
        import sys
        from types import ModuleType

        class FakeTask:
            def __init__(self, function):
                self.function = function

            def apply(self, *, kwargs):
                return self.function(**kwargs)

        class FakeCelery:
            def __init__(self, *_args, **_kwargs):
                self.conf = {}
                self.tasks = {}

            def task(self, *, name, **_options):
                def register(function):
                    task = FakeTask(function)
                    self.tasks[name] = task
                    return task

                return register

        celery_module = ModuleType("celery")
        celery_module.Celery = FakeCelery
        import_executor = lambda _job: None
        with (
            patch.dict(sys.modules, {"celery": celery_module}),
            patch("jobs.celery_app.run_job_by_id") as run_job,
            patch("jobs.import_handler.create_import_executor", return_value=import_executor),
        ):
            app = create_celery_app(
                {
                    "OTTORESTART_MODE": "TEST",
                    "OTTORESTART_CELERY_ENABLED": "true",
                    "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0",
                    "OTTORESTART_OTTO_READ_ENABLED": "true",
                },
                engine_provider=lambda: object(),
            )
            app.tasks[EXECUTE_JOB_TASK].apply(kwargs={"job_id": str(uuid4())})

        executors = run_job.call_args.kwargs["executors"]
        self.assertEqual(set(executors), {
            "import.process", "drip.release_item", "otto.submit_product",
            "otto.poll_product_task", "otto.confirm_online",
            "otto.availability.quantity", "otto.availability.delivery",
        })
        self.assertEqual(run_job.call_args.kwargs["allowed_job_types"], FIRST_RELEASE_JOB_ALLOWLIST)
        self.assertEqual(app.conf["beat_schedule"], {})

    def test_availability_outbox_jobs_require_the_explicit_write_flag(self):
        self.assertEqual(job_allowlist(), FIRST_RELEASE_JOB_ALLOWLIST)
        enabled = job_allowlist(availability_write_enabled=True)
        self.assertEqual(enabled[:len(FIRST_RELEASE_JOB_ALLOWLIST)], FIRST_RELEASE_JOB_ALLOWLIST)
        self.assertEqual(enabled[-len(AVAILABILITY_WRITE_JOB_TYPES):], AVAILABILITY_WRITE_JOB_TYPES)

    def test_production_worker_requires_fixed_cutoff_and_rejects_non_utc(self):
        base = {
            "OTTORESTART_MODE": "PRODUCTION",
            "OTTORESTART_CELERY_ENABLED": "true",
            "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0",
        }
        with self.assertRaisesRegex(CeleryConfigurationError, "MANUAL_IMPORTS_AFTER"):
            create_celery_app(base, engine_provider=lambda: object(), executors={})
        with self.assertRaisesRegex(CeleryConfigurationError, "UTC timezone"):
            create_celery_app(
                {**base, "OTTORESTART_MANUAL_IMPORTS_AFTER": "2026-11-10T12:00:00+02:00"},
                engine_provider=lambda: object(), executors={},
            )

    def test_weekly_beat_entry_requires_the_explicit_global_flag(self):
        import sys
        from types import ModuleType

        class FakeCelery:
            def __init__(self, *_args, **_kwargs):
                self.conf = {}
                self.tasks = {}

            def task(self, *, name, **_options):
                def register(function):
                    self.tasks[name] = function
                    return function
                return register

        celery_module = ModuleType("celery")
        celery_module.Celery = FakeCelery
        environment = {
            "OTTORESTART_MODE": "TEST",
            "OTTORESTART_CELERY_ENABLED": "true",
            "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0",
            "OTTORESTART_DISCOVERY_SCHEDULE_ENABLED": "true",
            "OTTORESTART_OTTO_READ_ENABLED": "true",
        }
        with (
            patch.dict(sys.modules, {"celery": celery_module}),
            patch("jobs.import_handler.create_import_executor", return_value=lambda _job: None),
        ):
            app = create_celery_app(environment, engine_provider=lambda: object())
        self.assertIn("ottorestart-weekly-discovery-tick", app.conf["beat_schedule"])
        self.assertIn(DISCOVERY_SCHEDULE_TASK, app.tasks)

    def test_weekly_schedule_is_rejected_without_otto_read_flag(self):
        environment = {
            "OTTORESTART_MODE": "TEST",
            "OTTORESTART_CELERY_ENABLED": "true",
            "OTTORESTART_CELERY_BROKER_URL": "redis://localhost:6379/0",
            "OTTORESTART_DISCOVERY_SCHEDULE_ENABLED": "true",
            "OTTORESTART_OTTO_READ_ENABLED": "false",
        }
        with self.assertRaises(CeleryConfigurationError):
            create_celery_app(environment, engine_provider=lambda: object())

    def test_dispatcher_loop_is_bounded_and_stops_cleanly(self):
        from jobs.celery_app import DISPATCH_OUTBOX_TASK
        from jobs.dispatcher_loop import run_dispatcher

        class StopAfterTicks:
            def __init__(self, maximum):
                self.maximum = maximum
                self.waits = 0
                self.stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, _interval):
                self.waits += 1
                self.stopped = self.waits >= self.maximum
                return self.stopped

        class Result:
            def failed(self):
                return False

        class Task:
            def __init__(self):
                self.calls = 0

            def apply(self):
                self.calls += 1
                return Result()

        task = Task()
        runtime = type("Runtime", (), {"tasks": {DISPATCH_OUTBOX_TASK: task}})()
        stop_event = StopAfterTicks(3)
        ticks = run_dispatcher(runtime, stop_event=stop_event, interval_seconds=0.25)

        self.assertEqual(ticks, 3)
        self.assertEqual(task.calls, 3)
        self.assertTrue(stop_event.is_set())

    def test_dispatch_interval_rejects_unbounded_values(self):
        from jobs.dispatcher_loop import DispatcherConfigurationError, dispatch_interval

        self.assertEqual(dispatch_interval({}), 2.0)
        with self.assertRaises(DispatcherConfigurationError):
            dispatch_interval({"OTTORESTART_DISPATCH_INTERVAL_SECONDS": "0.01"})
        with self.assertRaises(DispatcherConfigurationError):
            dispatch_interval({"OTTORESTART_DISPATCH_INTERVAL_SECONDS": "not-a-number"})

    def test_transport_sends_only_job_id_to_celery(self):
        job_id = str(uuid4())

        class RecordingApp:
            def __init__(self):
                self.calls = []

            def send_task(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        app = RecordingApp()
        CeleryJobTransport(app).publish_job(job_id)
        args, kwargs = app.calls[0]
        self.assertEqual(args, (EXECUTE_JOB_TASK,))
        self.assertEqual(kwargs["kwargs"], {"job_id": job_id})
        self.assertEqual(kwargs["queue"], DEFAULT_QUEUE)
        self.assertEqual(kwargs["serializer"], "json")
        self.assertTrue(kwargs["ignore_result"])
        self.assertEqual(kwargs["delivery_mode"], 2)
        self.assertNotIn("payload", kwargs)

    def test_transport_sanitizes_broker_connection_errors(self):
        from kombu.exceptions import OperationalError

        class FailedApp:
            def send_task(self, *args, **kwargs):
                raise OperationalError("redis://user:secret@private-host/0")

        with self.assertRaises(JobPublishError) as caught:
            CeleryJobTransport(FailedApp()).publish_job(str(uuid4()))
        self.assertEqual(str(caught.exception), "BROKER_UNAVAILABLE")
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

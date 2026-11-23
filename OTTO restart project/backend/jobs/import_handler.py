"""Background-job adapter for the initial file-import run processor."""

from __future__ import annotations

import os
import threading
from typing import Callable, Mapping

from sqlalchemy.engine import Engine

from integrations.otto_client import OttoMarketClient
from integrations.otto_sqlite_snapshot import SqliteSnapshotOttoClient, resolve_snapshot_db_path
from jobs.service import BackgroundJob, JobExecutionError, heartbeat_job
from runtime_mode import RuntimeConfig
from services.aftercool_lookup_service import AftercoolLookupService
from services.import_pipeline import process_import_run

DEFAULT_IMPORT_CONCURRENCY = 2
MAX_IMPORT_CONCURRENCY = 3


def _import_concurrency(environment: Mapping[str, str]) -> int:
    raw = str(environment.get("OTTORESTART_IMPORT_CONCURRENCY", str(DEFAULT_IMPORT_CONCURRENCY))).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_IMPORT_CONCURRENCY
    return max(1, min(MAX_IMPORT_CONCURRENCY, value))


def create_import_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
):
    """Build an executor without constructing clients or making network calls."""
    source = dict(os.environ if environment is None else environment)
    concurrency = _import_concurrency(source)

    def execute(job: BackgroundJob) -> None:
        run_id = job.payload.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise JobExecutionError("INVALID_IMPORT_JOB", retryable=False)
        chunk = job.payload.get("chunk", 1)
        if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk < 1:
            raise JobExecutionError("INVALID_IMPORT_CHUNK", retryable=False)
        config = RuntimeConfig.from_environment(source)
        created_snapshots: list[SqliteSnapshotOttoClient] = []

        def build_otto_client(account: str):
            live_client = OttoMarketClient.from_environment(
                account, environment=source, runtime_config=config
            )
            snapshot_path = resolve_snapshot_db_path(account, environment=source)
            if snapshot_path is None:
                return live_client
            wrapped = SqliteSnapshotOttoClient(account, snapshot_path, fallback_client=live_client)
            created_snapshots.append(wrapped)
            return wrapped

        stop = threading.Event()
        heartbeat_errors: list[str] = []

        def heartbeat_loop() -> None:
            while not stop.wait(15):
                try:
                    with engine_provider().begin() as connection:
                        heartbeat_job(
                            connection,
                            job_id=job.id,
                            account=job.account,
                            lease_token=job.lease_token or "",
                            lease_seconds=60,
                        )
                except Exception:
                    heartbeat_errors.append("IMPORT_LEASE_HEARTBEAT_FAILED")
                    stop.set()

        thread = threading.Thread(target=heartbeat_loop, name="ottorestart-import-heartbeat", daemon=True)
        thread.start()
        try:
            process_import_run(
                engine_provider(),
                run_id,
                otto_client_factory=build_otto_client,
                aftercool_service_factory=lambda: AftercoolLookupService(
                    runtime_mode=config.mode, runtime_config=config, environment=source
                ),
                runtime_config=config,
                chunk_number=chunk,
                job_attempt=job.attempts_in_cycle,
                max_attempts=job.max_attempts,
                concurrency=concurrency,
            )
        finally:
            stop.set()
            thread.join(timeout=2)
            for snapshot in created_snapshots:
                snapshot.close()
        if heartbeat_errors:
            raise JobExecutionError(heartbeat_errors[0], retryable=True)

    return execute

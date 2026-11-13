"""Read-only worker for resolving newly detected candidates to a verified source."""

from __future__ import annotations

import os
import threading
from typing import Callable, Mapping

from sqlalchemy.engine import Engine

from integrations.otto_client import OttoMarketClient
from jobs.service import BackgroundJob, JobExecutionError, heartbeat_job
from runtime_mode import RuntimeConfig, RuntimeMode
from services.aftercool_lookup_service import AftercoolLookupService
from services.candidate_enrichment_service import (
    mark_candidate_enrichment_unavailable,
    process_candidate_enrichment,
)


def create_candidate_enrichment_executor(
    engine_provider: Callable[[], Engine],
    *,
    environment: Mapping[str, str] | None = None,
    otto_client_factory: Callable[[str], object] | None = None,
    aftercool_service_factory: Callable[[], AftercoolLookupService] | None = None,
):
    """Create an executor lazily; external adapters are constructed only for a claimed job."""
    source = dict(os.environ if environment is None else environment)

    def execute(job: BackgroundJob) -> None:
        episode_id = job.payload.get("episodeId")
        observation_id = job.payload.get("observationId")
        if not isinstance(episode_id, str) or not isinstance(observation_id, str):
            raise JobExecutionError("INVALID_CANDIDATE_ENRICHMENT_JOB", retryable=False)
        config = RuntimeConfig.from_environment(source)
        if config.mode is not RuntimeMode.PRODUCTION:
            mark_candidate_enrichment_unavailable(
                engine_provider(), account=job.account, episode_id=episode_id, code="PRODUCTION_MODE_REQUIRED"
            )
            raise JobExecutionError("PRODUCTION_MODE_REQUIRED", retryable=False)
        if not config.otto_read_enabled:
            mark_candidate_enrichment_unavailable(
                engine_provider(), account=job.account, episode_id=episode_id, code="OTTO_READ_DISABLED"
            )
            raise JobExecutionError("OTTO_READ_DISABLED", retryable=False)
        if not config.aftercool_read_enabled:
            mark_candidate_enrichment_unavailable(
                engine_provider(), account=job.account, episode_id=episode_id, code="AFTERCOOL_READ_DISABLED"
            )
            raise JobExecutionError("AFTERCOOL_READ_DISABLED", retryable=False)

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
                    heartbeat_errors.append("CANDIDATE_ENRICHMENT_HEARTBEAT_FAILED")
                    stop.set()

        thread = threading.Thread(target=heartbeat_loop, name="ottorestart-candidate-heartbeat", daemon=True)
        thread.start()
        try:
            outcome = process_candidate_enrichment(
                engine_provider(),
                account=job.account,
                episode_id=episode_id,
                observation_id=observation_id,
                otto_client_factory=otto_client_factory or (
                    lambda account: OttoMarketClient.from_environment(
                        account, environment=source, runtime_config=config
                    )
                ),
                aftercool_service_factory=aftercool_service_factory or (
                    lambda: AftercoolLookupService(
                        runtime_mode=config.mode, runtime_config=config, environment=source
                    )
                ),
                max_attempts=job.max_attempts,
                attempt=job.attempts_in_cycle,
            )
        finally:
            stop.set()
            thread.join(timeout=2)
        if heartbeat_errors:
            raise JobExecutionError(heartbeat_errors[0], retryable=True)
        if outcome.get("state") == "ERROR" and job.attempts_in_cycle < job.max_attempts:
            # The service returns ERROR only for terminal adapter/configuration
            # outcomes; retryable transport errors raise before this point.
            raise JobExecutionError(str(outcome.get("code") or "CANDIDATE_ENRICHMENT_FAILED"), retryable=False)

    return execute

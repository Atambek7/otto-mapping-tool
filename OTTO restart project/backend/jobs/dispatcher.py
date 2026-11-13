"""One-shot PostgreSQL outbox dispatcher; the caller controls scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy.engine import Engine

from jobs.service import (
    claim_job_dispatches,
    mark_job_dispatched,
    reap_expired_leases,
    release_job_dispatch,
)
from jobs.transport import JobPublishError, JobTransport

FIRST_RELEASE_JOB_ALLOWLIST = (
    "import.process",
    # Scheduled release promotes an approved item in the local database and
    # may enqueue submission only after worker-side runtime gates are checked.
    "drip.release_item",
    # This is reachable only through the explicitly operator-started schedule.
    # The worker still rechecks QC, revision hash, account, and write flags.
    "otto.submit_product",
    # These workers perform OTTO GETs only; they reconcile 202 processing and
    # verify marketplace status after the separately gated availability steps.
    "otto.poll_product_task",
    "otto.confirm_online",
)
AVAILABILITY_WRITE_JOB_TYPES = (
    "otto.availability.quantity",
    "otto.availability.delivery",
)


def job_allowlist(*, availability_write_enabled: bool = False) -> tuple[str, ...]:
    """Expose availability outbox jobs only when the explicit write flag is on."""
    if type(availability_write_enabled) is not bool:
        raise ValueError("availability_write_enabled must be a boolean")
    if not availability_write_enabled:
        return FIRST_RELEASE_JOB_ALLOWLIST
    return FIRST_RELEASE_JOB_ALLOWLIST + AVAILABILITY_WRITE_JOB_TYPES


@dataclass(frozen=True)
class DispatchReport:
    claimed: int
    published: int
    publish_failed: int
    claim_raced: int
    leases_reaped: int


def dispatch_once(
    engine: Engine,
    transport: JobTransport,
    *,
    limit: int = 100,
    dispatch_lease_seconds: int = 30,
    stale_after_seconds: int = 3600,
    accounts: Sequence[str] | None = None,
    allowed_job_types: Sequence[str] | None = None,
    created_after: datetime | None = None,
    now: datetime | None = None,
) -> DispatchReport:
    """Publish due job IDs with bounded DB leases and at-least-once recovery.

    No DB transaction is held while contacting the broker. A crash after broker
    acceptance may result in duplicate messages; the worker claims by ID and
    PostgreSQL lease fencing makes duplicate delivery a no-op.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    with engine.begin() as connection:
        reaped = reap_expired_leases(
            connection,
            limit=limit,
            accounts=accounts,
            job_types=allowed_job_types,
            created_after=created_after,
            now=current,
        )
        claims = claim_job_dispatches(
            connection,
            limit=limit,
            lease_seconds=dispatch_lease_seconds,
            stale_after_seconds=stale_after_seconds,
            accounts=accounts,
            job_types=allowed_job_types,
            created_after=created_after,
            now=current,
        )

    published = 0
    failed = 0
    raced = 0
    for claim in claims:
        try:
            transport.publish_job(claim.job_id)
        except JobPublishError as error:
            error_code = error.code
            did_release = True
            with engine.begin() as connection:
                did_release = release_job_dispatch(
                    connection, claim=claim, error_code=error_code, now=current
                )
            failed += int(did_release)
            raced += int(not did_release)
        except Exception:
            # Do not persist or log third-party exception text; it may include a URL.
            with engine.begin() as connection:
                did_release = release_job_dispatch(
                    connection, claim=claim, error_code="BROKER_PUBLISH_FAILED", now=current
                )
            failed += int(did_release)
            raced += int(not did_release)
        else:
            with engine.begin() as connection:
                did_mark = mark_job_dispatched(connection, claim=claim, now=current)
            published += int(did_mark)
            raced += int(not did_mark)

    return DispatchReport(
        claimed=len(claims),
        published=published,
        publish_failed=failed,
        claim_raced=raced,
        leases_reaped=reaped,
    )

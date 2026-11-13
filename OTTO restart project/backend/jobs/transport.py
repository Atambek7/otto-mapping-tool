"""Message transport contract for the PostgreSQL-backed job outbox."""

from __future__ import annotations

from typing import Protocol


class JobPublishError(RuntimeError):
    """A publication failure with a stable code that contains no broker details."""

    def __init__(self, code: str = "BROKER_UNAVAILABLE") -> None:
        if code not in {"BROKER_UNAVAILABLE", "BROKER_PUBLISH_FAILED"}:
            raise ValueError("unsupported broker error code")
        self.code = code
        super().__init__(code)


class JobTransport(Protocol):
    def publish_job(self, job_id: str) -> None:
        """Publish only a durable job identifier, never business payload."""

"""Explicit registry for job handlers added by later OttoRestart stages."""

from __future__ import annotations

import re
from threading import RLock
from typing import Callable

from jobs.service import BackgroundJob

_JOB_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
JobExecutor = Callable[[BackgroundJob], None]
_executors: dict[str, JobExecutor] = {}
_lock = RLock()


def register_job_executor(job_type: str, executor: JobExecutor) -> None:
    if not isinstance(job_type, str) or not _JOB_TYPE.fullmatch(job_type):
        raise ValueError("job_type is invalid")
    if not callable(executor):
        raise ValueError("executor must be callable")
    with _lock:
        if job_type in _executors:
            raise ValueError("an executor is already registered for this job type")
        _executors[job_type] = executor


def executor_snapshot() -> dict[str, JobExecutor]:
    with _lock:
        return dict(_executors)

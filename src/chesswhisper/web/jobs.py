"""In-memory registry of the web UI's background analysis jobs.

Job state does not survive a server restart -- this mirrors the rest of
the app's local, single-process design (no migrations tooling, no
public-internet hardening). Every function here takes the module-level
lock, so callers never need to know the registry is shared across the
request thread and whichever thread is running the analysis.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

_JOB_TTL_SECONDS = 60 * 60  # one hour


def _get_current_time() -> float:
    """Get the current monotonic time. Separated to allow monkeypatching in tests."""
    return time.monotonic()


@dataclass
class Job:
    id: str
    user_id: int
    status: str = "running"  # "running" | "done" | "error"
    completed: int = 0
    total: int = 0
    record_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None
    created_at: float = field(default_factory=_get_current_time)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _prune_expired(now: float) -> None:
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if job.status != "running" and now - job.created_at > _JOB_TTL_SECONDS
    ]
    for job_id in expired:
        del _jobs[job_id]


def create_job(user_id: int) -> Job:
    job = Job(id=str(uuid.uuid4()), user_id=user_id)
    with _lock:
        _prune_expired(time.monotonic())
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def update_progress(job_id: str, completed: int, total: int) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.completed = completed
            job.total = total


def mark_done(job_id: str, record_id: int, warnings: list[str]) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.record_id = record_id
            job.warnings = warnings
            job.status = "done"


def mark_error(job_id: str, message: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.error_message = message
            job.status = "error"

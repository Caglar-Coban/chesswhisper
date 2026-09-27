"""The web UI's analysis jobs, stored in the analysis_jobs table.

A job is created "queued" with everything needed to run it, moves to
"running" when a worker picks it up (web/worker.py) and ends "done" or
"error". Because the inputs are stored, a job interrupted by a restart is
queued again by recover() and run from the start; the evaluation cache
makes the games it already analyzed cheap the second time.

Every function opens its own short session and returns plain Job
snapshots, so callers on the request thread and on worker threads never
share ORM objects.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func

from chesswhisper.web.db import session_scope, utcnow
from chesswhisper.web.models import AnalysisJob

logger = logging.getLogger(__name__)

JOB_RETENTION = timedelta(days=7)
# A job interrupted this many times is failed rather than retried again.
MAX_ATTEMPTS = 3

ACTIVE_STATUSES = ("queued", "running")

# The check-then-insert in create_job_unless_running must not interleave
# between threads; the app is documented as a single process.
_create_lock = threading.Lock()


@dataclass(frozen=True)
class JobInputs:
    pgn_text: str
    player: str | None
    lang: str
    llm: str
    ui_language: str
    fetch_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Job:
    id: str
    user_id: int
    status: str  # "queued" | "running" | "done" | "error"
    completed: int = 0
    total: int = 0
    record_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None


def _snapshot(row: AnalysisJob) -> Job:
    status = row.status
    return Job(
        id=row.id,
        user_id=row.user_id,
        status=status,
        completed=row.completed,
        total=row.total,
        record_id=row.record_id,
        # Until a job finishes, warnings_json holds the fetch warnings meant for the result.
        warnings=json.loads(row.warnings_json) if status == "done" else [],
        error_message=row.error_message,
    )


def _new_row(user_id: int, inputs: JobInputs | None) -> AnalysisJob:
    inputs = inputs or JobInputs(pgn_text="", player=None, lang="en", llm="none", ui_language="en")
    return AnalysisJob(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status="queued",
        completed=0,
        total=0,
        attempts=0,
        warnings_json=json.dumps(inputs.fetch_warnings),
        pgn_text=inputs.pgn_text,
        player=inputs.player,
        lang=inputs.lang,
        llm=inputs.llm,
        ui_language=inputs.ui_language,
        created_at=utcnow(),
    )


def create_job(user_id: int, inputs: JobInputs | None = None) -> Job:
    prune()
    with session_scope() as db:
        row = _new_row(user_id, inputs)
        db.add(row)
        db.flush()
        return _snapshot(row)


def running_job_for(user_id: int) -> Job | None:
    """The user's queued or running analysis, if any (each user runs one at a time)."""
    with session_scope() as db:
        row = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.user_id == user_id, AnalysisJob.status.in_(ACTIVE_STATUSES))
            .order_by(AnalysisJob.created_at)
            .first()
        )
        return _snapshot(row) if row is not None else None


def create_job_unless_running(user_id: int, inputs: JobInputs) -> tuple[Job, bool]:
    """(the user's active job, False) if there is one, else (a new job, True)."""
    with _create_lock:
        running = running_job_for(user_id)
        if running is not None:
            return running, False
        return create_job(user_id, inputs), True


def get_job(job_id: str) -> Job | None:
    with session_scope() as db:
        row = db.get(AnalysisJob, job_id)
        return _snapshot(row) if row is not None else None


def job_inputs(job_id: str) -> JobInputs | None:
    with session_scope() as db:
        row = db.get(AnalysisJob, job_id)
        if row is None:
            return None
        return JobInputs(
            pgn_text=row.pgn_text,
            player=row.player,
            lang=row.lang,
            llm=row.llm,
            ui_language=row.ui_language,
            fetch_warnings=json.loads(row.warnings_json),
        )


def queue_position(job_id: str) -> int:
    """How many queued jobs are ahead of this one."""
    with session_scope() as db:
        row = db.get(AnalysisJob, job_id)
        if row is None:
            return 0
        return (
            db.query(func.count(AnalysisJob.id))
            .filter(AnalysisJob.status == "queued", AnalysisJob.created_at < row.created_at)
            .scalar()
        )


def mark_running(job_id: str) -> bool:
    """Claim a queued job for a worker; False if it is gone or not queued."""
    with session_scope() as db:
        row = db.get(AnalysisJob, job_id)
        if row is None or row.status != "queued":
            return False
        row.status = "running"
        row.attempts += 1
        row.started_at = utcnow()
        return True


def update_progress(job_id: str, completed: int, total: int) -> None:
    # Progress is cosmetic: a failed write (say, a locked database) must not
    # abort the analysis that reported it.
    try:
        with session_scope() as db:
            row = db.get(AnalysisJob, job_id)
            if row is not None:
                row.completed = completed
                row.total = total
    except Exception:
        logger.warning("Could not save progress for job %s", job_id, exc_info=True)


def _finish(job_id: str, status: str, **values) -> None:
    with session_scope() as db:
        row = db.get(AnalysisJob, job_id)
        if row is None:
            return
        row.status = status
        row.finished_at = utcnow()
        row.pgn_text = ""
        for name, value in values.items():
            setattr(row, name, value)


def mark_done(job_id: str, record_id: int, warnings: list[str]) -> None:
    _finish(job_id, "done", record_id=record_id, warnings_json=json.dumps(warnings))


def mark_error(job_id: str, message: str) -> None:
    _finish(job_id, "error", error_message=message)


def recover() -> list[str]:
    """After a restart: requeue interrupted jobs and return every queued job id, oldest first.

    A job that was running when the process stopped has used an attempt; one
    that has used all of them is failed instead.
    """
    with session_scope() as db:
        for row in db.query(AnalysisJob).filter(AnalysisJob.status == "running"):
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "error"
                row.error_message = "The analysis was interrupted too many times."
                row.finished_at = utcnow()
                row.pgn_text = ""
            else:
                row.status = "queued"
                row.completed = 0
                row.total = 0
        db.flush()
        queued = (
            db.query(AnalysisJob.id)
            .filter(AnalysisJob.status == "queued")
            .order_by(AnalysisJob.created_at)
            .all()
        )
        return [job_id for (job_id,) in queued]


def prune(now: datetime | None = None) -> None:
    """Delete finished jobs older than JOB_RETENTION."""
    cutoff = (now or utcnow()) - JOB_RETENTION
    with session_scope() as db:
        db.query(AnalysisJob).filter(
            AnalysisJob.status.in_(("done", "error")), AnalysisJob.finished_at < cutoff
        ).delete(synchronize_session=False)

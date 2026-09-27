"""A small in-process pool that runs queued analysis jobs.

Job ids go into a queue.Queue; each of N daemon threads takes one, checks
the job is still queued and hands it to the runner. start() first asks
jobs.recover() for work left over from a previous process, so a restart
resumes where it stopped. N comes from CHESSWHISPER_WORKERS (default 2):
every analysis runs its own multi-threaded Stockfish, so this also caps
how hard the machine is pushed.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable

from chesswhisper.web import jobs

logger = logging.getLogger(__name__)

WORKERS_ENV = "CHESSWHISPER_WORKERS"
DEFAULT_WORKERS = 2

_STOP = object()


def worker_count_from_env() -> int:
    try:
        return max(1, int(os.environ.get(WORKERS_ENV, DEFAULT_WORKERS)))
    except ValueError:
        return DEFAULT_WORKERS


class JobWorker:
    def __init__(self, runner: Callable[[str], None], workers: int):
        self._runner = runner
        self._workers = workers
        self._queue: queue.Queue = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._pending:
                return
            self._pending.add(job_id)
        self._queue.put(job_id)

    def start(self) -> None:
        for job_id in jobs.recover():
            self.submit(job_id)
        for number in range(self._workers):
            thread = threading.Thread(target=self._loop, name=f"chesswhisper-worker-{number}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 5) -> None:
        """Let each thread finish its current job, then exit. Queued jobs stay queued in the database."""
        for _ in self._threads:
            self._queue.put(_STOP)
        for thread in self._threads:
            thread.join(timeout)
        self._threads.clear()

    def _loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is _STOP:
                return
            with self._lock:
                self._pending.discard(job_id)
            self._run(job_id)

    def _run(self, job_id: str) -> None:
        job = jobs.get_job(job_id)
        if job is None or job.status != "queued":
            return
        try:
            self._runner(job_id)
        except Exception:
            logger.exception("Analysis job %s failed", job_id)
            job = jobs.get_job(job_id)
            if job is not None and job.status in jobs.ACTIVE_STATUSES:
                jobs.mark_error(job_id, "The analysis stopped unexpectedly.")

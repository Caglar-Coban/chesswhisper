import threading

import pytest

from chesswhisper.web import jobs, worker
from chesswhisper.web.db import init_db, session_scope
from chesswhisper.web.models import User
from chesswhisper.web.worker import JobWorker, worker_count_from_env

WAIT = 5


@pytest.fixture
def user_id(tmp_path):
    init_db(tmp_path / "worker.db")
    with session_scope() as db:
        user = User(username="alice", password_hash="h")
        db.add(user)
        db.flush()
        return user.id


class RecordingRunner:
    """Marks each job running, waits for the test's go-ahead, then finishes it."""

    def __init__(self, block=False):
        self.order = []
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.finished = threading.Semaphore(0)
        if not block:
            self.release.set()

    def __call__(self, job_id):
        jobs.mark_running(job_id)
        self.order.append(job_id)
        self.started.release()
        self.release.wait(WAIT)
        jobs.mark_done(job_id, record_id=1, warnings=[])
        self.finished.release()


def _wait_finished(runner, count):
    for _ in range(count):
        assert runner.finished.acquire(timeout=WAIT)


def test_jobs_run_in_submission_order_with_one_worker(user_id):
    runner = RecordingRunner()
    pool = JobWorker(runner, workers=1)
    pool.start()
    ids = [jobs.create_job(user_id).id for _ in range(3)]
    for job_id in ids:
        pool.submit(job_id)

    _wait_finished(runner, 3)
    pool.stop()

    assert runner.order == ids


def test_worker_count_limits_how_many_run_at_once(user_id):
    runner = RecordingRunner(block=True)
    pool = JobWorker(runner, workers=2)
    pool.start()
    ids = [jobs.create_job(user_id).id for _ in range(3)]
    for job_id in ids:
        pool.submit(job_id)

    assert runner.started.acquire(timeout=WAIT)
    assert runner.started.acquire(timeout=WAIT)
    assert not runner.started.acquire(timeout=0.3)
    assert jobs.get_job(ids[2]).status == "queued"

    runner.release.set()
    _wait_finished(runner, 3)
    pool.stop()


def test_start_runs_jobs_left_over_from_a_previous_process(user_id):
    interrupted = jobs.create_job(user_id)
    jobs.mark_running(interrupted.id)
    waiting = jobs.create_job(user_id)

    runner = RecordingRunner()
    pool = JobWorker(runner, workers=1)
    pool.start()
    _wait_finished(runner, 2)
    pool.stop()

    assert runner.order == [interrupted.id, waiting.id]
    assert jobs.get_job(interrupted.id).status == "done"


def test_jobs_submitted_before_start_wait_for_it(user_id):
    runner = RecordingRunner()
    pool = JobWorker(runner, workers=1)
    job = jobs.create_job(user_id)
    pool.submit(job.id)

    assert not runner.finished.acquire(timeout=0.2)

    pool.start()
    _wait_finished(runner, 1)
    pool.stop()
    assert runner.order == [job.id]  # submitted and recovered, but run once


def test_a_failing_runner_does_not_stop_the_worker(user_id):
    calls = []
    done = threading.Event()

    def runner(job_id):
        calls.append(job_id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        jobs.mark_running(job_id)
        done.set()

    pool = JobWorker(runner, workers=1)
    pool.start()
    first, second = jobs.create_job(user_id), jobs.create_job(user_id)
    pool.submit(first.id)
    pool.submit(second.id)

    assert done.wait(WAIT)
    pool.stop()
    assert calls == [first.id, second.id]


def test_jobs_that_are_gone_or_already_finished_are_skipped(user_id):
    runner = RecordingRunner()
    pool = JobWorker(runner, workers=1)
    pool.start()
    finished = jobs.create_job(user_id)
    jobs.mark_error(finished.id, "boom")
    pool.submit("does-not-exist")
    pool.submit(finished.id)
    live = jobs.create_job(user_id)
    pool.submit(live.id)

    _wait_finished(runner, 1)
    pool.stop()
    assert runner.order == [live.id]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("3", 3), ("1", 1), ("0", 1), ("-2", 1), ("lots", worker.DEFAULT_WORKERS), (None, worker.DEFAULT_WORKERS)],
)
def test_worker_count_from_env(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(worker.WORKERS_ENV, raising=False)
    else:
        monkeypatch.setenv(worker.WORKERS_ENV, value)

    assert worker_count_from_env() == expected

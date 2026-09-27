import threading
from datetime import timedelta

import pytest

from chesswhisper.web import jobs
from chesswhisper.web.db import init_db, session_scope, utcnow
from chesswhisper.web.models import AnalysisJob, User

INPUTS = jobs.JobInputs(
    pgn_text="1. e4 e5 *", player="Alice", lang="en", llm="none", ui_language="en", fetch_warnings=["w"]
)


@pytest.fixture
def user_id(tmp_path):
    init_db(tmp_path / "jobs.db")
    with session_scope() as db:
        user = User(username="alice", password_hash="h")
        db.add(user)
        db.flush()
        return user.id


def _job_row(job_id):
    with session_scope() as db:
        row = db.get(AnalysisJob, job_id)
        db.expunge(row)
        return row


def test_create_job_starts_queued_with_zero_progress(user_id):
    job = jobs.create_job(user_id)

    assert job.status == "queued"
    assert job.completed == 0
    assert job.total == 0
    assert job.user_id == user_id
    assert job.record_id is None
    assert job.warnings == []
    assert job.error_message is None
    assert jobs.get_job(job.id) == job


def test_get_job_returns_none_for_unknown_id(user_id):
    assert jobs.get_job("does-not-exist") is None


def test_two_created_jobs_have_different_ids(user_id):
    assert jobs.create_job(user_id).id != jobs.create_job(user_id).id


def test_job_inputs_round_trip(user_id):
    job = jobs.create_job(user_id, INPUTS)

    assert jobs.job_inputs(job.id) == INPUTS
    assert jobs.job_inputs("does-not-exist") is None


def test_update_progress_sets_completed_and_total(user_id):
    job = jobs.create_job(user_id)

    jobs.update_progress(job.id, 3, 10)

    updated = jobs.get_job(job.id)
    assert (updated.completed, updated.total) == (3, 10)


def test_updates_to_unknown_jobs_are_ignored(user_id):
    jobs.update_progress("does-not-exist", 1, 2)
    jobs.mark_done("does-not-exist", record_id=1, warnings=[])
    jobs.mark_error("does-not-exist", "boom")
    assert jobs.mark_running("does-not-exist") is False


def test_update_progress_survives_a_database_error(user_id, monkeypatch):
    job = jobs.create_job(user_id)

    def broken_scope():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(jobs, "session_scope", broken_scope)
    jobs.update_progress(job.id, 1, 2)  # logged, not raised


def test_mark_running_counts_attempts(user_id):
    job = jobs.create_job(user_id)

    assert jobs.mark_running(job.id) is True

    row = _job_row(job.id)
    assert row.status == "running"
    assert row.attempts == 1
    assert row.started_at is not None


def test_mark_running_refuses_a_finished_job(user_id):
    job = jobs.create_job(user_id)
    jobs.mark_error(job.id, "boom")

    assert jobs.mark_running(job.id) is False
    assert jobs.get_job(job.id).status == "error"


def test_mark_done_records_result_and_clears_the_pgn(user_id):
    job = jobs.create_job(user_id, INPUTS)

    jobs.mark_done(job.id, record_id=42, warnings=["only 1 game(s) found."])

    updated = jobs.get_job(job.id)
    assert updated.status == "done"
    assert updated.record_id == 42
    assert updated.warnings == ["only 1 game(s) found."]
    row = _job_row(job.id)
    assert row.pgn_text == ""
    assert row.finished_at is not None


def test_mark_error_records_message_and_clears_the_pgn(user_id):
    job = jobs.create_job(user_id, INPUTS)

    jobs.mark_error(job.id, "Engine setup error: boom")

    updated = jobs.get_job(job.id)
    assert updated.status == "error"
    assert updated.error_message == "Engine setup error: boom"
    assert _job_row(job.id).pgn_text == ""


def test_running_job_for_covers_queued_and_running(user_id):
    assert jobs.running_job_for(user_id) is None

    job = jobs.create_job(user_id)
    assert jobs.running_job_for(user_id).id == job.id

    jobs.mark_running(job.id)
    assert jobs.running_job_for(user_id).id == job.id

    jobs.mark_done(job.id, record_id=1, warnings=[])
    assert jobs.running_job_for(user_id) is None


def test_create_job_unless_running_returns_the_active_job(user_id):
    first, created = jobs.create_job_unless_running(user_id, INPUTS)
    second, created_again = jobs.create_job_unless_running(user_id, INPUTS)

    assert created is True
    assert created_again is False
    assert second.id == first.id


def test_concurrent_submissions_create_one_job(user_id):
    results = []
    barrier = threading.Barrier(4)

    def submit():
        barrier.wait()
        results.append(jobs.create_job_unless_running(user_id, INPUTS))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(created for _, created in results) == 1
    assert len({job.id for job, _ in results}) == 1


def test_queue_position_counts_older_queued_jobs(user_id):
    first = jobs.create_job(user_id)
    second = jobs.create_job(user_id)
    third = jobs.create_job(user_id)

    assert [jobs.queue_position(j.id) for j in (first, second, third)] == [0, 1, 2]

    jobs.mark_running(first.id)
    assert jobs.queue_position(third.id) == 1


def test_recover_requeues_interrupted_jobs_oldest_first(user_id):
    interrupted = jobs.create_job(user_id)
    jobs.mark_running(interrupted.id)
    jobs.update_progress(interrupted.id, 2, 5)
    waiting = jobs.create_job(user_id)
    finished = jobs.create_job(user_id)
    jobs.mark_done(finished.id, record_id=1, warnings=[])

    assert jobs.recover() == [interrupted.id, waiting.id]

    restored = jobs.get_job(interrupted.id)
    assert restored.status == "queued"
    assert (restored.completed, restored.total) == (0, 0)


def test_recover_fails_a_job_that_used_its_last_attempt(user_id):
    job = jobs.create_job(user_id)
    jobs.mark_running(job.id)
    with session_scope() as db:
        db.get(AnalysisJob, job.id).attempts = jobs.MAX_ATTEMPTS

    assert jobs.recover() == []

    failed = jobs.get_job(job.id)
    assert failed.status == "error"
    assert failed.error_message


def test_prune_deletes_only_old_finished_jobs(user_id):
    old_done = jobs.create_job(user_id)
    jobs.mark_done(old_done.id, record_id=1, warnings=[])
    recent_done = jobs.create_job(user_id)
    jobs.mark_done(recent_done.id, record_id=1, warnings=[])
    old_queued = jobs.create_job(user_id)
    long_ago = utcnow() - jobs.JOB_RETENTION - timedelta(minutes=1)
    with session_scope() as db:
        db.get(AnalysisJob, old_done.id).finished_at = long_ago
        db.get(AnalysisJob, old_queued.id).created_at = long_ago

    jobs.prune()

    assert jobs.get_job(old_done.id) is None
    assert jobs.get_job(recent_done.id) is not None
    assert jobs.get_job(old_queued.id) is not None


def test_deleting_a_user_deletes_their_jobs(user_id):
    job = jobs.create_job(user_id)

    with session_scope() as db:
        db.delete(db.get(User, user_id))

    assert jobs.get_job(job.id) is None

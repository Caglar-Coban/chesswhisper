from chesswhisper.web import jobs


def test_create_job_starts_running_with_zero_progress():
    job = jobs.create_job(user_id=1)

    assert job.status == "running"
    assert job.completed == 0
    assert job.total == 0
    assert job.user_id == 1
    assert job.record_id is None
    assert job.warnings == []
    assert job.error_message is None
    assert jobs.get_job(job.id) is job


def test_get_job_returns_none_for_unknown_id():
    assert jobs.get_job("does-not-exist") is None


def test_two_created_jobs_have_different_ids():
    first = jobs.create_job(user_id=1)
    second = jobs.create_job(user_id=1)

    assert first.id != second.id


def test_update_progress_sets_completed_and_total():
    job = jobs.create_job(user_id=1)

    jobs.update_progress(job.id, 3, 10)

    updated = jobs.get_job(job.id)
    assert updated.completed == 3
    assert updated.total == 10
    assert updated.status == "running"


def test_update_progress_on_unknown_id_does_not_raise():
    jobs.update_progress("does-not-exist", 1, 2)


def test_mark_done_sets_status_record_id_and_warnings():
    job = jobs.create_job(user_id=1)

    jobs.mark_done(job.id, record_id=42, warnings=["only 1 game(s) found."])

    updated = jobs.get_job(job.id)
    assert updated.status == "done"
    assert updated.record_id == 42
    assert updated.warnings == ["only 1 game(s) found."]


def test_mark_done_on_unknown_id_does_not_raise():
    jobs.mark_done("does-not-exist", record_id=1, warnings=[])


def test_mark_error_sets_status_and_message():
    job = jobs.create_job(user_id=1)

    jobs.mark_error(job.id, "Motor kurulum hatası: boom")

    updated = jobs.get_job(job.id)
    assert updated.status == "error"
    assert updated.error_message == "Motor kurulum hatası: boom"


def test_mark_error_on_unknown_id_does_not_raise():
    jobs.mark_error("does-not-exist", "boom")


def test_create_job_prunes_entries_older_than_one_hour(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("chesswhisper.web.jobs.time.monotonic", lambda: fake_now[0])

    old_job = jobs.create_job(user_id=1)
    jobs.mark_done(old_job.id, record_id=1, warnings=[])
    fake_now[0] += jobs._JOB_TTL_SECONDS + 1

    new_job = jobs.create_job(user_id=2)

    assert jobs.get_job(old_job.id) is None
    assert jobs.get_job(new_job.id) is new_job


def test_create_job_never_prunes_a_still_running_job_no_matter_how_old(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("chesswhisper.web.jobs.time.monotonic", lambda: fake_now[0])

    old_job = jobs.create_job(user_id=1)
    fake_now[0] += jobs._JOB_TTL_SECONDS + 1

    new_job = jobs.create_job(user_id=2)

    assert jobs.get_job(old_job.id) is old_job
    assert jobs.get_job(new_job.id) is new_job


def test_create_job_does_not_prune_entries_within_the_ttl(monkeypatch):
    fake_now = [2000.0]
    monkeypatch.setattr("chesswhisper.web.jobs.time.monotonic", lambda: fake_now[0])

    job = jobs.create_job(user_id=1)
    fake_now[0] += jobs._JOB_TTL_SECONDS - 1

    jobs.create_job(user_id=2)

    assert jobs.get_job(job.id) is job


def test_create_job_unless_running_starts_one_job_per_user():
    first, created = jobs.create_job_unless_running(41)
    again, created_again = jobs.create_job_unless_running(41)
    other, created_other = jobs.create_job_unless_running(42)

    assert created and not created_again and created_other
    assert again.id == first.id
    assert other.id != first.id

    jobs.mark_done(first.id, record_id=1, warnings=[])
    after, created_after = jobs.create_job_unless_running(41)
    assert created_after and after.id != first.id

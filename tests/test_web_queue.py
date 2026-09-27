"""The web app and API on top of the persistent job queue."""

import re
import textwrap

import chess.engine
import pytest
from conftest import CsrfClient
from fastapi.testclient import TestClient

from chesswhisper.web import app as app_module
from chesswhisper.web import jobs
from chesswhisper.web.app import app
from chesswhisper.web.db import init_db, session_scope
from chesswhisper.web.models import AnalysisRecord, User, UserApiKey
from chesswhisper.web.security import encrypt_api_key
from chesswhisper.web.worker import JobWorker

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


class FakeEngine:
    def analyse(self, board, limit, **kwargs):
        return {"score": chess.engine.PovScore(chess.engine.Cp(15), chess.WHITE)}

    def quit(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    init_db(tmp_path / "queue_test.db")
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    with session_scope() as db:
        db.add(User(username="bob", password_hash="unused"))
    return c


def _user_id(name="alice"):
    with session_scope() as db:
        return db.query(User).filter_by(username=name).one().id


def _hold_jobs(monkeypatch):
    """Queue jobs without running them, as if every worker were busy."""
    monkeypatch.setattr(app_module, "_enqueue", lambda job_id: None)


def _submit(client, **data):
    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none", **data},
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[-1]


def test_a_waiting_job_reports_its_place_in_line(client, monkeypatch):
    _hold_jobs(monkeypatch)
    jobs.create_job(_user_id("bob"))  # someone else's job is ahead
    job_id = _submit(client)

    body = client.get(f"/analyze/status/{job_id}").json()

    assert body["status"] == "queued"
    assert body["position"] == 1


def test_progress_page_knows_the_queued_state(client, monkeypatch):
    _hold_jobs(monkeypatch)
    job_id = _submit(client)

    page = client.get(f"/analyze/progress/{job_id}").text

    assert "Waiting in line" in page


def test_queued_job_blocks_a_second_submission(client, monkeypatch):
    _hold_jobs(monkeypatch)
    first = _submit(client)

    assert _submit(client) == first


def test_a_job_left_by_a_previous_process_finishes_after_restart(client, monkeypatch):
    _hold_jobs(monkeypatch)
    job_id = _submit(client)
    jobs.mark_running(job_id)  # the old process died mid-analysis

    pool = JobWorker(app_module._run_analysis_job, workers=1)
    pool.start()
    pool.stop()  # waits for the recovered job to finish

    job = jobs.get_job(job_id)
    assert job.status == "done"
    with session_scope() as db:
        assert db.get(AnalysisRecord, job.record_id) is not None


def test_llm_key_is_read_when_the_job_runs(client, monkeypatch):
    user_id = _user_id()
    with session_scope() as db:
        db.add(UserApiKey(user_id=user_id, anthropic_key_encrypted=encrypt_api_key("sk-ant-first")))
    _hold_jobs(monkeypatch)
    job_id = _submit(client, llm="claude")
    with session_scope() as db:
        db.get(UserApiKey, user_id).anthropic_key_encrypted = encrypt_api_key("sk-ant-second")

    seen = {}

    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, on_progress=None, **kwargs):
        seen["key"] = llm_api_key
        raise ValueError("stop here")

    monkeypatch.setattr(app_module, "run_analysis", fake_run_analysis)
    app_module._run_analysis_job(job_id)

    assert seen["key"] == "sk-ant-second"


def test_job_fails_cleanly_if_the_llm_key_was_removed(client, monkeypatch):
    user_id = _user_id()
    with session_scope() as db:
        db.add(UserApiKey(user_id=user_id, anthropic_key_encrypted=encrypt_api_key("sk-ant-first")))
    _hold_jobs(monkeypatch)
    job_id = _submit(client, llm="claude")
    with session_scope() as db:
        db.delete(db.get(UserApiKey, user_id))

    app_module._run_analysis_job(job_id)

    job = jobs.get_job(job_id)
    assert job.status == "error"
    assert "API key" in job.error_message


def test_a_job_is_run_only_once(client, monkeypatch):
    _hold_jobs(monkeypatch)
    job_id = _submit(client)
    app_module._run_analysis_job(job_id)
    calls = []
    monkeypatch.setattr(app_module, "run_analysis", lambda *a, **k: calls.append(1))

    app_module._run_analysis_job(job_id)

    assert calls == []


def test_api_reports_a_queued_job(client, monkeypatch):
    _hold_jobs(monkeypatch)
    token = re.search(r"\b(cw_[A-Za-z0-9_-]{20,})\b", client.post("/settings/api-token").text).group(1)
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {token}"

    created = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": SAMPLE_PGN})
    status = api.get(created.json()["status_url"]).json()

    assert status["status"] == "queued"
    assert status["position"] == 0


def test_app_start_up_runs_jobs_waiting_in_the_database(client, monkeypatch):
    _hold_jobs(monkeypatch)
    job_id = _submit(client)

    with TestClient(app):  # runs the lifespan: the worker starts, then stops after the job
        pass

    assert jobs.get_job(job_id).status == "done"

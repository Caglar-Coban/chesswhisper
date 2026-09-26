import json
import textwrap

import chess.engine
import pytest
from conftest import CsrfClient

from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


class FakeEngine:
    def __init__(self):
        self._scores = [0, 20, 10, 15]
        self._i = 0

    def analyse(self, board, limit, **kwargs):
        cp = self._scores[self._i % len(self._scores)]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def _fake_aggregate_report(warnings=None):
    from chesswhisper.stats_engine import PHASES, AggregateReport, ErrorCounts, TimeManagementStats
    report = AggregateReport(
        player="Alice", games=[], overall_acpl=0.0, overall_accuracy=100.0,
        error_totals={p: ErrorCounts() for p in PHASES}, openings=[],
        time_management=TimeManagementStats(available=False),
    )
    return report, "", (warnings or [])


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "progress_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _job_id_from_redirect(response):
    return response.headers["location"].rsplit("/", 1)[-1]


def _real_start_background(target):
    import threading
    threading.Thread(target=target, daemon=True).start()


def _fail_if_job_created(*args, **kwargs):
    raise AssertionError("a job should not have been created")


def test_analyze_redirects_to_progress_page(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/analyze/progress/")


def test_analyze_job_completes_and_status_redirects_to_history(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["completed"] == body["total"] == 1

    db = next(get_db())
    records = db.query(AnalysisRecord).all()
    assert len(records) == 1
    assert body["redirect"] == f"/history/{records[0].id}?job={job_id}"


def test_analyze_runs_on_a_real_background_thread(client, monkeypatch):
    import time as time_module

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    # Undo the autouse inline-execution patch for this one test -- we want
    # to prove the real threading.Thread path works, not just the seam.
    monkeypatch.setattr("chesswhisper.web.app._start_background", _real_start_background)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    deadline = time_module.monotonic() + 5.0
    status_body = None
    while time_module.monotonic() < deadline:
        status_body = client.get(f"/analyze/status/{job_id}").json()
        if status_body["status"] != "running":
            break
        time_module.sleep(0.05)

    assert status_body is not None
    assert status_body["status"] == "done", f"job did not complete in time: {status_body}"

    db = next(get_db())
    records = db.query(AnalysisRecord).all()
    assert len(records) == 1


def test_history_detail_shows_warnings_only_via_the_job_query_param(client, monkeypatch):
    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, on_progress=None, **kwargs):
        if on_progress is not None:
            on_progress(1, 1)
        return _fake_aggregate_report(warnings=["only 1 game(s) found (requested 7)."])

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)
    status = client.get(f"/analyze/status/{job_id}")
    redirect = status.json()["redirect"]
    record_id = redirect.split("/history/")[1].split("?")[0]

    with_job = client.get(redirect)
    without_job = client.get(f"/history/{record_id}")

    assert with_job.status_code == 200
    assert "only 1 game(s) found" in with_job.text
    assert without_job.status_code == 200
    assert "only 1 game(s) found" not in without_job.text


def test_analyze_engine_failure_marks_the_job_as_an_error(client, monkeypatch):
    def fail_provisioning(**kwargs):
        raise StockfishProvisionError("no engine available")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_provisioning)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "error"
    assert "Motor kurulum hatası" in body["message"]


def test_analyze_unknown_player_marks_the_job_as_an_error(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Nobody", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    body = status.json()
    assert body["status"] == "error"
    assert "Nobody" in body["message"]


def test_analyze_unexpected_exception_marks_the_job_as_an_error_not_stuck_running(client, monkeypatch):
    def fake_run_analysis(*args, **kwargs):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    body = status.json()
    assert body["status"] == "error"
    assert body["message"] == "Beklenmeyen bir hata oluştu."


def test_on_progress_updates_the_job_before_it_completes(client, monkeypatch):
    from chesswhisper.web import jobs

    created_jobs = []
    original_create_job = jobs.create_job

    def spy_create_job(user_id):
        job = original_create_job(user_id)
        created_jobs.append(job)
        return job

    monkeypatch.setattr("chesswhisper.web.app.jobs.create_job", spy_create_job)

    captured = {}

    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, on_progress=None, **kwargs):
        if on_progress is not None:
            on_progress(1, 2)
        job = created_jobs[0]
        # Snapshot state right after the callback but before this function
        # returns -- a real concurrent status poll mid-run would see exactly
        # this: progress recorded, but not yet marked done.
        captured["mid_run"] = (job.completed, job.total, job.status)
        return _fake_aggregate_report()

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert captured["mid_run"] == (1, 2, "running")


def test_analyze_missing_api_key_does_not_create_a_job(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.jobs.create_job", _fail_if_job_created)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")


def test_analyze_progress_page_requires_login():
    anon = CsrfClient(app, follow_redirects=False)

    response = anon.get("/analyze/progress/some-job-id")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_analyze_progress_page_unknown_job_returns_404(client):
    response = client.get("/analyze/progress/does-not-exist")

    assert response.status_code == 404


def test_analyze_status_unknown_job_returns_404(client):
    response = client.get("/analyze/status/does-not-exist")

    assert response.status_code == 404


def test_analyze_progress_and_status_forbidden_for_another_users_job(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    other = CsrfClient(app, follow_redirects=False)
    other.post("/register", data={"username": "mallory", "password": "correct-horse"})

    progress = other.get(f"/analyze/progress/{job_id}")
    status = other.get(f"/analyze/status/{job_id}")

    assert progress.status_code == 403
    assert status.status_code == 403


def test_analyze_progress_page_renders_for_the_owning_user(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    progress = client.get(f"/analyze/progress/{job_id}")

    assert progress.status_code == 200
    assert job_id in progress.text


def test_completed_job_saves_critical_moves_with_the_record(client, monkeypatch):
    moves = [{"san": "f6", "cp_loss": 390}]
    monkeypatch.setattr("chesswhisper.web.app.run_analysis", lambda *a, **k: _fake_aggregate_report())
    monkeypatch.setattr("chesswhisper.web.app.critical_moves", lambda games: moves)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    assert response.status_code == 303

    record = next(get_db()).query(AnalysisRecord).one()
    assert json.loads(record.critical_moves_json) == moves


def test_completed_job_without_critical_moves_saves_null(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.run_analysis", lambda *a, **k: _fake_aggregate_report())

    client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert next(get_db()).query(AnalysisRecord).one().critical_moves_json is None


def test_completed_job_saves_game_timelines_with_the_record(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    record = next(get_db()).query(AnalysisRecord).one()
    [timeline] = json.loads(record.games_json)
    assert timeline["white"] == "Alice"
    assert [m["san"] for m in timeline["moves"]][:2] == ["e4", "e5"]

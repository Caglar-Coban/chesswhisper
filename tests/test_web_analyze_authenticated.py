import textwrap

import chess.engine
import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, UserApiKey
from chesswhisper.web.security import encrypt_api_key

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


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "analyze_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_index_redirects_when_logged_out(tmp_path):
    init_db(tmp_path / "logged_out_test.db")
    client = CsrfClient(app, follow_redirects=False)

    response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_index_shows_form_when_logged_in(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'name="pgn"' in response.text


def test_analyze_saves_history_record_for_current_user(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert "chesswhisper Analysis Report" in response.text

    db = next(get_db())
    records = db.query(AnalysisRecord).all()
    assert len(records) == 1
    assert records[0].player_name == "Alice"
    assert records[0].games_count == 1


def test_analyze_without_configured_key_redirects_to_settings(client, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_analysis should not be called without a configured key")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fail_if_called)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")


def test_analyze_uses_current_users_decrypted_key(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    db = next(get_db())
    from chesswhisper.web.models import User
    user = db.query(User).filter_by(username="alice").one()
    db.add(UserApiKey(user_id=user.id, anthropic_key_encrypted=encrypt_api_key("sk-alices-key")))
    db.commit()

    captured = {}

    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, **kwargs):
        captured["llm_api_key"] = llm_api_key
        from chesswhisper.stats_engine import PHASES, AggregateReport, ErrorCounts, TimeManagementStats
        return (
            AggregateReport(
                player="Alice", games=[], overall_acpl=0.0, overall_accuracy=100.0,
                error_totals={p: ErrorCounts() for p in PHASES}, openings=[],
                time_management=TimeManagementStats(available=False),
            ),
            "",
            [],
        )

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 200
    assert captured["llm_api_key"] == "sk-alices-key"


def test_analyze_unknown_player_returns_400(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Nobody", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 400
    assert "Hata" in response.text

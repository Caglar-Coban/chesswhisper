import textwrap

import chess.engine
import pytest
from conftest import CsrfClient

from chesswhisper.game_sources import FetchResult, GameFetchError, GameSourceUnavailable
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


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "fetch_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def fail_if_called(*args, **kwargs):
    raise AssertionError("fetch_pgn should not have been called")


def test_index_offers_game_source_choices(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'name="source"' in response.text
    assert 'value="lichess"' in response.text
    assert 'value="chesscom"' in response.text
    assert 'name="username"' in response.text
    assert 'name="max_games"' in response.text
    assert 'name="pgn"' in response.text


def test_analyze_fetches_lichess_games_and_saves_history(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice", warnings=["only 1 game(s) found (requested 7)."])

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post(
        "/analyze",
        data={"source": "lichess", "username": " alice ", "max_games": "7", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert calls["args"] == ("lichess", "alice", 7)
    assert "chesswhisper Analysis Report" in response.text
    assert "only 1 game(s) found" in response.text
    records = next(get_db()).query(AnalysisRecord).all()
    assert len(records) == 1
    assert records[0].player_name == "Alice"


def test_analyze_fetch_defaults_player_to_the_fetched_spelling(client, monkeypatch):
    monkeypatch.setattr(
        "chesswhisper.web.app.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Alice"),
    )
    captured = {}

    def fake_run_analysis(pgn_path, player, *args, **kwargs):
        captured["player"] = player
        raise ValueError("stop here")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    client.post("/analyze", data={"source": "chesscom", "username": "alice", "max_games": "5", "llm": "none"})

    assert captured["player"] == "Alice"


def test_analyze_fetch_form_player_overrides_the_fetched_spelling(client, monkeypatch):
    monkeypatch.setattr(
        "chesswhisper.web.app.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Alice"),
    )
    captured = {}

    def fake_run_analysis(pgn_path, player, *args, **kwargs):
        captured["player"] = player
        raise ValueError("stop here")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    client.post(
        "/analyze",
        data={"source": "lichess", "username": "alice", "max_games": "5", "player": "Bob", "llm": "none"},
    )

    assert captured["player"] == "Bob"


def test_analyze_fetch_requires_a_username(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post("/analyze", data={"source": "lichess", "username": "  ", "max_games": "5", "llm": "none"})

    assert response.status_code == 400
    assert "kullanıcı adı" in response.text


def test_analyze_fetch_rejects_a_non_numeric_game_count(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze", data={"source": "lichess", "username": "alice", "max_games": "lots", "llm": "none"}
    )

    assert response.status_code == 400
    assert "tam sayı" in response.text


def test_analyze_rejects_an_unknown_source(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post("/analyze", data={"source": "twitch", "username": "alice", "llm": "none"})

    assert response.status_code == 400
    assert "Geçersiz oyun kaynağı" in response.text


def test_analyze_fetch_error_returns_400_with_the_message(client, monkeypatch):
    def fake_fetch(platform, username, max_games):
        raise GameFetchError("Lichess user 'alice' not found.")

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post("/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "none"})

    assert response.status_code == 400
    assert "not found" in response.text


def test_analyze_source_unavailable_returns_502(client, monkeypatch):
    def fake_fetch(platform, username, max_games):
        raise GameSourceUnavailable("Lichess is rate limiting requests; wait a minute and try again.")

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post("/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "none"})

    assert response.status_code == 502
    assert "rate limiting" in response.text


def test_analyze_fetch_with_missing_api_key_redirects_before_fetching(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "claude"}
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")


def test_analyze_file_upload_still_works_when_source_is_file(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"source": "file", "player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert "chesswhisper Analysis Report" in response.text

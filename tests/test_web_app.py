import textwrap

import chess.engine
import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db

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
    init_db(tmp_path / "web_app_test.db")
    return CsrfClient(app, raise_server_exceptions=False)


def test_analyze_without_file_returns_400(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})

    response = client.post("/analyze", data={"player": "", "lang": "tr", "llm": "none"})

    assert response.status_code == 400
    assert "Hata" in response.text


def test_analyze_sanitizes_html_injected_via_pgn_headers(client, monkeypatch):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    hostile_pgn = textwrap.dedent("""
        [Event "Test"]
        [White "<script>alert(1)</script>"]
        [Black "Bob"]
        [Result "1-0"]

        1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
    """).strip() + "\n"

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", hostile_pgn, "application/octet-stream")},
        data={"player": "<script>alert(1)</script>", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert "<script>" not in response.text


def test_analyze_engine_provisioning_failure_returns_500(client, monkeypatch):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    from chesswhisper.engine_provision import StockfishProvisionError

    def fail_provisioning(**kwargs):
        raise StockfishProvisionError("no engine available")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_provisioning)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 500
    assert "Hata" in response.text

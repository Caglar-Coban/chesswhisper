"""Filtered analyses in the web UI and the API."""

import re
import textwrap

import chess.engine
import pytest
from conftest import CsrfClient
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db, session_scope
from chesswhisper.web.models import AnalysisRecord, User

PGN = textwrap.dedent("""
    [Event "Blitz"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]
    [TimeControl "180+2"]

    1. e4 e5 2. Nf3 Nc6 1-0

    [Event "Bullet"]
    [White "Bob"]
    [Black "Alice"]
    [Result "0-1"]
    [TimeControl "60+0"]

    1. d4 d5 2. c4 e6 0-1
""").strip() + "\n"


class FakeEngine:
    def analyse(self, board, limit, **kwargs):
        return {"score": chess.engine.PovScore(chess.engine.Cp(15), chess.WHITE)}

    def quit(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    init_db(tmp_path / "filter_test.db")
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _records():
    with session_scope() as db:
        rows = db.query(AnalysisRecord).order_by(AnalysisRecord.id).all()
        for row in rows:
            db.expunge(row)
        return rows


def _analyze(client, **data):
    return client.post(
        "/analyze",
        files={"pgn": ("games.pgn", PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none", **data},
    )


def test_the_analyze_form_offers_filters(client):
    page = client.get("/").text

    assert 'name="time_control" value="blitz"' in page
    assert 'name="color"' in page


def test_an_unfiltered_analysis_keeps_its_games(client):
    assert _analyze(client).status_code == 303

    [record] = _records()
    assert record.games_count == 2
    assert record.pgn_text == PGN
    assert record.filters_json is None


def test_a_filtered_analysis_from_the_form(client):
    response = _analyze(client, time_control=["blitz"], color="white")

    assert response.status_code == 303
    [record] = _records()
    assert record.games_count == 1
    assert record.filters_json == '{"time_classes": ["blitz"], "color": "white"}'
    assert "**Filter:** Blitz · as White" in record.report_markdown


def test_a_bad_filter_is_refused(client):
    response = _analyze(client, color="green")

    assert response.status_code == 400
    assert "filter" in response.text
    assert _records() == []


def test_a_saved_analysis_can_be_filtered_again(client):
    _analyze(client)
    [original] = _records()
    page = client.get(f"/history/{original.id}").text
    assert f'action="/history/{original.id}/filter"' in page

    response = client.post(f"/history/{original.id}/filter", data={"color": "black"})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/analyze/progress/")
    original_again, filtered = _records()
    assert original_again.games_count == 2
    assert (filtered.games_count, filtered.filters_json) == (1, '{"time_classes": [], "color": "black"}')
    filtered_page = client.get(f"/history/{filtered.id}").text
    assert re.search(r'<option value="black"\s+selected>', filtered_page)


def test_filtering_to_nothing_reports_the_error(client):
    _analyze(client)
    [original] = _records()

    client.post(f"/history/{original.id}/filter", data={"time_control": ["classical"]})

    records = _records()
    assert len(records) == 1  # no new report; the job ended with an error


def test_old_records_and_other_users_records_cannot_be_filtered(client):
    with session_scope() as db:
        user = db.query(User).filter_by(username="alice").one()
        old = AnalysisRecord(user_id=user.id, player_name="Alice", lang="en", llm_used="none", games_count=1,
                             overall_acpl=1, overall_accuracy=1, report_markdown="# r")
        db.add(old)
        db.flush()
        old_id = old.id

    assert "/filter" not in client.get(f"/history/{old_id}").text
    assert client.post(f"/history/{old_id}/filter", data={"color": "white"}).status_code == 404
    assert client.post("/history/9999/filter", data={"color": "white"}).status_code == 404


def test_the_api_accepts_filters(client):
    token = re.search(r"\b(cw_[A-Za-z0-9_-]{20,})\b", client.post("/settings/api-token").text).group(1)
    api = TestClient(app)
    api.headers["Authorization"] = f"Bearer {token}"

    created = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": PGN, "player": "Alice", "time_controls": ["bullet"]})

    assert created.status_code == 202
    [record] = _records()
    assert (record.games_count, record.filters_json) == (1, '{"time_classes": ["bullet"], "color": null}')
    bad = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": PGN, "color": "green"})
    assert bad.status_code == 422

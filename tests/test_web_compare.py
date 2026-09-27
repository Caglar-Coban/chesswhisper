import json
from datetime import datetime

import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db, session_scope
from chesswhisper.web.models import AnalysisRecord, User


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "compare_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    with session_scope() as db:
        db.add(User(username="bob", password_hash="unused"))
    return c


def _record(username, created_at, accuracy, acpl, summary=None):
    with session_scope() as db:
        user = db.query(User).filter_by(username=username).one()
        record = AnalysisRecord(
            user_id=user.id, player_name="Alice", lang="en", llm_used="none", games_count=10,
            overall_acpl=acpl, overall_accuracy=accuracy, report_markdown="# r",
            summary_json=json.dumps(summary) if summary is not None else None,
        )
        record.created_at = created_at
        db.add(record)
        db.flush()
        return record.id


def _summary(accuracy, blunders, punish):
    return {"games": 10, "accuracy": accuracy, "acpl": 40.0, "blunders_per_game": blunders, "punish_rate": punish}


def test_history_offers_comparison_with_two_or_more_analyses(client):
    first = _record("alice", datetime(2026, 9, 1), 85.0, 45.0)
    assert 'id="compare-form"' not in client.get("/history").text

    _record("alice", datetime(2026, 9, 2), 88.0, 40.0)
    page = client.get("/history").text

    assert 'id="compare-form"' in page
    assert f'name="ids" value="{first}" form="compare-form"' in page


def test_compare_two_analyses_older_first_with_verdicts(client):
    newer = _record("alice", datetime(2026, 9, 20), 90.0, 40.0, _summary(90.0, 0.4, 0.5))
    older = _record("alice", datetime(2026, 9, 1), 86.5, 40.0, _summary(86.5, 0.9, 0.75))

    page = client.get(f"/history/compare?ids={newer}&ids={older}").text

    assert page.index(f'href="/history/{older}"') < page.index(f'href="/history/{newer}"')
    assert '<td class="num change-better">+3.5%' in page  # accuracy
    assert '<td class="num change-better">−0.50' in page  # blunders per game
    assert '<td class="num change-worse">−25%' in page  # opponent errors punished
    assert "only the basic numbers" not in page


def test_older_records_compare_their_basic_numbers(client):
    old = _record("alice", datetime(2026, 9, 1), 86.0, 45.0)
    new = _record("alice", datetime(2026, 9, 2), 88.0, 41.0, _summary(88.0, 0.4, 0.5))

    page = client.get(f"/history/compare?ids={old}&ids={new}").text

    assert "only the basic numbers" in page
    assert "Blunders per game" in page  # the newer one has it; the older shows "-"
    assert '<td class="num">-</td>' in page


@pytest.mark.parametrize("query", ["", "?ids=1", "?ids=1&ids=1", "?ids=1&ids=2&ids=3", "?ids=1&ids=999"])
def test_compare_needs_exactly_two_of_your_analyses(client, query):
    _record("alice", datetime(2026, 9, 1), 86.0, 45.0)
    _record("alice", datetime(2026, 9, 2), 88.0, 41.0)
    _record("alice", datetime(2026, 9, 3), 88.0, 41.0)

    response = client.get(f"/history/compare{query}")

    assert response.status_code == 400


def test_compare_refuses_someone_elses_analysis(client):
    mine = _record("alice", datetime(2026, 9, 1), 86.0, 45.0)
    theirs = _record("bob", datetime(2026, 9, 2), 88.0, 41.0)

    assert client.get(f"/history/compare?ids={mine}&ids={theirs}").status_code == 400


def test_new_analyses_save_a_summary(client, monkeypatch):
    import chess.engine

    class FakeEngine:
        def analyse(self, board, limit, **kwargs):
            return {"score": chess.engine.PovScore(chess.engine.Cp(15), chess.WHITE)}

        def quit(self):
            pass

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    pgn = '[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'

    client.post("/analyze", files={"pgn": ("g.pgn", pgn, "text/plain")}, data={"player": "Alice", "llm": "none"})

    with session_scope() as db:
        record = db.query(AnalysisRecord).one()
        summary = json.loads(record.summary_json)
    assert summary["games"] == 1
    assert summary["score"] == 1.0

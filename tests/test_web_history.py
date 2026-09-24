import json
import re
from datetime import datetime

import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User
from chesswhisper.web.security import hash_password


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "history_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _seed_record(username: str, player_name: str) -> int:
    db = next(get_db())
    user = db.query(User).filter_by(username=username).one()
    record = AnalysisRecord(
        user_id=user.id, player_name=player_name, lang="en", llm_used="none",
        games_count=1, overall_acpl=10.0, overall_accuracy=95.0,
        report_markdown="# Test Report\n\nHello.",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record.id


def test_history_redirects_when_logged_out(tmp_path):
    init_db(tmp_path / "history_logged_out.db")
    client = CsrfClient(app, follow_redirects=False)

    response = client.get("/history")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_history_lists_only_current_users_records(client):
    # Create bob in DB without logging in as bob
    db = next(get_db())
    bob = User(username="bob", password_hash=hash_password("correct-horse"), is_admin=False)
    db.add(bob)
    db.commit()

    _seed_record("alice", "Alice")
    _seed_record("bob", "Bob")

    response = client.get("/history")

    assert response.status_code == 200
    assert "Alice" in response.text
    assert "Bob" not in response.text


def test_history_detail_renders_stored_report(client):
    record_id = _seed_record("alice", "Alice")

    response = client.get(f"/history/{record_id}")

    assert response.status_code == 200
    assert "Test Report" in response.text
    assert "Hello." in response.text


def test_can_delete_own_history_record(client):
    record_id = _seed_record("alice", "Alice")

    response = client.post(f"/history/{record_id}/delete")

    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    assert client.get(f"/history/{record_id}").status_code == 404


def test_cannot_delete_another_users_history_record(client):
    db = next(get_db())
    bob = User(username="bob", password_hash=hash_password("correct-horse"), is_admin=False)
    db.add(bob)
    db.commit()
    bob_record_id = _seed_record("bob", "Bob")

    response = client.post(f"/history/{bob_record_id}/delete")

    assert response.status_code == 404
    db2 = next(get_db())
    assert db2.get(AnalysisRecord, bob_record_id) is not None


def test_history_detail_404s_for_another_users_record(client):
    # Create bob in DB without logging in as bob
    db = next(get_db())
    bob = User(username="bob", password_hash=hash_password("correct-horse"), is_admin=False)
    db.add(bob)
    db.commit()

    bob_record_id = _seed_record("bob", "Bob")

    # Client is still logged in as alice, trying to access bob's record
    response = client.get(f"/history/{bob_record_id}")

    assert response.status_code == 404


def _seed_trend_record(username, player_name, day, accuracy, acpl):
    db = next(get_db())
    user = db.query(User).filter_by(username=username).one()
    db.add(
        AnalysisRecord(
            user_id=user.id, player_name=player_name, lang="tr", llm_used="none",
            games_count=4, overall_acpl=acpl, overall_accuracy=accuracy,
            report_markdown="# r", created_at=datetime(2026, 9, day, 12, 0),
        )
    )
    db.commit()


def _trend_data(response):
    match = re.search(r'<script type="application/json" id="trend-data">(.*?)</script>', response.text, re.S)
    assert match, "trend data block missing"
    return json.loads(match.group(1))


def test_history_trend_shows_most_analyzed_player_chronologically(client):
    _seed_trend_record("alice", "Alice", 3, accuracy=81.0, acpl=40.0)
    _seed_trend_record("alice", "Alice", 1, accuracy=74.0, acpl=55.0)
    _seed_trend_record("alice", "Friend", 2, accuracy=60.0, acpl=90.0)

    response = client.get("/history")

    data = _trend_data(response)
    assert data["player"] == "Alice"
    assert [p["accuracy"] for p in data["points"]] == [74.0, 81.0]
    assert data["accuracy_axis"]["low"] <= 74.0
    assert '<option value="Friend"' in response.text


def test_history_trend_player_query_param_selects_player(client):
    _seed_trend_record("alice", "Alice", 1, accuracy=74.0, acpl=55.0)
    _seed_trend_record("alice", "Alice", 2, accuracy=81.0, acpl=40.0)
    _seed_trend_record("alice", "Alice", 5, accuracy=83.0, acpl=38.0)
    _seed_trend_record("alice", "Friend", 3, accuracy=60.0, acpl=90.0)
    _seed_trend_record("alice", "Friend", 4, accuracy=65.0, acpl=80.0)

    assert _trend_data(client.get("/history?player=Friend"))["player"] == "Friend"
    assert _trend_data(client.get("/history?player=Nobody"))["player"] == "Alice"


def test_history_trend_never_includes_another_users_players(client):
    db = next(get_db())
    db.add(User(username="bob", password_hash=hash_password("correct-horse"), is_admin=False))
    db.commit()
    _seed_trend_record("alice", "Alice", 1, accuracy=74.0, acpl=55.0)
    _seed_trend_record("alice", "Alice", 2, accuracy=81.0, acpl=40.0)
    _seed_trend_record("bob", "BobPlayer", 3, accuracy=99.0, acpl=5.0)

    response = client.get("/history?player=BobPlayer")

    assert _trend_data(response)["player"] == "Alice"
    assert "BobPlayer" not in response.text


def test_history_trend_needs_two_analyses(client):
    _seed_trend_record("alice", "Alice", 1, accuracy=74.0, acpl=55.0)

    response = client.get("/history")

    assert 'id="trend-data"' not in response.text
    assert "en az 2 analiz" in response.text


def test_history_without_records_has_no_trend_card(client):
    response = client.get("/history")

    assert "Eğilim" not in response.text

import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User
from chesswhisper.web.security import hash_password


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "history_test.db")
    c = TestClient(app, follow_redirects=False)
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
    client = TestClient(app, follow_redirects=False)

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

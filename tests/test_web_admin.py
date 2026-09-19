import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User
from chesswhisper.web.security import verify_password


@pytest.fixture
def clients(tmp_path):
    init_db(tmp_path / "admin_test.db")
    admin_client = TestClient(app, follow_redirects=False)
    admin_client.post("/register", data={"username": "root", "password": "correct-horse"})

    db = next(get_db())
    root = db.query(User).filter_by(username="root").one()
    root.is_admin = True
    db.commit()

    plain_client = TestClient(app, follow_redirects=False)
    plain_client.post("/register", data={"username": "alice", "password": "correct-horse"})

    return admin_client, plain_client


def test_admin_route_forbidden_for_non_admin(clients):
    _, plain_client = clients

    response = plain_client.get("/admin")

    assert response.status_code == 403


def test_admin_route_lists_all_users(clients):
    admin_client, _ = clients

    response = admin_client.get("/admin")

    assert response.status_code == 200
    assert "root" in response.text
    assert "alice" in response.text


def test_admin_can_view_another_users_history(clients):
    admin_client, _ = clients
    db = next(get_db())
    alice = db.query(User).filter_by(username="alice").one()
    db.add(AnalysisRecord(
        user_id=alice.id, player_name="Alice", lang="en", llm_used="none",
        games_count=1, overall_acpl=10.0, overall_accuracy=95.0,
        report_markdown="# Alice's report",
    ))
    db.commit()

    response = admin_client.get(f"/admin/users/{alice.id}")

    assert response.status_code == 200
    assert "Alice" in response.text


def test_admin_cannot_delete_self(clients):
    admin_client, _ = clients
    db = next(get_db())
    root = db.query(User).filter_by(username="root").one()

    response = admin_client.post(f"/admin/users/{root.id}/delete")

    assert response.status_code == 400


def test_admin_can_delete_another_user_cascading_records(clients):
    admin_client, _ = clients
    db = next(get_db())
    alice = db.query(User).filter_by(username="alice").one()
    alice_id = alice.id
    db.add(AnalysisRecord(
        user_id=alice_id, player_name="Alice", lang="en", llm_used="none",
        games_count=1, overall_acpl=10.0, overall_accuracy=95.0, report_markdown="x",
    ))
    db.commit()

    response = admin_client.post(f"/admin/users/{alice_id}/delete")

    assert response.status_code == 303
    db2 = next(get_db())
    assert db2.get(User, alice_id) is None
    assert db2.query(AnalysisRecord).filter_by(user_id=alice_id).count() == 0


def test_admin_can_reset_another_users_password(clients):
    admin_client, _ = clients
    db = next(get_db())
    alice = db.query(User).filter_by(username="alice").one()

    response = admin_client.post(f"/admin/users/{alice.id}/reset-password", data={"new_password": "brand-new-pass"})

    assert response.status_code == 303
    db2 = next(get_db())
    alice2 = db2.get(User, alice.id)
    assert verify_password("brand-new-pass", alice2.password_hash)

from datetime import timedelta

import pytest
from conftest import CsrfClient

from chesswhisper.web import jobs
from chesswhisper.web.app import MAX_UPLOAD_BYTES, app
from chesswhisper.web.db import get_db, init_db, utcnow
from chesswhisper.web.models import User, UserApiKey
from chesswhisper.web.security import encrypt_api_key, verify_password


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "hardening_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _user(name="alice"):
    return next(get_db()).query(User).filter_by(username=name).one()


# --- registration ---------------------------------------------------------


@pytest.mark.parametrize("username", ["   ", "ab", "has space", "x" * 31])
def test_register_rejects_invalid_usernames(tmp_path, username):
    init_db(tmp_path / "register_invalid.db")
    c = CsrfClient(app, follow_redirects=False)

    response = c.post("/register", data={"username": username, "password": "correct-horse"})

    assert response.status_code == 200  # re-rendered form, like the other register errors
    assert "Usernames must be 3-30 characters" in response.text
    assert next(get_db()).query(User).count() == 0


def test_register_and_login_ignore_surrounding_whitespace(tmp_path):
    init_db(tmp_path / "register_strip.db")
    c = CsrfClient(app, follow_redirects=False)

    assert c.post("/register", data={"username": "  bob ", "password": "correct-horse"}).status_code == 303
    assert _user("bob") is not None

    other = CsrfClient(app, follow_redirects=False)
    assert other.post("/login", data={"username": " bob", "password": "correct-horse"}).status_code == 303


# --- analyze form validation ----------------------------------------------


def test_analyze_rejects_an_unknown_language(client):
    response = client.post(
        "/analyze", data={"lang": "xx", "llm": "none"}, files={"pgn": ("g.pgn", b"1. e4 *", "text/plain")}
    )

    assert response.status_code == 400


def test_analyze_rejects_an_unknown_llm(client):
    response = client.post(
        "/analyze", data={"lang": "en", "llm": "gemini"}, files={"pgn": ("g.pgn", b"1. e4 *", "text/plain")}
    )

    assert response.status_code == 400


def test_analyze_rejects_an_oversized_upload(client):
    too_big = b"x" * (MAX_UPLOAD_BYTES + 1)

    response = client.post(
        "/analyze", data={"lang": "en", "llm": "none"}, files={"pgn": ("g.pgn", too_big, "text/plain")}
    )

    assert response.status_code == 413


def test_analyze_sends_you_to_your_running_job_instead_of_starting_another(client, monkeypatch):
    running = jobs.create_job(_user().id)
    monkeypatch.setattr("chesswhisper.web.app._enqueue", lambda job_id: pytest.fail("queued a job"))

    response = client.post(
        "/analyze", data={"lang": "en", "llm": "none"}, files={"pgn": ("g.pgn", b"1. e4 *", "text/plain")}
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/analyze/progress/{running.id}"
    jobs.mark_error(running.id, "done with this test")


# --- settings: password and keys ------------------------------------------


def test_change_password_requires_the_current_password(client):
    response = client.post(
        "/settings/password",
        data={"current_password": "wrong-horse", "new_password": "battery-staple", "confirm_password": "battery-staple"},
    )

    assert response.status_code == 200
    assert "current password is wrong" in response.text
    assert verify_password("correct-horse", _user().password_hash)


def test_change_password_requires_matching_confirmation(client):
    response = client.post(
        "/settings/password",
        data={"current_password": "correct-horse", "new_password": "battery-staple", "confirm_password": "battery-stapl"},
    )

    assert response.status_code == 200
    assert "don&#39;t match" in response.text
    assert verify_password("correct-horse", _user().password_hash)


def test_change_password_requires_a_long_enough_password(client):
    response = client.post(
        "/settings/password",
        data={"current_password": "correct-horse", "new_password": "short", "confirm_password": "short"},
    )

    assert response.status_code == 200
    assert "at least 8" in response.text
    assert verify_password("correct-horse", _user().password_hash)


def test_change_password_updates_the_hash(client):
    response = client.post(
        "/settings/password",
        data={"current_password": "correct-horse", "new_password": "battery-staple", "confirm_password": "battery-staple"},
    )

    assert response.status_code == 200
    assert "Password updated" in response.text
    assert verify_password("battery-staple", _user().password_hash)


def test_delete_a_saved_api_key(client):
    client.post("/settings", data={"anthropic_key": "sk-ant-secret", "openai_key": "sk-openai-secret"})

    response = client.post("/settings/delete-key", data={"provider": "anthropic"})

    assert response.status_code == 200
    row = next(get_db()).get(UserApiKey, _user().id)
    assert row.anthropic_key_encrypted is None
    assert row.openai_key_encrypted is not None


def test_delete_key_rejects_an_unknown_provider(client):
    response = client.post("/settings/delete-key", data={"provider": "gemini"})

    assert response.status_code == 400


def test_settings_page_offers_to_remove_only_saved_keys(client):
    db = next(get_db())
    db.add(UserApiKey(user_id=_user().id, openai_key_encrypted=encrypt_api_key("sk-openai-secret")))
    db.commit()

    page = client.get("/settings").text

    assert 'value="openai"' in page
    assert 'value="anthropic"' not in page


# --- admin ----------------------------------------------------------------


def test_admin_password_reset_also_unlocks_the_account(tmp_path):
    init_db(tmp_path / "admin_unlock.db")
    admin = CsrfClient(app, follow_redirects=False)
    admin.post("/register", data={"username": "root", "password": "correct-horse"})
    CsrfClient(app, follow_redirects=False).post("/register", data={"username": "alice", "password": "correct-horse"})
    db = next(get_db())
    db.query(User).filter_by(username="root").one().is_admin = True
    alice = db.query(User).filter_by(username="alice").one()
    alice.failed_login_attempts = 5
    alice.locked_until = utcnow().replace(tzinfo=None) + timedelta(minutes=15)
    db.commit()

    admin.post(f"/admin/users/{alice.id}/reset-password", data={"new_password": "battery-staple"})

    fresh = CsrfClient(app, follow_redirects=False)
    response = fresh.post("/login", data={"username": "alice", "password": "battery-staple"})
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_admin_list_marks_locked_accounts(tmp_path):
    init_db(tmp_path / "admin_locked_badge.db")
    admin = CsrfClient(app, follow_redirects=False)
    admin.post("/register", data={"username": "root", "password": "correct-horse"})
    db = next(get_db())
    root = db.query(User).filter_by(username="root").one()
    root.is_admin = True
    db.commit()
    assert "Locked" not in admin.get("/admin").text

    root.locked_until = utcnow().replace(tzinfo=None) + timedelta(minutes=15)
    db.commit()

    assert "Locked" in admin.get("/admin").text

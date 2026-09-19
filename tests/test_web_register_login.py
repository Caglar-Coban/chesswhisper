import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "register_login_test.db")
    return CsrfClient(app, follow_redirects=False)


def test_register_creates_user_and_logs_in(client):
    response = client.post("/register", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # Starlette's SessionMiddleware uses a cookie named "session" by default --
    # its presence means register() wrote request.session["user_id"].
    assert "session" in client.cookies


def test_register_rejects_duplicate_username(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})

    response = client.post("/register", data={"username": "alice", "password": "another-pass"})

    assert response.status_code == 200  # re-rendered form, not a redirect
    assert "zaten" in response.text.lower() or "already" in response.text.lower()


def test_register_rejects_short_password(client):
    response = client.post("/register", data={"username": "bob", "password": "short"})

    assert response.status_code == 200
    assert "8" in response.text


def test_login_with_correct_credentials_succeeds(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    response = client.post("/login", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_with_wrong_password_fails(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    response = client.post("/login", data={"username": "alice", "password": "wrong-password"})

    assert response.status_code == 200
    assert "hata" in response.text.lower() or "incorrect" in response.text.lower() or "yanlış" in response.text.lower()


def test_login_locks_account_after_five_failed_attempts(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    for _ in range(5):
        client.post("/login", data={"username": "alice", "password": "wrong-password"})

    response = client.post("/login", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 200
    assert "kilit" in response.text.lower() or "locked" in response.text.lower()


def test_login_resets_failed_attempts_after_success(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    for _ in range(3):
        client.post("/login", data={"username": "alice", "password": "wrong-password"})
    client.post("/login", data={"username": "alice", "password": "correct-horse"})
    client.post("/logout")

    for _ in range(3):
        client.post("/login", data={"username": "alice", "password": "wrong-password"})
    response = client.post("/login", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_clears_session(client):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    assert "session" in client.cookies

    response = client.post("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    # Starlette's SessionMiddleware deletes the cookie once the session dict is empty.
    assert "session" not in client.cookies

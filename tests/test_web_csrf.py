import re

import pytest
from fastapi.testclient import TestClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]*)"')


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "csrf_test.db")
    return TestClient(app, follow_redirects=False)


def test_post_with_wrong_csrf_token_is_rejected(client):
    client.get("/login")  # establishes a session + real token, which we then ignore

    response = client.post(
        "/register", data={"username": "alice", "password": "correct-horse", "csrf_token": "forged-token"}
    )

    assert response.status_code == 403
    assert "session" not in client.cookies or "user_id" not in client.cookies.get("session", "")


def test_post_with_missing_csrf_token_is_rejected(client):
    response = client.post("/register", data={"username": "alice", "password": "correct-horse"})

    assert response.status_code == 422


def test_post_with_correct_csrf_token_succeeds(client):
    page = client.get("/login")
    token = _CSRF_RE.search(page.text).group(1)

    response = client.post(
        "/register", data={"username": "alice", "password": "correct-horse", "csrf_token": token}
    )

    assert response.status_code == 303

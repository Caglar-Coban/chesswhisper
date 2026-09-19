import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import UserApiKey
from chesswhisper.web.security import decrypt_api_key


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "settings_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_settings_redirects_when_logged_out(tmp_path):
    init_db(tmp_path / "settings_logged_out.db")
    client = CsrfClient(app, follow_redirects=False)

    response = client.get("/settings")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_settings_shows_no_keys_configured_initially(client):
    response = client.get("/settings")

    assert response.status_code == 200
    assert "kayıtlı değil" in response.text.lower() or "not set" in response.text.lower()


def test_settings_saves_and_masks_anthropic_key(client):
    response = client.post("/settings", data={"anthropic_key": "sk-ant-secret", "openai_key": ""})

    assert response.status_code == 200
    assert "sk-ant-secret" not in response.text  # never echoed back in plaintext

    from chesswhisper.web.models import User
    db = next(get_db())
    user = db.query(User).filter_by(username="alice").one()
    row = db.get(UserApiKey, user.id)
    assert decrypt_api_key(row.anthropic_key_encrypted) == "sk-ant-secret"


def test_settings_leaves_key_unchanged_when_field_left_blank(client):
    client.post("/settings", data={"anthropic_key": "sk-ant-secret", "openai_key": ""})

    client.post("/settings", data={"anthropic_key": "", "openai_key": "sk-openai-secret"})

    from chesswhisper.web.models import User
    db = next(get_db())
    user = db.query(User).filter_by(username="alice").one()
    row = db.get(UserApiKey, user.id)
    assert decrypt_api_key(row.anthropic_key_encrypted) == "sk-ant-secret"
    assert decrypt_api_key(row.openai_key_encrypted) == "sk-openai-secret"

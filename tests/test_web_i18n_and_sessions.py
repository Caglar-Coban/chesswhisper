import pytest
from conftest import CsrfClient

from chesswhisper.web import ui_text
from chesswhisper.web.app import LOGIN_RATE_LIMIT, app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import User


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "i18n_sessions.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


# --- interface language ---------------------------------------------------


def test_every_ui_string_has_both_languages():
    for key in ui_text.all_keys():
        entry = ui_text._STRINGS[key]
        assert set(entry) == {"tr", "en"}, key
        assert entry["tr"].count("{") == entry["en"].count("{"), key


def test_interface_is_english_by_default(client):
    page = client.get("/settings").text

    assert '<html lang="en">' in page
    assert "Change password" in page


def test_switching_to_turkish_translates_the_interface(client):
    response = client.get("/language/tr?next=/settings")

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    page = client.get("/settings").text
    assert '<html lang="tr">' in page
    assert "Parola değiştir" in page
    assert "Change password" not in page


def test_turkish_errors_and_report_language_default(client):
    client.get("/language/tr")

    assert "Kayıt bulunamadı." in client.get("/history/999").text
    assert '<option value="tr" selected>Türkçe</option>' in client.get("/").text


def test_english_errors_and_report_language_default(client):
    client.get("/language/en")

    assert "Record not found." in client.get("/history/999").text
    assert '<option value="en" selected>English</option>' in client.get("/").text


def test_language_switch_ignores_unknown_languages_and_foreign_redirects(client):
    assert client.get("/language/de?next=/").status_code == 303
    assert '<html lang="en">' in client.get("/").text

    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        response = client.get("/language/en", params={"next": target})
        assert response.headers["location"] == "/"


def test_language_switch_works_before_logging_in(tmp_path):
    init_db(tmp_path / "anon_lang.db")
    anon = CsrfClient(app, follow_redirects=False)

    anon.get("/language/tr?next=/login")

    assert "Giriş Yap" in anon.get("/login").text


# --- sessions end when the password changes --------------------------------


def test_changing_your_password_signs_out_your_other_sessions(client):
    other_device = CsrfClient(app, follow_redirects=False)
    other_device.post("/login", data={"username": "alice", "password": "correct-horse"})
    assert other_device.get("/settings").status_code == 200

    response = client.post(
        "/settings/password",
        data={"current_password": "correct-horse", "new_password": "battery-staple", "confirm_password": "battery-staple"},
    )

    assert response.status_code == 200
    assert client.get("/settings").status_code == 200  # this session stays signed in
    assert other_device.get("/settings").status_code == 303  # the other one is signed out


def test_admin_reset_signs_the_user_out_everywhere(tmp_path):
    init_db(tmp_path / "reset_sessions.db")
    admin = CsrfClient(app, follow_redirects=False)
    admin.post("/register", data={"username": "root", "password": "correct-horse"})
    alice = CsrfClient(app, follow_redirects=False)
    alice.post("/register", data={"username": "alice", "password": "correct-horse"})
    db = next(get_db())
    db.query(User).filter_by(username="root").one().is_admin = True
    alice_id = db.query(User).filter_by(username="alice").one().id
    db.commit()

    admin.post(f"/admin/users/{alice_id}/reset-password", data={"new_password": "battery-staple"})

    assert alice.get("/settings").status_code == 303


# --- registration can be closed --------------------------------------------


def test_registration_can_be_closed(tmp_path, monkeypatch):
    init_db(tmp_path / "closed.db")
    monkeypatch.setenv("CHESSWHISPER_ALLOW_REGISTRATION", "0")
    anon = CsrfClient(app, follow_redirects=False)

    assert "/register" not in anon.get("/login").text
    page = anon.get("/register")
    assert page.status_code == 403
    response = anon.post("/register", data={"username": "mallory", "password": "correct-horse"})
    assert response.status_code == 403
    assert next(get_db()).query(User).count() == 0


# --- login attempts are rate limited per client -----------------------------


def test_login_attempts_are_rate_limited(tmp_path):
    init_db(tmp_path / "ratelimit.db")
    anon = CsrfClient(app, follow_redirects=False)

    statuses = [
        anon.post("/login", data={"username": f"nobody{i}", "password": "wrong-password"}).status_code
        for i in range(LOGIN_RATE_LIMIT + 1)
    ]

    assert statuses[:LOGIN_RATE_LIMIT] == [200] * LOGIN_RATE_LIMIT
    assert statuses[-1] == 429


def test_background_job_errors_use_the_starters_language(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("nobody expected this")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", boom)
    client.get("/language/en")

    response = client.post(
        "/analyze", data={"lang": "en", "llm": "none"}, files={"pgn": ("g.pgn", b"1. e4 *", "text/plain")}
    )
    job_id = response.headers["location"].rsplit("/", 1)[-1]

    assert client.get(f"/analyze/status/{job_id}").json()["message"] == "Something unexpected went wrong."

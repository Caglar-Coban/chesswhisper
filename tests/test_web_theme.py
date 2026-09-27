import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "theme.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_the_theme_follows_the_system_by_default(client):
    page = client.get("/settings").text

    assert '<html lang="en">' in page
    assert "data-theme" not in page.split(">", 2)[1]
    # The header link offers the next theme in the cycle.
    assert 'href="/theme/light?next=/settings"' in page


@pytest.mark.parametrize(("theme", "offered"), [("light", "dark"), ("dark", "system")])
def test_choosing_a_theme_marks_the_page_and_offers_the_next_one(client, theme, offered):
    response = client.get(f"/theme/{theme}?next=/history")

    assert response.status_code == 303
    assert response.headers["location"] == "/history"
    page = client.get("/history").text
    assert f'<html lang="en" data-theme="{theme}">' in page
    assert f'href="/theme/{offered}?next=/history"' in page


def test_going_back_to_the_system_theme_drops_the_mark(client):
    client.get("/theme/dark")
    client.get("/theme/system")

    assert '<html lang="en">' in client.get("/settings").text


def test_theme_switch_ignores_unknown_themes_and_foreign_redirects(client):
    client.get("/theme/dark")
    assert client.get("/theme/purple?next=/").status_code == 303
    assert 'data-theme="dark"' in client.get("/settings").text
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        response = client.get("/theme/light", params={"next": target})
        assert response.headers["location"] == "/"


def test_theme_switch_works_before_logging_in(tmp_path):
    init_db(tmp_path / "anon.db")
    anon = CsrfClient(app, follow_redirects=False)

    anon.get("/theme/dark?next=/login")

    assert 'data-theme="dark"' in anon.get("/login").text

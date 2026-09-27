import re

import chess.engine
import pytest
from click.testing import CliRunner
from conftest import CsrfClient

from chesswhisper.cli import cli
from chesswhisper.web import demo
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User
from chesswhisper.web.security import verify_password


class FakeEngine:
    def analyse(self, board, limit, **kwargs):
        return {"score": chess.engine.PovScore(chess.engine.Cp(10), chess.WHITE)}

    def quit(self):
        pass


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())


# --- seeding ------------------------------------------------------------------


def test_bundled_demo_games_ship_with_the_package():
    assert demo.demo_pgn_path().is_file()


def test_seed_creates_the_demo_user_and_spaced_out_analyses(tmp_path, engine):
    init_db(tmp_path / "seed.db")

    created = demo.seed_demo(analyses=3)

    db = next(get_db())
    user = db.query(User).filter_by(username=demo.DEMO_USERNAME).one()
    assert verify_password(demo.DEMO_PASSWORD, user.password_hash)
    assert not user.is_admin
    records = db.query(AnalysisRecord).filter_by(user_id=user.id).order_by(AnalysisRecord.created_at).all()
    assert created == len(records) == 3
    assert sum(r.games_count for r in records) == 12
    assert records[0].created_at < records[1].created_at < records[2].created_at
    assert all(r.games_json for r in records)


def test_seeding_twice_changes_nothing(tmp_path, engine):
    init_db(tmp_path / "seed_twice.db")
    demo.seed_demo(analyses=2)

    assert demo.seed_demo(analyses=2) == 0
    assert next(get_db()).query(AnalysisRecord).count() == 2


def test_seed_demo_command(tmp_path, engine, monkeypatch):
    from chesswhisper.web import db as web_db

    monkeypatch.setattr(web_db, "_default_db_path", lambda: tmp_path / "seed_cmd.db")

    result = CliRunner().invoke(cli, ["seed-demo", "--analyses", "2"])

    assert result.exit_code == 0, result.output
    assert "2 analyses" in result.output


# --- demo mode ----------------------------------------------------------------


@pytest.fixture
def demo_client(tmp_path, monkeypatch, engine):
    monkeypatch.setenv("CHESSWHISPER_DEMO", "1")
    init_db(tmp_path / "demo_mode.db")
    demo.seed_demo(analyses=1)
    c = CsrfClient(app, follow_redirects=False)
    c.post("/login", data={"username": demo.DEMO_USERNAME, "password": demo.DEMO_PASSWORD})
    return c


def test_demo_banner_and_login_hint(demo_client):
    assert "Public demo" in demo_client.get("/").text
    login_page = CsrfClient(app).get("/login").text
    assert demo.DEMO_USERNAME in login_page and demo.DEMO_PASSWORD in login_page


def test_demo_has_no_llm_commentary_or_stored_api_keys(demo_client):
    assert 'value="claude"' not in demo_client.get("/").text
    settings = demo_client.get("/settings").text
    assert 'name="anthropic_key"' not in settings

    assert demo_client.post("/settings", data={"anthropic_key": "sk-ant-x"}).status_code == 403
    response = demo_client.post(
        "/analyze", data={"lang": "en", "llm": "claude"}, files={"pgn": ("g.pgn", b"1. e4 *", "text/plain")}
    )
    assert response.status_code == 400


def test_demo_caps_the_number_of_games(demo_client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", lambda *a: pytest.fail("fetched"))

    response = demo_client.post(
        "/analyze", data={"source": "lichess", "username": "someone", "max_games": "11", "lang": "en", "llm": "none"}
    )

    assert response.status_code == 400
    assert "10" in response.text
    assert f'max="{demo.DEMO_MAX_GAMES}"' in demo_client.get("/").text


def test_outside_demo_mode_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("CHESSWHISPER_DEMO", raising=False)
    init_db(tmp_path / "not_demo.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})

    assert "Public demo" not in c.get("/").text
    assert 'value="claude"' in c.get("/").text


def test_one_click_demo_login(tmp_path, monkeypatch, engine):
    monkeypatch.setenv("CHESSWHISPER_DEMO", "1")
    init_db(tmp_path / "one_click.db")
    demo.seed_demo(analyses=1)
    visitor = CsrfClient(app, follow_redirects=False)
    page = visitor.get("/login").text
    assert 'id="demo-login"' in page
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)

    response = visitor.post("/login", data={
        "csrf_token": token, "username": demo.DEMO_USERNAME, "password": demo.DEMO_PASSWORD,
    })

    assert response.status_code == 303
    assert visitor.get("/history").status_code == 200

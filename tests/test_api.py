import re
import textwrap

import chess.engine
import pytest
from conftest import CsrfClient
from fastapi.testclient import TestClient

from chesswhisper.web import jobs
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import User

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"

TOKEN_RE = re.compile(r"\b(cw_[A-Za-z0-9_-]{20,})\b")


class FakeEngine:
    def analyse(self, board, limit, **kwargs):
        return {"score": chess.engine.PovScore(chess.engine.Cp(15), chess.WHITE)}

    def quit(self):
        pass


def _token_for(web: CsrfClient) -> str:
    page = web.post("/settings/api-token").text
    match = TOKEN_RE.search(page)
    assert match, "the new token is shown once on the settings page"
    return match.group(1)


@pytest.fixture
def web(tmp_path):
    init_db(tmp_path / "api_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


@pytest.fixture
def api(web):
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_token_for(web)}"
    return client


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())


# --- tokens -----------------------------------------------------------------


def test_token_is_stored_hashed_and_shown_once(web):
    token = _token_for(web)

    user = next(get_db()).query(User).filter_by(username="alice").one()
    assert user.api_token_hash and token not in user.api_token_hash
    assert token not in web.get("/settings").text


def test_regenerating_a_token_invalidates_the_old_one(web):
    old = _token_for(web)
    new = _token_for(web)

    assert TestClient(app).get("/api/v1/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert TestClient(app).get("/api/v1/me", headers={"Authorization": f"Bearer {new}"}).status_code == 200


def test_revoked_token_stops_working(web):
    token = _token_for(web)

    web.post("/settings/api-token/delete")

    assert TestClient(app).get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("header", [None, "Bearer nope", "Basic abc", "Bearer "])
def test_requests_without_a_valid_token_are_rejected(web, header):
    headers = {"Authorization": header} if header else {}

    response = TestClient(app).get("/api/v1/analyses", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"]


def test_me_describes_the_token_owner(api):
    assert api.get("/api/v1/me").json() == {"username": "alice", "is_admin": False}


# --- analyses -----------------------------------------------------------------


def _run(api, **body):
    response = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": SAMPLE_PGN, "player": "Alice", **body})
    assert response.status_code == 202, response.text
    job = api.get(response.json()["status_url"]).json()
    assert job["status"] == "done", job
    return job


def test_start_an_analysis_and_follow_it_to_the_result(api, engine):
    response = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": SAMPLE_PGN, "player": "Alice"})

    assert response.status_code == 202
    body = response.json()
    assert body["status_url"] == f"/api/v1/jobs/{body['job_id']}"
    assert response.headers["location"] == body["status_url"]

    job = api.get(body["status_url"]).json()
    assert job["status"] == "done"
    assert job["completed"] == job["total"] == 1
    assert job["analysis_url"] == f"/api/v1/analyses/{job['analysis_id']}"

    detail = api.get(job["analysis_url"]).json()
    assert detail["player"] == "Alice"
    assert detail["games_count"] == 1
    assert detail["report_markdown"].startswith("# chesswhisper Analysis Report")
    assert [g["opponent"] for g in detail["games"]] == ["Bob"]
    assert "moves" not in detail["games"][0]
    assert isinstance(detail["critical_moves"], list)


def test_game_moves_are_included_on_request(api, engine):
    job = _run(api)

    detail = api.get(f"{job['analysis_url']}?include_moves=true").json()

    assert [m["san"] for m in detail["games"][0]["moves"]][:3] == ["e4", "e5", "Nf3"]


def test_list_analyses_newest_first(api, engine):
    first = _run(api)["analysis_id"]
    second = _run(api)["analysis_id"]

    listed = api.get("/api/v1/analyses").json()

    assert [a["id"] for a in listed] == [second, first]
    assert set(listed[0]) == {"id", "created_at", "player", "games_count", "accuracy", "acpl", "lang", "llm"}


def test_delete_an_analysis(api, engine):
    analysis_id = _run(api)["analysis_id"]

    assert api.delete(f"/api/v1/analyses/{analysis_id}").status_code == 204
    assert api.get(f"/api/v1/analyses/{analysis_id}").status_code == 404
    assert api.delete(f"/api/v1/analyses/{analysis_id}").status_code == 404


def test_other_users_analyses_and_jobs_are_invisible(api, engine, web):
    job = _run(api)
    bob_web = CsrfClient(app, follow_redirects=False)
    bob_web.post("/register", data={"username": "bob", "password": "correct-horse"})
    bob = TestClient(app, headers={"Authorization": f"Bearer {_token_for(bob_web)}"})

    assert bob.get(job["analysis_url"]).status_code == 404
    assert bob.delete(job["analysis_url"]).status_code == 404
    assert bob.get(f"/api/v1/jobs/{job['job_id']}").status_code == 404
    assert bob.get("/api/v1/analyses").json() == []


@pytest.mark.parametrize(
    "body, status",
    [
        ({"source": "pgn"}, 400),                                   # no PGN
        ({"source": "pgn", "pgn": SAMPLE_PGN, "lang": "de"}, 422),  # schema: unknown language
        ({"source": "carrier-pigeon"}, 422),                        # schema: unknown source
        ({"source": "lichess"}, 400),                               # no username
        ({"source": "lichess", "username": "x", "max_games": 0}, 422),
    ],
)
def test_bad_requests_are_explained(api, body, status):
    response = api.post("/api/v1/analyses", json=body)

    assert response.status_code == status
    assert response.json()["detail"]


def test_commentary_needs_a_saved_key(api):
    response = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": SAMPLE_PGN, "llm": "claude"})

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]


def test_one_analysis_at_a_time(api):
    user = next(get_db()).query(User).filter_by(username="alice").one()
    running = jobs.create_job(user.id)

    response = api.post("/api/v1/analyses", json={"source": "pgn", "pgn": SAMPLE_PGN})

    assert response.status_code == 409
    assert response.json()["job_id"] == running.id


def test_fetching_by_username_passes_the_request_through(api, engine, monkeypatch):
    from chesswhisper.game_sources import FetchResult

    seen = {}

    def fake_fetch(platform, username, count):
        seen["args"] = (platform, username, count)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice")

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = api.post("/api/v1/analyses", json={"source": "lichess", "username": "alice", "max_games": 7})

    assert response.status_code == 202
    assert seen["args"] == ("lichess", "alice", 7)


# --- documentation ------------------------------------------------------------


def test_openapi_documents_only_the_api(web):
    spec = TestClient(app).get("/api/openapi.json").json()

    assert spec["info"]["title"] == "chesswhisper API"
    assert spec["paths"] and all(path.startswith("/api/v1/") for path in spec["paths"])
    assert TestClient(app).get("/api/docs").status_code == 200
    assert TestClient(app).get("/docs").status_code == 404

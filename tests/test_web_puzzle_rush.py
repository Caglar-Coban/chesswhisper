import pytest
from conftest import CsrfClient
from test_web_puzzles import _seed

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import PuzzleAttempt, PuzzleRushRun


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "rush.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_the_rush_page_offers_lengths_and_themes(client):
    _seed()

    page = client.get("/puzzles/rush").text

    assert "Timed round" in page
    assert 'data-minutes="3"' in page and 'data-minutes="5"' in page
    assert 'id="rush-theme"' in page
    assert 'href="/puzzles/rush"' in client.get("/puzzles/1/0").text


def test_without_puzzles_the_rush_page_says_so(client):
    assert "No puzzles yet" in client.get("/puzzles/rush").text


def test_the_set_holds_every_puzzle_in_some_order(client):
    record_id = _seed()

    data = client.get("/puzzles/rush/set").json()

    assert sorted((p["record_id"], p["index"]) for p in data["puzzles"]) == [(record_id, 0), (record_id, 1)]
    first = data["puzzles"][0]
    assert first["your_color"] == "white" and "e2e4" in first["legal_moves"]


def test_checking_a_move_saves_no_attempt(client):
    record_id = _seed()

    right = client.post("/puzzles/rush/check", data={"record_id": record_id, "index": 0, "move": "e2e4"}).json()
    wrong = client.post("/puzzles/rush/check", data={"record_id": record_id, "index": 0, "move": "g1f3"}).json()

    assert (right["correct"], right["status"]) == (True, "solved")
    assert (wrong["correct"], wrong["best_uci"]) == (False, "e2e4")
    db = next(get_db())
    try:
        assert db.query(PuzzleAttempt).count() == 0
    finally:
        db.close()


def test_illegal_or_unknown_moves_are_refused(client):
    record_id = _seed()

    illegal = client.post("/puzzles/rush/check", data={"record_id": record_id, "index": 0, "move": "e2e5"})
    unknown = client.post("/puzzles/rush/check", data={"record_id": 999, "index": 0, "move": "e2e4"})

    assert (illegal.status_code, unknown.status_code) == (400, 404)


def test_finishing_keeps_the_best_per_length(client):
    _seed()

    first = client.post("/puzzles/rush/finish", data={"minutes": 3, "solved": 4, "misses": 3}).json()
    worse = client.post("/puzzles/rush/finish", data={"minutes": 3, "solved": 2, "misses": 3}).json()
    other = client.post("/puzzles/rush/finish", data={"minutes": 5, "solved": 1, "misses": 1, "theme": "fork"}).json()

    assert first == {"best": 4, "new_best": True}
    assert worse == {"best": 4, "new_best": False}
    assert other == {"best": 1, "new_best": True}
    page = client.get("/puzzles/rush").text
    assert '<strong id="best-3">4</strong>' in page
    assert "Recent rounds" in page


def test_impossible_results_are_refused(client):
    for data in ({"minutes": 4, "solved": 1, "misses": 0}, {"minutes": 3, "solved": -1, "misses": 0},
                 {"minutes": 3, "solved": 1, "misses": 4}):
        assert client.post("/puzzles/rush/finish", data=data).status_code == 400
    db = next(get_db())
    try:
        assert db.query(PuzzleRushRun).count() == 0
    finally:
        db.close()


def test_a_round_needs_the_csrf_token(client):
    record_id = _seed()

    response = client.post("/puzzles/rush/check",
                           data={"record_id": record_id, "index": 0, "move": "e2e4", "csrf_token": "wrong"})

    assert response.status_code == 403

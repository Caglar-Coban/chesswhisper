import json
import re

import chess
import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, PuzzleAttempt, User

START = chess.STARTING_FEN


def _entry(uci="a2a3", best="e2e4", opponent="Bob", game_index=0):
    board = chess.Board(START)
    return {
        "game_index": game_index, "opponent": opponent, "your_color": "white", "ply": 1, "move_number": 1,
        "san": board.san(chess.Move.from_uci(uci)), "uci": uci, "best_uci": best,
        "best_san": board.san(chess.Move.from_uci(best)), "fen_before": START,
        "cp_before": 40, "cp_after": -320, "cp_loss": 360, "classification": "blunder",
    }


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "puzzles_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _seed(username="alice", entries=None):
    db = next(get_db())
    user = db.query(User).filter_by(username=username).one()
    record = AnalysisRecord(
        user_id=user.id, player_name="Alice", lang="en", llm_used="none", games_count=1,
        overall_acpl=30.0, overall_accuracy=90.0, report_markdown="# r",
        critical_moves_json=json.dumps(entries if entries is not None else [_entry(), _entry("h2h3", "d2d4", "Carol")]),
    )
    db.add(record)
    db.commit()
    return record.id


def _attempt(client, record_id, index, move):
    page = client.get(f"/puzzles/{record_id}/{index}").text
    token = re.search(r'name="csrf_token" value="([^"]*)"', page).group(1)
    return client.post(f"/puzzles/{record_id}/{index}/attempt", data={"move": move, "csrf_token": token})


def test_navigation_links_to_puzzles(client):
    assert 'href="/puzzles"' in client.get("/").text


def test_no_puzzles_until_an_analysis_has_critical_moves(client):
    _seed(entries=[])

    response = client.get("/puzzles")

    assert response.status_code == 200
    assert "No puzzles yet" in response.text


def test_puzzles_page_sends_you_to_the_next_puzzle(client):
    record_id = _seed()

    response = client.get("/puzzles")

    assert response.status_code == 303
    assert response.headers["location"] == f"/puzzles/{record_id}/0"


def test_puzzle_page_shows_the_position_and_legal_moves(client):
    record_id = _seed()

    page = client.get(f"/puzzles/{record_id}/1").text

    assert START in page
    assert '"e2e4"' in page  # legal moves are embedded for the board script
    assert "Carol" in page
    assert "0 of 2 solved" in page


def test_puzzles_are_private_and_bounded(client):
    db = next(get_db())
    db.add(User(username="bob", password_hash="x", is_admin=False))
    db.commit()
    bobs = _seed("bob")
    mine = _seed()

    assert client.get(f"/puzzles/{bobs}/0").status_code == 404
    assert client.get(f"/puzzles/{mine}/2").status_code == 404
    assert _attempt(client, mine, 0, "e2e4").status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]*)"', client.get(f"/puzzles/{mine}/0").text).group(1)
    assert client.post(f"/puzzles/{bobs}/0/attempt", data={"move": "e2e4", "csrf_token": token}).status_code == 404


def test_finding_the_engine_move_solves_the_puzzle(client):
    record_id = _seed()

    body = _attempt(client, record_id, 0, "e2e4").json()

    assert body["correct"] is True
    assert body["best_san"] == "e4"
    assert body["played_san"] == "a3"
    assert body["solved"] == 1 and body["total"] == 2
    [attempt] = next(get_db()).query(PuzzleAttempt).all()
    assert (attempt.record_id, attempt.move_index, attempt.move_uci, attempt.solved) == (record_id, 0, "e2e4", True)


def test_a_different_legal_move_is_a_miss(client):
    record_id = _seed()

    body = _attempt(client, record_id, 0, "g1f3").json()

    assert body["correct"] is False
    assert body["best_san"] == "e4" and body["best_uci"] == "e2e4"
    assert body["solved"] == 0


def test_illegal_or_malformed_moves_are_rejected(client):
    record_id = _seed()

    assert _attempt(client, record_id, 0, "e2e5").status_code == 400
    assert _attempt(client, record_id, 0, "nonsense").status_code == 400
    assert next(get_db()).query(PuzzleAttempt).count() == 0


def test_attempts_need_a_csrf_token(client):
    record_id = _seed()

    response = client.post(f"/puzzles/{record_id}/0/attempt", data={"move": "e2e4", "csrf_token": "forged"})

    assert response.status_code == 403


def test_solved_puzzles_move_to_the_back_of_the_queue(client):
    record_id = _seed()
    _attempt(client, record_id, 0, "e2e4")

    assert client.get("/puzzles").headers["location"] == f"/puzzles/{record_id}/1"


def test_deleting_an_analysis_deletes_its_attempts(client):
    record_id = _seed()
    _attempt(client, record_id, 0, "e2e4")
    token = re.search(r'name="csrf_token" value="([^"]*)"', client.get("/history").text).group(1)

    client.post(f"/history/{record_id}/delete", data={"csrf_token": token})

    assert next(get_db()).query(PuzzleAttempt).count() == 0


def test_report_page_offers_the_puzzles(client):
    record_id = _seed()

    assert 'id="practice-link" href="/puzzles"' in client.get(f"/history/{record_id}").text

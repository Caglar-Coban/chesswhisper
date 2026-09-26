import json

import chess
import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord, User


def _timeline(number, opponent="Bob"):
    board = chess.Board()
    moves = []
    for ply, san in enumerate(["e4", "e5", "Qh5"], start=1):
        move = board.parse_san(san)
        color = "white" if board.turn else "black"
        board.push(move)
        moves.append({
            "ply": ply, "move_number": (ply + 1) // 2, "color": color, "san": san, "uci": move.uci(),
            "fen": board.fen(), "eval": 20, "classification": "mistake" if ply == 3 else "fine",
            "best_san": "Nf3" if ply == 3 else None, "best_uci": "g1f3" if ply == 3 else None,
        })
    return {
        "game_number": number, "white": "Alice", "black": opponent, "your_color": "white",
        "opponent": opponent, "result": "win", "opening": "King's Pawn Game", "eco": "C20",
        "time_class": "blitz", "start_fen": chess.STARTING_FEN, "moves": moves,
    }


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "viewer_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _seed(username="alice", games_json="default"):
    db = next(get_db())
    user = db.query(User).filter_by(username=username).one()
    if games_json == "default":
        games_json = json.dumps([_timeline(1), _timeline(2, opponent="Carol")])
    record = AnalysisRecord(
        user_id=user.id, player_name="Alice", lang="en", llm_used="none", games_count=2,
        overall_acpl=10.0, overall_accuracy=95.0, report_markdown="# Report", games_json=games_json,
    )
    db.add(record)
    db.commit()
    return record.id


def test_viewer_shows_the_game_and_its_moves(client):
    record_id = _seed()

    response = client.get(f"/history/{record_id}/games/2")

    assert response.status_code == 200
    assert "Carol" in response.text
    assert '"san": "Qh5"' in response.text  # the timeline is embedded for the viewer script
    assert f"/history/{record_id}/games/1" in response.text  # link to the previous game


def test_viewer_rejects_a_game_number_out_of_range(client):
    record_id = _seed()

    assert client.get(f"/history/{record_id}/games/0").status_code == 404
    assert client.get(f"/history/{record_id}/games/3").status_code == 404


def test_viewer_is_private_to_the_records_owner(client):
    db = next(get_db())
    db.add(User(username="bob", password_hash="x", is_admin=False))
    db.commit()
    record_id = _seed("bob")

    assert client.get(f"/history/{record_id}/games/1").status_code == 404


def test_viewer_404s_for_records_saved_without_timelines(client):
    record_id = _seed(games_json=None)

    assert client.get(f"/history/{record_id}/games/1").status_code == 404


def test_report_page_links_each_game_to_the_viewer(client):
    record_id = _seed()

    page = client.get(f"/history/{record_id}").text

    assert f"/history/{record_id}/games/1" in page
    assert f"/history/{record_id}/games/2" in page
    assert "Carol" in page


def test_report_page_without_timelines_has_no_game_links(client):
    record_id = _seed(games_json=None)

    assert "/games/" not in client.get(f"/history/{record_id}").text


def test_admin_can_open_another_users_game(tmp_path):
    init_db(tmp_path / "viewer_admin.db")
    admin = CsrfClient(app, follow_redirects=False)
    admin.post("/register", data={"username": "root", "password": "correct-horse"})
    CsrfClient(app, follow_redirects=False).post("/register", data={"username": "alice", "password": "correct-horse"})
    db = next(get_db())
    db.query(User).filter_by(username="root").one().is_admin = True
    alice_id = db.query(User).filter_by(username="alice").one().id
    db.commit()
    record_id = _seed("alice")

    response = admin.get(f"/admin/users/{alice_id}/history/{record_id}/games/1")

    assert response.status_code == 200
    assert "Bob" in response.text


def test_critical_move_cards_link_to_the_move_in_the_viewer(client):
    board = chess.Board()
    fen_before = board.fen()
    moves = [{
        "game_index": 1, "opponent": "Carol", "your_color": "white", "ply": 1, "move_number": 1,
        "san": "e4", "uci": "e2e4", "best_uci": "d2d4", "best_san": "d4", "fen_before": fen_before,
        "cp_before": 0, "cp_after": -400, "cp_loss": 400, "classification": "blunder",
    }]
    record_id = _seed()
    db = next(get_db())
    db.get(AnalysisRecord, record_id).critical_moves_json = json.dumps(moves)
    db.commit()

    page = client.get(f"/history/{record_id}").text

    assert f"/history/{record_id}/games/2?ply=1" in page

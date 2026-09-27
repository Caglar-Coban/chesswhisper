import json

import chess
import pytest
from conftest import CsrfClient

from chesswhisper.web.app import app
from chesswhisper.web.db import init_db, session_scope
from chesswhisper.web.models import AnalysisRecord, User


def _timeline(sans, color="white", result="win", black="Bob"):
    board, moves = chess.Board(), []
    for san in sans:
        move = board.parse_san(san)
        moves.append({"san": san, "uci": move.uci(), "color": "white" if board.turn else "black",
                      "classification": None})
        board.push(move)
    white = "Alice" if color == "white" else "Carol"
    return {"white": white, "black": black if color == "white" else "Alice", "your_color": color,
            "result": result, "start_fen": chess.STARTING_FEN, "moves": moves}


def _add_record(username, player, timelines):
    with session_scope() as db:
        user = db.query(User).filter_by(username=username).one()
        db.add(AnalysisRecord(
            user_id=user.id, player_name=player, lang="en", llm_used="none", games_count=len(timelines),
            overall_acpl=10.0, overall_accuracy=90.0, report_markdown="# R", games_json=json.dumps(timelines),
        ))


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "repertoire.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def test_without_analyses_the_page_says_so(client):
    page = client.get("/repertoire").text

    assert "No analysed games yet." in page
    assert 'href="/repertoire"' in page  # in the header


def test_the_start_position_lists_the_players_first_moves(client):
    _add_record("alice", "Alice", [_timeline(["e4", "e5"], black="B1"), _timeline(["e4", "c5"], black="B2"),
                                   _timeline(["d4", "d5"], result="loss", black="B3")])

    page = client.get("/repertoire").text

    assert "Games reaching this position: 3" in page
    assert "/repertoire?player=Alice&amp;color=white&amp;line=e2e4" in page
    assert ">e4</a>" in page and ">d4</a>" in page
    assert "100%" in page and "0%" in page


def test_following_a_line_shows_the_replies_and_the_breadcrumb(client):
    _add_record("alice", "Alice", [_timeline(["e4", "e5", "Nf3"], black="B1"), _timeline(["e4", "c5"], black="B2")])

    page = client.get("/repertoire", params={"player": "Alice", "color": "white", "line": "e2e4"}).text

    assert "Games reaching this position: 2" in page
    assert "their move" in page
    assert 'aria-current="page">1. e4</a>' in page
    assert "line=e2e4%2Ce7e5" in page


def test_colour_and_player_choose_the_games(client):
    _add_record("alice", "Alice", [_timeline(["e4", "e5"]), _timeline(["d4", "Nf6"], color="black")])
    _add_record("alice", "Zed", [_timeline(["c4"])])

    black = client.get("/repertoire", params={"player": "Alice", "color": "black"}).text
    assert ">d4</a>" in black and ">e4</a>" not in black
    zed = client.get("/repertoire", params={"player": "Zed"}).text
    assert ">c4</a>" in zed and ">e4</a>" not in zed


def test_bad_or_unreached_lines_start_over(client):
    _add_record("alice", "Alice", [_timeline(["e4", "e5"])])

    for line in ("d2d4", "zz99", "e2e4,<script>"):
        page = client.get("/repertoire", params={"line": line}).text
        assert "Games reaching this position: 1" in page
        assert "your move" in page


def test_other_users_games_are_not_shown(client, tmp_path):
    other = CsrfClient(app, follow_redirects=False)
    other.post("/register", data={"username": "bob", "password": "correct-horse"})
    _add_record("bob", "Alice", [_timeline(["e4"])])

    assert "No analysed games yet." in client.get("/repertoire").text

import io

import chess
import chess.pgn

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import LoadedGame
from chesswhisper.stats_engine import build_game_report
from chesswhisper.timeline import game_timelines

ECO = load_eco_table()


def _report(player="Alice", moves="1. e4 e5 2. Qh5 Nc6", losses=None, best=None, headers=""):
    text = f'[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n[TimeControl "180+2"]\n{headers}\n{moves} 1-0\n'
    game = chess.pgn.read_game(io.StringIO(text))
    board = game.board()
    evals = []
    for ply, move in enumerate(game.mainline_moves(), start=1):
        fen_before, san, color = board.fen(), board.san(move), board.turn
        board.push(move)
        loss = (losses or {}).get(ply, 0)
        evals.append(PositionEval(
            ply=ply, move_san=san, color=color, cp_before=30, cp_after=30 - loss, fen_after=board.fen(),
            fen_before=fen_before, move_uci=move.uci(), best_move_uci=(best or {}).get(ply),
        ))
    loaded = LoadedGame(index=0, game=game, headers=dict(game.headers))
    return build_game_report(loaded, evals, player, ECO)


def test_timeline_describes_the_game():
    [timeline] = game_timelines([_report()])

    assert timeline["game_number"] == 1
    assert (timeline["white"], timeline["black"]) == ("Alice", "Bob")
    assert timeline["your_color"] == "white"
    assert timeline["result"] == "win"
    assert timeline["time_class"] == "blitz"
    assert timeline["start_fen"] == chess.STARTING_FEN
    assert [m["san"] for m in timeline["moves"]] == ["e4", "e5", "Qh5", "Nc6"]


def test_moves_carry_position_eval_from_whites_side_and_classification():
    [timeline] = game_timelines([_report(losses={2: 400, 3: 120})])
    moves = timeline["moves"]

    # Ply 2 is Black's e5 losing 400 from Black's side: White's eval goes up.
    assert moves[1]["color"] == "black"
    assert moves[1]["eval"] == -(30 - 400)
    assert moves[1]["classification"] == "blunder"
    assert moves[2]["eval"] == 30 - 120
    assert moves[2]["classification"] == "mistake"
    assert moves[0]["classification"] == "fine"
    assert moves[2]["move_number"] == 2
    board = chess.Board(moves[2]["fen"])
    assert board.piece_at(chess.H5) == chess.Piece(chess.QUEEN, chess.WHITE)


def test_best_move_is_given_only_when_it_differs_from_the_move_played():
    [timeline] = game_timelines([_report(best={1: "d2d4", 2: "e7e5", 3: "zzzz"})])
    moves = timeline["moves"]

    assert moves[0]["best_san"] == "d4"
    assert moves[0]["best_uci"] == "d2d4"
    assert moves[1]["best_san"] is None  # the engine agreed with e5
    assert moves[2]["best_san"] is None  # unreadable move is dropped


def test_games_started_from_a_fen_keep_their_start_position():
    fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 30"
    report = _report(moves="30. e4 Kd7", headers=f'[SetUp "1"]\n[FEN "{fen}"]')

    [timeline] = game_timelines([report])

    assert timeline["start_fen"] == fen
    assert timeline["moves"][0]["move_number"] == 30

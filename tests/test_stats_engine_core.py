import chess
from chesswhisper.stats_engine import (
    classify_move, game_phase, win_percent, move_accuracy,
)


def test_classify_move_boundaries():
    assert classify_move(0) == "fine"
    assert classify_move(49) == "fine"
    assert classify_move(50) == "inaccuracy"
    assert classify_move(99) == "inaccuracy"
    assert classify_move(100) == "mistake"
    assert classify_move(299) == "mistake"
    assert classify_move(300) == "blunder"
    assert classify_move(1500) == "blunder"


def test_game_phase_opening_by_ply():
    start_fen = chess.STARTING_FEN
    assert game_phase(1, start_fen) == "opening"
    assert game_phase(20, start_fen) == "opening"


def test_game_phase_endgame_by_material():
    # Only kings and a couple of pawns left -> should be endgame despite high ply not required
    bare_fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 40"
    assert game_phase(40, bare_fen) == "endgame"


def test_game_phase_middlegame_when_material_remains():
    # Full starting material, past the opening cutoff
    assert game_phase(21, chess.STARTING_FEN) == "middlegame"


def test_win_percent_symmetry_around_zero():
    assert abs(win_percent(0) - 50.0) < 1e-6
    assert win_percent(100) > 50.0
    assert win_percent(-100) < 50.0


def test_move_accuracy_no_drop_is_perfect():
    assert move_accuracy(60.0, 60.0) == 100.0


def test_move_accuracy_big_drop_is_low():
    acc = move_accuracy(90.0, 10.0)
    assert 0.0 <= acc < 20.0

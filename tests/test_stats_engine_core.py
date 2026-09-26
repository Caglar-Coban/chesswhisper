import chess
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.stats_engine import (
    EVAL_CLAMP_CP,
    _player_stats_for_color,
    classify_move,
    cp_loss,
    game_phase,
    move_accuracy,
    win_percent,
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


def test_game_phase_force_material_based_ignores_ply_cutoff():
    # Ply 1 would normally be "opening", but a custom-start game with almost no
    # material on the board should be classified as "endgame" instead.
    bare_fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
    assert game_phase(1, bare_fen, force_material_based=True) == "endgame"


def test_win_percent_symmetry_around_zero():
    assert abs(win_percent(0) - 50.0) < 1e-6
    assert win_percent(100) > 50.0
    assert win_percent(-100) < 50.0


def test_move_accuracy_no_drop_is_perfect():
    assert move_accuracy(60.0, 60.0) == 100.0


def test_move_accuracy_big_drop_is_low():
    acc = move_accuracy(90.0, 10.0)
    assert 0.0 <= acc < 20.0


def _eval(ply, cp_before, cp_after, color=True):
    return PositionEval(ply=ply, move_san="x", color=color, cp_before=cp_before, cp_after=cp_after, fen_after="")


def test_cp_loss_clamps_mate_scores_to_a_decisive_advantage():
    # Missing a mate in 2 while staying a queen up is a mistake, not a 99,000 cp loss.
    assert cp_loss(_eval(1, 99_998, 900)) == EVAL_CLAMP_CP - 900
    # Walking from +3 into a forced mate is still a blunder.
    assert classify_move(cp_loss(_eval(1, 300, -99_995))) == "blunder"
    # Mate kept or delivered costs nothing.
    assert cp_loss(_eval(1, 99_998, 100_000)) == 0
    assert cp_loss(_eval(1, 50, 80)) == 0


def test_one_missed_mate_does_not_dominate_a_games_acpl():
    evals = [_eval(2 * i + 1, 30, 30) for i in range(39)]
    evals.append(_eval(79, 99_998, 900))
    stats, _ = _player_stats_for_color(evals, True, {ev.ply: "middlegame" for ev in evals})
    assert stats.acpl == pytest.approx(100 / 40)

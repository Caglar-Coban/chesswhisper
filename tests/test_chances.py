import chess

from chesswhisper.chances import chance_stats, opponent_chances
from chesswhisper.engine_analyzer import PositionEval

# White to move: Rxd5 wins the loose knight.
FREE_KNIGHT_FEN = "4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1"


def _ev(ply, color, cp_before, cp_after, fen_before=None, move_uci=None, best=None, san="x"):
    return PositionEval(ply=ply, move_san=san, color=color, cp_before=cp_before, cp_after=cp_after,
                        fen_after="f", fen_before=fen_before, move_uci=move_uci, best_move_uci=best)


def test_a_punished_blunder():
    evals = [
        _ev(1, chess.BLACK, 0, -400),  # the opponent (Black) blunders 4 pawns
        _ev(2, chess.WHITE, 400, 380),  # the player keeps the advantage
    ]

    [chance] = opponent_chances(evals, chess.WHITE)

    assert (chance.ply, chance.classification, chance.punished, chance.reply_loss) == (1, "blunder", True, 20)


def test_a_missed_chance_names_the_tactic_that_was_there():
    evals = [
        _ev(1, chess.BLACK, 0, -250),  # a mistake by Black: the d5 knight is loose
        _ev(2, chess.WHITE, 250, 0, fen_before=FREE_KNIGHT_FEN, move_uci="e1e2", best="d1d5", san="Ke2"),
    ]

    [chance] = opponent_chances(evals, chess.WHITE)

    assert (chance.classification, chance.punished, chance.motif, chance.reply_ply) == ("mistake", False, "hanging", 2)
    assert chance.reply_san == "Ke2"


def test_only_opponent_mistakes_and_blunders_count():
    evals = [
        _ev(1, chess.WHITE, 0, -400),  # the player's own blunder
        _ev(2, chess.BLACK, 400, 340),  # the opponent's inaccuracy
        _ev(3, chess.WHITE, 60, 0),
        _ev(4, chess.BLACK, 0, -500),  # the opponent's blunder on the last ply: no reply to judge
    ]

    assert opponent_chances(evals, chess.WHITE) == []


def test_chance_stats_count_punished_and_missed_by_motif():
    evals = [
        _ev(1, chess.BLACK, 0, -400), _ev(2, chess.WHITE, 400, 390),
        _ev(3, chess.BLACK, 0, -300), _ev(4, chess.WHITE, 300, 0, fen_before=FREE_KNIGHT_FEN, move_uci="e1e2", best="d1d5"),
        _ev(5, chess.BLACK, 0, -300), _ev(6, chess.WHITE, 300, 0),
    ]

    class _Report:
        chances = opponent_chances(evals, chess.WHITE)

    stats = chance_stats([_Report(), _Report()])

    assert (stats.total, stats.punished) == (6, 2)
    assert stats.missed_by_motif == [("hanging", 2), ("other", 2)]
    assert stats.punish_rate == 2 / 6


def test_chance_stats_with_no_chances():
    class _Report:
        chances = []

    stats = chance_stats([_Report()])

    assert (stats.total, stats.punished, stats.missed_by_motif, stats.punish_rate) == (0, 0, [], None)

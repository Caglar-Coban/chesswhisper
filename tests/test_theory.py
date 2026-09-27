import chess
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import EcoEntry
from chesswhisper.theory import AFTER_PLIES, book_prefixes, theory_exit, theory_stats

TABLE = [
    EcoEntry("C20", "King's Pawn Game", ("e4", "e5")),
    EcoEntry("C60", "Ruy Lopez", ("e4", "e5", "Nf3", "Nc6", "Bb5")),
    EcoEntry("B20", "Sicilian Defense", ("e4", "c5")),
]
PREFIXES = book_prefixes(TABLE)


def _evals(sans, white_evals=None):
    """PositionEvals for *sans*; white_evals[i] is the eval after ply i+1 from White's side."""
    board = chess.Board()
    evals = []
    for ply, san in enumerate(sans, start=1):
        color = board.turn
        move = board.parse_san(san)
        board.push(move)
        white_eval = (white_evals or {}).get(ply, 0)
        evals.append(PositionEval(ply=ply, move_san=san, color=color, cp_before=0,
                                  cp_after=white_eval if color == chess.WHITE else -white_eval,
                                  fen_after=board.fen()))
    return evals


def test_prefixes_cover_every_step_of_every_line():
    assert ("e4",) in PREFIXES
    assert ("e4", "e5", "Nf3") in PREFIXES
    assert ("e4", "e5", "Nf3", "Nc6", "Bb5") in PREFIXES
    assert ("d4",) not in PREFIXES


def test_the_player_leaving_theory():
    # Ruy Lopez book to 2...Nc6, then White (the player) plays 3. Bc4.
    evals = _evals(["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"], {5: 20})

    exit_ = theory_exit(evals, chess.WHITE, PREFIXES)

    assert (exit_.book_plies, exit_.left_by, exit_.exit_move, exit_.exit_san) == (4, "you", 3, "Bc4")
    assert exit_.eval_at_exit == 20


def test_the_opponent_leaving_theory_seen_from_black():
    evals = _evals(["e4", "e5", "Qh5", "Nc6"], {3: -30})

    exit_ = theory_exit(evals, chess.BLACK, PREFIXES)

    assert (exit_.book_plies, exit_.left_by) == (2, "opponent")
    assert exit_.eval_at_exit == 30  # White's -30 is +30 for Black


def test_eval_later_is_taken_after_plies_or_at_the_end():
    sans = ["e4", "e5", "a3"] + ["Nf6", "Nf3", "Ng8", "Ng1"] * 6
    evals = _evals(sans, {3: -10, 3 + AFTER_PLIES: -150})

    exit_ = theory_exit(evals, chess.WHITE, PREFIXES)
    assert (exit_.eval_at_exit, exit_.eval_later) == (-10, -150)

    short = theory_exit(_evals(["e4", "e5", "a3", "a6"], {3: -10, 4: -40}), chess.WHITE, PREFIXES)
    assert short.eval_later == -40


def test_evals_are_clamped():
    exit_ = theory_exit(_evals(["e4", "e5", "a3"], {3: -99_990}), chess.WHITE, PREFIXES)

    assert exit_.eval_at_exit == -1000


def test_a_game_that_never_leaves_theory():
    exit_ = theory_exit(_evals(["e4", "c5"]), chess.WHITE, PREFIXES)

    assert (exit_.book_plies, exit_.left_by, exit_.eval_at_exit) == (2, None, None)


def test_a_first_move_outside_theory():
    exit_ = theory_exit(_evals(["d4", "d5"]), chess.WHITE, PREFIXES)

    assert (exit_.book_plies, exit_.left_by, exit_.exit_move) == (0, "you", 1)


def test_no_exit_for_games_from_a_set_up_position():
    evals = _evals(["e4"])
    evals[0].fen_before = "8/8/8/8/8/8/8/K6k w - - 0 1"

    assert theory_exit(evals, chess.WHITE, PREFIXES, custom_start=True) is None


class _Game:
    def __init__(self, opening, theory):
        self.opening_name = opening
        self.eco = "X"
        self.theory = theory


def _exit(book, left_by, at=0, later=0):
    from chesswhisper.theory import TheoryExit

    return TheoryExit(book_plies=book, left_by=left_by, exit_move=book // 2 + 1, eval_at_exit=at, eval_later=later)


def test_theory_stats_per_opening():
    games = [
        _Game("Ruy Lopez", _exit(8, "you", at=20, later=-80)),
        _Game("Ruy Lopez", _exit(12, "opponent", at=50, later=150)),
        _Game("Sicilian Defense", _exit(4, "you", at=0, later=-200)),
        _Game("Sicilian Defense", None),
    ]

    stats = theory_stats(games)

    ruy, sicilian = stats
    assert (ruy.name, ruy.games, ruy.you_left, ruy.opponent_left) == ("Ruy Lopez", 2, 1, 1)
    assert ruy.avg_book_moves == pytest.approx(5.0)
    assert ruy.avg_eval_at_exit == pytest.approx(35.0)
    assert ruy.avg_change == pytest.approx(0.0)
    assert (sicilian.games, sicilian.avg_change) == (1, -200)


def test_theory_stats_ignores_games_still_in_book_for_evals():
    stats = theory_stats([_Game("Sicilian Defense", _exit(2, None, at=None, later=None))])

    [row] = stats
    assert (row.games, row.you_left, row.opponent_left, row.avg_eval_at_exit, row.avg_change) == (1, 0, 0, None, None)


def test_variations_are_grouped_by_opening_family():
    games = [
        _Game("French Defense: Winawer Variation", _exit(8, "you", at=0, later=-100)),
        _Game("French Defense: Rubinstein Variation", _exit(10, "opponent", at=0, later=100)),
        _Game("French Defense", _exit(4, "you", at=0, later=0)),
    ]

    [row] = theory_stats(games)

    assert (row.name, row.games, row.you_left, row.opponent_left) == ("French Defense", 3, 2, 1)

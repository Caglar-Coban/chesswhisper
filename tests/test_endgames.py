import chess
import pytest

from chesswhisper.endgames import endgame_entry, endgame_stats, endgame_type
from chesswhisper.engine_analyzer import PositionEval

MIDDLEGAME = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
ROOK_ENDING = "8/5pk1/8/8/8/8/5PK1/3R4 w - - 0 40"


@pytest.mark.parametrize(
    ("fen", "kind"),
    [
        ("8/5pk1/8/8/8/8/5PK1/8 w - - 0 1", "pawn"),
        ("3r4/5pk1/8/8/8/8/5PK1/3R4 w - - 0 1", "rook"),
        ("3q4/5pk1/8/8/8/8/5PK1/3Q4 w - - 0 1", "queen"),
        ("3b4/5pk1/8/8/8/8/5PK1/3N4 w - - 0 1", "minor"),
        ("3r4/5pk1/8/8/8/8/5PK1/3N4 w - - 0 1", "rook_minor"),
        ("3q4/5pk1/8/8/8/8/5PK1/3N4 w - - 0 1", "mixed"),
    ],
)
def test_endgame_type(fen, kind):
    assert endgame_type(chess.Board(fen)) == kind


def _ev(ply, color, fen_after, cp_after, cp_before=None):
    return PositionEval(ply=ply, move_san="x", color=color, cp_before=cp_after if cp_before is None else cp_before,
                        cp_after=cp_after, fen_after=fen_after)


def test_entry_is_the_first_endgame_position():
    evals = [
        _ev(40, chess.WHITE, MIDDLEGAME, 50),
        _ev(41, chess.BLACK, ROOK_ENDING, -250),  # Black's move: +250 for White
        _ev(42, chess.WHITE, ROOK_ENDING, 200, cp_before=250),  # White gives back 50
        _ev(43, chess.BLACK, ROOK_ENDING, -200),
    ]

    entry = endgame_entry(evals, chess.WHITE, "win")

    assert (entry.kind, entry.move_number, entry.eval_at_entry, entry.expectation) == ("rook", 40, 250, "winning")
    assert entry.acpl == pytest.approx(50.0)
    assert entry.result == "win"


def test_no_entry_for_a_game_that_never_reaches_an_endgame():
    assert endgame_entry([_ev(40, chess.WHITE, MIDDLEGAME, 0)], chess.WHITE, "draw") is None


def test_expectation_from_the_players_side():
    evals = [_ev(41, chess.WHITE, ROOK_ENDING, -30), _ev(42, chess.BLACK, ROOK_ENDING, 400)]

    assert endgame_entry(evals, chess.BLACK, "loss").expectation == "level"
    assert endgame_entry(evals[1:], chess.BLACK, "loss").expectation == "winning"
    assert endgame_entry(evals[1:], chess.WHITE, "win").expectation == "losing"


class _Game:
    def __init__(self, endgame):
        self.endgame = endgame


def _entry(kind, expectation, result, acpl=20.0):
    from chesswhisper.endgames import EndgameEntry

    return EndgameEntry(kind=kind, move_number=40, eval_at_entry=0, expectation=expectation, result=result, acpl=acpl)


def test_endgame_stats_count_conversions_holds_and_saves():
    games = [
        _Game(_entry("rook", "winning", "win", acpl=10)),
        _Game(_entry("rook", "winning", "draw", acpl=30)),
        _Game(_entry("rook", "level", "draw")),
        _Game(_entry("rook", "losing", "draw")),
        _Game(_entry("rook", "losing", "unknown")),
        _Game(_entry("pawn", "level", "loss")),
        _Game(None),
    ]

    stats = endgame_stats(games)

    pawn, rook = sorted(stats, key=lambda s: s.kind)
    assert (rook.games, rook.winning, rook.converted, rook.level, rook.held, rook.losing, rook.saved) == (5, 2, 1, 1, 1, 1, 1)
    assert rook.acpl == pytest.approx(20.0)
    assert (pawn.games, pawn.level, pawn.held) == (1, 1, 0)
    assert [s.kind for s in stats] == ["pawn", "rook"]  # ENDGAME_TYPES order

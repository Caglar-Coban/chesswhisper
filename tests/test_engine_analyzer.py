import chess
import chess.engine
import chess.pgn
import io

from chesswhisper.engine_analyzer import analyze_game, parse_clock_seconds, PositionEval


class FakeEngine:
    """Returns a queued sequence of PovScore-like objects, ignoring the board/limit."""

    def __init__(self, scores: list[int]):
        # scores are plain White-relative centipawns, one per analyse() call
        self._scores = iter(scores)

    def analyse(self, board, limit):
        cp = next(self._scores)
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def _parse(pgn_text: str) -> chess.pgn.Game:
    return chess.pgn.read_game(io.StringIO(pgn_text))


SIMPLE_GAME = "1. e4 e5 2. Nf3 *"
GAME_WITH_CLOCK = "1. e4 {[%clk 0:09:58]} e5 {[%clk 0:00:25]} 2. Nf3 {[%clk 0:09:40]} *"


def test_analyze_game_returns_one_eval_per_ply():
    game = _parse(SIMPLE_GAME)
    # initial eval, after e4, after e5, after Nf3
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    assert len(evals) == 3
    assert [e.move_san for e in evals] == ["e4", "e5", "Nf3"]
    assert evals[0].color == chess.WHITE
    assert evals[1].color == chess.BLACK
    assert evals[2].color == chess.WHITE


def test_analyze_game_computes_cp_from_movers_perspective():
    game = _parse(SIMPLE_GAME)
    # White plays e4: eval goes from 0 (White's view) to 30 (White's view) -> mover gained, cp_loss 0
    # Black plays e5: eval was 30 (White's view) = -30 (Black's view);
    #   after e5 eval is 20 (White's view) = -20 (Black's view) -> Black IMPROVED from -30 to -20, cp_loss 0
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    e4_eval = evals[0]
    assert e4_eval.cp_before == 0
    assert e4_eval.cp_after == 30

    e5_eval = evals[1]
    assert e5_eval.cp_before == -30
    assert e5_eval.cp_after == -20


def test_analyze_game_records_fen_after():
    game = _parse(SIMPLE_GAME)
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    board = chess.Board()
    board.push_san("e4")
    assert evals[0].fen_after == board.fen()


def test_analyze_game_clock_seconds_none_without_comment():
    game = _parse(SIMPLE_GAME)
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    assert all(e.clock_seconds is None for e in evals)


def test_analyze_game_extracts_clock_seconds():
    game = _parse(GAME_WITH_CLOCK)
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    assert evals[0].clock_seconds == 598.0
    assert evals[1].clock_seconds == 25.0
    assert evals[2].clock_seconds == 580.0


def test_parse_clock_seconds_various_formats():
    assert parse_clock_seconds("[%clk 0:05:23]") == 323.0
    assert parse_clock_seconds("[%clk 1:00:00]") == 3600.0
    assert parse_clock_seconds("no clock here") is None
    assert parse_clock_seconds("") is None

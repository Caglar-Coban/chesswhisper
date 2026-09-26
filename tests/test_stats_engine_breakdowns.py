import io

import chess.pgn
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import LoadedGame
from chesswhisper.stats_engine import (
    EVAL_STATES,
    MOVE_RANGES,
    build_aggregate_report,
    build_game_report,
    time_class,
)

ECO = load_eco_table()


@pytest.mark.parametrize(
    "header, expected",
    [
        ("60", "bullet"),
        ("120+1", "bullet"),
        ("180+2", "blitz"),
        ("300", "blitz"),
        ("600", "rapid"),
        ("900+10", "rapid"),
        ("1800", "classical"),
        ("-", "correspondence"),
        ("1/259200", "correspondence"),
        (None, "unknown"),
        ("?", "unknown"),
        ("garbage", "unknown"),
    ],
)
def test_time_class_follows_lichess_estimated_duration(header, expected):
    assert time_class(header) == expected


def _game(index, white, black, result, headers=None):
    extra = "".join(f'[{k} "{v}"]\n' for k, v in (headers or {}).items())
    text = f'[White "{white}"]\n[Black "{black}"]\n[Result "{result}"]\n{extra}\n1. e4 e5 {result}\n'
    game = chess.pgn.read_game(io.StringIO(text))
    return LoadedGame(index=index, game=game, headers=dict(game.headers))


def _evals(white_loss=0, white_before=0):
    """Two plies from the start; White's move loses *white_loss* cp from *white_before*."""
    board = chess.Board()
    fen0 = board.fen()
    board.push_san("e4")
    fen1 = board.fen()
    board.push_san("e5")
    return [
        PositionEval(ply=1, move_san="e4", color=True, cp_before=white_before,
                     cp_after=white_before - white_loss, fen_after=fen1, fen_before=fen0, move_uci="e2e4"),
        PositionEval(ply=2, move_san="e5", color=False, cp_before=0, cp_after=0,
                     fen_after=board.fen(), fen_before=fen1, move_uci="e7e5"),
    ]


def test_game_report_reads_time_class_and_ratings():
    loaded = _game(0, "Alice", "Bob", "1-0", {"TimeControl": "180+2", "WhiteElo": "1500", "BlackElo": "1620"})

    report = build_game_report(loaded, _evals(), "Alice", ECO)

    assert report.time_class == "blitz"
    assert report.your_elo == 1500
    assert report.opponent_elo == 1620


def test_game_report_treats_unknown_ratings_as_none():
    loaded = _game(0, "Alice", "Bob", "1-0", {"WhiteElo": "?"})

    report = build_game_report(loaded, _evals(), "Alice", ECO)

    assert report.your_elo is None and report.opponent_elo is None


def test_game_report_records_each_of_your_moves():
    loaded = _game(0, "Alice", "Bob", "1-0")

    report = build_game_report(loaded, _evals(white_loss=150, white_before=250), "Alice", ECO)

    assert len(report.moves) == 1
    move = report.moves[0]
    assert move.move_number == 1
    assert move.eval_before == 250
    assert move.cp_loss == 150
    assert move.classification == "mistake"


def _aggregate(specs):
    """specs: (time_control, your_elo, opp_elo, result, white_loss, white_before) per game, Alice is White."""
    reports = []
    for i, (tc, you, opp, result, loss, before) in enumerate(specs):
        headers = {"TimeControl": tc}
        if you is not None:
            headers["WhiteElo"] = str(you)
        if opp is not None:
            headers["BlackElo"] = str(opp)
        reports.append(build_game_report(_game(i, "Alice", "Bob", result, headers), _evals(loss, before), "Alice", ECO))
    return build_aggregate_report("Alice", reports)


def test_aggregate_breaks_results_down_by_time_class():
    agg = _aggregate([
        ("60", None, None, "1-0", 0, 0),
        ("60", None, None, "0-1", 400, 0),
        ("600", None, None, "1/2-1/2", 0, 0),
    ])

    by_label = {seg.label: seg for seg in agg.by_time_class}
    assert list(by_label) == ["bullet", "rapid"]  # fastest first, empty classes left out
    assert (by_label["bullet"].games, by_label["bullet"].wins, by_label["bullet"].losses) == (2, 1, 1)
    assert by_label["bullet"].acpl == pytest.approx(200.0)
    assert by_label["rapid"].draws == 1


def test_aggregate_breaks_results_down_by_opponent_strength():
    agg = _aggregate([
        ("180", 1500, 1650, "0-1", 0, 0),   # stronger
        ("180", 1500, 1550, "1-0", 0, 0),   # similar
        ("180", 1500, 1350, "1-0", 0, 0),   # weaker
        ("180", 1500, None, "1-0", 0, 0),   # unrated opponent: left out
    ])

    by_label = {seg.label: seg for seg in agg.by_opponent_strength}
    assert list(by_label) == ["stronger", "similar", "weaker"]
    assert [seg.games for seg in agg.by_opponent_strength] == [1, 1, 1]
    assert by_label["stronger"].losses == 1


def test_aggregate_counts_errors_by_move_range_and_eval_state():
    agg = _aggregate([
        ("180", None, None, "1-0", 150, 300),   # a mistake while winning, on move 1
        ("180", None, None, "1-0", 0, 0),       # a fine move while equal
    ])

    ranges = {r.label: r for r in agg.move_ranges}
    assert [r.label for r in agg.move_ranges] == list(MOVE_RANGES_LABELS)
    assert ranges["1-10"].moves == 2 and ranges["1-10"].errors == 1
    assert ranges["11-20"].moves == 0

    states = {s.label: s for s in agg.eval_states}
    assert [s.label for s in agg.eval_states] == list(EVAL_STATES)
    assert states["winning"].moves == 1 and states["winning"].errors == 1
    assert states["equal"].moves == 1 and states["equal"].errors == 0
    assert states["winning"].error_rate == pytest.approx(1.0)
    assert states["losing"].error_rate is None


MOVE_RANGES_LABELS = [label for label, _, _ in MOVE_RANGES]

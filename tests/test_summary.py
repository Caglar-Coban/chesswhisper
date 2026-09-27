import pytest

from chesswhisper.chances import ChanceStats
from chesswhisper.endgames import EndgameStats
from chesswhisper.stats_engine import (
    PHASES,
    AggregateReport,
    ErrorCounts,
    MotifStat,
    TimeManagementStats,
)
from chesswhisper.summary import compare, report_summary


class _Game:
    def __init__(self, result):
        self.result = result


def _report():
    errors = {p: ErrorCounts() for p in PHASES}
    errors["opening"].inaccuracy = 2
    errors["middlegame"].mistake = 3
    errors["middlegame"].blunder = 1
    errors["endgame"].blunder = 2
    return AggregateReport(
        player="Alice", games=[_Game("win"), _Game("draw"), _Game("loss"), _Game("unknown")],
        overall_acpl=42.0, overall_accuracy=88.5, error_totals=errors, openings=[],
        time_management=TimeManagementStats(available=False),
        motifs=[MotifStat("fork", allowed=2, missed=1), MotifStat("hanging", allowed=2), MotifStat("other", unsided=5)],
        chances=ChanceStats(total=4, punished=3, missed_by_motif=[]),
        endgames=[EndgameStats("rook", winning=2, converted=1), EndgameStats("pawn", winning=0)],
    )


def test_report_summary():
    summary = report_summary(_report())

    assert summary == {
        "games": 4,
        "accuracy": 88.5,
        "acpl": 42.0,
        "score": pytest.approx(0.5),
        "inaccuracies_per_game": 0.5,
        "mistakes_per_game": 0.75,
        "blunders_per_game": 0.75,
        "opening_errors_per_game": 0.0,
        "middlegame_errors_per_game": 1.0,
        "endgame_errors_per_game": 0.5,
        "allowed_tactics_per_game": 1.0,
        "missed_tactics_per_game": 0.25,
        "punish_rate": 0.75,
        "conversion_rate": 0.5,
    }


def test_summary_without_results_chances_or_winning_endgames():
    report = _report()
    report.games = [_Game("unknown")]
    report.chances = ChanceStats(total=0, punished=0, missed_by_motif=[])
    report.endgames = []

    summary = report_summary(report)

    assert (summary["score"], summary["punish_rate"], summary["conversion_rate"]) == (None, None, None)


def test_compare_says_which_way_each_metric_moved():
    before = {"games": 10, "accuracy": 86.0, "acpl": 50.0, "blunders_per_game": 0.8, "punish_rate": 0.6}
    after = {"games": 12, "accuracy": 89.0, "acpl": 55.0, "blunders_per_game": 0.8, "punish_rate": None}

    rows = {row.key: row for row in compare(before, after)}

    assert rows["games"].verdict is None  # no better or worse
    assert (rows["accuracy"].verdict, rows["accuracy"].change) == ("better", 3.0)
    assert rows["acpl"].verdict == "worse"
    assert rows["blunders_per_game"].verdict is None  # unchanged
    assert (rows["punish_rate"].verdict, rows["punish_rate"].change) == (None, None)
    assert "score" not in rows  # neither side has it
    assert [row.key for row in compare(before, after)][:3] == ["games", "accuracy", "acpl"]


def test_tiny_changes_are_no_change():
    rows = {row.key: row for row in compare({"accuracy": 88.01, "score": 0.501}, {"accuracy": 88.04, "score": 0.503})}

    assert rows["accuracy"].verdict is None
    assert rows["score"].verdict is None

from dataclasses import dataclass
from datetime import datetime

from chesswhisper.web.trend import MIN_POINTS, Axis, build_trend, nice_axis


@dataclass
class _Record:
    id: int
    player_name: str
    created_at: datetime
    overall_accuracy: float
    overall_acpl: float
    games_count: int = 5


def _rec(id, player, day, accuracy=80.0, acpl=40.0):
    return _Record(id, player, datetime(2026, 9, day, 12, 0), accuracy, acpl)


def test_no_records_means_no_trend():
    assert build_trend([], None) is None


def test_players_are_ordered_by_analysis_count_then_recency():
    records = [
        _rec(1, "alice", 1),
        _rec(2, "bob", 2),
        _rec(3, "bob", 3),
        _rec(4, "carol", 5),
        _rec(5, "alice", 4),
    ]

    trend = build_trend(records, None)

    # alice and bob tie at 2; bob's latest analysis (day 3) is older than alice's (day 4).
    assert trend.players == ["alice", "bob", "carol"]
    assert trend.selected == "alice"


def test_requested_player_is_selected_when_known():
    records = [_rec(1, "alice", 1), _rec(2, "alice", 2), _rec(3, "bob", 3)]

    assert build_trend(records, "bob").selected == "bob"


def test_unknown_requested_player_falls_back_to_default():
    records = [_rec(1, "alice", 1), _rec(2, "alice", 2), _rec(3, "bob", 3)]

    assert build_trend(records, "mallory").selected == "alice"


def test_points_are_only_the_selected_players_in_chronological_order():
    records = [
        _rec(3, "alice", 9, accuracy=90.0, acpl=20.0),
        _rec(2, "bob", 5),
        _rec(1, "alice", 2, accuracy=70.0, acpl=60.0),
    ]

    trend = build_trend(records, "alice")

    assert [p["id"] for p in trend.points] == [1, 3]
    assert trend.points[0] == {
        "id": 1,
        "date": "2026-09-02",
        "label": "02.09.2026",
        "iso": "2026-09-02T12:00:00Z",
        "accuracy": 70.0,
        "acpl": 60.0,
        "games": 5,
    }


def test_has_enough_points_needs_at_least_min_points():
    one = build_trend([_rec(1, "alice", 1)], None)
    two = build_trend([_rec(1, "alice", 1), _rec(2, "alice", 2)], None)

    assert MIN_POINTS == 2
    assert one.has_enough_points is False
    assert two.has_enough_points is True


def test_axes_cover_the_selected_players_values():
    records = [_rec(1, "alice", 1, accuracy=72.4, acpl=35.0), _rec(2, "alice", 2, accuracy=88.1, acpl=61.0)]

    trend = build_trend(records, None)

    assert trend.accuracy_axis.low <= 72.4 and trend.accuracy_axis.high >= 88.1
    assert trend.acpl_axis.low <= 35.0 and trend.acpl_axis.high >= 61.0


def test_nice_axis_uses_round_ticks_that_span_the_data():
    axis = nice_axis([72.4, 88.1])

    assert axis == Axis(low=70.0, high=90.0, ticks=[70.0, 75.0, 80.0, 85.0, 90.0])


def test_nice_axis_pads_a_flat_series_instead_of_dividing_by_zero():
    axis = nice_axis([50.0, 50.0])

    assert axis.low < 50.0 < axis.high
    assert axis.ticks[0] == axis.low and axis.ticks[-1] == axis.high


def test_nice_axis_respects_bounds():
    accuracy = nice_axis([97.0, 99.5], lower_bound=0.0, upper_bound=100.0)
    acpl = nice_axis([0.5, 3.0], lower_bound=0.0)

    assert accuracy.high == 100.0
    assert acpl.low == 0.0
    assert all(0.0 <= t <= 100.0 for t in accuracy.ticks)

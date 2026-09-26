from chesswhisper.insights import key_findings, render_finding
from chesswhisper.stats_engine import (
    AggregateReport,
    PatternBucket,
    SegmentStats,
    TimeManagementStats,
    _TimeBuckets,
)


class _Game:
    def __init__(self, buckets):
        self.time_buckets = buckets


def _agg(move_ranges=(), eval_states=(), by_time_class=(), time_buckets=None):
    return AggregateReport(
        player="Alice", games=[_Game(time_buckets or _TimeBuckets())], overall_acpl=30.0, overall_accuracy=85.0,
        error_totals={}, openings=[], time_management=TimeManagementStats(available=False),
        by_time_class=list(by_time_class), move_ranges=list(move_ranges), eval_states=list(eval_states),
    )


def test_no_findings_without_enough_data():
    assert key_findings(_agg()) == []


def test_flags_the_move_range_where_errors_cluster():
    agg = _agg(move_ranges=[
        PatternBucket("1-10", moves=40, errors=1),
        PatternBucket("11-20", moves=40, errors=2),
        PatternBucket("21-30", moves=40, errors=9),
    ])

    findings = key_findings(agg)

    assert findings[0] == ("move_range", {"range": "21-30", "rate": 22, "overall": 10})


def test_ignores_a_hotspot_built_on_too_few_moves():
    agg = _agg(move_ranges=[PatternBucket("1-10", moves=60, errors=3), PatternBucket("41+", moves=4, errors=3)])

    assert key_findings(agg) == []


def test_flags_the_eval_state_with_the_most_errors():
    agg = _agg(eval_states=[
        PatternBucket("winning", moves=30, errors=6),
        PatternBucket("equal", moves=60, errors=3),
        PatternBucket("losing", moves=20, errors=1),
    ])

    assert ("eval_state", {"state": "winning", "rate": 20, "overall": 9}) in key_findings(agg)


def test_flags_time_pressure_when_errors_jump_under_the_clock():
    buckets = _TimeBuckets(low_total=20, low_errors=8, normal_total=100, normal_errors=10)

    assert ("time_pressure", {"low": 40, "normal": 10}) in key_findings(_agg(time_buckets=buckets))


def test_flags_a_clear_gap_between_time_controls():
    agg = _agg(by_time_class=[
        SegmentStats("bullet", games=5, accuracy=78.0),
        SegmentStats("rapid", games=4, accuracy=90.5),
    ])

    assert ("time_class", {"best": "rapid", "best_acc": 90.5, "worst": "bullet", "worst_acc": 78.0}) in key_findings(agg)


def test_renders_findings_in_both_languages():
    finding = ("eval_state", {"state": "winning", "rate": 20, "overall": 9})

    assert "20%" in render_finding(finding, "en") and "ahead" in render_finding(finding, "en")
    assert "%20" in render_finding(finding, "tr") and "öndeyken" in render_finding(finding, "tr")

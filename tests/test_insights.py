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


def _with_motifs(*stats):
    agg = _agg()
    agg.motifs = list(stats)
    return agg


def test_flags_the_motif_behind_a_large_share_of_mistakes():
    from chesswhisper.stats_engine import MotifStat

    agg = _with_motifs(MotifStat("hanging", allowed=6, missed=1), MotifStat("fork", missed=2), MotifStat("other", unsided=6))

    finding = ("motif_allowed", {"motif": "hanging", "count": 6, "total": 15})
    assert finding in key_findings(agg)
    assert render_finding(finding, "en") == "In 6 of your 15 mistakes and blunders you allowed a hanging piece."
    assert render_finding(finding, "tr") == "15 hata ve gafının 6 tanesi rakibine korumasız taşı alma fırsatı verdi."


def test_flags_a_missed_motif():
    from chesswhisper.stats_engine import MotifStat

    agg = _with_motifs(MotifStat("fork", allowed=1, missed=5), MotifStat("other", unsided=4))

    finding = ("motif_missed", {"motif": "fork", "count": 5, "total": 10})
    assert finding in key_findings(agg)
    assert render_finding(finding, "en") == "In 5 of your 10 mistakes and blunders you missed a fork."


def test_no_motif_finding_below_the_thresholds():
    from chesswhisper.stats_engine import MotifStat

    too_few = _with_motifs(MotifStat("fork", allowed=4))
    too_small_a_share = _with_motifs(MotifStat("fork", allowed=5), MotifStat("other", unsided=20))
    only_other = _with_motifs(MotifStat("other", unsided=20))

    for agg in (too_few, too_small_a_share, only_other):
        assert not [f for f in key_findings(agg) if f[0].startswith("motif")]


def _theory_row(name, left, change):
    from chesswhisper.theory import TheoryStats

    return TheoryStats(name=name, eco="X", games=left, you_left=left, opponent_left=0, avg_book_moves=4.0,
                       avg_eval_at_exit=0.0, avg_change=change)


def test_flags_an_opening_where_leaving_theory_goes_badly():
    agg = _agg()
    agg.opening_theory = [_theory_row("Ruy Lopez", 3, -200.0), _theory_row("Sicilian Defense", 4, -160.0)]

    finding = ("theory_opening", {"opening": "Ruy Lopez", "pawns": "2.0", "n": 3})
    assert finding in key_findings(agg)
    assert render_finding(finding, "en").startswith("In the Ruy Lopez you are 2.0 pawns worse")


def test_flags_an_overall_drop_after_theory():
    agg = _agg()
    agg.opening_theory = [_theory_row("Ruy Lopez", 2, -140.0), _theory_row("Sicilian Defense", 2, -100.0)]

    assert ("theory_drop", {"pawns": "1.2", "n": 4}) in key_findings(agg)


def test_no_theory_finding_for_small_or_mild_drops():
    few = _agg()
    few.opening_theory = [_theory_row("Ruy Lopez", 2, -400.0)]
    mild = _agg()
    mild.opening_theory = [_theory_row("Ruy Lopez", 5, -50.0)]

    for agg in (few, mild):
        assert not [f for f in key_findings(agg) if f[0].startswith("theory")]


def test_flags_unpunished_opponent_errors():
    from chesswhisper.chances import ChanceStats

    agg = _agg()
    agg.chances = ChanceStats(total=8, punished=3, missed_by_motif=[("other", 5)])

    finding = ("chances", {"punished": 3, "total": 8})
    assert finding in key_findings(agg)
    assert render_finding(finding, "en").startswith("You punished only 3 of your opponents' 8 mistakes")


def test_no_chances_finding_when_most_are_punished_or_too_few():
    from chesswhisper.chances import ChanceStats

    for stats in (ChanceStats(total=8, punished=5, missed_by_motif=[]), ChanceStats(total=4, punished=0, missed_by_motif=[])):
        agg = _agg()
        agg.chances = stats
        assert not [f for f in key_findings(agg) if f[0] == "chances"]


def test_flags_poor_conversion_of_winning_endgames():
    from chesswhisper.endgames import EndgameStats

    agg = _agg()
    agg.endgames = [EndgameStats("rook", games=4, winning=3, converted=1), EndgameStats("pawn", games=1, winning=1, converted=1)]

    finding = ("conversion", {"converted": 2, "winning": 4})
    assert finding in key_findings(agg)
    assert render_finding(finding, "en") == "You converted only 2 of 4 winning endgames: endgame technique would win you points."


def test_no_conversion_finding_when_converting_well_or_too_few():
    from chesswhisper.endgames import EndgameStats

    for rows in ([EndgameStats("rook", winning=5, converted=4)], [EndgameStats("rook", winning=2, converted=0)]):
        agg = _agg()
        agg.endgames = rows
        assert not [f for f in key_findings(agg) if f[0] == "conversion"]

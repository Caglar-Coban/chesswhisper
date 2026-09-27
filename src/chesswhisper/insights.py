"""Plain-language findings drawn from an aggregate report's breakdowns.

Each finding fires only when the data behind it is large enough and the gap
clear enough to be worth a player's attention; the thresholds below keep a
handful of games from producing confident-sounding noise.
"""

from __future__ import annotations

from chesswhisper.i18n import t
from chesswhisper.motifs import OTHER
from chesswhisper.stats_engine import AggregateReport, PatternBucket, _TimeBuckets

MIN_BUCKET_MOVES = 15
MIN_LOW_TIME_MOVES = 10
MIN_SEGMENT_GAMES = 3
HOTSPOT_RATIO = 1.5
ACCURACY_GAP = 5.0
MIN_MOTIF_ERRORS = 5
MOTIF_SHARE = 0.3
MIN_THEORY_GAMES = 3
MIN_CHANCES = 5
MIN_WINNING_ENDGAMES = 3
CONVERSION_FLOOR = 0.6
PUNISH_RATE_FLOOR = 0.5
THEORY_DROP_CP = 100
THEORY_OPENING_DROP_CP = 150

Finding = tuple[str, dict]


def _pct(value: float) -> int:
    return round(value * 100)


def _hotspot(buckets: list[PatternBucket]) -> tuple[PatternBucket, float] | None:
    """The bucket whose error rate stands out from the overall rate, if one does."""
    moves = sum(b.moves for b in buckets)
    errors = sum(b.errors for b in buckets)
    if not moves or not errors:
        return None
    overall = errors / moves
    candidates = [b for b in buckets if b.moves >= MIN_BUCKET_MOVES and b.error_rate is not None]
    if not candidates:
        return None
    worst = max(candidates, key=lambda b: b.error_rate or 0.0)
    if (worst.error_rate or 0.0) < overall * HOTSPOT_RATIO:
        return None
    return worst, overall


def key_findings(report: AggregateReport) -> list[Finding]:
    findings: list[Finding] = []

    hotspot = _hotspot(report.move_ranges)
    if hotspot is not None:
        bucket, overall = hotspot
        findings.append(("move_range", {"range": bucket.label, "rate": _pct(bucket.error_rate or 0.0),
                                        "overall": _pct(overall)}))

    hotspot = _hotspot(report.eval_states)
    if hotspot is not None:
        bucket, overall = hotspot
        findings.append(("eval_state", {"state": bucket.label, "rate": _pct(bucket.error_rate or 0.0),
                                        "overall": _pct(overall)}))

    clock = _TimeBuckets()
    for game in report.games:
        clock.merge(game.time_buckets)
    if clock.low_total >= MIN_LOW_TIME_MOVES and clock.normal_total:
        low, normal = clock.low_errors / clock.low_total, clock.normal_errors / clock.normal_total
        if low >= normal * HOTSPOT_RATIO and low > normal:
            findings.append(("time_pressure", {"low": _pct(low), "normal": _pct(normal)}))

    segments = [s for s in report.by_time_class if s.games >= MIN_SEGMENT_GAMES and s.label != "unknown"]
    if len(segments) >= 2:
        best = max(segments, key=lambda s: s.accuracy)
        worst = min(segments, key=lambda s: s.accuracy)
        if best.accuracy - worst.accuracy >= ACCURACY_GAP:
            findings.append(("time_class", {"best": best.label, "best_acc": round(best.accuracy, 1),
                                            "worst": worst.label, "worst_acc": round(worst.accuracy, 1)}))

    motif = _motif_finding(report)
    if motif is not None:
        findings.append(motif)

    findings.extend(_theory_findings(report))

    winning = sum(row.winning for row in report.endgames)
    converted = sum(row.converted for row in report.endgames)
    if winning >= MIN_WINNING_ENDGAMES and converted / winning < CONVERSION_FLOOR:
        findings.append(("conversion", {"converted": converted, "winning": winning}))

    chances = report.chances
    if chances.total >= MIN_CHANCES and (chances.punish_rate or 0) < PUNISH_RATE_FLOOR:
        findings.append(("chances", {"punished": chances.punished, "total": chances.total}))

    return findings


def _pawns(cp: float) -> str:
    return f"{abs(cp) / 100:.1f}"


def _theory_findings(report: AggregateReport) -> list[Finding]:
    """A drop after leaving theory: in one opening if one stands out, else overall."""
    rows = [row for row in report.opening_theory if row.avg_change is not None]
    standouts = [
        row for row in rows
        if row.you_left + row.opponent_left >= MIN_THEORY_GAMES and (row.avg_change or 0) <= -THEORY_OPENING_DROP_CP
    ]
    if standouts:
        worst = min(standouts, key=lambda row: row.avg_change or 0)
        return [("theory_opening", {"opening": worst.name, "pawns": _pawns(worst.avg_change or 0),
                                    "n": worst.you_left + worst.opponent_left})]

    games = sum(row.you_left + row.opponent_left for row in rows)
    if games < MIN_THEORY_GAMES:
        return []
    average = sum((row.avg_change or 0) * (row.you_left + row.opponent_left) for row in rows) / games
    if average > -THEORY_DROP_CP:
        return []
    return [("theory_drop", {"pawns": _pawns(average), "n": games})]


def _motif_finding(report: AggregateReport) -> Finding | None:
    """The one motif and side behind a large share of the player's mistakes, if there is one."""
    total = sum(stat.total for stat in report.motifs)
    candidates = [
        (count, stat.motif, side)
        for stat in report.motifs
        if stat.motif != OTHER
        for side, count in (("allowed", stat.allowed), ("missed", stat.missed))
    ]
    if not total or not candidates:
        return None
    count, motif, side = max(candidates, key=lambda c: c[0])
    if count < MIN_MOTIF_ERRORS or count / total < MOTIF_SHARE:
        return None
    return f"motif_{side}", {"motif": motif, "count": count, "total": total}


def render_finding(finding: Finding, lang: str) -> str:
    kind, params = finding
    values = dict(params)
    if kind == "eval_state":
        values["state"] = t(f"state_{params['state']}", lang)
    if kind in ("motif_allowed", "motif_missed"):
        values["motif"] = t(f"motif_{params['motif']}", lang)
    if kind == "time_class":
        values["best"] = t(f"tc_{params['best']}", lang)
        values["worst"] = t(f"tc_{params['worst']}", lang)
    return t(f"finding_{kind}", lang).format(**values)

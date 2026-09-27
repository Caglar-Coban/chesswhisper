"""A small set of numbers per analysis, kept so two analyses can be compared.

report_summary() flattens an aggregate report into plain metrics; the web UI
stores them with each analysis. compare() lines two summaries up, and says
for each metric whether the change is for the better.
"""

from __future__ import annotations

from dataclasses import dataclass

# (key, which way is better: "higher", "lower" or None, format). Formats:
# "percent" is already a percentage (accuracy), "ratio" a 0-1 share shown as
# a percentage, "num" a plain number, "int" a count.
METRICS = (
    ("games", None, "int"),
    ("accuracy", "higher", "percent"),
    ("acpl", "lower", "num"),
    ("score", "higher", "ratio"),
    ("inaccuracies_per_game", "lower", "num"),
    ("mistakes_per_game", "lower", "num"),
    ("blunders_per_game", "lower", "num"),
    ("opening_errors_per_game", "lower", "num"),
    ("middlegame_errors_per_game", "lower", "num"),
    ("endgame_errors_per_game", "lower", "num"),
    ("allowed_tactics_per_game", "lower", "num"),
    ("missed_tactics_per_game", "lower", "num"),
    ("punish_rate", "higher", "ratio"),
    ("conversion_rate", "higher", "ratio"),
)

_SCORES = {"win": 1.0, "draw": 0.5, "loss": 0.0}


def report_summary(report) -> dict[str, float | None]:
    games = len(report.games) or 1
    totals = report.error_totals
    scored = [_SCORES[game.result] for game in report.games if game.result in _SCORES]
    motifs = [stat for stat in report.motifs if stat.motif != "other"]
    winning = sum(row.winning for row in report.endgames)
    return {
        "games": len(report.games),
        "accuracy": report.overall_accuracy,
        "acpl": report.overall_acpl,
        "score": sum(scored) / len(scored) if scored else None,
        "inaccuracies_per_game": sum(c.inaccuracy for c in totals.values()) / games,
        "mistakes_per_game": sum(c.mistake for c in totals.values()) / games,
        "blunders_per_game": sum(c.blunder for c in totals.values()) / games,
        **{
            f"{phase}_errors_per_game": (totals[phase].mistake + totals[phase].blunder) / games
            for phase in ("opening", "middlegame", "endgame")
            if phase in totals
        },
        "allowed_tactics_per_game": sum(stat.allowed for stat in motifs) / games,
        "missed_tactics_per_game": sum(stat.missed for stat in motifs) / games,
        "punish_rate": report.chances.punish_rate,
        "conversion_rate": sum(row.converted for row in report.endgames) / winning if winning else None,
    }


@dataclass(frozen=True)
class Comparison:
    key: str
    before: float | None
    after: float | None
    fmt: str
    verdict: str | None  # "better" | "worse" | None (no change, no direction or missing data)

    @property
    def change(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


# Changes smaller than these are shown as no change.
_NOISE = {"percent": 0.05, "ratio": 0.005, "num": 0.005, "int": 0.5}


def compare(before: dict, after: dict) -> list[Comparison]:
    """One row per metric that at least one of the two summaries has."""
    rows = []
    for key, better, fmt in METRICS:
        a, b = before.get(key), after.get(key)
        if a is None and b is None:
            continue
        verdict = None
        if better is not None and a is not None and b is not None and abs(b - a) > _NOISE[fmt]:
            improved = b > a if better == "higher" else b < a
            verdict = "better" if improved else "worse"
        rows.append(Comparison(key=key, before=a, after=b, fmt=fmt, verdict=verdict))
    return rows

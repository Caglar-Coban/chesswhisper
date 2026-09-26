"""Data for the history page's trend chart.

Everything here is plain computation over AnalysisRecord-like objects: which
players the history contains, which one the chart shows, that player's points
in chronological order, and round-numbered axes for each metric. The page's
script only maps these numbers onto pixels.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

# A single analysis is a dot, not a trend.
MIN_POINTS = 2

_NICE_STEPS = (1.0, 2.0, 2.5, 5.0, 10.0)
_TARGET_INTERVALS = 4


class TrendRecord(Protocol):
    id: int
    player_name: str
    created_at: datetime
    overall_accuracy: float
    overall_acpl: float
    games_count: int


@dataclass(frozen=True)
class Axis:
    low: float
    high: float
    ticks: list[float]


@dataclass(frozen=True)
class Trend:
    players: list[str]
    selected: str
    points: list[dict[str, Any]]
    accuracy_axis: Axis
    acpl_axis: Axis

    @property
    def has_enough_points(self) -> bool:
        return len(self.points) >= MIN_POINTS

    def chart_data(self) -> dict[str, Any]:
        """What the page's chart script reads (serialized with Jinja's tojson)."""
        return {
            "player": self.selected,
            "points": self.points,
            "accuracy_axis": asdict(self.accuracy_axis),
            "acpl_axis": asdict(self.acpl_axis),
        }


def _nice_step(span: float) -> float:
    raw = span / _TARGET_INTERVALS
    magnitude = 10 ** math.floor(math.log10(raw))
    return next(step * magnitude for step in _NICE_STEPS if step * magnitude >= raw)


def nice_axis(
    values: Iterable[float], lower_bound: float | None = None, upper_bound: float | None = None
) -> Axis:
    """Round-numbered axis spanning *values*, clamped to the given bounds."""
    data = list(values)
    low, high = min(data), max(data)
    if high == low:
        pad = max(abs(low) * 0.1, 1.0)
        low, high = low - pad, high + pad
    step = _nice_step(high - low)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    if lower_bound is not None:
        low = max(low, lower_bound)
    if upper_bound is not None:
        high = min(high, upper_bound)
    count = round((high - low) / step)
    ticks = [round(low + i * step, 10) for i in range(count + 1)]
    if ticks[-1] < high:
        ticks.append(high)
    return Axis(low=float(low), high=float(high), ticks=ticks)


def _players_by_count(records: Sequence[TrendRecord]) -> list[str]:
    counts: dict[str, int] = {}
    latest: dict[str, datetime] = {}
    for record in records:
        counts[record.player_name] = counts.get(record.player_name, 0) + 1
        if record.player_name not in latest or record.created_at > latest[record.player_name]:
            latest[record.player_name] = record.created_at
    return sorted(counts, key=lambda name: (-counts[name], -latest[name].timestamp()))


def build_trend(records: Sequence[TrendRecord], requested_player: str | None) -> Trend | None:
    """The chart for *requested_player*, or the most-analyzed player if unknown."""
    if not records:
        return None
    players = _players_by_count(records)
    selected = requested_player if requested_player in players else players[0]
    chosen = sorted((r for r in records if r.player_name == selected), key=lambda r: r.created_at)
    points = [
        {
            "id": r.id,
            "date": r.created_at.strftime("%Y-%m-%d"),
            "label": r.created_at.strftime("%d.%m.%Y"),
            # UTC, for the page script to relabel in the viewer's time zone.
            "iso": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "accuracy": r.overall_accuracy,
            "acpl": r.overall_acpl,
            "games": r.games_count,
        }
        for r in chosen
    ]
    return Trend(
        players=players,
        selected=selected,
        points=points,
        accuracy_axis=nice_axis((r.overall_accuracy for r in chosen), lower_bound=0.0, upper_bound=100.0),
        acpl_axis=nice_axis((r.overall_acpl for r in chosen), lower_bound=0.0),
    )

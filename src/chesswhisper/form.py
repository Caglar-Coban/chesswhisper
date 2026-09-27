"""Recent form: the player's newest games against the ones before them.

Games are put in date order from their PGN headers (Lichess and chess.com
both give UTCDate and UTCTime). The newest half, up to RECENT_MAX games, is
compared with the rest. With fewer than MIN_GAMES dated games there is not
enough to compare, and a single undated game makes the order unreliable, so
there is no comparison then either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

MIN_GAMES = 10
RECENT_MAX = 20

_SCORES = {"win": 1.0, "draw": 0.5, "loss": 0.0}


def game_datetime(headers: dict) -> datetime | None:
    day = headers.get("UTCDate") or headers.get("Date")
    if not day:
        return None
    try:
        date = datetime.strptime(day, "%Y.%m.%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    try:
        clock = datetime.strptime(headers.get("UTCTime", ""), "%H:%M:%S")
    except ValueError:
        return date
    return date.replace(hour=clock.hour, minute=clock.minute, second=clock.second)


@dataclass(frozen=True)
class FormPeriod:
    games: int
    first: datetime
    last: datetime
    accuracy: float
    acpl: float
    blunders_per_game: float
    score: float | None  # points per game with a known result


@dataclass(frozen=True)
class RecentForm:
    recent: FormPeriod
    earlier: FormPeriod


def _weighted(games: list, attribute: str) -> float:
    """The per-game *attribute* of your_stats, averaged over all moves (over games if there are none)."""
    moves = sum(game.your_stats.move_count for game in games)
    if not moves:
        return sum(getattr(game.your_stats, attribute) for game in games) / len(games)
    return sum(getattr(game.your_stats, attribute) * game.your_stats.move_count for game in games) / moves


def _period(games: list) -> FormPeriod:
    scored = [_SCORES[game.result] for game in games if game.result in _SCORES]
    blunders = sum(counts.blunder for game in games for counts in game.your_stats.errors_by_phase.values())
    return FormPeriod(
        games=len(games),
        first=games[0].played_at,
        last=games[-1].played_at,
        accuracy=_weighted(games, "accuracy"),
        acpl=_weighted(games, "acpl"),
        blunders_per_game=blunders / len(games),
        score=sum(scored) / len(scored) if scored else None,
    )


def recent_form(game_reports: list) -> RecentForm | None:
    if len(game_reports) < MIN_GAMES or any(game.played_at is None for game in game_reports):
        return None
    ordered = sorted(game_reports, key=lambda game: game.played_at)
    recent_count = min(RECENT_MAX, len(ordered) // 2)
    return RecentForm(recent=_period(ordered[-recent_count:]), earlier=_period(ordered[:-recent_count]))

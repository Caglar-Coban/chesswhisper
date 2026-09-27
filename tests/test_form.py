from datetime import UTC, datetime, timedelta

import pytest

from chesswhisper.form import MIN_GAMES, RECENT_MAX, game_datetime, recent_form
from chesswhisper.stats_engine import PHASES, ErrorCounts, PlayerGameStats


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"UTCDate": "2026.04.08", "UTCTime": "19:39:03", "Date": "2026.04.07"}, datetime(2026, 4, 8, 19, 39, 3, tzinfo=UTC)),
        ({"UTCDate": "2026.04.08"}, datetime(2026, 4, 8, tzinfo=UTC)),
        ({"Date": "2026.04.08"}, datetime(2026, 4, 8, tzinfo=UTC)),
        ({"Date": "2026.??.??"}, None),
        ({"Date": "????.??.??"}, None),
        ({}, None),
        ({"UTCDate": "2026.04.08", "UTCTime": "nonsense"}, datetime(2026, 4, 8, tzinfo=UTC)),
    ],
)
def test_game_datetime(headers, expected):
    assert game_datetime(headers) == expected


class _Game:
    def __init__(self, played_at, accuracy, acpl, blunders, result, moves=30):
        errors = {p: ErrorCounts() for p in PHASES}
        errors["middlegame"].blunder = blunders
        self.your_stats = PlayerGameStats(acpl=acpl, accuracy=accuracy, errors_by_phase=errors, move_count=moves)
        self.played_at = played_at
        self.result = result


START = datetime(2026, 1, 1, tzinfo=UTC)


def _games(n, accuracy=lambda i: 90.0, result="win"):
    return [_Game(START + timedelta(days=i), accuracy(i), 30.0, 1, result) for i in range(n)]


def test_needs_enough_dated_games():
    assert recent_form(_games(MIN_GAMES - 1)) is None
    undated = _games(MIN_GAMES)
    undated[0].played_at = None
    assert recent_form(undated) is None


def test_recent_games_are_the_newest_half_up_to_the_cap():
    form = recent_form(_games(12))

    assert (form.recent.games, form.earlier.games) == (6, 6)
    assert form.recent.first == START + timedelta(days=6)
    assert form.earlier.last == START + timedelta(days=5)

    big = recent_form(_games(50))
    assert (big.recent.games, big.earlier.games) == (RECENT_MAX, 50 - RECENT_MAX)


def test_periods_are_sorted_by_date_whatever_the_input_order():
    games = _games(10, accuracy=lambda i: 80.0 if i < 5 else 95.0)

    form = recent_form(list(reversed(games)))

    assert form.recent.accuracy == pytest.approx(95.0)
    assert form.earlier.accuracy == pytest.approx(80.0)


def test_period_numbers():
    games = _games(10)
    games[9].result, games[8].result, games[7].result = "loss", "draw", "unknown"

    form = recent_form(games)

    recent = form.recent
    assert (recent.acpl, recent.blunders_per_game) == (30.0, 1.0)
    assert recent.score == pytest.approx((2 + 0.5) / 4)  # the unknown result is left out
    assert form.earlier.score == 1.0

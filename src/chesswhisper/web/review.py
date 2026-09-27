"""When to show a puzzle again: spaced repetition from the attempts already saved.

Attempts close together (within REVIEW_GAP) are one review, and a review's
result is its first answer: getting it wrong and then right on the retry
is still a miss. After a miss the puzzle is due a day later; each review in
a row answered right waits longer, following INTERVALS_DAYS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

INTERVALS_DAYS = (1, 3, 7, 14, 30)
MISS_INTERVAL = timedelta(days=1)
REVIEW_GAP = timedelta(hours=1)


def _utc(moment: datetime) -> datetime:
    # SQLite gives back naive datetimes; everything is stored in UTC.
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


@dataclass(frozen=True)
class ReviewState:
    streak: int = 0  # reviews in a row answered right
    due_at: datetime | None = None  # None: never attempted

    def status(self, now: datetime) -> str:
        if self.due_at is None:
            return "new"
        return "due" if self.due_at <= now else "upcoming"

    def days_until_due(self, now: datetime) -> int | None:
        if self.due_at is None:
            return None
        return max(0, math.ceil((self.due_at - now) / timedelta(days=1)))


def review_state(attempts: list[tuple[datetime, bool]]) -> ReviewState:
    """The review state after *attempts*, each (when, solved), in any order."""
    if not attempts:
        return ReviewState()
    ordered = sorted((_utc(when), solved) for when, solved in attempts)

    reviews: list[tuple[datetime, bool]] = []  # (first attempt time, first answer right)
    last_attempt: datetime | None = None
    for when, solved in ordered:
        if last_attempt is None or when - last_attempt > REVIEW_GAP:
            reviews.append((when, solved))
        last_attempt = when

    streak = 0
    for _, solved in reversed(reviews):
        if not solved:
            break
        streak += 1

    started = reviews[-1][0]
    if streak == 0:
        return ReviewState(streak=0, due_at=started + MISS_INTERVAL)
    interval = INTERVALS_DAYS[min(streak, len(INTERVALS_DAYS)) - 1]
    return ReviewState(streak=streak, due_at=started + timedelta(days=interval))

from datetime import UTC, datetime, timedelta

import pytest

from chesswhisper.web.review import INTERVALS_DAYS, REVIEW_GAP, review_state

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _at(days_ago=0.0, minutes=0.0):
    return NOW - timedelta(days=days_ago) + timedelta(minutes=minutes)


def test_no_attempts_is_new():
    state = review_state([])

    assert state.status(NOW) == "new"
    assert state.due_at is None
    assert state.streak == 0


def test_a_miss_is_due_again_a_day_later():
    state = review_state([(_at(days_ago=0.5), False)])

    assert state.streak == 0
    assert state.due_at == _at(days_ago=0.5) + timedelta(days=1)
    assert state.status(NOW) == "upcoming"
    assert state.status(NOW + timedelta(days=1)) == "due"


@pytest.mark.parametrize("streak", range(1, 8))
def test_each_success_in_a_row_waits_longer(streak):
    attempts = [(_at(days_ago=100 - 10 * i), True) for i in range(streak)]

    state = review_state(attempts)

    assert state.streak == streak
    interval = INTERVALS_DAYS[min(streak, len(INTERVALS_DAYS)) - 1]
    assert state.due_at == attempts[-1][0] + timedelta(days=interval)


def test_a_miss_resets_the_streak():
    state = review_state([(_at(days_ago=30), True), (_at(days_ago=20), True), (_at(days_ago=2), False)])

    assert state.streak == 0
    assert state.status(NOW) == "due"


def test_a_retry_in_the_same_sitting_does_not_count_as_a_success():
    first = _at(days_ago=3)
    state = review_state([(first, False), (first + timedelta(minutes=2), True)])

    assert state.streak == 0
    assert state.due_at == first + timedelta(days=1)


def test_attempts_far_enough_apart_are_separate_reviews():
    first = _at(days_ago=10)
    later = first + REVIEW_GAP + timedelta(minutes=1)

    state = review_state([(first, False), (later, True)])

    assert state.streak == 1
    assert state.due_at == later + timedelta(days=INTERVALS_DAYS[0])


def test_attempts_are_sorted_and_naive_times_are_utc():
    naive_old = datetime(2026, 9, 1, 12, 0)
    aware_new = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    state = review_state([(aware_new, True), (naive_old, True)])

    assert state.streak == 2
    assert state.due_at == aware_new + timedelta(days=INTERVALS_DAYS[1])


def test_days_until_due():
    state = review_state([(_at(days_ago=0), True), ])

    assert state.days_until_due(NOW) == 1
    assert review_state([]).days_until_due(NOW) is None
    assert review_state([(_at(days_ago=5), False)]).days_until_due(NOW) == 0

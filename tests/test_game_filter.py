import pytest

from chesswhisper.game_filter import GameFilter

BLITZ_AS_WHITE = {"White": "Alice", "Black": "Bob", "TimeControl": "180+2"}
BULLET_AS_BLACK = {"White": "Bob", "Black": "Alice", "TimeControl": "60+0"}


def test_an_empty_filter_matches_everything():
    game_filter = GameFilter()

    assert game_filter.is_empty
    assert game_filter.matches(BLITZ_AS_WHITE, "Alice")
    assert game_filter.matches({}, "Alice")
    assert game_filter.to_json() is None


def test_time_control_filter():
    game_filter = GameFilter(time_classes=frozenset({"blitz", "rapid"}))

    assert game_filter.matches(BLITZ_AS_WHITE, "Alice")
    assert not game_filter.matches(BULLET_AS_BLACK, "Alice")
    assert not game_filter.matches({"White": "Alice"}, "Alice")  # unknown time control


def test_colour_filter_is_the_players_colour():
    as_black = GameFilter(color="black")

    assert as_black.matches(BULLET_AS_BLACK, "Alice")
    assert not as_black.matches(BLITZ_AS_WHITE, "Alice")
    assert as_black.matches(BLITZ_AS_WHITE, "Bob")


def test_json_round_trip_keeps_a_stable_order():
    game_filter = GameFilter(time_classes=frozenset({"rapid", "bullet"}), color="white")

    text = game_filter.to_json()

    assert text == '{"time_classes": ["bullet", "rapid"], "color": "white"}'
    assert GameFilter.from_json(text) == game_filter
    assert GameFilter.from_json(None) == GameFilter()


@pytest.mark.parametrize("kwargs", [{"time_classes": frozenset({"hyper"})}, {"color": "green"}])
def test_unknown_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        GameFilter(**kwargs)

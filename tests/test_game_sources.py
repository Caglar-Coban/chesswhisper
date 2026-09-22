import pytest
import requests

from chesswhisper.game_sources import (
    DEFAULT_MAX_GAMES,
    FetchResult,
    GameFetchError,
    GameSourceUnavailable,
    fetch_pgn,
)
from chesswhisper.pgn_loader import load_pgn

LICHESS_URL = "https://lichess.org/api/games/user/caglar"
LICHESS_PERF_TYPES = "ultraBullet,bullet,blitz,rapid,classical,correspondence"


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.content = text.encode("utf-8")
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data


def install_fake_get(mocker, routes):
    """Patches requests.get; `routes` maps URL -> FakeResponse or an Exception to raise.

    Requesting a URL that isn't in `routes` raises KeyError, failing the test.
    Returns the list that records every call.
    """
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        result = routes[url]
        if isinstance(result, Exception):
            raise result
        return result

    mocker.patch("chesswhisper.game_sources.requests.get", side_effect=fake_get)
    return calls


def make_pgn(white="Caglar", black="Bob", event="Test", variant=None):
    headers = [("Event", event), ("White", white), ("Black", black), ("Result", "1-0")]
    if variant is not None:
        headers.append(("Variant", variant))
    head = "\n".join(f'[{key} "{value}"]' for key, value in headers)
    return f"{head}\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0\n\n"


# --- validation (no request may be made) ---


@pytest.mark.parametrize(
    "username",
    ["", "a", "x" * 31, "../etc/passwd", "a/b", "has space", "a\n", "über", None],
)
def test_invalid_username_rejected_before_any_request(mocker, username):
    calls = install_fake_get(mocker, {})

    with pytest.raises(GameFetchError, match="username"):
        fetch_pgn("lichess", username, 5)

    assert calls == []


@pytest.mark.parametrize("max_games", [0, -1, 51, "5", True, 2.5, None])
def test_invalid_max_games_rejected_before_any_request(mocker, max_games):
    calls = install_fake_get(mocker, {})

    with pytest.raises(GameFetchError, match="between 1 and 50"):
        fetch_pgn("lichess", "caglar", max_games)

    assert calls == []


def test_unknown_platform_rejected_before_any_request(mocker):
    calls = install_fake_get(mocker, {})

    with pytest.raises(GameFetchError, match="platform"):
        fetch_pgn("twitch", "caglar", 5)

    assert calls == []


def test_default_max_games_is_ten():
    assert DEFAULT_MAX_GAMES == 10


# --- Lichess ---


def test_lichess_returns_pgn_with_normalized_player(mocker):
    calls = install_fake_get(
        mocker,
        {LICHESS_URL: FakeResponse(text=make_pgn("Caglar", "Bob", event="one") + make_pgn("Eve", "Caglar", event="two"))},
    )

    result = fetch_pgn("lichess", "caglar", 2)

    assert isinstance(result, FetchResult)
    assert result.player == "Caglar"
    assert result.pgn_text.count("[Event ") == 2
    assert result.warnings == []
    assert len(calls) == 1
    call = calls[0]
    assert call["params"] == {"max": 2, "perfType": LICHESS_PERF_TYPES}
    assert call["headers"]["Accept"] == "application/x-chess-pgn"
    assert call["headers"]["User-Agent"].startswith("chesswhisper")
    assert call["timeout"] == 30


def test_lichess_result_is_loadable_by_pgn_loader(mocker, tmp_path):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(text=make_pgn(event="one") + make_pgn(event="two"))})
    result = fetch_pgn("lichess", "caglar", 2)
    pgn_file = tmp_path / "fetched.pgn"
    pgn_file.write_bytes(result.pgn_text.encode("utf-8"))

    loaded = load_pgn(str(pgn_file))

    assert len(loaded.games) == 2
    assert loaded.warnings == []


def test_lichess_warns_when_fewer_games_than_requested(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(text=make_pgn(event="one") + make_pgn(event="two"))})

    result = fetch_pgn("lichess", "caglar", 5)

    assert any("only 2 game(s) found" in warning for warning in result.warnings)


def test_lichess_drops_non_standard_variants_with_warning(mocker):
    text = make_pgn(event="std") + make_pgn(event="960", variant="Chess960") + make_pgn(event="crazy", variant="Crazyhouse")
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(text=text)})

    result = fetch_pgn("lichess", "caglar", 3)

    assert result.pgn_text.count("[Event ") == 1
    assert '[Event "std"]' in result.pgn_text
    assert any("skipped 2 non-standard variant game(s)" in warning for warning in result.warnings)


def test_lichess_unknown_user_raises_plain_fetch_error(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(status_code=404)})

    with pytest.raises(GameFetchError, match="not found") as excinfo:
        fetch_pgn("lichess", "caglar", 5)

    assert not isinstance(excinfo.value, GameSourceUnavailable)


def test_lichess_rate_limit_raises_source_unavailable(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(status_code=429)})

    with pytest.raises(GameSourceUnavailable, match="rate"):
        fetch_pgn("lichess", "caglar", 5)


def test_lichess_server_error_raises_source_unavailable(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(status_code=500, text="<html>boom secret</html>")})

    with pytest.raises(GameSourceUnavailable, match="HTTP 500") as excinfo:
        fetch_pgn("lichess", "caglar", 5)

    assert "secret" not in str(excinfo.value)


def test_lichess_timeout_raises_source_unavailable(mocker):
    install_fake_get(mocker, {LICHESS_URL: requests.Timeout("slow")})

    with pytest.raises(GameSourceUnavailable, match="in time"):
        fetch_pgn("lichess", "caglar", 5)


def test_lichess_connection_error_raises_source_unavailable(mocker):
    install_fake_get(mocker, {LICHESS_URL: requests.ConnectionError("down")})

    with pytest.raises(GameSourceUnavailable, match="Could not reach"):
        fetch_pgn("lichess", "caglar", 5)


def test_lichess_no_games_raises_fetch_error(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(text="")})

    with pytest.raises(GameFetchError, match="No standard-chess games"):
        fetch_pgn("lichess", "caglar", 5)


def test_lichess_only_variant_games_raises_fetch_error(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(text=make_pgn(variant="Chess960"))})

    with pytest.raises(GameFetchError, match="No standard-chess games"):
        fetch_pgn("lichess", "caglar", 5)


def test_lichess_games_not_naming_the_user_raises_fetch_error(mocker):
    install_fake_get(mocker, {LICHESS_URL: FakeResponse(text=make_pgn("Eve", "Mallory"))})

    with pytest.raises(GameFetchError, match="none of them names"):
        fetch_pgn("lichess", "caglar", 5)

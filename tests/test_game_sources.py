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


# --- chess.com ---

CHESSCOM_ARCHIVES_URL = "https://api.chess.com/pub/player/caglar/games/archives"


def month_url(month):
    return f"https://api.chess.com/pub/player/caglar/games/{month}"


def month_response(*games):
    """`games` are (event, rules) pairs given oldest-first, like the real API."""
    entries = [
        {"rules": rules, "pgn": make_pgn(event=event), "end_time": index}
        for index, (event, rules) in enumerate(games)
    ]
    return FakeResponse(json_data={"games": entries})


def archives_response(*months):
    return FakeResponse(json_data={"archives": [month_url(month) for month in months]})


def events_in(pgn_text):
    return sorted(line.split('"')[1] for line in pgn_text.splitlines() if line.startswith("[Event "))


def test_chesscom_walks_months_newest_first_and_takes_latest_games(mocker):
    calls = install_fake_get(
        mocker,
        {
            CHESSCOM_ARCHIVES_URL: archives_response("2026/07", "2026/08", "2026/09"),
            month_url("2026/09"): month_response(("sep-1", "chess"), ("sep-2", "chess")),
            month_url("2026/08"): month_response(("aug-1", "chess"), ("aug-2", "chess"), ("aug-3", "chess")),
        },
    )

    result = fetch_pgn("chesscom", "caglar", 4)

    assert events_in(result.pgn_text) == ["aug-2", "aug-3", "sep-1", "sep-2"]
    assert result.player == "Caglar"
    assert result.warnings == []
    requested = [call["url"] for call in calls]
    assert requested == [CHESSCOM_ARCHIVES_URL, month_url("2026/09"), month_url("2026/08")]
    assert all(call["headers"]["User-Agent"].startswith("chesswhisper") for call in calls)
    assert all(call["timeout"] == 30 for call in calls)


def test_chesscom_keeps_only_standard_rules(mocker):
    install_fake_get(
        mocker,
        {
            CHESSCOM_ARCHIVES_URL: archives_response("2026/09"),
            month_url("2026/09"): month_response(("std", "chess"), ("960", "chess960"), ("crazy", "crazyhouse")),
        },
    )

    result = fetch_pgn("chesscom", "caglar", 10)

    assert events_in(result.pgn_text) == ["std"]


def test_chesscom_looks_back_at_most_six_archives(mocker):
    months = [f"2026/{month:02d}" for month in range(1, 9)]  # 8 archives, oldest first
    routes = {CHESSCOM_ARCHIVES_URL: archives_response(*months)}
    for month in months[-6:]:
        routes[month_url(month)] = month_response()
    calls = install_fake_get(mocker, routes)

    with pytest.raises(GameFetchError, match="No standard-chess games"):
        fetch_pgn("chesscom", "caglar", 5)

    assert len(calls) == 1 + 6
    assert month_url("2026/02") not in [call["url"] for call in calls]


def test_chesscom_unknown_user_raises_plain_fetch_error(mocker):
    install_fake_get(mocker, {CHESSCOM_ARCHIVES_URL: FakeResponse(status_code=404)})

    with pytest.raises(GameFetchError, match="not found") as excinfo:
        fetch_pgn("chesscom", "caglar", 5)

    assert not isinstance(excinfo.value, GameSourceUnavailable)


def test_chesscom_skips_a_month_that_404s_with_a_warning(mocker):
    install_fake_get(
        mocker,
        {
            CHESSCOM_ARCHIVES_URL: archives_response("2026/08", "2026/09"),
            month_url("2026/09"): FakeResponse(status_code=404),
            month_url("2026/08"): month_response(("aug-1", "chess")),
        },
    )

    result = fetch_pgn("chesscom", "caglar", 1)

    assert events_in(result.pgn_text) == ["aug-1"]
    assert any("2026/09" in warning and "skipped" in warning for warning in result.warnings)


def test_chesscom_rate_limited_month_raises_source_unavailable(mocker):
    install_fake_get(
        mocker,
        {
            CHESSCOM_ARCHIVES_URL: archives_response("2026/09"),
            month_url("2026/09"): FakeResponse(status_code=429),
        },
    )

    with pytest.raises(GameSourceUnavailable, match="rate"):
        fetch_pgn("chesscom", "caglar", 5)


def test_chesscom_empty_archive_list_raises_fetch_error(mocker):
    install_fake_get(mocker, {CHESSCOM_ARCHIVES_URL: FakeResponse(json_data={"archives": []})})

    with pytest.raises(GameFetchError, match="No standard-chess games"):
        fetch_pgn("chesscom", "caglar", 5)


def test_chesscom_never_requests_archive_urls_outside_the_api_host(mocker):
    install_fake_get(
        mocker,
        {
            CHESSCOM_ARCHIVES_URL: FakeResponse(
                json_data={"archives": [month_url("2026/08"), "https://evil.example/2026/09"]}
            ),
            month_url("2026/08"): month_response(("aug-1", "chess")),
        },
    )

    result = fetch_pgn("chesscom", "caglar", 5)  # a request to evil.example would KeyError

    assert events_in(result.pgn_text) == ["aug-1"]


def test_chesscom_skips_entries_without_a_pgn(mocker):
    good = {"rules": "chess", "pgn": make_pgn(event="good"), "end_time": 1}
    no_pgn = {"rules": "chess", "end_time": 2}
    install_fake_get(
        mocker,
        {
            CHESSCOM_ARCHIVES_URL: archives_response("2026/09"),
            month_url("2026/09"): FakeResponse(json_data={"games": [good, no_pgn]}),
        },
    )

    result = fetch_pgn("chesscom", "caglar", 5)

    assert events_in(result.pgn_text) == ["good"]


def test_chesscom_unreadable_json_raises_source_unavailable(mocker):
    install_fake_get(mocker, {CHESSCOM_ARCHIVES_URL: FakeResponse(json_data=None)})

    with pytest.raises(GameSourceUnavailable, match="couldn't read"):
        fetch_pgn("chesscom", "caglar", 5)

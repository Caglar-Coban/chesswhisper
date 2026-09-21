# Game Fetching (Lichess / chess.com) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users analyze a Lichess or chess.com player's most recent games by typing a username (CLI flags and a web-form option) instead of supplying a `.pgn` file.

**Architecture:** A new `game_sources.py` module turns `(platform, username, max_games)` into PGN text plus the player's true name spelling. The CLI and web app write that text to a temporary `.pgn` file and hand it to the unchanged `run_analysis`, exactly as an uploaded file is handled today.

**Tech Stack:** Python 3.11, `requests` (already a core dependency), `python-chess`, `click`, FastAPI + Jinja2, `pytest` / `pytest-mock`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-09-22-chesswhisper-game-fetch-design.md`

## Global Constraints

- No new dependencies: `requests` and `python-chess` are already core dependencies.
- Username must fully match `[A-Za-z0-9_-]{2,30}` before any network call; it is URL-quoted when placed in a path.
- `max_games` is a whole number in 1..50 (default 10); `bool` is not accepted.
- Every request sends `User-Agent: chesswhisper (github.com/Caglar-Coban/chesswhisper)` and uses a 30 s timeout.
- Requests are strictly serial (no threads/parallel requests to chess.com).
- chess.com: walk at most the 6 most recent monthly archives, newest first; keep only `rules == "chess"`; a 404 on a single monthly URL is skipped with a warning; only follow archive URLs starting with `https://api.chess.com/pub/player/`.
- Lichess: `GET https://lichess.org/api/games/user/{username}` with `Accept: application/x-chess-pgn`, `max`, and `perfType=ultraBullet,bullet,blitz,rapid,classical,correspondence`.
- Games whose `Variant` header exists and is not `Standard` are dropped with a warning.
- Error messages never include raw response bodies or stack traces.
- No live network in tests or CI: `requests.get` is mocked.
- Code style: `ruff` (line length 110, rules E,F,I,UP,B) and `mypy` (`check_untyped_defs`) must pass; `pytest` must stay green.
- Commit messages: plain `type: subject` lines, no `Co-Authored-By` trailer and no AI/tool attribution of any kind (the repo owner's standing rule for every commit).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/chesswhisper/game_sources.py` (create) | Validation, HTTP calls, variant filtering, player-name normalization. No imports from `cli.py` or `web/`. |
| `tests/test_game_sources.py` (create) | Unit tests for the module; `requests.get` mocked. |
| `src/chesswhisper/cli.py` (modify) | `analyze` accepts `--lichess`, `--chesscom`, `--max`; `PGN_PATH` becomes optional. |
| `tests/test_cli.py` (modify) | New CLI tests appended. |
| `src/chesswhisper/web/app.py` (modify) | `/analyze` accepts `source`, `username`, `max_games`. |
| `src/chesswhisper/web/templates/index.html` (modify) | Source selector + username/count fields + small inline JS. |
| `tests/test_web_fetch.py` (create) | Web tests for the fetch path. |
| `README.md` (modify) | Document the CLI flags and the web option. |

---

### Task 1: `game_sources.py` core + Lichess

**Files:**
- Create: `src/chesswhisper/game_sources.py`
- Create: `tests/test_game_sources.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (used by Tasks 2-4):
  - `DEFAULT_MAX_GAMES: int` (= 10), `MAX_GAMES_LIMIT: int` (= 50)
  - `class GameFetchError(Exception)`; `class GameSourceUnavailable(GameFetchError)`
  - `@dataclass FetchResult(pgn_text: str, player: str, warnings: list[str] = [])`
  - `fetch_pgn(platform: str, username: str, max_games: int = DEFAULT_MAX_GAMES) -> FetchResult`
  - internal registry `_FETCHERS: dict[str, Callable[[str, int, list[str]], list[chess.pgn.Game]]]` that Task 2 extends with `"chesscom"`; every fetcher has the signature `(username, max_games, warnings) -> list[Game]` (newest first).
  - internal helpers reused by Task 2: `_get(url, label, accept=None, params=None)`, `_ensure_ok(resp, label)`, `_json_object(resp, label)`, `_parse_games(pgn_text)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_game_sources.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_game_sources.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'chesswhisper.game_sources'`.

- [ ] **Step 3: Write the implementation**

Create `src/chesswhisper/game_sources.py`:

```python
"""Fetch a player's most recent games from Lichess or chess.com as PGN text."""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote

import chess.pgn
import requests

DEFAULT_MAX_GAMES = 10
MAX_GAMES_LIMIT = 50
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "chesswhisper (github.com/Caglar-Coban/chesswhisper)"

_USERNAME_RE = re.compile(r"[A-Za-z0-9_-]{2,30}")
_PLATFORM_LABELS = {"lichess": "Lichess", "chesscom": "chess.com"}
_LICHESS_GAMES_URL = "https://lichess.org/api/games/user/{username}"
_LICHESS_PERF_TYPES = "ultraBullet,bullet,blitz,rapid,classical,correspondence"


class GameFetchError(Exception):
    """The request can't be satisfied: bad input, unknown user, or no eligible games."""


class GameSourceUnavailable(GameFetchError):
    """The platform couldn't be reached or refused to answer (rate limit, outage, timeout)."""


@dataclass
class FetchResult:
    pgn_text: str
    player: str  # the username as spelled in the games' White/Black headers
    warnings: list[str] = field(default_factory=list)


def _validate(platform: str, username: str, max_games: int) -> None:
    if platform not in _FETCHERS:
        raise GameFetchError(f"Unknown platform {platform!r}; expected one of: {', '.join(sorted(_FETCHERS))}.")
    if not isinstance(username, str) or not _USERNAME_RE.fullmatch(username):
        raise GameFetchError("Invalid username: use 2-30 letters, digits, '_' or '-'.")
    if isinstance(max_games, bool) or not isinstance(max_games, int) or not 1 <= max_games <= MAX_GAMES_LIMIT:
        raise GameFetchError(f"The number of games must be a whole number between 1 and {MAX_GAMES_LIMIT}.")


def _get(url: str, label: str, accept: str | None = None, params: dict | None = None):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    try:
        return requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.Timeout as exc:
        raise GameSourceUnavailable(f"{label} did not respond in time; try again in a moment.") from exc
    except requests.RequestException as exc:
        raise GameSourceUnavailable(f"Could not reach {label}; check your connection and try again.") from exc


def _ensure_ok(resp, label: str) -> None:
    if resp.status_code == 429:
        raise GameSourceUnavailable(f"{label} is rate limiting requests; wait a minute and try again.")
    if resp.status_code != 200:
        raise GameSourceUnavailable(f"{label} returned an unexpected response (HTTP {resp.status_code}).")


def _json_object(resp, label: str) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise GameSourceUnavailable(f"{label} sent a response chesswhisper couldn't read.") from exc
    if not isinstance(data, dict):
        raise GameSourceUnavailable(f"{label} sent a response chesswhisper couldn't read.")
    return data


def _parse_games(pgn_text: str) -> list[chess.pgn.Game]:
    stream = io.StringIO(pgn_text)
    games = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            return games
        games.append(game)


def _fetch_lichess(username: str, max_games: int, warnings: list[str]) -> list[chess.pgn.Game]:
    resp = _get(
        _LICHESS_GAMES_URL.format(username=quote(username, safe="")),
        "Lichess",
        accept="application/x-chess-pgn",
        params={"max": max_games, "perfType": _LICHESS_PERF_TYPES},
    )
    if resp.status_code == 404:
        raise GameFetchError(f"Lichess user {username!r} not found.")
    _ensure_ok(resp, "Lichess")
    return _parse_games(resp.content.decode("utf-8", errors="replace"))


# Each fetcher returns games newest-first and may append to `warnings`.
_FETCHERS: dict[str, Callable[[str, int, list[str]], list[chess.pgn.Game]]] = {
    "lichess": _fetch_lichess,
}


def _spelled_player(games: list[chess.pgn.Game], username: str) -> str | None:
    wanted = username.lower()
    for game in games:
        for name in (game.headers.get("White", ""), game.headers.get("Black", "")):
            if name.lower() == wanted:
                return name
    return None


def fetch_pgn(platform: str, username: str, max_games: int = DEFAULT_MAX_GAMES) -> FetchResult:
    """Fetches a player's most recent standard-chess games as PGN text.

    Raises GameFetchError for bad input, an unknown user, or no eligible games,
    and its subclass GameSourceUnavailable when the platform can't be reached
    or is rate limiting.
    """
    _validate(platform, username, max_games)
    label = _PLATFORM_LABELS[platform]
    warnings: list[str] = []

    fetched = _FETCHERS[platform](username, max_games, warnings)

    games = [game for game in fetched if game.headers.get("Variant", "Standard").lower() == "standard"]
    dropped = len(fetched) - len(games)
    if dropped:
        warnings.append(f"skipped {dropped} non-standard variant game(s).")
    if not games:
        raise GameFetchError(f"No standard-chess games found for {username!r} on {label}.")

    player = _spelled_player(games, username)
    if player is None:
        raise GameFetchError(f"{label} returned games, but none of them names {username!r} as a player.")

    if len(games) < max_games:
        warnings.append(f"only {len(games)} game(s) found (requested {max_games}).")

    pgn_text = "\n\n".join(str(game) for game in games) + "\n"
    return FetchResult(pgn_text=pgn_text, player=player, warnings=warnings)
```

- [ ] **Step 4: Run tests, lint, and type-check**

Run: `pytest tests/test_game_sources.py -v`
Expected: all PASS.

Run: `ruff check src tests && mypy src`
Expected: no errors. (If `ruff` reports import-order (`I001`) issues, run `ruff check --fix` on the two new files and re-run.)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/game_sources.py tests/test_game_sources.py
git commit -m "feat: add game_sources module with Lichess game fetching"
```

---

### Task 2: chess.com fetcher

**Files:**
- Modify: `src/chesswhisper/game_sources.py`
- Modify: `tests/test_game_sources.py`

**Interfaces:**
- Consumes (from Task 1): `_get`, `_ensure_ok`, `_json_object`, `_parse_games`, `_PLATFORM_LABELS`, `_FETCHERS`, `MAX_GAMES_LIMIT`, `GameFetchError`, `quote` import.
- Produces: `fetch_pgn("chesscom", username, max_games)` now works; `_fetch_chesscom(username, max_games, warnings)` registered in `_FETCHERS["chesscom"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_game_sources.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_game_sources.py -k chesscom -v`
Expected: FAIL. `fetch_pgn("chesscom", ...)` raises `GameFetchError: Unknown platform 'chesscom'...` in every test.

- [ ] **Step 3: Write the implementation**

In `src/chesswhisper/game_sources.py`, add these constants directly below `_LICHESS_PERF_TYPES = ...`:

```python
MAX_CHESSCOM_ARCHIVES = 6
_CHESSCOM_API_PREFIX = "https://api.chess.com/pub/player/"
_CHESSCOM_ARCHIVES_URL = _CHESSCOM_API_PREFIX + "{username}/games/archives"
```

Add this function directly above the `# Each fetcher returns games newest-first...` comment:

```python
def _month_label(archive_url: str) -> str:
    return "/".join(archive_url.rstrip("/").split("/")[-2:])


def _fetch_chesscom(username: str, max_games: int, warnings: list[str]) -> list[chess.pgn.Game]:
    label = _PLATFORM_LABELS["chesscom"]
    resp = _get(_CHESSCOM_ARCHIVES_URL.format(username=quote(username, safe="")), label)
    if resp.status_code == 404:
        raise GameFetchError(f"chess.com user {username!r} not found.")
    _ensure_ok(resp, label)
    archives = _json_object(resp, label).get("archives", [])
    if not isinstance(archives, list):
        return []

    games: list[chess.pgn.Game] = []
    for url in reversed(archives[-MAX_CHESSCOM_ARCHIVES:]):  # newest month first
        if not isinstance(url, str) or not url.startswith(_CHESSCOM_API_PREFIX):
            continue
        resp = _get(url, label)
        if resp.status_code == 404:
            # Observed live: the current month's URL can 404 while earlier months are fine.
            warnings.append(f"chess.com archive {_month_label(url)} was unavailable and was skipped.")
            continue
        _ensure_ok(resp, label)
        entries = _json_object(resp, label).get("games", [])
        if not isinstance(entries, list):
            continue
        for entry in reversed(entries):  # the API lists a month oldest-first
            if not isinstance(entry, dict) or entry.get("rules") != "chess":
                continue
            pgn = entry.get("pgn")
            if not isinstance(pgn, str) or not pgn.strip():
                continue
            games.extend(_parse_games(pgn))
            if len(games) >= max_games:
                return games[:max_games]
    return games
```

Replace the `_FETCHERS` dict with:

```python
_FETCHERS: dict[str, Callable[[str, int, list[str]], list[chess.pgn.Game]]] = {
    "lichess": _fetch_lichess,
    "chesscom": _fetch_chesscom,
}
```

- [ ] **Step 4: Run tests, lint, and type-check**

Run: `pytest tests/test_game_sources.py -v`
Expected: all PASS (Task 1's tests included).

Run: `ruff check src tests && mypy src`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/game_sources.py tests/test_game_sources.py
git commit -m "feat: fetch recent chess.com games from monthly archives"
```

---

### Task 3: CLI `--lichess` / `--chesscom` / `--max`

**Files:**
- Modify: `src/chesswhisper/cli.py` (imports; new helper `_fetch_to_tempfile`; the `analyze` command at lines 117-164)
- Modify: `tests/test_cli.py` (append tests)
- Modify: `README.md` (usage section)

**Interfaces:**
- Consumes (from Tasks 1-2): `fetch_pgn(platform, username, max_games) -> FetchResult`, `FetchResult.pgn_text/.player/.warnings`, `GameFetchError`, `DEFAULT_MAX_GAMES`.
- Produces: `chesswhisper analyze [PGN_PATH] [--lichess USER | --chesscom USER] [--max N]`; module-level `fetch_pgn` name in `chesswhisper.cli` (tests monkeypatch `chesswhisper.cli.fetch_pgn`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add to the imports at the top (keep the file's existing import grouping; `chesswhisper.*` imports stay together):

```python
from chesswhisper.game_sources import FetchResult, GameFetchError
```

Append at the end of `tests/test_cli.py`:

```python
# --- fetching games from Lichess / chess.com ---


def test_analyze_requires_exactly_one_game_source(tmp_path):
    runner = CliRunner()

    none_given = runner.invoke(cli, ["analyze"])
    two_given = runner.invoke(cli, ["analyze", str(tmp_path / "g.pgn"), "--lichess", "alice"])
    both_platforms = runner.invoke(cli, ["analyze", "--lichess", "alice", "--chesscom", "alice"])

    for result in (none_given, two_given, both_platforms):
        assert result.exit_code == 1
        assert "exactly one of" in result.output


def test_analyze_max_is_rejected_with_a_pgn_file(tmp_path):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    result = CliRunner().invoke(cli, ["analyze", str(pgn_path), "--max", "5"])

    assert result.exit_code == 1
    assert "--max" in result.output


def test_analyze_lichess_fetches_games_and_uses_the_fetched_player(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice", warnings=["only 1 game(s) found (requested 3)."])

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)
    output_path = tmp_path / "report.md"

    result = CliRunner().invoke(
        cli,
        ["analyze", "--lichess", "alice", "--max", "3", "--lang", "en", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls["args"] == ("lichess", "alice", 3)
    assert "Warning: only 1 game(s) found" in result.output
    assert "Alice" in output_path.read_text(encoding="utf-8")


def test_analyze_chesscom_defaults_to_ten_games(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice")

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)

    result = CliRunner().invoke(cli, ["analyze", "--chesscom", "alice", "-o", str(tmp_path / "r.md")])

    assert result.exit_code == 0, result.output
    assert calls["args"] == ("chesscom", "alice", 10)


def test_analyze_player_option_overrides_the_fetched_player(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr(
        "chesswhisper.cli.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Bob"),
    )
    captured = {}
    real_run_analysis = run_analysis

    def spy_run_analysis(pgn_path, player, *args, **kwargs):
        captured["player"] = player
        return real_run_analysis(pgn_path, player, *args, **kwargs)

    monkeypatch.setattr("chesswhisper.cli.run_analysis", spy_run_analysis)

    result = CliRunner().invoke(
        cli, ["analyze", "--lichess", "bob", "--player", "Alice", "-o", str(tmp_path / "r.md")]
    )

    assert result.exit_code == 0, result.output
    assert captured["player"] == "Alice"


def test_analyze_fetch_error_exits_cleanly_without_running_the_engine(tmp_path, monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("the engine should not start when fetching fails")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_if_called)

    def fake_fetch(platform, username, max_games):
        raise GameFetchError("Lichess user 'alice' not found.")

    monkeypatch.setattr("chesswhisper.cli.fetch_pgn", fake_fetch)

    result = CliRunner().invoke(cli, ["analyze", "--lichess", "alice", "-o", str(tmp_path / "r.md")])

    assert result.exit_code == 1
    assert "Error: Lichess user 'alice' not found." in result.output


def test_analyze_removes_the_temporary_pgn_after_a_fetched_run(tmp_path, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr(
        "chesswhisper.cli.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Alice"),
    )
    seen = {}
    real_run_analysis = run_analysis

    def spy_run_analysis(pgn_path, *args, **kwargs):
        seen["path"] = pgn_path
        seen["existed_during_run"] = os.path.exists(pgn_path)
        return real_run_analysis(pgn_path, *args, **kwargs)

    monkeypatch.setattr("chesswhisper.cli.run_analysis", spy_run_analysis)

    result = CliRunner().invoke(cli, ["analyze", "--lichess", "alice", "-o", str(tmp_path / "r.md")])

    assert result.exit_code == 0, result.output
    assert seen["existed_during_run"] is True
    assert not os.path.exists(seen["path"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: the new tests FAIL, either at import time (`chesswhisper.cli` has no `fetch_pgn` to monkeypatch: `AttributeError`) or with click usage errors (`No such option: --lichess`). Existing tests still pass.

- [ ] **Step 3: Write the implementation**

In `src/chesswhisper/cli.py`:

Add `import tempfile` to the stdlib imports (after `import sys`), and add this import in the `chesswhisper` import block (alphabetical position, after `engine_provision`):

```python
from chesswhisper.game_sources import DEFAULT_MAX_GAMES, GameFetchError, fetch_pgn
```

Add this helper directly above `run_analysis`:

```python
def _fetch_to_tempfile(platform, username, max_games):
    """Fetches games and writes them to a temporary .pgn file.

    Returns (temp_path, player, warnings). Raises GameFetchError. The caller
    owns the returned file and must delete it.
    """
    fetched = fetch_pgn(platform, username, max_games)
    fd, temp_path = tempfile.mkstemp(suffix=".pgn")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(fetched.pgn_text.encode("utf-8"))
    except OSError:
        os.remove(temp_path)
        raise
    return temp_path, fetched.player, fetched.warnings
```

Replace the whole `analyze` command (from the `@cli.command()` above `@click.argument("pgn_path", ...)` through the final `click.echo(f"Report written to {output_path}")`) with:

```python
@cli.command()
@click.argument("pgn_path", required=False, type=click.Path(exists=False))
@click.option("--lichess", "lichess_user", default=None, help="Fetch this Lichess user's recent games instead of reading a PGN file.")
@click.option("--chesscom", "chesscom_user", default=None, help="Fetch this chess.com user's recent games instead of reading a PGN file.")
@click.option("--max", "max_games", default=None, type=int, help="Number of recent games to fetch, 1-50 (default 10). Only with --lichess/--chesscom.")
@click.option("--player", default=None, help="Player name to analyze (matches PGN White/Black headers).")
@click.option("--lang", type=click.Choice(["tr", "en"]), default="tr")
@click.option("--llm", "llm_name", type=click.Choice(["claude", "openai", "none"]), default="none")
@click.option("-o", "--output", "output_path", default="report.md", type=click.Path())
@click.option("--movetime", default=300, help="Engine think time per position, in milliseconds.")
@click.option("--depth", default=None, type=int, help="Fixed search depth (overrides --movetime).")
@click.option("--engine-path", default=None, help="Path to an existing Stockfish binary (skips auto-download).")
def analyze(
    pgn_path, lichess_user, chesscom_user, max_games, player, lang, llm_name, output_path, movetime, depth, engine_path
):
    sources_given = [s for s in (pgn_path, lichess_user, chesscom_user) if s is not None]
    if len(sources_given) != 1:
        click.echo("Error: provide exactly one of PGN_PATH, --lichess USER, or --chesscom USER.", err=True)
        sys.exit(1)
    fetching = pgn_path is None
    if max_games is not None and not fetching:
        click.echo("Error: --max only applies with --lichess or --chesscom.", err=True)
        sys.exit(1)

    if pgn_path is not None and os.path.abspath(output_path) == os.path.abspath(pgn_path):
        click.echo(
            f"Error: output path {output_path} is the same as the input PGN file; "
            "refusing to overwrite it. Use -o to choose a different output path.",
            err=True,
        )
        sys.exit(1)

    existed_before = os.path.exists(output_path)
    try:
        with open(output_path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        click.echo(f"Error: cannot write to output path {output_path}: {exc}", err=True)
        sys.exit(1)
    if not existed_before:
        try:
            os.remove(output_path)
        except OSError:
            pass

    temp_path = None
    try:
        analysis_path = pgn_path
        if fetching:
            platform, username = ("lichess", lichess_user) if lichess_user is not None else ("chesscom", chesscom_user)
            count = DEFAULT_MAX_GAMES if max_games is None else max_games
            try:
                temp_path, fetched_player, fetch_warnings = _fetch_to_tempfile(platform, username, count)
            except (GameFetchError, OSError) as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)
            for warning in fetch_warnings:
                click.echo(f"Warning: {warning}", err=True)
            analysis_path = temp_path
            player = player or fetched_player

        try:
            aggregate, commentary, warnings = run_analysis(
                analysis_path, player, lang, llm_name, movetime=movetime, depth=depth, engine_path=engine_path
            )
        except (OSError, ValueError, StockfishProvisionError) as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)

    try:
        write_report(aggregate, commentary, lang, output_path)
    except OSError as exc:
        click.echo(f"Error: failed to write report to {output_path}: {exc}", err=True)
        sys.exit(1)
    click.echo(f"Report written to {output_path}")
```

In `README.md`, in the **Usage** section, insert this block directly after the `--engine-path` bullet (the bullet ending "...is also picked up automatically."):

```markdown
Instead of a PGN file, you can fetch a player's most recent games straight
from Lichess or chess.com:

```
chesswhisper analyze --lichess YourLichessName --max 20 --lang en -o report.md
chesswhisper analyze --chesscom YourChessComName --llm claude
```

- Give exactly one of `PGN_PATH`, `--lichess USER`, or `--chesscom USER`.
- `--max N` — how many of the most recent games to fetch, 1-50 (default: `10`). Only valid with `--lichess`/`--chesscom`.
- The username is also used as `--player` (matched case-insensitively); pass `--player` explicitly to override it.
- Only public games are fetched (no login), and chess variants such as Chess960 are skipped.
- Analysis time grows with the number of games; at the default 300 ms per position, tens of games can take several minutes.
```

(When editing the README, write the inner triple-backtick fence as a normal code fence; the outer fence above is only for this plan's formatting.)

- [ ] **Step 4: Run tests, lint, and type-check**

Run: `pytest tests/test_cli.py tests/test_game_sources.py -v`
Expected: all PASS, including the pre-existing CLI tests (`test_analyze_refuses_to_overwrite_input_pgn`, `test_analyze_missing_pgn_file_fails_fast`, etc.).

Run: `ruff check src tests && mypy src`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/cli.py tests/test_cli.py README.md
git commit -m "feat: add --lichess, --chesscom and --max options to analyze"
```

---

### Task 4: Web form option + `/analyze` fetch path

**Files:**
- Modify: `src/chesswhisper/web/app.py` (imports; the `/analyze` handler at lines 296-359)
- Modify: `src/chesswhisper/web/templates/index.html`
- Create: `tests/test_web_fetch.py`
- Modify: `README.md` (Web UI section)

**Interfaces:**
- Consumes (from Tasks 1-2): `fetch_pgn`, `FetchResult`, `GameFetchError`, `GameSourceUnavailable`, `DEFAULT_MAX_GAMES`.
- Produces: form fields `source` (`file` | `lichess` | `chesscom`), `username`, `max_games` on `POST /analyze`; module-level `fetch_pgn` name in `chesswhisper.web.app` (tests monkeypatch `chesswhisper.web.app.fetch_pgn`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_fetch.py`:

```python
import textwrap

import chess.engine
import pytest
from conftest import CsrfClient

from chesswhisper.game_sources import FetchResult, GameFetchError, GameSourceUnavailable
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


class FakeEngine:
    def __init__(self):
        self._scores = [0, 20, 10, 15]
        self._i = 0

    def analyse(self, board, limit, **kwargs):
        cp = self._scores[self._i % len(self._scores)]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "fetch_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def fail_if_called(*args, **kwargs):
    raise AssertionError("fetch_pgn should not have been called")


def test_index_offers_game_source_choices(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'name="source"' in response.text
    assert 'value="lichess"' in response.text
    assert 'value="chesscom"' in response.text
    assert 'name="username"' in response.text
    assert 'name="max_games"' in response.text
    assert 'name="pgn"' in response.text


def test_analyze_fetches_lichess_games_and_saves_history(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice", warnings=["only 1 game(s) found (requested 7)."])

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post(
        "/analyze",
        data={"source": "lichess", "username": " alice ", "max_games": "7", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert calls["args"] == ("lichess", "alice", 7)
    assert "chesswhisper Analysis Report" in response.text
    assert "only 1 game(s) found" in response.text
    records = next(get_db()).query(AnalysisRecord).all()
    assert len(records) == 1
    assert records[0].player_name == "Alice"


def test_analyze_fetch_defaults_player_to_the_fetched_spelling(client, monkeypatch):
    monkeypatch.setattr(
        "chesswhisper.web.app.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Alice"),
    )
    captured = {}

    def fake_run_analysis(pgn_path, player, *args, **kwargs):
        captured["player"] = player
        raise ValueError("stop here")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    client.post("/analyze", data={"source": "chesscom", "username": "alice", "max_games": "5", "llm": "none"})

    assert captured["player"] == "Alice"


def test_analyze_fetch_form_player_overrides_the_fetched_spelling(client, monkeypatch):
    monkeypatch.setattr(
        "chesswhisper.web.app.fetch_pgn",
        lambda platform, username, max_games: FetchResult(pgn_text=SAMPLE_PGN, player="Alice"),
    )
    captured = {}

    def fake_run_analysis(pgn_path, player, *args, **kwargs):
        captured["player"] = player
        raise ValueError("stop here")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    client.post(
        "/analyze",
        data={"source": "lichess", "username": "alice", "max_games": "5", "player": "Bob", "llm": "none"},
    )

    assert captured["player"] == "Bob"


def test_analyze_fetch_requires_a_username(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post("/analyze", data={"source": "lichess", "username": "  ", "max_games": "5", "llm": "none"})

    assert response.status_code == 400
    assert "kullanıcı adı" in response.text


def test_analyze_fetch_rejects_a_non_numeric_game_count(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze", data={"source": "lichess", "username": "alice", "max_games": "lots", "llm": "none"}
    )

    assert response.status_code == 400
    assert "tam sayı" in response.text


def test_analyze_rejects_an_unknown_source(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post("/analyze", data={"source": "twitch", "username": "alice", "llm": "none"})

    assert response.status_code == 400
    assert "Geçersiz oyun kaynağı" in response.text


def test_analyze_fetch_error_returns_400_with_the_message(client, monkeypatch):
    def fake_fetch(platform, username, max_games):
        raise GameFetchError("Lichess user 'alice' not found.")

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post("/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "none"})

    assert response.status_code == 400
    assert "not found" in response.text


def test_analyze_source_unavailable_returns_502(client, monkeypatch):
    def fake_fetch(platform, username, max_games):
        raise GameSourceUnavailable("Lichess is rate limiting requests; wait a minute and try again.")

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post("/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "none"})

    assert response.status_code == 502
    assert "rate limiting" in response.text


def test_analyze_fetch_with_missing_api_key_redirects_before_fetching(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "claude"}
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")


def test_analyze_file_upload_still_works_when_source_is_file(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"source": "file", "player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 200
    assert "chesswhisper Analysis Report" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_web_fetch.py -v`
Expected: FAIL. `monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", ...)` raises `AttributeError` (no such attribute yet), and `test_index_offers_game_source_choices` fails on the missing `source` field.

- [ ] **Step 3: Write the implementation**

In `src/chesswhisper/web/app.py`:

Add `run_in_threadpool` to the FastAPI imports, in alphabetical order with the other `fastapi` imports:

```python
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
```

Add to the `chesswhisper` import block (after the `engine_provision` import):

```python
from chesswhisper.game_sources import DEFAULT_MAX_GAMES, GameFetchError, GameSourceUnavailable, fetch_pgn
```

Add this constant directly below `LOGIN_LOCKOUT_MINUTES = 15`:

```python
FETCH_SOURCES = ("lichess", "chesscom")
```

Replace the entire `analyze` handler (from `@app.post("/analyze", ...)` through its final `return _render(request, "result.html", ...)`) with:

```python
@app.post("/analyze", response_class=HTMLResponse, response_model=None)
async def analyze(
    request: Request,
    pgn: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI's own idiom
    source: str = Form(default="file"),
    username: str = Form(default=""),
    max_games: str = Form(default=str(DEFAULT_MAX_GAMES)),
    player: str = Form(default=""),
    lang: str = Form(default="tr"),
    llm: str = Form(default="none"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    fetching = source in FETCH_SOURCES
    if source != "file" and not fetching:
        return _render_error(request, "Geçersiz oyun kaynağı.", 400, current_user)

    contents = b""
    game_count = DEFAULT_MAX_GAMES
    if fetching:
        username = username.strip()
        if not username:
            return _render_error(request, "Lütfen bir kullanıcı adı girin.", 400, current_user)
        try:
            game_count = int(max_games)
        except ValueError:
            return _render_error(request, "Oyun sayısı geçerli bir tam sayı olmalı.", 400, current_user)
    else:
        if pgn is None or not pgn.filename:
            return _render_error(request, "Lütfen analiz edilecek bir PGN dosyası seçin.", 400, current_user)
        contents = await pgn.read()

    llm_api_key = None
    if llm != "none":
        key_row = db.get(UserApiKey, current_user.id)
        encrypted = None
        if key_row is not None:
            encrypted = key_row.anthropic_key_encrypted if llm == "claude" else key_row.openai_key_encrypted
        if not encrypted:
            return RedirectResponse("/settings?message=missing_key", status_code=303)
        llm_api_key = decrypt_api_key(encrypted)

    fetch_warnings: list[str] = []
    if fetching:
        try:
            fetched = await run_in_threadpool(fetch_pgn, source, username, game_count)
        except GameSourceUnavailable as exc:
            return _render_error(request, f"Oyunlar alınamadı: {exc}", 502, current_user)
        except GameFetchError as exc:
            return _render_error(request, str(exc), 400, current_user)
        contents = fetched.pgn_text.encode("utf-8")
        fetch_warnings = fetched.warnings
        player = player or fetched.player

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pgn", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        try:
            aggregate, commentary, warnings = run_analysis(
                tmp_path, player or None, lang, llm, llm_api_key=llm_api_key
            )
        except (OSError, ValueError) as exc:
            return _render_error(request, str(exc), 400, current_user)
        except StockfishProvisionError as exc:
            return _render_error(request, f"Motor kurulum hatası: {exc}", 500, current_user)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    report_markdown = render_markdown(aggregate, commentary, lang)

    record = AnalysisRecord(
        user_id=current_user.id,
        player_name=aggregate.player,
        lang=lang,
        llm_used=llm,
        games_count=len(aggregate.games),
        overall_acpl=aggregate.overall_acpl,
        overall_accuracy=aggregate.overall_accuracy,
        report_markdown=report_markdown,
    )
    db.add(record)
    db.commit()

    report_html = _report_to_safe_html(report_markdown)

    return _render(
        request, "result.html",
        {
            "report_html": report_html,
            "warnings": fetch_warnings + warnings,
            "current_user": current_user,
            "saved": True,
        },
    )
```

In `src/chesswhisper/web/templates/index.html`, replace the PGN-file block (the `<label for="pgn">` ... `<p class="hint">Chess.com veya Lichess'ten indirdiğin bir .pgn dosyası.</p>` lines) and the player field with:

```html
    <label for="source">Oyun kaynağı</label>
    <select id="source" name="source" onchange="updateSource()">
      <option value="file" selected>PGN dosyası</option>
      <option value="lichess">Lichess kullanıcısı</option>
      <option value="chesscom">chess.com kullanıcısı</option>
    </select>

    <div id="file-field">
      <label for="pgn">PGN dosyası</label>
      <input type="file" id="pgn" name="pgn" accept=".pgn" required>
      <p class="hint">Chess.com veya Lichess'ten indirdiğin bir .pgn dosyası.</p>
    </div>

    <div id="fetch-fields" style="display: none;">
      <label for="username">Kullanıcı adı</label>
      <input type="text" id="username" name="username" placeholder="Platformdaki kullanıcı adın">
      <label for="max_games">Oyun sayısı</label>
      <input type="number" id="max_games" name="max_games" value="10" min="1" max="50">
      <p class="hint">Son oynadığın oyunlar çekilir (en fazla 50, yalnızca herkese açık oyunlar). Ne kadar çok oyun seçersen analiz o kadar uzun sürer.</p>
    </div>

    <label for="player">Oyuncu adı (opsiyonel)</label>
    <input type="text" id="player" name="player" placeholder="Boş bırakırsan otomatik tespit edilir">
    <p class="hint">Kullanıcı adıyla oyun çekiyorsan boş bırakabilirsin; kullanıcı adın kullanılır.</p>
```

(Keep the existing `<label for="lang">`, LLM select, submit button, and status line exactly as they are.)

In the `<script>` block of `index.html`, add this function above `handleSubmit` and call it once:

```html
  function updateSource() {
    var fetching = document.getElementById("source").value !== "file";
    document.getElementById("file-field").style.display = fetching ? "none" : "block";
    document.getElementById("fetch-fields").style.display = fetching ? "block" : "none";
    document.getElementById("pgn").required = !fetching;
    document.getElementById("username").required = fetching;
  }
  updateSource();
```

In `README.md`, in the **Web UI** section, add this paragraph directly after the paragraph that ends "...reset a user's password.":

```markdown
On the analysis form you can either upload a PGN file or pick **Lichess**
or **chess.com**, type a username, and choose how many of that player's
most recent games (1-50) to fetch and analyze; the username is used as the
player name unless you fill in the player field yourself. Only public
games are fetched, and more games means a longer wait.
```

- [ ] **Step 4: Run tests, lint, type-check, and the full suite**

Run: `pytest tests/test_web_fetch.py -v`
Expected: all PASS.

Run: `pytest -v`
Expected: the entire suite PASSES, including every pre-existing `test_web_*` file (the upload path is unchanged: `test_analyze_saves_history_record_for_current_user`, `test_analyze_unknown_player_returns_400`, and the rest).

Run: `ruff check src tests && mypy src`
Expected: no errors.

- [ ] **Step 5: Live smoke check (manual; needs network)**

Run:

```bash
python -c "from chesswhisper.game_sources import fetch_pgn; r = fetch_pgn('lichess', 'DrNykterstein', 2); print(r.player, r.warnings, r.pgn_text.count('[Event '))"
python -c "from chesswhisper.game_sources import fetch_pgn; r = fetch_pgn('chesscom', 'hikaru', 3); print(r.player, r.warnings, r.pgn_text.count('[Event '))"
python -c "from chesswhisper.game_sources import fetch_pgn; fetch_pgn('lichess', 'zzzz_no_such_user_98765', 1)"
```

Expected: the first prints `DrNykterstein [] 2`; the second prints `Hikaru` (or the account's exact spelling), a warning list that may include a skipped-month warning (the current-month 404 quirk), and `3`; the third exits with a `GameFetchError: Lichess user 'zzzz_no_such_user_98765' not found.` traceback.

Then, only if a Stockfish binary is already installed or you accept the one-time auto-download, run one end-to-end analysis:

```bash
chesswhisper analyze --lichess DrNykterstein --max 1 --lang en -o smoke-report.md
```

Expected: `Report written to smoke-report.md`; delete `smoke-report.md` afterwards (do not commit it).

- [ ] **Step 6: Commit**

```bash
git add src/chesswhisper/web/app.py src/chesswhisper/web/templates/index.html tests/test_web_fetch.py README.md
git commit -m "feat: fetch games from Lichess or chess.com in the web analyze form"
```

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
MAX_CHESSCOM_ARCHIVES = 6
_CHESSCOM_API_PREFIX = "https://api.chess.com/pub/player/"
_CHESSCOM_ARCHIVES_URL = _CHESSCOM_API_PREFIX + "{username}/games/archives"


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
    games: list[chess.pgn.Game] = []
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
        # clocks=true adds the [%clk] comments the time-management stats read.
        params={"max": max_games, "perfType": _LICHESS_PERF_TYPES, "clocks": "true"},
    )
    if resp.status_code == 404:
        raise GameFetchError(f"Lichess user {username!r} not found.")
    _ensure_ok(resp, "Lichess")
    return _parse_games(resp.content.decode("utf-8", errors="replace"))


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
        warnings.append(f"{label} returned archives in an unexpected format.")
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
            warnings.append(f"{label} archive {_month_label(url)} had an unexpected format and was skipped.")
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


# Each fetcher returns games newest-first and may append to `warnings`.
_FETCHERS: dict[str, Callable[[str, int, list[str]], list[chess.pgn.Game]]] = {
    "lichess": _fetch_lichess,
    "chesscom": _fetch_chesscom,
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

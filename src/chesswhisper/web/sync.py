"""Automatic analysis of new games from a linked Lichess or chess.com account.

A user links one account in Settings. Every CHESSWHISPER_SYNC_MINUTES
(default 60, 0 turns it off) a background thread fetches that account's
latest games and queues an analysis of the ones it hasn't seen before,
without LLM commentary so it costs nothing. The first check only takes
note of the games already there: linking an account doesn't analyse its
past. A user whose analysis is still running is skipped until the next
round, so nothing is lost.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import chess.pgn

from chesswhisper import game_sources
from chesswhisper.game_sources import GameFetchError
from chesswhisper.web import jobs
from chesswhisper.web.db import session_scope, utcnow
from chesswhisper.web.models import GameSync

logger = logging.getLogger(__name__)

SYNC_MINUTES_ENV = "CHESSWHISPER_SYNC_MINUTES"
DEFAULT_SYNC_MINUTES = 60
MIN_SYNC_MINUTES = 5  # the platforms' APIs are free; don't lean on them
SYNC_FETCH_GAMES = 20  # the newest games looked at in each check
MAX_SEEN_KEYS = 200
# The first automatic check waits this long after startup.
STARTUP_DELAY_SECONDS = 60

# One check at a time, so the scheduler and a "Check now" can't queue the same games twice.
_check_lock = threading.Lock()


def sync_minutes_from_env() -> int:
    """Minutes between automatic checks; 0 means they are off."""
    try:
        minutes = int(os.environ.get(SYNC_MINUTES_ENV, DEFAULT_SYNC_MINUTES))
    except ValueError:
        return DEFAULT_SYNC_MINUTES
    return 0 if minutes <= 0 else max(MIN_SYNC_MINUTES, minutes)


@dataclass(frozen=True)
class SyncStatus:
    platform: str
    username: str
    lang: str
    baseline_taken: bool
    last_checked_at: datetime | None
    last_new_games: int
    last_job_id: str | None
    last_error: str | None


def game_key(game: chess.pgn.Game) -> str:
    """A stable id for a game: its URL on the platform, else a hash of the game itself."""
    headers = game.headers
    for name in ("Link", "Site"):
        value = headers.get(name, "")
        if value.startswith("https://"):
            return value
    exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
    text = "|".join(headers.get(h, "") for h in ("White", "Black", "Date", "UTCTime")) + "|" + game.accept(exporter)
    return "sha1:" + hashlib.sha1(text.encode("utf-8")).hexdigest()


def get_sync(user_id: int) -> SyncStatus | None:
    with session_scope() as db:
        row = db.get(GameSync, user_id)
        if row is None:
            return None
        return SyncStatus(
            platform=row.platform, username=row.username, lang=row.lang,
            baseline_taken=row.seen_keys_json is not None,
            last_checked_at=row.last_checked_at, last_new_games=row.last_new_games,
            last_job_id=row.last_job_id, last_error=row.last_error,
        )


def link_account(user_id: int, platform: str, username: str, lang: str) -> None:
    """Link (or replace) the user's account; the next check takes note of its current games."""
    with _check_lock, session_scope() as db:
        row = db.get(GameSync, user_id)
        if row is None:
            row = GameSync(user_id=user_id)
            db.add(row)
        row.platform, row.username, row.lang = platform, username, lang
        row.seen_keys_json = None
        row.created_at = utcnow()
        row.last_checked_at = None
        row.last_new_games = 0
        row.last_job_id = None
        row.last_error = None


def unlink_account(user_id: int) -> None:
    with _check_lock, session_scope() as db:
        row = db.get(GameSync, user_id)
        if row is not None:
            db.delete(row)


def _parse(pgn_text: str) -> list[chess.pgn.Game]:
    stream = io.StringIO(pgn_text)
    games = []
    while (game := chess.pgn.read_game(stream)) is not None:
        games.append(game)
    return games


def _save(user_id: int, **fields) -> None:
    with session_scope() as db:
        row = db.get(GameSync, user_id)
        if row is None:  # unlinked meanwhile
            return
        for name, value in fields.items():
            setattr(row, name, value)
        row.last_checked_at = utcnow()


def check(user_id: int, enqueue: Callable[[str], None], fetch=None) -> str:
    """Look for new games once. Returns what happened:

    "unlinked" (no account), "busy" (an analysis is already running; try
    later), "error" (the fetch failed; kept in last_error), "baseline" (first
    check: games noted, none analysed), "none" (nothing new) or "queued".
    """
    fetch = fetch or game_sources.fetch_pgn
    with _check_lock:
        status = get_sync(user_id)
        if status is None:
            return "unlinked"
        if jobs.running_job_for(user_id) is not None:
            return "busy"
        try:
            fetched = fetch(status.platform, status.username, SYNC_FETCH_GAMES)
        except GameFetchError as exc:
            _save(user_id, last_error=str(exc))
            return "error"

        games = _parse(fetched.pgn_text)
        keys = [game_key(game) for game in games]
        with session_scope() as db:
            row = db.get(GameSync, user_id)
            seen = json.loads(row.seen_keys_json) if row is not None and row.seen_keys_json else None
        merged = json.dumps((keys + [k for k in (seen or []) if k not in keys])[:MAX_SEEN_KEYS])
        if seen is None:
            _save(user_id, seen_keys_json=merged, last_new_games=0, last_error=None)
            return "baseline"

        seen_set = set(seen)
        new_games = [game for game, key in zip(games, keys, strict=True) if key not in seen_set]
        if not new_games:
            _save(user_id, seen_keys_json=merged, last_new_games=0, last_error=None)
            return "none"

        inputs = jobs.JobInputs(
            pgn_text="\n\n".join(str(game) for game in new_games) + "\n",
            player=fetched.player,
            lang=status.lang,
            llm="none",
            ui_language=status.lang,
            origin="sync",
        )
        job, created = jobs.create_job_unless_running(user_id, inputs)
        if not created:
            return "busy"
        _save(user_id, seen_keys_json=merged, last_new_games=len(new_games), last_job_id=job.id, last_error=None)
    enqueue(job.id)
    return "queued"


def check_all(enqueue: Callable[[str], None], fetch=None) -> None:
    with session_scope() as db:
        user_ids = [row.user_id for row in db.query(GameSync.user_id).all()]
    for user_id in user_ids:
        try:
            check(user_id, enqueue, fetch)
        except Exception:
            logger.exception("Game sync for user %s failed", user_id)


class SyncScheduler:
    """A daemon thread running check_all() every *interval_seconds*, the first after *first_delay*."""

    def __init__(self, enqueue: Callable[[str], None], interval_seconds: float, first_delay: float):
        self._enqueue = enqueue
        self._interval = interval_seconds
        self._first_delay = first_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._interval <= 0 or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="chesswhisper-sync", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self) -> None:
        delay = self._first_delay
        while not self._stop.wait(delay):
            check_all(self._enqueue)
            delay = self._interval

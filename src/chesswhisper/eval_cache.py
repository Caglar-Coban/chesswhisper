"""On-disk cache of per-move engine evaluations, keyed by game and search limit.

Fetching the same player's recent games again mostly returns games that were
already analyzed; with this cache only the new ones reach Stockfish. The key
covers everything the stored evaluations depend on: the starting position,
the moves and their clock comments, the search limit, and the bundled
Stockfish release (a different engine gives different numbers, so bumping the
release starts a fresh cache).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import chess.pgn

from chesswhisper.engine_analyzer import PositionEval, parse_clock_seconds
from chesswhisper.engine_provision import STOCKFISH_RELEASE_TAG
from chesswhisper.paths import data_dir

CACHE_FILENAME = "eval_cache.sqlite3"
# Bumped when PositionEval gains data older entries lack (2: best_line_uci),
# so those games are analyzed again rather than served without it.
CACHE_FORMAT = 2


def cache_key(game: chess.pgn.Game, movetime_ms: int, depth: int | None) -> str:
    limit = f"depth:{depth}" if depth is not None else f"time:{movetime_ms}"
    moves = [(node.move.uci(), parse_clock_seconds(node.comment)) for node in game.mainline()]
    material = json.dumps([CACHE_FORMAT, STOCKFISH_RELEASE_TAG, limit, game.board().fen(), moves])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class EvalCache:
    def __init__(self, path: Path | None = None):
        self._path = path or data_dir() / CACHE_FILENAME
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS evals (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # A short-lived connection per call: the web UI reads and writes from
        # several analysis threads at once. Closed explicitly, since sqlite3's
        # own context manager only commits.
        with closing(sqlite3.connect(self._path, timeout=30)) as conn, conn:
            yield conn

    def get(self, key: str) -> list[PositionEval] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM evals WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return [PositionEval(**entry) for entry in json.loads(row[0])]
        except (ValueError, TypeError):
            return None

    def put(self, key: str, evals: list[PositionEval]) -> None:
        payload = json.dumps([dataclasses.asdict(ev) for ev in evals])
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO evals (key, payload) VALUES (?, ?)", (key, payload))

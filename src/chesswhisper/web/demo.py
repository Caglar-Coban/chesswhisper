"""Public demo mode and its seed data.

With CHESSWHISPER_DEMO=1 the web UI runs as a public showcase: a banner says
data resets, the login page shows the demo account, LLM commentary and API
key storage are off (strangers shouldn't paste real keys into a shared
server), and analyses are capped at DEMO_MAX_GAMES games. `seed-demo` fills
the demo account with analyses of the bundled games so a visitor sees a
populated report, trend, viewer and puzzles straight away.
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from importlib import resources
from pathlib import Path

DEMO_ENV = "CHESSWHISPER_DEMO"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-password"
DEMO_MAX_GAMES = 10
# Twelve public Lichess games (Lichess game data is CC0).
DEMO_PGN = "demo_games.pgn"
DEMO_PLAYER = "DrNykterstein"


def demo_mode() -> bool:
    return os.environ.get(DEMO_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def demo_pgn_path() -> Path:
    return Path(str(resources.files("chesswhisper.data").joinpath(DEMO_PGN)))


def _split_pgn(path: Path, parts: int) -> list[str]:
    """The file's games, oldest first, as *parts* PGN texts of roughly equal size."""
    import chess.pgn

    games = []
    with open(path, encoding="utf-8") as fh:
        while (game := chess.pgn.read_game(fh)) is not None:
            games.append(game)
    games.reverse()  # fetched newest first
    parts = max(1, min(parts, len(games)))
    size = -(-len(games) // parts)  # ceiling division
    return ["\n\n".join(str(g) for g in games[i:i + size]) + "\n" for i in range(0, len(games), size)]


def seed_demo(analyses: int = 3, movetime: int = 300, threads: int | None = None) -> int:
    """Create the demo account and its analyses unless it already has some; returns how many were added.

    The bundled games are split into *analyses* chronological groups, each
    saved as its own analysis a day apart, so the history has a trend.
    """
    from chesswhisper.cli import run_analysis
    from chesswhisper.web.app import save_analysis
    from chesswhisper.web.db import get_db, utcnow
    from chesswhisper.web.models import AnalysisRecord, User
    from chesswhisper.web.security import hash_password

    db = next(get_db())
    try:
        user = db.query(User).filter_by(username=DEMO_USERNAME).first()
        if user is None:
            user = User(username=DEMO_USERNAME, password_hash=hash_password(DEMO_PASSWORD), is_admin=False)
            db.add(user)
            db.commit()
        if db.query(AnalysisRecord).filter_by(user_id=user.id).count():
            return 0

        chunks = _split_pgn(demo_pgn_path(), analyses)
        now = utcnow()
        for i, text in enumerate(chunks):
            fd, path = tempfile.mkstemp(suffix=".pgn")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                aggregate, commentary, _ = run_analysis(
                    path, DEMO_PLAYER, "en", "none", movetime=movetime, threads=threads
                )
            finally:
                os.remove(path)
            save_analysis(db, user.id, aggregate, commentary, "en", "none",
                          created_at=now - timedelta(days=len(chunks) - 1 - i))
        return len(chunks)
    finally:
        db.close()

"""Where chesswhisper keeps its files: the Stockfish download, and for the web UI its database and keys."""

import os
from pathlib import Path

from platformdirs import user_data_dir

DATA_DIR_ENV = "CHESSWHISPER_DATA_DIR"


def data_dir() -> Path:
    """The data directory, created if missing.

    CHESSWHISPER_DATA_DIR overrides the per-user platform default, so a
    service account or a throwaway demo can point everything at one place.
    """
    override = os.environ.get(DATA_DIR_ENV)
    path = Path(override) if override else Path(user_data_dir("chesswhisper"))
    path.mkdir(parents=True, exist_ok=True)
    return path

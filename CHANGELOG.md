# Changelog

All notable changes to chesswhisper are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-26

First release.

### Command line

- `chesswhisper analyze` runs every position of a PGN through Stockfish and
  writes a Markdown report: accuracy, ACPL, inaccuracies/mistakes/blunders by
  game phase, opening repertoire with results, time-pressure error rates and a
  per-game breakdown, in Turkish or English.
- Optional coaching commentary from Claude or OpenAI (`--llm`), with keys from
  the environment or a `.env` file; a failed LLM call falls back to the
  stats-only report.
- `--lichess USER` / `--chesscom USER` with `--max N` fetch a player's latest
  public games instead of reading a file.
- Stockfish is found on `PATH`, via `--engine-path` or
  `CHESSWHISPER_STOCKFISH_PATH`, or downloaded automatically.
- `--player` is matched case-insensitively and auto-detected when omitted;
  `--movetime`/`--depth` must be positive; `--version` prints the version.

### Web UI

- `chesswhisper serve` starts a FastAPI app with registration, login, and an
  admin account created with `chesswhisper create-admin`.
- Per-user Anthropic/OpenAI keys, encrypted at rest; users can replace or
  remove them and change their own password.
- Analyses run in the background with a progress page and are saved to a
  per-user history with a trend chart of accuracy and ACPL.
- Reports show up to 12 of the costliest blunders and mistakes on boards, with
  the played move and the engine's choice drawn as arrows.
- Admin page to list users, view their history, reset passwords (which also
  lifts a lockout) and delete accounts.
- Hardening: bcrypt password hashes, a 15-minute lockout after 5 failed logins,
  CSRF tokens on every form, 5 MB upload limit, one running analysis per user.
- SQLite schema managed by Alembic migrations that run on startup.
- `CHESSWHISPER_DATA_DIR` relocates the database, keys and Stockfish download.

### Fixed before release

- Mate scores no longer inflate centipawn loss: evaluations are capped at ±10
  pawns when measuring a move's loss, so one missed mate can't push a game's
  ACPL into the thousands.
- Per-game tables in the report render as tables instead of literal pipes.
- Games fetched from Lichess include clock data, so time-management stats work
  for them.
- History times are shown in the viewer's time zone.

[0.1.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.1.0

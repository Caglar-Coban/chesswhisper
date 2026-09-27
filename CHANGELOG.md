# Changelog

All notable changes to chesswhisper are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.8.0] - 2026-09-27

### Added

- Spaced repetition for puzzles. A puzzle answered right comes back after
  1, 3, 7, 14 and then 30 days for each review in a row answered right; one
  answered wrong comes back the next day. Due reviews are offered first
  (most overdue first), then new puzzles, then the rest. A retry in the
  same sitting does not count as getting it right.
- The puzzle page shows whether a puzzle is new or due, how many reviews
  are due, and after a correct answer when it comes back.

### Changed

- Puzzles are no longer ordered new, then missed, then solved; see above.
  The schedule is worked out from attempts already saved, so there is no
  migration and past attempts count.

## [0.7.0] - 2026-09-27

### Added

- Puzzles have themes: the tactic in the solution (fork, pin, hanging
  piece, mate, back-rank mate, promotion), "defense" when the mistake
  allowed the opponent a tactic, or "positional". The puzzle page filters
  by theme, with a count for each, and the filter carries over to the next
  puzzle. Themes are worked out when puzzles are shown, so puzzles from
  analyses saved before 0.6.0 get them too.

## [0.6.0] - 2026-09-27

### Added

- Tactical motifs: every mistake and blunder is labeled with the tactic
  behind it (back-rank mate, mate, promotion, fork, pin or hanging piece)
  and whether the move allowed it or missed it, judged from the engine's
  refutation and preferred move with no extra engine time.
- Reports get a "Tactical Patterns" table, a key finding when one motif is
  behind at least 5 mistakes and 30% of them ("In 6 of your 15 mistakes
  and blunders you allowed a hanging piece."), and the LLM prompt gets the
  same counts.
- Critical-move boards in the web UI name the motif ("Allowed: fork").
  Analyses saved before this release show none.

## [0.5.0] - 2026-09-27

### Added

- `chesswhisper backup` writes the web UI's database and keys (and, with
  `--include-cache`, the evaluation cache) to one zip file. It is safe while
  the server runs.
- `chesswhisper restore FILE` puts a backup back. It validates the archive
  before changing anything, refuses to replace an existing database without
  `--force`, keeps the replaced files in a `pre-restore-*` folder, and
  refuses backups from a newer version.

## [0.4.0] - 2026-09-27

### Added

- Analyses submitted in the web UI or through the API go into a queue kept
  in the database (migration 0007). A restart no longer loses them: queued
  and interrupted analyses run again when the server starts, reusing cached
  evaluations for games already done. One interrupted three times is marked
  failed.
- `CHESSWHISPER_WORKERS` (default 2) limits how many analyses run at once.
  The progress page and the API's job status show a waiting analysis's
  place in line (`status: "queued"`, `position`).

### Changed

- Finished jobs are kept for 7 days instead of an hour.
- An LLM key is read when the analysis starts rather than held in memory
  with the job; if it was removed in between, the analysis fails with a
  message saying so.

## [0.3.0] - 2026-09-27

### Added

- Puzzles made from your own mistakes: each blunder or mistake with a better
  engine move becomes a "find the better move" puzzle. Unattempted puzzles
  come first, then missed ones, then solved ones; attempts are saved
  (migration 0006) and the same position from overlapping analyses appears
  once.
- JSON API under `/api/v1` with personal bearer tokens (created and revoked
  in Settings, stored hashed; migration 0005): start analyses, follow jobs,
  list, read and delete analyses. Interactive docs at `/api/docs`.
- Test coverage is measured in CI, which fails below 90% (currently about
  97%).

### Changed

- English is now the default for the web interface, the analyze form's report
  language and the command line's `--lang`. Turkish is one click (or
  `--lang tr`) away.
- The web form and the API start analyses through one shared function.
- The game viewer and the puzzles share one board-drawing script.

### Fixed

- Buttons meant to stay hidden until you answer a puzzle no longer show early
  (class styles were overriding the `hidden` attribute).
- Pages no longer request a missing `/favicon.ico`.

## [0.2.0] - 2026-09-27

### Analysis

- Stockfish runs with several threads (half the CPU cores, at most 4) and a
  128 MB hash: about 4x the positions searched in the same think time.
  `--threads` overrides it.
- Evaluations are cached per game on disk, so re-analyzing a player's recent
  games only runs the engine on new ones (a repeat 3-game run went from 105 s
  to under 2 s). `--no-cache` opts out.
- Reports break results down by time control and by opponent strength, show
  error rates by move number and by whether you were ahead, level or behind,
  and open with key findings drawn from those numbers. The LLM prompt gets the
  same data.

### Web UI

- Move-by-move game viewer with an evaluation graph and annotated move list;
  report pages list the games and every critical-move board links into it.
- Reports can be downloaded as Markdown or printed to PDF with a print layout.
- English interface alongside Turkish, switched from the header.
- Changing your password signs out your other sessions; an admin reset signs
  the user out everywhere (migration 0004).
- `CHESSWHISPER_ALLOW_REGISTRATION=0` closes self-service sign-up.
- Login attempts are limited to 20 per client address per 5 minutes.

### Build and tooling

- Docker image with Stockfish included, and a docker-compose file.
- CI builds the image and checks it serves; Dependabot keeps dependencies,
  actions and the base image current; tagged releases get a wheel and sdist.

### Fixed

- Trend chart dates use the viewer's time zone, like the history table.
- English error-pattern tables no longer head two columns "Moves".

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

[0.8.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.8.0
[0.7.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.7.0
[0.6.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.6.0
[0.5.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.5.0
[0.4.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.4.0
[0.3.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.3.0
[0.2.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.2.0
[0.1.0]: https://github.com/Caglar-Coban/chesswhisper/releases/tag/v0.1.0

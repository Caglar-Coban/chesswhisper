# chesswhisper

[![CI](https://github.com/Caglar-Coban/chesswhisper/actions/workflows/ci.yml/badge.svg)](https://github.com/Caglar-Coban/chesswhisper/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A590%25-brightgreen)](https://github.com/Caglar-Coban/chesswhisper/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

chesswhisper analyzes your chess games with Stockfish and turns the raw
engine numbers into a coaching report: accuracy and average centipawn loss,
where in the game your mistakes happen, how you do at each time control and
against stronger or weaker opponents, and, if you want it, a written summary
from Claude or OpenAI.

It runs as a command-line tool that writes a Markdown report, or as a small
multi-user web app with saved history, a progress trend chart, board
diagrams of your worst moves, a move-by-move game viewer, puzzles made from
your own mistakes and a JSON API. The web interface is available in English
and Turkish.

![A report: the analyzed games, then the costliest mistakes drawn on boards](docs/images/report.png)

## Features

- **Engine analysis.** Every position is evaluated with Stockfish (downloaded
  automatically, or one you already have), using several threads.
- **Cached evaluations.** Games analyzed before are read back from disk, so
  re-running a player's recent games only analyzes the new ones.
- **Your stats, not the game's.** Accuracy, ACPL, and inaccuracies, mistakes
  and blunders split by opening, middlegame and endgame, for the player you
  pick.
- **Breakdowns.** Results and accuracy by time control (bullet, blitz, rapid,
  classical) and by opponent strength, plus error rates by move number and by
  whether you were ahead, level or behind.
- **Tactical motifs.** Each mistake and blunder is labeled with the tactic
  behind it (fork, pin, hanging piece, mate, back-rank mate, promotion) and
  whether you allowed it or missed it.
- **Recent form.** Your newest games against the ones before them.
- **Endgames.** By kind of endgame, whether you win the winning ones, hold
  the level ones and save the losing ones.
- **Filters.** Narrow an analysis to some time controls or to your games
  with one colour, up front or afterwards from the report page.
- **Missed chances.** How many of your opponents' mistakes you punished,
  and what the ones you let go needed.
- **Key findings.** The clearest patterns stated in a sentence each, such as
  "your mistakes cluster in moves 31-40", with minimum sample sizes so a few
  games don't produce noise.
- **Opening repertoire and theory.** Openings named from the Lichess
  opening list with your record in each, where each game left theory, who
  left it and how the next ten moves went.
- **Time management.** Your error rate with under 30 seconds on the clock.
- **Games straight from Lichess or chess.com** by username.
- **Optional coaching commentary** from Claude or OpenAI, in English or
  Turkish, written from all of the above.
- **Puzzles from your own mistakes.** Every blunder and mistake becomes a
  "find the better move" puzzle in the position where you went wrong, with
  your progress tracked and a filter by theme (fork, pin, hanging piece,
  mate, defense...).
- **Web UI** with accounts, encrypted per-user API keys, background analysis
  with a progress page, history with a trend chart, critical-move boards, a
  game viewer, Markdown download and a print/PDF layout.
- **JSON API** with personal tokens and interactive OpenAPI docs, for
  scripting analyses or building another front end.

<table>
  <tr>
    <td width="50%"><img src="docs/images/game-viewer.png" alt="Game viewer with board, evaluation graph and annotated move list"></td>
    <td width="50%"><img src="docs/images/report-stats.png" alt="Key findings, time-control and opponent breakdowns, and error patterns"></td>
  </tr>
  <tr>
    <td>Step through any analyzed game: board, evaluation graph and a move list marked <code>?!</code>, <code>?</code> and <code>??</code>.</td>
    <td>Key findings, performance by time control and opponent strength, and error patterns.</td>
  </tr>
  <tr>
    <td><img src="docs/images/history-trend.png" alt="History page with the accuracy and ACPL trend chart"></td>
    <td><img src="docs/images/analyze-form.png" alt="The analyze form"></td>
  </tr>
  <tr>
    <td>Accuracy and ACPL over time for each player you've analyzed.</td>
    <td>Upload a PGN or fetch games by Lichess / chess.com username.</td>
  </tr>
  <tr>
    <td><img src="docs/images/puzzle.png" alt="A puzzle built from one of your mistakes, with a piece selected and its legal moves marked"></td>
    <td><img src="docs/images/api-docs.png" alt="Interactive API documentation"></td>
  </tr>
  <tr>
    <td>Puzzles from your own games: click a piece to see its moves, then find the engine's choice.</td>
    <td>The JSON API, documented and testable at <code>/api/docs</code>.</td>
  </tr>
</table>

## Quick start

With Docker (Stockfish is built into the image):

```
git clone https://github.com/Caglar-Coban/chesswhisper.git
cd chesswhisper
docker compose up -d
docker compose exec chesswhisper chesswhisper create-admin
```

Then open http://127.0.0.1:8000.

With Python 3.11 or newer:

```
pip install ".[web]"        # or just "." for the command line alone
chesswhisper analyze --lichess YourLichessName --max 10 --lang en -o report.md
```

The first run downloads Stockfish (about 100 MB) into chesswhisper's data
directory. At the default 0.3 seconds per position a typical game takes 20-40
seconds to analyze the first time and well under a second after that.

## How it works

```mermaid
flowchart LR
    A[PGN file] --> L[pgn_loader]
    B[Lichess / chess.com API] --> F[game_sources] --> L
    L --> E[engine_analyzer<br/>Stockfish, per position]
    C[(eval_cache<br/>SQLite)] <--> E
    E --> S[stats_engine<br/>accuracy, ACPL, phases,<br/>breakdowns, critical moves]
    S --> I[insights<br/>key findings]
    S --> R[report_writer<br/>Markdown, TR/EN]
    I --> R
    S --> P[llm<br/>Claude / OpenAI]
    I --> P
    P --> R
    R --> O1[report.md]
    R --> W[FastAPI web UI<br/>and JSON API]
    S --> T[timeline<br/>move-by-move data] --> W
    S --> Z[puzzles<br/>from critical moves] --> W
    W --> DB[(SQLite + Alembic)]
```

The command line and the web UI share one pipeline (`run_analysis` in
`cli.py`). The web app runs it on a background thread per analysis, reports
progress to the page, and stores the report, the critical moves and each
game's timeline so the report, boards and viewer can be rebuilt without
re-running the engine. The web form and the JSON API start analyses through
the same function, so they validate and behave identically.

### How the numbers are computed

- **Centipawn loss** is how much the engine's evaluation drops, from the
  mover's side, between the position before and after a move. Evaluations are
  capped at ±10 pawns first, as Lichess does, so missing a mate while still a
  queen up doesn't count as a 99,000-centipawn loss.
- **Classification:** a loss of 50+ cp is an inaccuracy, 100+ a mistake, 300+
  a blunder. "Error rate" in the breakdowns counts mistakes and blunders.
- **Accuracy** converts evaluations to winning chances and scores each move by
  how much of them it gave away, using Lichess's published formula, then
  averages over your moves.
- **Phases:** the first 20 plies are the opening; after that it's an endgame
  once the queens, rooks and minor pieces left are worth 20 points or less
  (Q=9, R=5, B=N=3), and a middlegame otherwise. Games starting from a custom
  FEN are classified by material only.
- **Time controls** follow Lichess: base time plus 40 increments, under 3
  minutes is bullet, under 8 blitz, under 25 rapid, else classical.
- **Opponent strength** compares ratings from the PGN: 100+ Elo above you is
  stronger, 100+ below is weaker.
- **Ahead / behind** means the evaluation before your move was at least two
  pawns in your favor / against you.
- **Time pressure** means under 30 seconds on your clock when you moved.

## Command line

```
chesswhisper analyze games.pgn --player "YourName" --llm claude -o report.md
chesswhisper analyze --lichess YourLichessName --max 20 --lang en -o report.md
chesswhisper analyze --chesscom YourChessComName --llm claude
```

| Option | What it does |
|--------|--------------|
| `PGN_PATH` / `--lichess USER` / `--chesscom USER` | Where the games come from. Give exactly one. |
| `--max N` | With `--lichess`/`--chesscom`: how many recent games to fetch, 1-50 (default 10). Only public, standard-chess games are used. |
| `--player NAME` | Whose moves to analyze, matched against the PGN's `White`/`Black` headers (case-insensitive). Defaults to the fetched username, or to the name that appears in the most games of the file. |
| `--lang {en,tr}` | Report language (default `en`). Opening names stay in their standard English form. |
| `--llm {claude,openai,none}` | Coaching commentary provider (default `none`, stats only). |
| `-o, --output PATH` | Where to write the Markdown report (default `report.md`). |
| `--movetime MS` | Engine think time per position in milliseconds (default 300). |
| `--depth N` | Fixed search depth instead of a time limit. |
| `--threads N` | Stockfish threads (default: half your CPU cores, at most 4). |
| `--no-cache` | Re-analyze every game instead of reusing saved evaluations. |
| `--time-control TC` | Only games at this time control (`bullet`, `blitz`, `rapid`, `classical`, `correspondence`); repeat for several. |
| `--color COLOR` | Only games where the player had this colour (`white` or `black`). |
| `--engine-path PATH` | Use this Stockfish binary instead of downloading one. |

`chesswhisper --version` prints the installed version.

`--llm claude` reads `ANTHROPIC_API_KEY` and `--llm openai` reads
`OPENAI_API_KEY`, from the environment or from a `.env` file in the current
directory (already in `.gitignore`). If the LLM call fails you still get the
stats-only report and a warning.

## Web UI

```
pip install ".[web]"
chesswhisper create-admin          # prompts for a username and password
chesswhisper serve                  # http://127.0.0.1:8000
chesswhisper backup                 # database and keys to one zip; `restore` puts it back
```

- **Analyze:** upload a PGN (up to 5 MB) or type a Lichess or chess.com
  username. A progress page counts games as they're analyzed and opens the
  report when it's done; it's safe to leave and come back from History. Each
  user runs one analysis at a time.
- **Report:** the games with links into the viewer, up to 12 of the costliest
  blunders and mistakes on boards (the move played in red, the engine's choice
  in green, each linking to that moment in the game), then the full report.
  Download it as Markdown or print it to PDF.
- **Game viewer:** board, evaluation graph and annotated move list; click,
  use the arrow keys or Home/End, and flip the board. An arrow shows the
  engine's move, and you can play its line out on the board.
- **Repertoire:** an opening explorer over your analysed games: the moves
  you and your opponents played in each position, your score, how often
  your move was an error, and where you left theory.
- **Puzzles:** your blunders and mistakes as "find the better move"
  exercises (tactical ones play out the engine's line over several moves),
  scheduled with spaced repetition: reviews that are due come
  first, then new puzzles; one you get right comes back after 1, 3, 7, 14
  and then 30 days, one you miss comes back the next day. The same position
  from overlapping analyses appears once. Filter them by theme to practice
  one kind of tactic.
- **History:** every past analysis and a trend chart of accuracy and ACPL for
  each player. Tick two analyses to compare them side by side.
- **Settings:** store, replace or remove your own Anthropic/OpenAI keys
  (encrypted at rest), change your password, and create or revoke an API
  token.
- **Automatic analysis:** link a Lichess or chess.com account in Settings
  and its new games are analysed (without LLM commentary) about once an
  hour and added to History; "Check now" looks straight away. A badge on
  the History link counts the ones you haven't looked at yet.
- **Admin:** list users, see who is locked out, view anyone's history, reset
  a password or delete an account.
- **Language:** English by default; switch to Turkish from the header.
- **Theme:** follows the system's light or dark mode by default; the header
  link switches to always light or always dark.

Security: bcrypt password hashes; a 15-minute lockout after 5 failed logins
on one account and at most 20 login attempts per client address per 5
minutes; CSRF tokens on every form; changing a password signs out your other
sessions, and an admin reset signs the user out everywhere. Sign-up can be
closed with `CHESSWHISPER_ALLOW_REGISTRATION=0`.

The server binds to `127.0.0.1` by default and is meant for you and people
you trust, not the open internet: it has no HTTPS of its own and no email
verification. The [deployment guide](docs/deployment.md) covers running it
as a service or in Docker, reaching it from other machines, backups and
upgrades.

## JSON API

Create a token under **Settings → API access** (it's shown once and stored
hashed), then send it as a bearer token. Interactive documentation is at
`/api/docs`.

```
TOKEN=cw_...
curl -X POST http://127.0.0.1:8000/api/v1/analyses \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"source": "lichess", "username": "YourLichessName", "max_games": 10}'
# add "time_controls": ["blitz"] or "color": "black" to narrow it
# -> 202 {"job_id": "...", "status_url": "/api/v1/jobs/..."}

curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/jobs/<job_id>
# -> {"status": "done", "completed": 10, "total": 10, "analysis_url": "/api/v1/analyses/12", ...}

curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/v1/analyses/12?include_moves=true"
```

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/me` | Who the token belongs to. |
| `POST /api/v1/analyses` | Start an analysis from PGN text (`"source": "pgn"`) or a Lichess / chess.com username. `409` if one is already running. |
| `GET /api/v1/jobs/{id}` | Progress, warnings, and the analysis URL once done. |
| `GET /api/v1/analyses` | Your analyses, newest first. |
| `GET /api/v1/analyses/{id}` | Report, critical moves and games; `?include_moves=true` adds every move with its evaluation. |
| `DELETE /api/v1/analyses/{id}` | Delete an analysis. |

## Configuration

| Environment variable | Purpose |
|----------------------|---------|
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | LLM keys for the command line (the web UI uses per-user keys). |
| `CHESSWHISPER_STOCKFISH_PATH` | Use this Stockfish binary. A `stockfish` on `PATH` is also picked up. |
| `CHESSWHISPER_DATA_DIR` | Where to keep Stockfish, the evaluation cache and the web UI's database and keys. Defaults to the per-user data directory (`~/.local/share/chesswhisper` on Linux, `%LOCALAPPDATA%\chesswhisper\chesswhisper` on Windows); `/data` in Docker. |
| `CHESSWHISPER_ALLOW_REGISTRATION` | `0` closes self-service sign-up in the web UI. |
| `CHESSWHISPER_WORKERS` | How many web analyses run at once (default 2); the rest wait in a queue that survives restarts. |
| `CHESSWHISPER_SYNC_MINUTES` | Minutes between checks for linked accounts' new games (default 60, at least 5; 0 turns the automatic checks off). |
| `CHESSWHISPER_DATABASE_URL` | Keep the web UI's database in PostgreSQL (`postgresql://user:password@host/db`) instead of the SQLite file in the data directory. Needs `pip install ".[web,postgres]"`. See the [deployment guide](docs/deployment.md#postgresql-instead-of-sqlite). |
| `CHESSWHISPER_DEMO` | `1` runs the web UI as a public demo: a banner, a one-click demo login, no LLM commentary or stored API keys, and at most 10 games per analysis. `chesswhisper seed-demo` fills the demo account. See the [deployment guide](docs/deployment.md#a-public-demo-on-hugging-face-spaces). |

## Development

```
pip install -e ".[dev,web]"
ruff check src tests   # lint
mypy src               # type-check
pytest --cov           # about 400 tests with coverage; a real-Stockfish smoke test runs when one is on PATH
```

CI runs lint, type checks and tests on Linux and Windows with Python 3.11,
3.12 and 3.13, fails if test coverage drops below 90% (it is about 97%; each
run's summary shows the exact figure), and builds the Docker image and checks
it serves requests.
Dependabot keeps dependencies, actions and the base image current. Pushing a
`v*` tag builds the wheel and source distribution and attaches them to the
GitHub release.

The web UI's SQLite schema is managed by Alembic. Migrations live in
`src/chesswhisper/web/migrations/versions/` and run automatically when the app
starts. To change the schema:

```
alembic upgrade head                           # bring the scratch dev.db up to date
# edit src/chesswhisper/web/models.py
alembic revision --autogenerate -m "what changed"
```

Review the generated file before committing it; `pytest` fails if the models
and the migrations disagree.

### Project layout

```
src/chesswhisper/
  cli.py              command line and the shared analysis pipeline
  pgn_loader.py       PGN parsing
  game_sources.py     Lichess / chess.com fetching
  engine_provision.py finding or downloading Stockfish
  engine_analyzer.py  per-move engine evaluations
  eval_cache.py       on-disk cache of evaluations per game
  stats_engine.py     accuracy, ACPL, phases, breakdowns, critical moves
  insights.py         key findings
  timeline.py         move-by-move data for the game viewer
  report_writer.py    Markdown report (with i18n.py for Turkish/English)
  llm/                Claude / OpenAI commentary
  paths.py            the data directory
  web/
    app.py            pages, forms and start_analysis
    api.py            the JSON API
    puzzles.py        puzzles from critical moves
    jobs.py           background analysis jobs
    models.py         SQLAlchemy models (schema managed by migrations/)
    ui_text.py        interface text in English and Turkish
    templates/        Jinja pages; charts, boards and viewer are plain JS + SVG
docs/
  deployment.md       running the web UI as a service or in Docker
  superpowers/        design specs and implementation plans for each feature
```

Built with Python, [python-chess](https://python-chess.readthedocs.io/),
Stockfish, FastAPI, SQLAlchemy and Alembic, Jinja2, and plain JavaScript and
SVG for the charts, boards and viewer (no front-end framework or build step).

See [CHANGELOG.md](CHANGELOG.md) for what changed between versions.

## License

MIT, see [LICENSE](LICENSE).

The opening names in `src/chesswhisper/data/eco.tsv` come from
[lichess-org/chess-openings](https://github.com/lichess-org/chess-openings),
which is in the public domain (CC0).

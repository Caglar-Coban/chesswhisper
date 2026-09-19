# chesswhisper

![CI](https://github.com/Caglar-Coban/chesswhisper/actions/workflows/ci.yml/badge.svg)

A CLI tool that analyzes chess games with Stockfish and turns the raw
engine evaluations into natural-language coaching feedback via an LLM
(Claude or OpenAI).

## Status

Both the CLI and a local web UI are implemented — see
[`docs/superpowers/specs/2026-09-19-chesswhisper-design.md`](docs/superpowers/specs/2026-09-19-chesswhisper-design.md)
and
[`docs/superpowers/specs/2026-09-19-chesswhisper-web-ui-design.md`](docs/superpowers/specs/2026-09-19-chesswhisper-web-ui-design.md)
for the full design specs.

## Usage

Install (editable, for development):

```
pip install -e ".[dev]"
```

Analyze a PGN file:

```
chesswhisper analyze games.pgn --player "YourName" --lang tr --llm claude -o report.md
```

- `--player` — your name as it appears in the PGN `White`/`Black` headers. If omitted, chesswhisper tries to auto-detect it from whichever name repeats most often across the file's games.
- `--lang {tr,en}` — report language (default: `tr`). Opening names are always kept in their standard international form.
- `--llm {claude,openai,none}` — coaching-commentary provider (default: `none`, stats-only). `claude` requires `ANTHROPIC_API_KEY`; `openai` requires `OPENAI_API_KEY` in the environment.
- `-o, --output` — output Markdown path (default: `report.md`).
- `--movetime` — milliseconds of engine think time per position (default: `300`).
- `--depth` — fixed search depth; overrides `--movetime` when set.
- `--engine-path` — path to an already-installed Stockfish binary (skips auto-download). `CHESSWHISPER_STOCKFISH_PATH` env var works the same way; a `stockfish` binary already on `PATH` is also picked up automatically.

The first run downloads a matching Stockfish binary automatically and caches it locally, unless an existing installation is found via `--engine-path`, `CHESSWHISPER_STOCKFISH_PATH`, or `PATH`.

### API keys via `.env`

Instead of exporting `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in your shell, you can
put them in a `.env` file in the directory you run `chesswhisper` from:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

`.env` is already listed in `.gitignore` — never commit it.

## Web UI

For a browser-based alternative to the CLI, with accounts, per-user API
keys, and analysis history, install the `web` extra:

```
pip install -e ".[web]"
```

Create the first admin account:

```
chesswhisper create-admin
```

Then start the server:

```
chesswhisper serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`: anyone who can reach this address can
register their own account. Each user sets their own Anthropic/OpenAI API
key under **Settings** (encrypted at rest, never in an environment
variable shared across users) and every analysis they run is saved to
their **History**, where they can also delete their own past records.
The admin account additionally sees an **Admin** page listing every
registered user, can view any user's history, delete a user, or reset a
user's password.

A few hardening details: accounts lock for 15 minutes after 5 consecutive
failed login attempts, and every state-changing form (login, register,
settings, analyze, history/user deletion, password reset) is protected
against CSRF via a per-session token.

This is still a local-only tool: the server binds to `127.0.0.1` by
default and isn't hardened for or intended to be exposed on the public
internet (no HTTPS, no rate limiting, no email verification) — the
threat model is "a few people who already have access to this machine,"
not the open internet.

## Development

```
pip install -e ".[dev]"
ruff check src tests   # lint
mypy src                # type-check
pytest -v               # tests
```

CI (`.github/workflows/ci.yml`) runs all three on every push and PR, across
Python 3.11/3.12 on Linux and Windows.

## License

MIT — see [LICENSE](LICENSE).

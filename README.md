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

For a browser-based alternative to the CLI, install the `web` extra and start
the server:

```
pip install -e ".[web]"
chesswhisper serve --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000` in a browser: upload a `.pgn` file, pick a
player/language/coaching-provider, and the rendered report appears on the
same page once analysis finishes (this can take a few minutes for larger
files — the page shows a loading state while it waits). `--engine-path`,
`CHESSWHISPER_STOCKFISH_PATH`, and `.env`-provided API keys all work the same
way as with the CLI. The server binds to localhost only by default and isn't
intended to be exposed on a public network.

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

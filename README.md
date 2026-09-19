# chesswhisper

![CI](https://github.com/Caglar-Coban/chesswhisper/actions/workflows/ci.yml/badge.svg)

A CLI tool that analyzes chess games with Stockfish and turns the raw
engine evaluations into natural-language coaching feedback via an LLM
(Claude or OpenAI).

## Status

Core CLI implemented — see [`docs/superpowers/specs/2026-09-19-chesswhisper-design.md`](docs/superpowers/specs/2026-09-19-chesswhisper-design.md)
for the full design spec. A web UI is planned; see
[`docs/superpowers/specs/2026-09-19-chesswhisper-web-ui-design.md`](docs/superpowers/specs/2026-09-19-chesswhisper-web-ui-design.md).

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

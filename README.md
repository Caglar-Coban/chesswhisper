# chesswhisper

A CLI tool that analyzes chess games with Stockfish and turns the raw
engine evaluations into natural-language coaching feedback via an LLM
(Claude or OpenAI).

## Status

Implementation in progress — see [`docs/superpowers/specs/2026-09-19-chesswhisper-design.md`](docs/superpowers/specs/2026-09-19-chesswhisper-design.md)
for the full design spec.

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

The first run downloads a matching Stockfish binary automatically and caches it locally.

## License

MIT — see [LICENSE](LICENSE).

# chesswhisper

A CLI tool that analyzes chess games with Stockfish and turns the raw
engine evaluations into natural-language coaching feedback via an LLM
(Claude or OpenAI).

## Status

Early design stage — see [`docs/superpowers/specs/2026-09-19-chesswhisper-design.md`](docs/superpowers/specs/2026-09-19-chesswhisper-design.md)
for the full design spec. Implementation not started yet.

## Planned usage

```
chesswhisper analyze games.pgn --lang tr --llm claude -o report.md
```

Given a local PGN file, it will:
- Evaluate every position with Stockfish.
- Compute accuracy/ACPL, blunder/mistake/inaccuracy counts by game phase,
  opening repertoire stats, and time-management analysis (when clock data
  is present).
- Use an LLM to explain the results in plain Turkish or English.
- Write everything to a Markdown report.

## License

MIT — see [LICENSE](LICENSE).

# chesswhisper — Design Spec

Date: 2026-09-19
Status: Approved (pending implementation plan)

## 1. Overview

`chesswhisper` is a personal CLI tool that takes a local PGN file, analyzes
every game with the Stockfish engine, computes standard chess statistics
(accuracy, blunders, opening repertoire, time management), and uses an LLM
to translate the raw engine evaluations into natural-language coaching
commentary. Output is a single Markdown report per run.

## 2. Goals

- Analyze one or more games from a local `.pgn` file with Stockfish.
- Compute: ACPL/accuracy%, blunder/mistake/inaccuracy counts by game phase
  (opening/middlegame/endgame), opening repertoire stats by ECO code, and
  time-management analysis when clock tags are present in the PGN.
- Turn those stats (plus the most critical positions) into natural-language
  coaching feedback via a pluggable LLM provider (Claude or OpenAI).
- Produce a Markdown report, in Turkish or English depending on a `--lang`
  flag.
- Auto-provision the Stockfish binary if it isn't already installed.
- Ship as a private GitHub repo under the user's own account, single
  contributor.

## 3. Non-goals (v1)

- No web UI, no persistent database, no multi-user support.
- No live game fetching from chess.com/Lichess APIs — PGN input only.
- No real-time / in-game analysis — batch analysis of completed games only.
- No support for LLM providers beyond Claude and OpenAI in v1.

## 4. Architecture

```
.pgn file
   |
   v
pgn_loader ---> list[Game]
   |
   v
engine_analyzer (Stockfish UCI, per-position eval)
   |
   v
stats_engine (ACPL, blunders, openings, clock usage)
   |
   v
llm_coach (LLMProvider: Claude | OpenAI | none)
   |
   v
report_writer ---> report.md (TR/EN)
```

Language: Python 3.11+. Chess/PGN handling via `python-chess`. CLI via
`click` (or `typer` — decide during implementation, functionally
equivalent for this scope).

## 5. Components

### 5.1 `pgn_loader`
- Parses the input `.pgn` file with `python-chess`.
- Returns a list of parsed `Game` objects (one file may contain multiple
  games, e.g. a full chess.com export).
- On a malformed game, skips it, records a warning with the game index,
  and continues with the rest of the file.

### 5.2 `engine_analyzer`
- Spawns Stockfish as a UCI subprocess (`python-chess`'s `engine` module).
- For each position in each game, requests an evaluation (centipawn or
  mate score) and the engine's best move, at a fixed depth/time budget
  (configurable, sane default e.g. depth 14 or 0.3s/move — tuned during
  implementation for speed/accuracy trade-off).
- If no Stockfish binary is found on `PATH` or in a configured location,
  runs the auto-download step (see 5.2.1) before analysis.

#### 5.2.1 Stockfish auto-provisioning
- Detects OS (Windows/Linux/macOS) and CPU architecture.
- Downloads the matching official Stockfish release binary into a local
  app-data directory (not into the repo).
- Verifies the binary runs (`stockfish` responds to a UCI handshake)
  before proceeding.
- If auto-download fails (no network, unsupported platform), exits with a
  clear error message telling the user how to install Stockfish manually
  and point the tool at it via config/flag.

### 5.3 `stats_engine`
Takes the raw per-position evaluations and produces:
- **Accuracy metrics**: ACPL (average centipawn loss) and an accuracy %
  per player per game, using the standard formula (eval-loss based, same
  spirit as chess.com/Lichess accuracy).
- **Error classification**: each move is classified as blunder / mistake
  / inaccuracy / fine, based on centipawn-loss thresholds, and tagged
  with game phase (opening: first N fully-booked moves or until a
  repertoire-database cutoff proxy — implementation detail; middlegame;
  endgame: heuristic based on material count).
- **Opening repertoire**: groups games by ECO code (derived from opening
  moves via `python-chess`'s opening book data or a bundled ECO table),
  with win/loss/draw rate per opening.
- **Time management**: only computed when the PGN has `%clk` comments;
  correlates low time remaining with error rate. Skipped (reported as
  "not available") when clock data is absent.

Output of this stage is a plain data structure (dataclasses), independent
of language/formatting — this is what gets serialized for the LLM prompt
and the report.

### 5.4 `llm_coach`
- `LLMProvider` abstract base class with one method, e.g.
  `generate_commentary(stats: GameStats, lang: str) -> str`.
- Two concrete implementations: `ClaudeProvider` (Anthropic API) and
  `OpenAIProvider` (OpenAI API). API keys read from environment variables
  / a local `.env` file, never committed (`.gitignore`'d).
- A `NoneProvider` (or `--llm none` flag) that skips LLM commentary
  entirely — the report still contains all computed statistics, just no
  natural-language coaching section. This is the fallback when no API key
  is configured or the API call fails.
- Prompt includes: the computed stats summary + a short list of the most
  significant blunders/mistakes (with FEN/move context) so the LLM can
  give concrete, position-grounded advice rather than generic commentary.

### 5.5 `report_writer`
- Renders a Markdown report from: stats + LLM commentary (if any).
- Two string tables (TR/EN) for all static labels/headers; numbers and
  computed data are language-independent.
- Report includes, per game and as an aggregate summary across all games
  in the file: accuracy/ACPL, error counts by phase, opening stats, time
  management (if available), and the LLM coaching section.

### 5.6 `cli`
Example invocation:
```
chesswhisper analyze games.pgn --lang tr --llm claude -o report.md
```
Flags:
- `analyze <pgn_path>` — required positional argument.
- `--lang {tr,en}` — default `tr`.
- `--llm {claude,openai,none}` — default reads from config if set, else
  prompts/errors if ambiguous.
- `-o, --output <path>` — default `report.md` in the current directory.
- `--depth` / `--movetime` — optional engine tuning knobs, sane defaults.

## 6. Error Handling

| Situation | Behavior |
|---|---|
| Stockfish not found, auto-download fails | Exit with clear error + manual-install instructions |
| Malformed game in PGN | Skip that game, warn with index, continue |
| Invalid/missing PGN file path | Exit immediately with a clear error |
| LLM API key missing or API call fails | Fall back to stats-only report, warn in output |
| Report output path not writable | Exit with clear error before doing engine work (fail fast) |

## 7. Testing Strategy

- **Unit tests, `stats_engine`**: feed fixed/synthetic sequences of
  centipawn evaluations, assert expected ACPL, accuracy%, and
  blunder/mistake/inaccuracy counts.
- **Unit tests, `pgn_loader`**: small bundled sample PGNs (valid,
  multi-game, and deliberately malformed) — assert correct parsing and
  correct skip-and-warn behavior.
- **Mocked tests, `engine_analyzer` and `llm_coach`**: no real Stockfish
  process or real API calls in the default test run; both are mocked at
  their interface boundary.
- **One end-to-end smoke test**: a tiny real PGN run through a real
  Stockfish binary (skipped automatically if Stockfish isn't available in
  the environment, e.g. in CI without it installed), asserting the report
  file is produced and non-empty.

## 8. Repo & Tooling

- Location: `C:\Users\Omen\Desktop\Projects\chesswhisper`.
- GitHub: new **private** repository under the user's own account, single
  contributor. Commits use only the user's configured git identity — no
  AI/tool co-authorship, per the user's global git policy.
- License: MIT.
- `.gitignore`: Python defaults + `.env` + downloaded Stockfish binaries
  (they live outside the repo in an app-data dir, not committed).
- Dependency management: `pyproject.toml` (or `requirements.txt` —
  decided during implementation), pinned key deps (`python-chess`,
  `click`/`typer`, `anthropic`, `openai`).

## 9. Open Items for Implementation Planning

- Exact centipawn-loss thresholds for blunder/mistake/inaccuracy
  classification (use commonly accepted values, e.g. Lichess-style
  cutoffs, unless the user wants custom ones).
- Exact engine depth/movetime default — balance analysis speed vs.
  accuracy for a typical game-length PGN.
- Whether ECO/opening name lookup uses a bundled static table or
  `python-chess`'s polyglot/opening data.

These are implementation-detail decisions, not open design questions —
the writing-plans step will pin down concrete values.

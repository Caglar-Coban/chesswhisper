# chesswhisper Critical Moves on the Board — Design Spec

Date: 2026-09-25
Status: Draft (pending user review)

## 1. Overview

A saved report lists each game's worst moves as bare SAN ("Top mistakes:
Qxd5, Nf6"). To learn from a mistake, the reader has to open the game
elsewhere and find the position. This feature stores the analysis's most
costly moves with the position they were played from and the engine's
preferred move. The report page then shows each one on a board: the
played move as a red arrow, the engine's move as a green arrow, and the
evaluation swing underneath.

This is the first schema change since Alembic was introduced (spec
`2026-09-25-chesswhisper-alembic-design.md`), shipped as migration
`0002`.

## 2. Goals

- For every new web analysis, persist up to **12** critical moves across
  all analyzed games. Critical means classified `blunder` or `mistake` by
  `stats_engine.classify_move` (inaccuracies excluded). They are ranked by
  centipawn loss, largest first.
- The report page (`GET /history/{id}` and the admin's
  `GET /admin/users/{uid}/history/{id}`) shows a "Kritik hatalar" card
  above the report. It holds a responsive grid of boards: 3 columns on
  desktop, 1 on phones.
- Each board shows:
  - the position before the move, oriented from the analyzed player's
    side
  - a red arrow for the played move and a green arrow for the engine's
    best move (one arrow only when they are the same move)
  - a caption: game number, opponent, move number with SAN
    (`24... Qxd5`), the classification, "Daha iyisi: <best SAN>", and
    the evaluation before → after from the player's point of view in
    pawns (`+1.2 → −3.4`), with mate scores shown as `#N` / `−#N`.
- Boards are rendered server-side with `chess.svg` (python-chess, already
  a dependency). No JavaScript and no new dependencies.
- Records without the data (saved before this feature, or with no
  blunders/mistakes) show no card. Unreadable data also shows no card
  and never breaks the page.

## 3. Non-Goals

- No change to the CLI report, the Markdown report text, or the LLM
  prompt.
- No interactive board (move stepping, variations, PGN export).
- No backfill for existing records (it would require re-running
  Stockfish).
- No separate `critical_moves` table: the data is always read together
  with its record and never queried on its own.

## 4. Design

### 4.1 Analyzer (`engine_analyzer.py`)

`PositionEval` gains three fields, all defaulting to `None` so existing
constructors and tests keep working:

- `fen_before: str | None`: `board.fen()` before the move is pushed.
- `move_uci: str | None`: the played move's UCI.
- `best_move_uci: str | None`: the first move of the engine's principal
  variation from the analysis of the position *before* the move. This is
  the `info["pv"][0]` that `analyse` already returns, so the engine does
  no extra work. `None` when the engine returns no PV.

`analyze_game` keeps the previous `info` (not just its score) so the
before-move PV is available.

### 4.2 Selection (`stats_engine.py`)

A new function:

```python
def critical_moves(game_reports: list[GameReport], limit: int = 12) -> list[dict]
```

It walks each report's `top_mistakes`, which are already the player's
flagged moves sorted by loss (up to 5 per game), and keeps
`blunder`/`mistake` entries whose `fen_before` and `move_uci` are
present. It sorts them by `cp_loss` descending, with ties broken by game
index and then ply so the order is stable, and returns at most `limit`
dicts:

```
{"game_index", "opponent", "your_color", "ply", "move_number", "san",
 "uci", "best_uci", "best_san", "fen_before", "cp_before", "cp_after",
 "cp_loss", "classification"}
```

`move_number` is `(ply + 1) // 2`. `best_san` is computed from
`fen_before` + `best_uci` (`None` if `best_uci` is missing or not legal
in that position). Five per game is enough: a game's sixth-worst move
can never be in the analysis-wide top 12 ahead of that game's top five.

### 4.3 Storage

- `AnalysisRecord.critical_moves_json: Mapped[str | None] =
  mapped_column(Text, default=None)`.
- Migration `versions/0002_critical_moves.py` (`down_revision =
  "0001_baseline"`): `batch_alter_table("analysis_records")` adds the
  nullable `Text` column. `downgrade()` drops it.
- `_run_analysis_job` in `web/app.py` stores
  `json.dumps(critical_moves(aggregate.games))` when the list is
  non-empty, and `None` otherwise.

### 4.4 Rendering (`web/boards.py`)

A new module holds the pure presentation logic:

```python
def board_cards(critical_moves_json: str | None) -> list[BoardCard]
```

It parses the JSON and returns cards with the caption fields and an
`svg` string. Any `ValueError`/`KeyError`/`TypeError` from bad JSON, a
bad FEN, or bad UCI returns `[]` (logged at warning level). The SVG comes
from:

```python
chess.svg.board(board, orientation=<player color>, arrows=[...], size=None,
                coordinates=True, colors=<site palette>)
```

- Played-move arrow: `#c0392bcc`. Best-move arrow: `#2e8b57cc`.
- The board squares use muted colors that work in both themes: light
  `#e9e4d8`, dark `#a9b3bf`.
- Coordinates stay on.

The SVG is produced entirely from validated `chess` objects, never from
raw user strings, and is inserted with `|safe`. Caption text goes through
normal Jinja autoescaping.

The eval format helper: `abs(cp) >= 90_000` is mate. Mate-in-N is
recovered as `100_000 - abs(cp)` (python-chess's `mate_score` encoding)
and shown as `#N` with the sign. Otherwise the value is shown as
`f"{cp / 100:+.1f}"` with a real minus sign (U+2212).

### 4.5 Template

`history_detail.html` renders the card only when `cards` is non-empty:

- a `.section-title` "Kritik hatalar" with a muted hint line ("Kırmızı:
  oynanan hamle · Yeşil: motorun önerisi")
- a CSS grid `.board-grid`: `repeat(auto-fill, minmax(220px, 1fr))`,
  12px gap
- each `.board-card` holds the SVG (width 100%) and a caption. The
  classification is a small label: "Blunder" in the `--danger` token,
  "Hata" in muted text, never color alone.

Both the user and admin detail routes pass `cards`.

## 5. Testing

- `tests/test_engine_analyzer.py`: `analyze_game` fills `fen_before`,
  `move_uci`, and `best_move_uci` (from the fake engine's `pv`), and
  `best_move_uci` is `None` when no PV is returned.
- `tests/test_stats_engine_*`: `critical_moves` ranks by loss across
  games, caps at 12, excludes inaccuracies, skips entries missing
  `fen_before`, computes `move_number`/`best_san`, and returns
  `best_san=None` for an illegal best move.
- `tests/test_web_boards.py`: `board_cards` for valid JSON (SVG contains
  two arrows, or one when best == played; orientation follows color);
  `None`, `"[]"`, malformed JSON and a bad FEN each give `[]`; the eval
  format for positive, negative and mate scores.
- `tests/test_web_migrations.py`: a DB at `0001_baseline` with a row
  upgrades to head with the row intact and `critical_moves_json` NULL.
  The existing drift test covers the model/migration match, and the
  `HEAD` constant moves to `0002_critical_moves`.
- `tests/test_web_history.py`: the detail page shows the card for a
  record with data, hides it for a `NULL` record, returns 200 with no
  card for malformed JSON, and the admin detail view shows it too.
- `tests/test_web_progress.py` / analyze flow: a completed job's record
  has `critical_moves_json` populated when the fake pipeline yields
  mistakes.

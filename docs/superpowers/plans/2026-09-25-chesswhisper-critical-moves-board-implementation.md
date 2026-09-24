# Critical Moves on the Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each web analysis's 12 costliest blunders/mistakes with their starting position and the engine's preferred move, and show them as SVG boards (red arrow for the played move, green for the engine's) on the report page.

**Architecture:**
- `engine_analyzer.analyze_game` records `fen_before`, `move_uci` and the before-move PV's first move (`best_move_uci`) on each `PositionEval`.
- `stats_engine.critical_moves()` ranks the players' blunders and mistakes across games into JSON-ready dicts.
- The web job stores them in a new nullable `analysis_records.critical_moves_json` column, added by Alembic migration `0002_critical_moves`.
- A new `web/boards.py` turns that JSON into caption fields plus a `chess.svg` board.
- `history_detail.html` renders the boards in a responsive grid.

**Tech Stack:** Python 3.11, python-chess (`chess.svg`, already a dependency), SQLAlchemy 2.0 + Alembic, FastAPI + Jinja2, `pytest`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-09-25-chesswhisper-critical-moves-board-design.md`

## Global Constraints

- No new dependencies. Boards come from `chess.svg.board`. No JavaScript.
- At most **12** critical moves per analysis (`CRITICAL_MOVES_LIMIT = 12`). Only the `blunder` and `mistake` classes count; inaccuracies are excluded. Ranking: `cp_loss` descending, then `game_index`, then `ply`.
- The new `PositionEval` fields default to `None`, so every existing constructor and test keeps working unchanged.
- The migration revision id is `"0002_critical_moves"`, with `down_revision = "0001_baseline"`. It adds a nullable `Text` column via `batch_alter_table`.
- `critical_moves_json` is `None` when there are no critical moves (never `"[]"`).
- Unreadable stored data (bad JSON, FEN, UCI, or missing keys) renders no card and never breaks the page.
- Arrow colors: played `#c0392bcc`, engine `#2e8b57cc`. Square colors: light `#e9e4d8`, dark `#a9b3bf`. When the best move equals the played move, or is missing, only the red arrow is drawn.
- Eval display is from the player's side: `+1.2` / `−3.4` (U+2212 minus); mate as `#N` / `−#N`.
- The CLI report, the Markdown report and the LLM prompt are unchanged.
- Code style: `ruff check src tests` (line length 110, E,F,I,UP,B) and `mypy src` pass. The whole `pytest` suite stays green.
- Commit messages: plain `type: subject` lines, no `Co-Authored-By` trailer and no AI/tool attribution of any kind (the repo owner's standing rule for every commit).

## Review Focus

- **A player name or opponent name containing HTML** (PGN headers are user-supplied): captions must be escaped, while the trusted SVG is not. Test: `test_history_detail_escapes_opponent_names_in_captions` (Task 5).
- **A stored record whose JSON is valid but has the wrong shape** (an object instead of a list, or entries missing keys): no card, page 200. Tests: `test_missing_or_unreadable_data_gives_no_cards` (Task 4) and `test_history_detail_survives_unreadable_critical_moves` (Task 5).
- **An engine PV move that isn't legal in the stored position** (engine and board out of sync, or a custom start): `best_san` is `None`, and the card still renders. Test: `test_best_san_is_none_for_a_missing_or_illegal_best_move` (Task 2).
- **An existing database from before this feature, with rows**: it upgrades to `0002`, keeps its rows, and the new column is NULL. Test: `test_baseline_database_gains_critical_moves_column_and_keeps_rows` (Task 3).
- **The analyzed player playing Black:** the board must be seen from Black's side, and move labels use `N...`. Tests: `test_board_is_seen_from_the_players_side` and `test_card_captions_come_from_the_entry` (Task 4).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/chesswhisper/engine_analyzer.py` (modify) | `PositionEval` gains `fen_before`, `move_uci`, `best_move_uci`; `analyze_game` fills them. |
| `src/chesswhisper/stats_engine.py` (modify) | `CRITICAL_MOVES_LIMIT`, `critical_moves()`, `_best_san()`. |
| `src/chesswhisper/web/models.py` (modify) | `AnalysisRecord.critical_moves_json`. |
| `src/chesswhisper/web/migrations/versions/0002_critical_moves.py` (create) | Adds the column. |
| `src/chesswhisper/web/boards.py` (create) | `BoardCard`, `format_eval()`, `board_cards()`. |
| `src/chesswhisper/web/app.py` (modify) | Saves `critical_moves_json`; passes `cards` to both detail routes. |
| `src/chesswhisper/web/templates/history_detail.html` (modify) | "Kritik hatalar" card and board grid. |
| `src/chesswhisper/web/templates/base.html` (modify) | Board grid, legend and badge CSS. |
| `tests/test_engine_analyzer.py`, `tests/test_stats_engine_critical_moves.py` (new), `tests/test_web_migrations.py`, `tests/test_web_boards.py` (new), `tests/test_web_history.py`, `tests/test_web_admin.py`, `tests/test_web_progress.py` | Tests per task. |

Diffs below are unified diffs against `master` at the commit this plan was written on. Apply each one with `git apply` (or by hand); every hunk was verified in a scratch checkout, where the full suite passed (299 passed, 1 skipped).

---

### Task 1: Analyzer records the position, the played move and the engine's move

**Files:**
- Modify: `src/chesswhisper/engine_analyzer.py`
- Test: `tests/test_engine_analyzer.py`

**Interfaces:**
- Produces: `PositionEval.fen_before: str | None`, `PositionEval.move_uci: str | None`, `PositionEval.best_move_uci: str | None` (all default `None`). `analyze_game` sets all three for every ply (`best_move_uci` is `None` when the engine's info has no `"pv"`).

- [ ] **Step 1: Write the failing tests**

Apply to `tests/test_engine_analyzer.py`:

```diff
diff --git a/tests/test_engine_analyzer.py b/tests/test_engine_analyzer.py
index 3e01612..70c14d8 100644
--- a/tests/test_engine_analyzer.py
+++ b/tests/test_engine_analyzer.py
@@ -118,3 +118,42 @@ def test_parse_clock_seconds_various_formats():
     assert parse_clock_seconds("[%clk 1:00:00]") == 3600.0
     assert parse_clock_seconds("no clock here") is None
     assert parse_clock_seconds("") is None
+
+
+class PvFakeEngine(FakeEngine):
+    """FakeEngine that also returns a principal variation (one UCI move, or None) per call."""
+
+    def __init__(self, scores: list[int], pvs: list[str | None]):
+        super().__init__(scores)
+        self._pvs = iter(pvs)
+
+    def analyse(self, board, limit, **kwargs):
+        info = super().analyse(board, limit, **kwargs)
+        pv = next(self._pvs)
+        if pv is not None:
+            info["pv"] = [chess.Move.from_uci(pv)]
+        return info
+
+
+def test_analyze_game_records_position_played_move_and_engine_best_move():
+    game = _parse(SIMPLE_GAME)
+    # One PV per analyse() call: before e4, before e5, before Nf3, after Nf3.
+    engine = PvFakeEngine([0, 30, 20, 50], ["d2d4", "c7c5", "g1f3", None])
+
+    evals = analyze_game(game, engine, movetime_ms=10)
+
+    assert evals[0].fen_before == chess.STARTING_FEN
+    assert evals[0].move_uci == "e2e4"
+    assert evals[0].best_move_uci == "d2d4"
+    assert evals[1].fen_before == evals[0].fen_after
+    assert evals[1].move_uci == "e7e5"
+    assert evals[1].best_move_uci == "c7c5"
+    assert evals[2].move_uci == "g1f3"
+    assert evals[2].best_move_uci == "g1f3"
+
+
+def test_analyze_game_leaves_best_move_empty_when_engine_returns_no_pv():
+    evals = analyze_game(_parse(SIMPLE_GAME), FakeEngine([0, 30, 20, 50]), movetime_ms=10)
+
+    assert [e.best_move_uci for e in evals] == [None, None, None]
+    assert [e.move_uci for e in evals] == ["e2e4", "e7e5", "g1f3"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_engine_analyzer.py -v`
Expected: the two new tests FAIL with `AttributeError: 'PositionEval' object has no attribute 'fen_before'`. The existing tests pass.

- [ ] **Step 3: Implement**

Apply to `src/chesswhisper/engine_analyzer.py`:

```diff
diff --git a/src/chesswhisper/engine_analyzer.py b/src/chesswhisper/engine_analyzer.py
index b00cd64..a258537 100644
--- a/src/chesswhisper/engine_analyzer.py
+++ b/src/chesswhisper/engine_analyzer.py
@@ -19,6 +19,11 @@ class PositionEval:
     cp_after: int
     fen_after: str
     clock_seconds: float | None = None
+    # Position the move was played from, the move itself, and the engine's
+    # preferred move there -- what a board diagram of this move needs.
+    fen_before: str | None = None
+    move_uci: str | None = None
+    best_move_uci: str | None = None
 
 
 def parse_clock_seconds(comment: str) -> float | None:
@@ -42,18 +47,20 @@ def analyze_game(
     board = game.board()
     results: list[PositionEval] = []
 
-    prev_score = engine.analyse(board, limit, game=game)["score"]
+    info_before = engine.analyse(board, limit, game=game)
 
     for ply, node in enumerate(game.mainline(), start=1):
         move = node.move
         mover = board.turn
         san = board.san(move)
-        cp_before = prev_score.pov(mover).score(mate_score=100_000)
+        fen_before = board.fen()
+        cp_before = info_before["score"].pov(mover).score(mate_score=100_000)
+        pv = info_before.get("pv")
+        best_move_uci = pv[0].uci() if pv else None
 
         board.push(move)
         info_after = engine.analyse(board, limit, game=game)
-        current_score = info_after["score"]
-        cp_after = current_score.pov(mover).score(mate_score=100_000)
+        cp_after = info_after["score"].pov(mover).score(mate_score=100_000)
 
         results.append(
             PositionEval(
@@ -64,8 +71,11 @@ def analyze_game(
                 cp_after=cp_after,
                 fen_after=board.fen(),
                 clock_seconds=parse_clock_seconds(node.comment),
+                fen_before=fen_before,
+                move_uci=move.uci(),
+                best_move_uci=best_move_uci,
             )
         )
-        prev_score = current_score
+        info_before = info_after
 
     return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_engine_analyzer.py tests/test_smoke_real_stockfish.py -v`
Expected: all pass (the real-Stockfish smoke test may be skipped if no binary is available).

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/engine_analyzer.py tests/test_engine_analyzer.py
git commit -m "feat: record each move's starting position and the engine's best move"
```

---

### Task 2: `critical_moves()` selects and ranks the moves to draw

**Files:**
- Modify: `src/chesswhisper/stats_engine.py` (append at end of file)
- Create: `tests/test_stats_engine_critical_moves.py`

**Interfaces:**
- Consumes: `PositionEval.fen_before`, `.move_uci`, `.best_move_uci` from Task 1. `GameReport.top_mistakes`, `.game_index`, `.opponent`, `.your_color` (existing).
- Produces: `stats_engine.CRITICAL_MOVES_LIMIT = 12` and `stats_engine.critical_moves(game_reports: list, limit: int = CRITICAL_MOVES_LIMIT) -> list[dict]`. Each dict has exactly these keys: `game_index, opponent, your_color, ply, move_number, san, uci, best_uci, best_san, fen_before, cp_before, cp_after, cp_loss, classification`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stats_engine_critical_moves.py`:

```python
import io

import chess
import chess.pgn

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import LoadedGame
from chesswhisper.stats_engine import CRITICAL_MOVES_LIMIT, build_game_report, critical_moves

ECO = load_eco_table()
# 12 plies: White (odd plies) and Black (even plies) each move six times.
MOVES = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 6. Re1 b5"


def _evals(losses, best=None):
    """PositionEvals for MOVES; losses maps ply -> centipawns lost by the mover (default 0)."""
    game = chess.pgn.read_game(io.StringIO(MOVES + " *"))
    board = game.board()
    evals = []
    for ply, move in enumerate(game.mainline_moves(), start=1):
        fen_before, san, color = board.fen(), board.san(move), board.turn
        board.push(move)
        evals.append(
            PositionEval(
                ply=ply, move_san=san, color=color, cp_before=0, cp_after=-losses.get(ply, 0),
                fen_after=board.fen(), fen_before=fen_before, move_uci=move.uci(),
                best_move_uci=(best or {}).get(ply),
            )
        )
    return evals


def _report(index, evals, white="Alice", black="Bob"):
    text = f'[White "{white}"]\n[Black "{black}"]\n[Result "*"]\n\n{MOVES} *\n'
    game = chess.pgn.read_game(io.StringIO(text))
    loaded = LoadedGame(index=index, game=game, headers=dict(game.headers))
    return build_game_report(loaded, evals, "Alice", ECO)


def test_ranks_blunders_and_mistakes_across_games_and_drops_inaccuracies():
    first = _report(0, _evals({1: 400, 3: 60, 5: 150}))
    second = _report(1, _evals({1: 350}))

    moves = critical_moves([first, second])

    assert [(m["game_index"], m["ply"], m["classification"]) for m in moves] == [
        (0, 1, "blunder"),
        (1, 1, "blunder"),
        (0, 5, "mistake"),
    ]


def test_equal_losses_keep_game_then_ply_order():
    first = _report(0, _evals({3: 200, 1: 200}))
    second = _report(1, _evals({1: 200}))

    moves = critical_moves([second, first])

    assert [(m["game_index"], m["ply"]) for m in moves] == [(0, 1), (0, 3), (1, 1)]


def test_caps_the_list_at_the_limit():
    reports = [_report(i, _evals({1: 400, 3: 400, 5: 400, 7: 400, 9: 400})) for i in range(3)]

    assert CRITICAL_MOVES_LIMIT == 12
    assert len(critical_moves(reports)) == 12
    assert len(critical_moves(reports, limit=2)) == 2


def test_skips_moves_saved_without_a_starting_position():
    evals = [
        PositionEval(ply=1, move_san="e4", color=True, cp_before=0, cp_after=-400,
                     fen_after=chess.STARTING_FEN),
    ]

    assert critical_moves([_report(0, evals)]) == []


def test_entry_carries_what_a_board_diagram_needs():
    evals = _evals({2: 500}, best={2: "c7c5"})
    report = _report(4, evals, white="Bob", black="Alice")

    [move] = critical_moves([report])

    assert move == {
        "game_index": 4,
        "opponent": "Bob",
        "your_color": "black",
        "ply": 2,
        "move_number": 1,
        "san": "e5",
        "uci": "e7e5",
        "best_uci": "c7c5",
        "best_san": "c5",
        "fen_before": evals[1].fen_before,
        "cp_before": 0,
        "cp_after": -500,
        "cp_loss": 500,
        "classification": "blunder",
    }


def test_best_san_is_none_for_a_missing_or_illegal_best_move():
    no_best = _report(0, _evals({1: 400}))
    illegal_best = _report(0, _evals({1: 400}, best={1: "e7e5"}))

    assert critical_moves([no_best])[0]["best_san"] is None
    assert critical_moves([illegal_best])[0]["best_san"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_stats_engine_critical_moves.py -v`
Expected: collection error, `ImportError: cannot import name 'CRITICAL_MOVES_LIMIT' from 'chesswhisper.stats_engine'`.

- [ ] **Step 3: Implement**

Apply to `src/chesswhisper/stats_engine.py`:

```diff
diff --git a/src/chesswhisper/stats_engine.py b/src/chesswhisper/stats_engine.py
index 614e69b..b4b83ff 100644
--- a/src/chesswhisper/stats_engine.py
+++ b/src/chesswhisper/stats_engine.py
@@ -325,3 +325,59 @@ def build_aggregate_report(player: str, game_reports: list) -> AggregateReport:
         openings=sorted(opening_map.values(), key=lambda o: o.games, reverse=True),
         time_management=combined_time_buckets.to_stats(),
     )
+
+
+CRITICAL_MOVES_LIMIT = 12
+_CRITICAL_CLASSES = ("blunder", "mistake")
+
+
+def _best_san(fen_before: str, best_uci: str | None) -> str | None:
+    if best_uci is None:
+        return None
+    try:
+        board = chess.Board(fen_before)
+        move = chess.Move.from_uci(best_uci)
+    except ValueError:
+        return None
+    if move not in board.legal_moves:
+        return None
+    return board.san(move)
+
+
+def critical_moves(game_reports: list, limit: int = CRITICAL_MOVES_LIMIT) -> list[dict]:
+    """The analysis's costliest blunders and mistakes, with what a board diagram needs.
+
+    Draws from each game's top_mistakes (already the player's flagged moves,
+    worst first), keeps blunders and mistakes that carry their starting
+    position, and ranks them across all games by centipawn loss.
+    """
+    candidates = []
+    for report in game_reports:
+        for ev in report.top_mistakes:
+            if ev.fen_before is None or ev.move_uci is None:
+                continue
+            cp_loss = max(0, ev.cp_before - ev.cp_after)
+            classification = classify_move(cp_loss)
+            if classification in _CRITICAL_CLASSES:
+                candidates.append((cp_loss, report, ev, classification))
+    candidates.sort(key=lambda c: (-c[0], c[1].game_index, c[2].ply))
+
+    return [
+        {
+            "game_index": report.game_index,
+            "opponent": report.opponent,
+            "your_color": report.your_color,
+            "ply": ev.ply,
+            "move_number": (ev.ply + 1) // 2,
+            "san": ev.move_san,
+            "uci": ev.move_uci,
+            "best_uci": ev.best_move_uci,
+            "best_san": _best_san(ev.fen_before, ev.best_move_uci),
+            "fen_before": ev.fen_before,
+            "cp_before": ev.cp_before,
+            "cp_after": ev.cp_after,
+            "cp_loss": cp_loss,
+            "classification": classification,
+        }
+        for cp_loss, report, ev, classification in candidates[:limit]
+    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_stats_engine_critical_moves.py tests/test_stats_engine_core.py tests/test_stats_engine_reports.py -v`
Expected: all pass (6 new).

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/stats_engine.py tests/test_stats_engine_critical_moves.py
git commit -m "feat: rank an analysis's critical moves across games"
```

---

### Task 3: `critical_moves_json` column and migration `0002`

**Files:**
- Modify: `src/chesswhisper/web/models.py`
- Create: `src/chesswhisper/web/migrations/versions/0002_critical_moves.py`
- Test: `tests/test_web_migrations.py`

**Interfaces:**
- Produces: `AnalysisRecord.critical_moves_json: Mapped[str | None]` (Text, default `None`), and migration head `"0002_critical_moves"`.

Note on the test changes:
- `HEAD` moves to the new revision.
- The legacy-DB test used to build its "pre-Alembic" schema with `Base.metadata.create_all`, which would now include the new column. The stamp + `0002` upgrade would then fail with a duplicate column. The test now builds the baseline schema through Alembic and drops `alembic_version`, which is exactly what a pre-Alembic database looks like.
- The failing-migration fixture's revision moves on top of the new head so it doesn't create a second head.

- [ ] **Step 1: Write the failing tests**

Apply to `tests/test_web_migrations.py`:

```diff
diff --git a/tests/test_web_migrations.py b/tests/test_web_migrations.py
index 4fc6116..cc6e976 100644
--- a/tests/test_web_migrations.py
+++ b/tests/test_web_migrations.py
@@ -14,7 +14,7 @@ from chesswhisper.web import db as db_module
 from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata
 from chesswhisper.web.db import BASELINE_REVISION, Base, get_db, init_db, migrations_dir
 
-HEAD = BASELINE_REVISION
+HEAD = "0002_critical_moves"
 
 
 def _engine(db_path):
@@ -26,6 +26,14 @@ def _versions(engine):
         return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]
 
 
+def _build_schema_at(engine, revision):
+    with engine.begin() as conn:
+        config = Config()
+        config.set_main_option("script_location", str(migrations_dir()))
+        config.attributes["connection"] = conn
+        command.upgrade(config, revision)
+
+
 def test_fresh_database_is_migrated_to_head(tmp_path):
     db_path = tmp_path / "fresh.db"
     init_db(db_path)
@@ -39,8 +47,10 @@ def test_fresh_database_is_migrated_to_head(tmp_path):
 def test_legacy_create_all_database_is_stamped_and_keeps_its_rows(tmp_path):
     db_path = tmp_path / "legacy.db"
     legacy = _engine(db_path)
-    Base.metadata.create_all(legacy)
+    # The schema the pre-Alembic create_all() built is exactly the baseline's.
+    _build_schema_at(legacy, BASELINE_REVISION)
     with legacy.begin() as conn:
+        conn.execute(text("DROP TABLE alembic_version"))
         conn.execute(
             text(
                 "INSERT INTO users (id, username, password_hash, is_admin, created_at, "
@@ -137,17 +147,17 @@ def test_cli_mode_without_url_refuses_to_guess_a_database():
 
 
 def _migrations_with_failing_revision(tmp_path):
-    """A copy of the real migrations plus a 0002 that does DDL, then raises."""
+    """A copy of the real migrations plus a revision on top that does DDL, then raises."""
     directory = tmp_path / "migrations"
     shutil.copytree(migrations_dir(), directory, ignore=shutil.ignore_patterns("__pycache__"))
-    (directory / "versions" / "0002_boom.py").write_text(
+    (directory / "versions" / "0099_boom.py").write_text(
         textwrap.dedent(
             '''
             import sqlalchemy as sa
             from alembic import op
 
-            revision = "0002_boom"
-            down_revision = "0001_baseline"
+            revision = "0099_boom"
+            down_revision = "0002_critical_moves"
             branch_labels = None
             depends_on = None
 
@@ -221,3 +231,32 @@ def test_concurrent_lazy_get_db_migrates_exactly_once(tmp_path, monkeypatch):
 
         assert errors == []
         assert _versions(_engine(db_path)) == [HEAD]
+
+
+def test_baseline_database_gains_critical_moves_column_and_keeps_rows(tmp_path):
+    db_path = tmp_path / "at_baseline.db"
+    engine = _engine(db_path)
+    _build_schema_at(engine, BASELINE_REVISION)
+    with engine.begin() as conn:
+        conn.execute(
+            text(
+                "INSERT INTO users (id, username, password_hash, is_admin, created_at, "
+                "failed_login_attempts) VALUES (1, 'alice', 'h', 0, '2026-01-01 00:00:00', 0)"
+            )
+        )
+        conn.execute(
+            text(
+                "INSERT INTO analysis_records (user_id, created_at, player_name, lang, llm_used, "
+                "games_count, overall_acpl, overall_accuracy, report_markdown) VALUES "
+                "(1, '2026-01-01 00:00:00', 'alice', 'tr', 'none', 3, 42.0, 80.5, '# r')"
+            )
+        )
+    engine.dispose()
+
+    init_db(db_path)
+
+    engine = _engine(db_path)
+    assert _versions(engine) == [HEAD]
+    with engine.connect() as conn:
+        rows = conn.execute(text("SELECT report_markdown, critical_moves_json FROM analysis_records")).all()
+    assert [tuple(r) for r in rows] == [("# r", None)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_migrations.py -v`
Expected:
- The tests asserting `_versions(...) == [HEAD]` FAIL (`['0001_baseline'] != ['0002_critical_moves']`).
- The failing-migration tests FAIL, because Alembic can't locate the `0002_critical_moves` down-revision.
- `test_baseline_database_gains_critical_moves_column_and_keeps_rows` FAILs.

- [ ] **Step 3: Implement**

Apply to `src/chesswhisper/web/models.py`:

```diff
diff --git a/src/chesswhisper/web/models.py b/src/chesswhisper/web/models.py
index 7e71ead..92f3ce2 100644
--- a/src/chesswhisper/web/models.py
+++ b/src/chesswhisper/web/models.py
@@ -51,5 +51,8 @@ class AnalysisRecord(Base):
     overall_acpl: Mapped[float]
     overall_accuracy: Mapped[float]
     report_markdown: Mapped[str] = mapped_column(Text)
+    # JSON list from stats_engine.critical_moves(); None for records saved
+    # before it existed or with no blunders/mistakes.
+    critical_moves_json: Mapped[str | None] = mapped_column(Text, default=None)
 
     user: Mapped[User] = relationship(back_populates="analyses")
```

Create `src/chesswhisper/web/migrations/versions/0002_critical_moves.py`:

```python
"""add analysis_records.critical_moves_json

Revision ID: 0002_critical_moves
Revises: 0001_baseline
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_critical_moves"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("critical_moves_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.drop_column("critical_moves_json")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_web_migrations.py tests/test_web_db.py -v`
Expected: all pass. The drift test (`test_migrated_schema_matches_models`) confirms the model and the migration agree.

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/web/models.py src/chesswhisper/web/migrations/versions/0002_critical_moves.py tests/test_web_migrations.py
git commit -m "feat: add analysis_records.critical_moves_json with migration 0002"
```

---

### Task 4: `web/boards.py` turns stored moves into board cards

**Files:**
- Create: `src/chesswhisper/web/boards.py`
- Create: `tests/test_web_boards.py`

**Interfaces:**
- Consumes: the dict shape produced by Task 2 (as JSON text).
- Produces:
  - `boards.BoardCard`: a frozen dataclass with `game_number: int` (= `game_index + 1`), `opponent: str`, `move_label: str`, `classification: str`, `best_san: str | None`, `eval_before: str`, `eval_after: str`, `svg: str`.
  - `boards.format_eval(cp: int) -> str`
  - `boards.board_cards(critical_moves_json: str | None) -> list[BoardCard]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_boards.py`:

```python
import json
import re

import chess

from chesswhisper.web.boards import board_cards, format_eval


def _fen_after(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.fen()


def _entry(**overrides):
    entry = {
        "game_index": 2, "opponent": "Bob", "your_color": "black", "ply": 2, "move_number": 1,
        "san": "f6", "uci": "f7f6", "best_uci": "e7e5", "best_san": "e5",
        "fen_before": _fen_after("e4"), "cp_before": -30, "cp_after": -420, "cp_loss": 390,
        "classification": "blunder",
    }
    entry.update(overrides)
    return entry


def _a1_position(svg):
    match = re.search(r'<rect x="(\d+)" y="(\d+)"[^>]*class="square [a-z]+ a1"', svg)
    return int(match.group(1)), int(match.group(2))


def test_card_captions_come_from_the_entry():
    [card] = board_cards(json.dumps([_entry()]))

    assert card.game_number == 3
    assert card.opponent == "Bob"
    assert card.move_label == "1... f6"
    assert card.classification == "blunder"
    assert card.best_san == "e5"
    assert (card.eval_before, card.eval_after) == ("−0.3", "−4.2")


def test_white_moves_are_labelled_without_the_ellipsis():
    [card] = board_cards(json.dumps([_entry(your_color="white", ply=1, san="e4", uci="e2e4",
                                            best_uci=None, best_san=None, fen_before=chess.STARTING_FEN)]))

    assert card.move_label == "1. e4"


def test_board_draws_played_move_red_and_engine_move_green():
    [card] = board_cards(json.dumps([_entry()]))

    assert card.svg.startswith("<svg")
    assert "#c0392b" in card.svg
    assert "#2e8b57" in card.svg


def test_only_the_played_arrow_when_engine_agrees_or_has_no_move():
    [same] = board_cards(json.dumps([_entry(best_uci="f7f6", best_san="f6")]))
    [none] = board_cards(json.dumps([_entry(best_uci=None, best_san=None)]))

    for card in (same, none):
        assert "#c0392b" in card.svg
        assert "#2e8b57" not in card.svg


def test_board_is_seen_from_the_players_side():
    [black] = board_cards(json.dumps([_entry()]))
    [white] = board_cards(json.dumps([_entry(your_color="white")]))

    # a1 sits bottom-left from White's side and top-right from Black's.
    white_x, white_y = _a1_position(white.svg)
    black_x, black_y = _a1_position(black.svg)
    assert white_y > black_y
    assert white_x < black_x


def test_missing_or_unreadable_data_gives_no_cards():
    assert board_cards(None) == []
    assert board_cards("") == []
    assert board_cards("[]") == []
    assert board_cards("not json") == []
    assert board_cards('{"not": "a list"}') == []
    assert board_cards(json.dumps([_entry(fen_before="not a fen")])) == []
    assert board_cards(json.dumps([_entry(uci="zz99")])) == []
    assert board_cards(json.dumps([{"san": "e4"}])) == []


def test_format_eval_shows_pawns_and_mates_from_the_players_side():
    assert format_eval(120) == "+1.2"
    assert format_eval(-340) == "−3.4"
    assert format_eval(0) == "+0.0"
    assert format_eval(99_997) == "#3"
    assert format_eval(-99_998) == "−#2"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_boards.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'chesswhisper.web.boards'`.

- [ ] **Step 3: Implement**

Create `src/chesswhisper/web/boards.py`:

```python
"""Board diagrams for a saved analysis's critical moves.

Turns the JSON stored in AnalysisRecord.critical_moves_json into caption
fields plus an SVG board drawn by python-chess. The SVG is built only from
parsed chess objects, so it is safe to insert unescaped; captions are plain
strings the template escapes as usual.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import chess
import chess.svg

logger = logging.getLogger(__name__)

# python-chess encodes mate-in-N as +/-(100_000 - N) with mate_score=100_000.
_MATE_SCORE = 100_000
_MATE_THRESHOLD = 90_000

_BOARD_COLORS = {
    "square light": "#e9e4d8",
    "square dark": "#a9b3bf",
    "margin": "#00000000",
    "coord": "#8a9099",
    "arrow red": "#c0392bcc",
    "arrow green": "#2e8b57cc",
}


@dataclass(frozen=True)
class BoardCard:
    game_number: int
    opponent: str
    move_label: str
    classification: str
    best_san: str | None
    eval_before: str
    eval_after: str
    svg: str


def format_eval(cp: int) -> str:
    """Centipawns from the player's side as pawns (+1.2 / −3.4) or mate (#3 / −#3)."""
    sign = "−" if cp < 0 else "+"
    if abs(cp) >= _MATE_THRESHOLD:
        mate_in = _MATE_SCORE - abs(cp)
        return f"#{mate_in}" if cp > 0 else f"−#{mate_in}"
    return f"{sign}{abs(cp) / 100:.1f}"


def _move_label(move_number: int, your_color: str, san: str) -> str:
    return f"{move_number}. {san}" if your_color == "white" else f"{move_number}... {san}"


def _card(entry: dict) -> BoardCard:
    board = chess.Board(entry["fen_before"])
    played = chess.Move.from_uci(entry["uci"])
    arrows = [chess.svg.Arrow(played.from_square, played.to_square, color="red")]
    if entry["best_uci"] is not None and entry["best_uci"] != entry["uci"]:
        best = chess.Move.from_uci(entry["best_uci"])
        arrows.append(chess.svg.Arrow(best.from_square, best.to_square, color="green"))
    svg = chess.svg.board(
        board,
        orientation=entry["your_color"] == "white",
        arrows=arrows,
        coordinates=True,
        colors=_BOARD_COLORS,
    )
    return BoardCard(
        game_number=int(entry["game_index"]) + 1,
        opponent=str(entry["opponent"]),
        move_label=_move_label(int(entry["move_number"]), entry["your_color"], str(entry["san"])),
        classification=str(entry["classification"]),
        best_san=entry["best_san"],
        eval_before=format_eval(int(entry["cp_before"])),
        eval_after=format_eval(int(entry["cp_after"])),
        svg=svg,
    )


def board_cards(critical_moves_json: str | None) -> list[BoardCard]:
    """Cards for the report page, or [] when there is nothing (valid) to show."""
    if not critical_moves_json:
        return []
    try:
        return [_card(entry) for entry in json.loads(critical_moves_json)]
    except (ValueError, KeyError, TypeError):
        logger.warning("ignoring unreadable critical_moves_json", exc_info=True)
        return []
```

(`chess.svg` resolves an arrow's `color="red"` through the `"arrow red"` key of `colors`, and splits the `cc` alpha suffix into an opacity attribute. That is why the tests look for `#c0392b` without the alpha.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_web_boards.py -v && mypy src`
Expected: 7 passed. `Success: no issues found`.

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/web/boards.py tests/test_web_boards.py
git commit -m "feat: render critical moves as chess.svg board cards"
```

---

### Task 5: Save critical moves with each analysis and show them on the report page

**Files:**
- Modify: `src/chesswhisper/web/app.py`
- Modify: `src/chesswhisper/web/templates/history_detail.html`
- Modify: `src/chesswhisper/web/templates/base.html`
- Test: `tests/test_web_history.py`, `tests/test_web_admin.py`, `tests/test_web_progress.py`

**Interfaces:**
- Consumes: `stats_engine.critical_moves` (Task 2), `AnalysisRecord.critical_moves_json` (Task 3), `boards.board_cards` (Task 4).
- Produces: both detail routes pass `cards: list[BoardCard]` to `history_detail.html`. `_run_analysis_job` stores `json.dumps(critical_moves(aggregate.games))`, or `None` when that list is empty.

- [ ] **Step 1: Write the failing tests**

Apply to `tests/test_web_history.py`:

```diff
diff --git a/tests/test_web_history.py b/tests/test_web_history.py
index d70def9..0ede8be 100644
--- a/tests/test_web_history.py
+++ b/tests/test_web_history.py
@@ -2,6 +2,7 @@ import json
 import re
 from datetime import datetime
 
+import chess
 import pytest
 from conftest import CsrfClient
 
@@ -180,3 +181,68 @@ def test_history_without_records_has_no_trend_card(client):
     response = client.get("/history")
 
     assert "Eğilim" not in response.text
+
+
+def _critical_moves_json():
+    board = chess.Board()
+    board.push_san("e4")
+    return json.dumps([{
+        "game_index": 0, "opponent": "Bob", "your_color": "black", "ply": 2, "move_number": 1,
+        "san": "f6", "uci": "f7f6", "best_uci": "e7e5", "best_san": "e5", "fen_before": board.fen(),
+        "cp_before": -30, "cp_after": -420, "cp_loss": 390, "classification": "blunder",
+    }])
+
+
+def _seed_record_with_moves(username, critical_moves_json):
+    db = next(get_db())
+    user = db.query(User).filter_by(username=username).one()
+    record = AnalysisRecord(
+        user_id=user.id, player_name="Alice", lang="tr", llm_used="none", games_count=1,
+        overall_acpl=10.0, overall_accuracy=95.0, report_markdown="# Rapor",
+        critical_moves_json=critical_moves_json,
+    )
+    db.add(record)
+    db.commit()
+    return record.id
+
+
+def test_history_detail_shows_critical_move_boards(client):
+    record_id = _seed_record_with_moves("alice", _critical_moves_json())
+
+    response = client.get(f"/history/{record_id}")
+
+    assert response.status_code == 200
+    assert "Kritik hatalar" in response.text
+    assert "1... f6" in response.text
+    assert "Daha iyisi: <strong>e5</strong>" in response.text
+    assert response.text.count("<svg") == 1
+
+
+def test_history_detail_hides_boards_for_records_without_them(client):
+    record_id = _seed_record_with_moves("alice", None)
+
+    response = client.get(f"/history/{record_id}")
+
+    assert response.status_code == 200
+    assert "Kritik hatalar" not in response.text
+
+
+def test_history_detail_survives_unreadable_critical_moves(client):
+    record_id = _seed_record_with_moves("alice", "{broken")
+
+    response = client.get(f"/history/{record_id}")
+
+    assert response.status_code == 200
+    assert "Kritik hatalar" not in response.text
+    assert "Rapor" in response.text
+
+
+def test_history_detail_escapes_opponent_names_in_captions(client):
+    moves = json.loads(_critical_moves_json())
+    moves[0]["opponent"] = "<script>alert(1)</script>"
+    record_id = _seed_record_with_moves("alice", json.dumps(moves))
+
+    response = client.get(f"/history/{record_id}")
+
+    assert "<script>alert(1)</script>" not in response.text
+    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
```

Apply to `tests/test_web_admin.py`:

```diff
diff --git a/tests/test_web_admin.py b/tests/test_web_admin.py
index 52584cb..0bd77a5 100644
--- a/tests/test_web_admin.py
+++ b/tests/test_web_admin.py
@@ -1,3 +1,6 @@
+import json
+
+import chess
 import pytest
 from conftest import CsrfClient
 
@@ -125,3 +128,27 @@ def test_admin_reset_password_rejects_short_password(clients):
     db2 = next(get_db())
     alice2 = db2.get(User, alice.id)
     assert alice2.password_hash == original_hash
+
+
+def test_admin_report_view_shows_critical_move_boards(clients):
+    admin_client, _ = clients
+    board = chess.Board()
+    board.push_san("e4")
+    db = next(get_db())
+    alice = db.query(User).filter_by(username="alice").one()
+    record = AnalysisRecord(
+        user_id=alice.id, player_name="Alice", lang="en", llm_used="none", games_count=1,
+        overall_acpl=10.0, overall_accuracy=95.0, report_markdown="# r",
+        critical_moves_json=json.dumps([{
+            "game_index": 0, "opponent": "Bob", "your_color": "black", "ply": 2, "move_number": 1,
+            "san": "f6", "uci": "f7f6", "best_uci": None, "best_san": None, "fen_before": board.fen(),
+            "cp_before": -30, "cp_after": -420, "cp_loss": 390, "classification": "blunder",
+        }]),
+    )
+    db.add(record)
+    db.commit()
+
+    response = admin_client.get(f"/admin/users/{alice.id}/history/{record.id}")
+
+    assert response.status_code == 200
+    assert "Kritik hatalar" in response.text
```

Apply to `tests/test_web_progress.py`:

```diff
diff --git a/tests/test_web_progress.py b/tests/test_web_progress.py
index d7f8c59..48df21a 100644
--- a/tests/test_web_progress.py
+++ b/tests/test_web_progress.py
@@ -1,3 +1,4 @@
+import json
 import textwrap
 
 import chess.engine
@@ -315,3 +316,31 @@ def test_analyze_progress_page_renders_for_the_owning_user(client, monkeypatch):
 
     assert progress.status_code == 200
     assert job_id in progress.text
+
+
+def test_completed_job_saves_critical_moves_with_the_record(client, monkeypatch):
+    moves = [{"san": "f6", "cp_loss": 390}]
+    monkeypatch.setattr("chesswhisper.web.app.run_analysis", lambda *a, **k: _fake_aggregate_report())
+    monkeypatch.setattr("chesswhisper.web.app.critical_moves", lambda games: moves)
+
+    response = client.post(
+        "/analyze",
+        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
+        data={"player": "Alice", "lang": "en", "llm": "none"},
+    )
+    assert response.status_code == 303
+
+    record = next(get_db()).query(AnalysisRecord).one()
+    assert json.loads(record.critical_moves_json) == moves
+
+
+def test_completed_job_without_critical_moves_saves_null(client, monkeypatch):
+    monkeypatch.setattr("chesswhisper.web.app.run_analysis", lambda *a, **k: _fake_aggregate_report())
+
+    client.post(
+        "/analyze",
+        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
+        data={"player": "Alice", "lang": "en", "llm": "none"},
+    )
+
+    assert next(get_db()).query(AnalysisRecord).one().critical_moves_json is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_history.py tests/test_web_admin.py tests/test_web_progress.py -v`
Expected:
- The board-rendering tests FAIL (`"Kritik hatalar"` not in the page).
- `test_completed_job_saves_critical_moves_with_the_record` FAILs: monkeypatching `chesswhisper.web.app.critical_moves` raises `AttributeError`, because app.py doesn't import it yet.
- The hide/unreadable/escape tests and the NULL-save test may already pass, since they assert absence. That's expected; they guard against regressions.

- [ ] **Step 3: Implement the wiring**

Apply to `src/chesswhisper/web/app.py`:

```diff
diff --git a/src/chesswhisper/web/app.py b/src/chesswhisper/web/app.py
index fc83f1d..4db5871 100644
--- a/src/chesswhisper/web/app.py
+++ b/src/chesswhisper/web/app.py
@@ -1,3 +1,4 @@
+import json
 import os
 import tempfile
 import threading
@@ -24,6 +25,7 @@ from chesswhisper.game_sources import (
     fetch_pgn,
 )
 from chesswhisper.report_writer import render_markdown
+from chesswhisper.stats_engine import critical_moves
 from chesswhisper.web import jobs
 from chesswhisper.web.auth import (
     Forbidden,
@@ -34,6 +36,7 @@ from chesswhisper.web.auth import (
     require_admin,
     verify_csrf,
 )
+from chesswhisper.web.boards import board_cards
 from chesswhisper.web.db import get_db, utcnow
 from chesswhisper.web.models import AnalysisRecord, User, UserApiKey
 from chesswhisper.web.security import (
@@ -134,6 +137,7 @@ def _run_analysis_job(
             return
 
         report_markdown = render_markdown(aggregate, commentary, lang)
+        moves = critical_moves(aggregate.games)
         record = AnalysisRecord(
             user_id=user_id,
             player_name=aggregate.player,
@@ -143,6 +147,7 @@ def _run_analysis_job(
             overall_acpl=aggregate.overall_acpl,
             overall_accuracy=aggregate.overall_accuracy,
             report_markdown=report_markdown,
+            critical_moves_json=json.dumps(moves) if moves else None,
         )
         db.add(record)
         db.commit()
@@ -353,6 +358,7 @@ async def history_detail(
         request, "history_detail.html",
         {
             "current_user": current_user, "report_html": report_html,
+            "cards": board_cards(record.critical_moves_json),
             "back_url": "/history", "warnings": warnings,
         },
     )
@@ -541,7 +547,10 @@ async def admin_user_history_detail(
     report_html = _report_to_safe_html(record.report_markdown)
     return _render(
         request, "history_detail.html",
-        {"current_user": current_user, "report_html": report_html, "back_url": f"/admin/users/{user_id}"},
+        {
+            "current_user": current_user, "report_html": report_html,
+            "cards": board_cards(record.critical_moves_json), "back_url": f"/admin/users/{user_id}",
+        },
     )
 
 
```

- [ ] **Step 4: Implement the template and styles**

Apply to `src/chesswhisper/web/templates/history_detail.html`:

```diff
diff --git a/src/chesswhisper/web/templates/history_detail.html b/src/chesswhisper/web/templates/history_detail.html
index 40e2c78..f6744f2 100644
--- a/src/chesswhisper/web/templates/history_detail.html
+++ b/src/chesswhisper/web/templates/history_detail.html
@@ -9,6 +9,35 @@
   </ul>
 </div>
 {% endif %}
+{% if cards %}
+<section class="card board-section" aria-labelledby="critical-title">
+  <h2 id="critical-title" class="section-title">Kritik hatalar</h2>
+  <p class="hint board-legend">
+    <span class="legend-key legend-played"></span> oynanan hamle
+    <span class="legend-key legend-best"></span> motorun önerisi
+  </p>
+  <div class="board-grid">
+    {% for c in cards %}
+    <figure class="board-card">
+      <div class="board-svg">{{ c.svg | safe }}</div>
+      <figcaption>
+        <div class="board-move">
+          <strong>{{ c.move_label }}</strong>
+          {% if c.classification == "blunder" %}
+          <span class="move-badge badge-blunder">Blunder</span>
+          {% else %}
+          <span class="move-badge badge-mistake">Hata</span>
+          {% endif %}
+        </div>
+        <div class="board-meta">Oyun {{ c.game_number }} · {{ c.opponent }}</div>
+        {% if c.best_san %}<div class="board-best">Daha iyisi: <strong>{{ c.best_san }}</strong></div>{% endif %}
+        <div class="board-eval">{{ c.eval_before }} → {{ c.eval_after }}</div>
+      </figcaption>
+    </figure>
+    {% endfor %}
+  </div>
+</section>
+{% endif %}
 <div class="card report">
   {{ report_html | safe }}
 </div>
```

Apply to `src/chesswhisper/web/templates/base.html`:

```diff
diff --git a/src/chesswhisper/web/templates/base.html b/src/chesswhisper/web/templates/base.html
index c924a5c..9929d33 100644
--- a/src/chesswhisper/web/templates/base.html
+++ b/src/chesswhisper/web/templates/base.html
@@ -234,6 +234,27 @@
   .trend-tooltip .tooltip-date { color: var(--text-muted); font-size: 12px; margin-bottom: 2px; }
   .trend-tooltip span { color: var(--text-muted); }
 
+  /* Critical-move boards */
+  .board-legend { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin: -8px 0 16px; }
+  .legend-key { display: inline-block; width: 18px; height: 3px; border-radius: 2px; margin-left: 8px; }
+  .legend-key:first-child { margin-left: 0; }
+  .legend-played { background: #c0392b; }
+  .legend-best { background: #2e8b57; }
+  .board-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
+  .board-card { margin: 0; }
+  .board-svg svg { display: block; width: 100%; height: auto; border-radius: 6px; }
+  .board-card figcaption { font-size: 13px; margin-top: 8px; line-height: 1.5; }
+  .board-move { display: flex; align-items: center; gap: 8px; font-size: 14px; }
+  .board-meta, .board-best { color: var(--text-muted); }
+  .board-best strong { color: var(--text); }
+  .board-eval { font-variant-numeric: tabular-nums; font-weight: 600; }
+  .move-badge {
+    font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
+    border: 1px solid currentColor; line-height: 1.5;
+  }
+  .badge-blunder { color: var(--danger); }
+  .badge-mistake { color: var(--text-muted); }
+
   @media (max-width: 560px) {
     .page { padding-top: 20px; }
     header.site-header { flex-wrap: wrap; margin-bottom: 20px; }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest -q && ruff check src tests && mypy src`
Expected: the whole suite passes (the prior count plus 23 new tests), `All checks passed!`, `Success: no issues found`.

- [ ] **Step 6: Look at it**

Render a detail page with a few real positions (both colors, one mate score, one opponent name containing `<b>`) against a scratch database, never the app's real one. Screenshot it with headless Chrome at desktop width in light and dark mode, and at 375px inside an iframe (Chrome's minimum window width on Windows is about 500px). Check that:
- arrows point the right way
- boards are oriented from the player's side
- captions don't overflow
- the grid collapses to one column on the phone width

- [ ] **Step 7: Commit**

```bash
git add src/chesswhisper/web/app.py src/chesswhisper/web/templates/history_detail.html src/chesswhisper/web/templates/base.html tests/test_web_history.py tests/test_web_admin.py tests/test_web_progress.py
git commit -m "feat: show an analysis's critical moves on boards in the report page"
```

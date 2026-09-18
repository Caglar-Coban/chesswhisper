# chesswhisper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI (`chesswhisper`) that analyzes a local PGN file with Stockfish, computes accuracy/blunder/opening/time-management statistics for one named player, and uses an LLM to turn those statistics into natural-language coaching commentary in a Markdown report.

**Architecture:** A linear pipeline of small, independently-testable modules — `pgn_loader` → `engine_analyzer` (Stockfish, dependency-injected engine object) → `stats_engine` (pure functions + report dataclasses) → `llm` (pluggable `LLMProvider`) → `report_writer` — wired together by `cli.py`. `engine_provision` handles first-run Stockfish auto-download, decoupled from analysis so it's independently testable.

**Tech Stack:** Python 3.11+, `python-chess`, `click`, `anthropic`, `openai`, `requests`, `platformdirs`, `pytest`, `pytest-mock`. Package layout: `src/chesswhisper/`.

**Spec:** `docs/superpowers/specs/2026-09-19-chesswhisper-design.md`

## Global Constraints

- Language: Python 3.11+, `src/` layout, packaged with Hatchling.
- No real network calls or real Stockfish process in unit tests — `engine_analyzer` and `llm` providers are tested via dependency injection / mocking. Only one explicit, auto-skipping smoke test may touch a real Stockfish binary.
- API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are read from environment/`.env` only, never hardcoded, never logged, never committed.
- Every module has a single responsibility; dataclasses carry data between stages, no stage reaches into another stage's internals.
- Report/report-writer strings are TR/EN via `i18n.py`; **opening names stay in their standard international form regardless of `--lang`** (this is intentional — Turkish chess literature commonly uses the same names — do not attempt Turkish translations of ECO opening names).
- Centipawn-loss classification thresholds (finalizing spec's open item): `fine` &lt; 50cp, `inaccuracy` 50–99cp, `mistake` 100–299cp, `blunder` ≥ 300cp. These are approximate, documented constants — not Lichess's exact WDL-based algorithm.
- Game-phase heuristic (finalizing spec's open item): ply ≤ 20 (10 full moves) → `opening`; else non-pawn material (Q=9,R=5,B=3,N=3, summed both colors) ≤ 20 → `endgame`; else → `middlegame`.
- Time-management heuristic (finalizing spec's open item): a move made with less than 30 seconds left on the clock (`LOW_TIME_THRESHOLD_SECONDS = 30`) counts as "time trouble"; time management is `available=False` whenever the analyzed player's moves carry no `%clk` data at all.
- Engine analysis default (finalizing spec's open item): `movetime=300` ms per position; `--depth` flag overrides with fixed-depth search instead.
- Stockfish provisioning pins release tag `sf_19` (finalizing spec's open item), downloading the official universal binaries from `official-stockfish/Stockfish` GitHub releases; the binary is located by walking the extracted archive rather than assuming an internal path (release layouts have changed before).
- Opening/ECO lookup (finalizing spec's open item): a bundled static TSV table (`src/chesswhisper/data/eco.tsv`) matched by longest-move-sequence-prefix — no external opening-book dependency.
- Win%/accuracy formula uses the published Lichess constants (`50 + 50*(2/(1+e^(-0.00368208*cp))-1)` for win%, `103.1668*e^(-0.04354*Δwin%) - 3.1669` clamped to [0,100] for per-move accuracy); game accuracy is the plain mean of per-move accuracies (documented simplification vs. Lichess's volatility-weighted mean).

---

## File Structure

```
chesswhisper/
  pyproject.toml
  src/chesswhisper/
    __init__.py
    cli.py                  # click group + `analyze` command, wires everything
    pgn_loader.py            # parse .pgn -> LoadedGame list
    engine_provision.py      # find-or-download a working Stockfish binary
    engine_analyzer.py       # PositionEval dataclass + analyze_game()
    stats_engine.py          # thresholds, phase, accuracy math, report dataclasses/builders
    openings.py              # ECO table loading + prefix matching
    i18n.py                  # TR/EN label strings
    report_writer.py         # render_markdown() / write_report()
    llm/
      __init__.py
      base.py                # LLMProvider ABC + LLMProviderError
      none_provider.py
      claude_provider.py
      openai_provider.py
    data/
      eco.tsv
  tests/
    test_pgn_loader.py
    test_openings.py
    test_stats_engine_core.py
    test_engine_provision.py
    test_engine_analyzer.py
    test_stats_engine_reports.py
    test_report_writer.py
    test_llm_none_provider.py
    test_llm_claude_provider.py
    test_llm_openai_provider.py
    test_cli.py
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/chesswhisper/__init__.py`
- Create: `src/chesswhisper/llm/__init__.py`

**Interfaces:**
- Produces: an installable package `chesswhisper` (editable install), a `pytest` runner, a `chesswhisper` console script (wired to `chesswhisper.cli:cli`, created in Task 13).

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "chesswhisper"
version = "0.1.0"
description = "Stockfish + LLM chess coaching CLI"
requires-python = ">=3.11"
dependencies = [
    "python-chess>=1.11",
    "click>=8.1",
    "anthropic>=0.40",
    "openai>=1.50",
    "requests>=2.31",
    "platformdirs>=4.0",
]

[project.scripts]
chesswhisper = "chesswhisper.cli:cli"

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]

[tool.hatch.build.targets.wheel]
packages = ["src/chesswhisper"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package `__init__.py` files**

`src/chesswhisper/__init__.py`:
```python
```

`src/chesswhisper/llm/__init__.py`:
```python
```

- [ ] **Step 3: Editable-install the package and confirm pytest runs**

Run: `pip install -e ".[dev]"` from `C:\Users\Omen\Desktop\Projects\chesswhisper`
Run: `pytest --collect-only`
Expected: exits 0, "no tests ran" (no test files yet).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/chesswhisper/__init__.py src/chesswhisper/llm/__init__.py
git commit -m "chore: scaffold Python package"
```

---

### Task 2: `pgn_loader`

**Files:**
- Create: `src/chesswhisper/pgn_loader.py`
- Test: `tests/test_pgn_loader.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class LoadedGame:
      index: int
      game: chess.pgn.Game
      headers: dict[str, str]

  @dataclass
  class PgnLoadResult:
      games: list[LoadedGame]
      warnings: list[str]

  def load_pgn(path: str) -> PgnLoadResult: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pgn_loader.py
import textwrap
from chesswhisper.pgn_loader import load_pgn

VALID_SINGLE = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"

VALID_MULTI = VALID_SINGLE + "\n" + textwrap.dedent("""
    [Event "Test2"]
    [White "Bob"]
    [Black "Alice"]
    [Result "0-1"]

    1. d4 d5 2. c4 e6 0-1
""").strip() + "\n"

MALFORMED_THEN_VALID = textwrap.dedent("""
    [Event "Broken"]
    [White "Alice"]
    [Black "Bob"]
    [Result "*"]

    1. e4 e5 2. Nf3 Nc6 3. Qh5$$$ garbage not a move *

    [Event "Ok"]
    [White "Bob"]
    [Black "Alice"]
    [Result "1/2-1/2"]

    1. e4 e5 1/2-1/2
""").strip() + "\n"


def test_loads_single_game(tmp_path):
    p = tmp_path / "one.pgn"
    p.write_text(VALID_SINGLE, encoding="utf-8")

    result = load_pgn(str(p))

    assert len(result.games) == 1
    assert result.warnings == []
    assert result.games[0].index == 0
    assert result.games[0].headers["White"] == "Alice"
    assert result.games[0].headers["Result"] == "1-0"


def test_loads_multiple_games(tmp_path):
    p = tmp_path / "multi.pgn"
    p.write_text(VALID_MULTI, encoding="utf-8")

    result = load_pgn(str(p))

    assert len(result.games) == 2
    assert result.games[0].headers["White"] == "Alice"
    assert result.games[1].headers["White"] == "Bob"


def test_skips_malformed_game_and_warns(tmp_path):
    p = tmp_path / "bad.pgn"
    p.write_text(MALFORMED_THEN_VALID, encoding="utf-8")

    result = load_pgn(str(p))

    assert len(result.games) == 1
    assert result.games[0].headers["Event"] == "Ok"
    assert len(result.warnings) == 1
    assert "0" in result.warnings[0]


def test_missing_file_raises(tmp_path):
    missing = tmp_path / "nope.pgn"
    try:
        load_pgn(str(missing))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pgn_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.pgn_loader'`

- [ ] **Step 3: Implement `pgn_loader.py`**

```python
# src/chesswhisper/pgn_loader.py
from dataclasses import dataclass
import os

import chess.pgn


@dataclass
class LoadedGame:
    index: int
    game: chess.pgn.Game
    headers: dict[str, str]


@dataclass
class PgnLoadResult:
    games: list[LoadedGame]
    warnings: list[str]


def load_pgn(path: str) -> PgnLoadResult:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"PGN file not found: {path}")

    games: list[LoadedGame] = []
    warnings: list[str] = []

    with open(path, encoding="utf-8", errors="replace") as fh:
        index = 0
        while True:
            offset_before = fh.tell()
            try:
                game = chess.pgn.read_game(fh)
            except (ValueError, UnicodeDecodeError) as exc:
                warnings.append(f"Game at index {index} could not be parsed: {exc}")
                break

            if game is None:
                break

            if game.errors:
                warnings.append(
                    f"Game at index {index} had {len(game.errors)} parse error(s) "
                    f"and was skipped: {game.errors[0]}"
                )
                index += 1
                continue

            games.append(LoadedGame(index=index, game=game, headers=dict(game.headers)))
            index += 1

    return PgnLoadResult(games=games, warnings=warnings)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pgn_loader.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/pgn_loader.py tests/test_pgn_loader.py
git commit -m "feat: add PGN loading with per-game error tolerance"
```

---

### Task 3: `openings` (ECO table + matching)

**Files:**
- Create: `src/chesswhisper/data/eco.tsv`
- Create: `src/chesswhisper/openings.py`
- Test: `tests/test_openings.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class EcoEntry:
      eco: str
      name: str
      moves: list[str]  # SAN, in order from move 1

  UNKNOWN_OPENING = EcoEntry(eco="A00", name="Uncommon Opening", moves=[])

  def load_eco_table() -> list[EcoEntry]: ...
  def match_opening(san_moves: list[str], table: list[EcoEntry]) -> EcoEntry: ...
  ```

- [ ] **Step 1: Create the bundled ECO data file**

`src/chesswhisper/data/eco.tsv` (tab-separated: `eco`, `name`, `moves` where moves is space-separated SAN):

```
eco	name	moves
B20	Sicilian Defense	e4 c5
B27	Sicilian Defense	e4 c5 Nf3
B30	Sicilian Defense	e4 c5 Nc3
B22	Sicilian Alapin Variation	e4 c5 c3
B90	Sicilian Najdorf	e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6
C60	Ruy Lopez	e4 e5 Nf3 Nc6 Bb5
C65	Ruy Lopez Berlin Defense	e4 e5 Nf3 Nc6 Bb5 Nf6
C50	Italian Game	e4 e5 Nf3 Nc6 Bc4
C51	Evans Gambit	e4 e5 Nf3 Nc6 Bc4 Bc5 b4
C57	Italian Game Two Knights Defense	e4 e5 Nf3 Nc6 Bc4 Nf6
C30	King's Gambit	e4 e5 f4
C40	King's Knight Opening	e4 e5 Nf3
C42	Petrov's Defense	e4 e5 Nf3 Nf6
C44	Scotch Game	e4 e5 Nf3 Nc6 d4
C45	Scotch Game Mieses Variation	e4 e5 Nf3 Nc6 d4 exd4 Nxd4
C20	King's Pawn Game	e4 e5
B01	Scandinavian Defense	e4 d5
B07	Pirc Defense	e4 d6 d4 Nf6 Nc3 g6
B10	Caro-Kann Defense	e4 c6
B12	Caro-Kann Advance Variation	e4 c6 d4 d5 e5
B18	Caro-Kann Classical Variation	e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5
C00	French Defense	e4 e6
C01	French Exchange Variation	e4 e6 d4 d5 exd5
C02	French Advance Variation	e4 e6 d4 d5 e5
C10	French Rubinstein Variation	e4 e6 d4 d5 Nc3 dxe4
D00	Queen's Pawn Game	d4 d5
D06	Queen's Gambit	d4 d5 c4
D30	Queen's Gambit Declined	d4 d5 c4 e6
D10	Slav Defense	d4 d5 c4 c6
D80	Grunfeld Defense	d4 Nf6 c4 g6 Nc3 d5
E60	King's Indian Defense	d4 Nf6 c4 g6
E90	King's Indian Defense Classical	d4 Nf6 c4 g6 Nc3 Bg7 e4 d6 Nf3
E20	Nimzo-Indian Defense	d4 Nf6 c4 e6 Nc3
E00	Catalan Opening	d4 Nf6 c4 e6 g3
A45	Trompowsky Attack	d4 Nf6 Bg5
D02	London System	d4 Nf6 Nf3 d5 Bf4
A40	Queen's Pawn	d4
A04	Reti Opening	Nf3
A10	English Opening	c4
A20	English Opening	c4 e5
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_openings.py
from chesswhisper.openings import load_eco_table, match_opening, UNKNOWN_OPENING


def test_load_eco_table_has_entries():
    table = load_eco_table()
    assert len(table) > 20
    assert any(e.eco == "C60" for e in table)


def test_match_exact_short_sequence():
    table = load_eco_table()
    result = match_opening(["e4", "c5"], table)
    assert result.eco == "B20"


def test_match_prefers_longest_match():
    table = load_eco_table()
    # Ruy Lopez Berlin should win over plain Ruy Lopez (C60) since it's a longer match
    result = match_opening(["e4", "e5", "Nf3", "Nc6", "Bb5", "Nf6"], table)
    assert result.eco == "C65"


def test_match_falls_back_to_unknown():
    table = load_eco_table()
    result = match_opening(["a3", "a6", "a4", "a5"], table)
    assert result is UNKNOWN_OPENING
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_openings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.openings'`

- [ ] **Step 4: Implement `openings.py`**

```python
# src/chesswhisper/openings.py
from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class EcoEntry:
    eco: str
    name: str
    moves: tuple[str, ...]


UNKNOWN_OPENING = EcoEntry(eco="A00", name="Uncommon Opening", moves=())


def load_eco_table() -> list[EcoEntry]:
    data_text = resources.files("chesswhisper.data").joinpath("eco.tsv").read_text(encoding="utf-8")
    lines = data_text.strip().splitlines()
    entries = []
    for line in lines[1:]:  # skip header
        eco, name, moves_str = line.split("\t")
        entries.append(EcoEntry(eco=eco, name=name, moves=tuple(moves_str.split())))
    return entries


def match_opening(san_moves: list[str], table: list[EcoEntry]) -> EcoEntry:
    best: EcoEntry = UNKNOWN_OPENING
    for entry in table:
        n = len(entry.moves)
        if n == 0 or n > len(san_moves):
            continue
        if tuple(san_moves[:n]) == entry.moves and n > len(best.moves):
            best = entry
    return best
```

`src/chesswhisper/data/__init__.py` must exist so `importlib.resources` can treat it as a package:

```python
```

- [ ] **Step 5: Add the data package marker and register it for packaging**

Create `src/chesswhisper/data/__init__.py` (empty, as above).

Edit `pyproject.toml`'s `[tool.hatch.build.targets.wheel]` table to include the data file:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/chesswhisper"]
artifacts = ["src/chesswhisper/data/eco.tsv"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pip install -e ".[dev]"` (re-run so the new data package is picked up)
Run: `pytest tests/test_openings.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add src/chesswhisper/data/eco.tsv src/chesswhisper/data/__init__.py src/chesswhisper/openings.py tests/test_openings.py pyproject.toml
git commit -m "feat: add bundled ECO table and opening matcher"
```

---

### Task 4: `stats_engine` core math (classification, phase, accuracy)

**Files:**
- Create: `src/chesswhisper/stats_engine.py`
- Test: `tests/test_stats_engine_core.py`

**Interfaces:**
- Produces:
  ```python
  INACCURACY_CP = 50
  MISTAKE_CP = 100
  BLUNDER_CP = 300
  OPENING_PLY_CUTOFF = 20
  ENDGAME_MATERIAL_THRESHOLD = 20

  def classify_move(cp_loss: int) -> str: ...       # "fine" | "inaccuracy" | "mistake" | "blunder"
  def game_phase(ply: int, fen_after: str) -> str: ...  # "opening" | "middlegame" | "endgame"
  def win_percent(cp: int) -> float: ...
  def move_accuracy(wp_before: float, wp_after: float) -> float: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stats_engine_core.py
import chess
from chesswhisper.stats_engine import (
    classify_move, game_phase, win_percent, move_accuracy,
)


def test_classify_move_boundaries():
    assert classify_move(0) == "fine"
    assert classify_move(49) == "fine"
    assert classify_move(50) == "inaccuracy"
    assert classify_move(99) == "inaccuracy"
    assert classify_move(100) == "mistake"
    assert classify_move(299) == "mistake"
    assert classify_move(300) == "blunder"
    assert classify_move(1500) == "blunder"


def test_game_phase_opening_by_ply():
    start_fen = chess.STARTING_FEN
    assert game_phase(1, start_fen) == "opening"
    assert game_phase(20, start_fen) == "opening"


def test_game_phase_endgame_by_material():
    # Only kings and a couple of pawns left -> should be endgame despite high ply not required
    bare_fen = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 40"
    assert game_phase(40, bare_fen) == "endgame"


def test_game_phase_middlegame_when_material_remains():
    # Full starting material, past the opening cutoff
    assert game_phase(21, chess.STARTING_FEN) == "middlegame"


def test_win_percent_symmetry_around_zero():
    assert abs(win_percent(0) - 50.0) < 1e-6
    assert win_percent(100) > 50.0
    assert win_percent(-100) < 50.0


def test_move_accuracy_no_drop_is_perfect():
    assert move_accuracy(60.0, 60.0) == 100.0


def test_move_accuracy_big_drop_is_low():
    acc = move_accuracy(90.0, 10.0)
    assert 0.0 <= acc < 20.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stats_engine_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.stats_engine'`

- [ ] **Step 3: Implement the core functions**

```python
# src/chesswhisper/stats_engine.py
import math

import chess

INACCURACY_CP = 50
MISTAKE_CP = 100
BLUNDER_CP = 300
OPENING_PLY_CUTOFF = 20
ENDGAME_MATERIAL_THRESHOLD = 20

_PIECE_VALUES = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3}


def classify_move(cp_loss: int) -> str:
    if cp_loss >= BLUNDER_CP:
        return "blunder"
    if cp_loss >= MISTAKE_CP:
        return "mistake"
    if cp_loss >= INACCURACY_CP:
        return "inaccuracy"
    return "fine"


def _non_pawn_material(board: chess.Board) -> int:
    total = 0
    for piece_type, value in _PIECE_VALUES.items():
        total += value * (
            len(board.pieces(piece_type, chess.WHITE))
            + len(board.pieces(piece_type, chess.BLACK))
        )
    return total


def game_phase(ply: int, fen_after: str) -> str:
    if ply <= OPENING_PLY_CUTOFF:
        return "opening"
    board = chess.Board(fen_after)
    if _non_pawn_material(board) <= ENDGAME_MATERIAL_THRESHOLD:
        return "endgame"
    return "middlegame"


def win_percent(cp: int) -> float:
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(wp_before: float, wp_after: float) -> float:
    win_diff = max(0.0, wp_before - wp_after)
    raw = 103.1668 * math.exp(-0.04354 * win_diff) - 3.1669
    return max(0.0, min(100.0, raw))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_stats_engine_core.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/stats_engine.py tests/test_stats_engine_core.py
git commit -m "feat: add move classification, phase detection, and accuracy math"
```

---

### Task 5: `engine_provision` (find-or-download Stockfish)

**Files:**
- Create: `src/chesswhisper/engine_provision.py`
- Test: `tests/test_engine_provision.py`

**Interfaces:**
- Produces:
  ```python
  STOCKFISH_RELEASE_TAG = "sf_19"

  class StockfishProvisionError(Exception): ...

  def asset_name_for_platform(system: str, machine: str) -> str: ...
  def get_stockfish_path(app_data_dir: "Path | None" = None) -> "Path": ...
  ```
  (`app_data_dir` param exists purely so tests can redirect storage to `tmp_path` instead of the real user data dir.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine_provision.py
import os
import stat
import tarfile
import zipfile

import pytest

from chesswhisper.engine_provision import (
    asset_name_for_platform,
    get_stockfish_path,
    StockfishProvisionError,
    _find_binary,
    _extract,
)


@pytest.mark.parametrize("system,machine,expected", [
    ("Windows", "AMD64", "stockfish-windows-x86-64-universal.zip"),
    ("Windows", "ARM64", "stockfish-windows-arm64-universal.zip"),
    ("Linux", "x86_64", "stockfish-linux-x86-64-universal.tar.gz"),
    ("Linux", "aarch64", "stockfish-linux-arm64-universal.tar.gz"),
    ("Darwin", "x86_64", "stockfish-macos-universal.tar.gz"),
    ("Darwin", "arm64", "stockfish-macos-universal.tar.gz"),
])
def test_asset_name_for_platform(system, machine, expected):
    assert asset_name_for_platform(system, machine) == expected


def test_asset_name_for_unsupported_platform_raises():
    with pytest.raises(StockfishProvisionError):
        asset_name_for_platform("Plan9", "mips")


def test_find_binary_locates_nested_executable(tmp_path):
    nested = tmp_path / "stockfish-windows-x86-64-universal" / "stockfish"
    nested.parent.mkdir(parents=True)
    exe = nested.with_suffix(".exe")
    exe.write_bytes(b"fake binary")

    found = _find_binary(tmp_path)

    assert found == exe


def test_find_binary_raises_when_missing(tmp_path):
    with pytest.raises(StockfishProvisionError):
        _find_binary(tmp_path)


def test_extract_zip(tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("inner/stockfish.exe", "fake")
    dest = tmp_path / "out"

    _extract(archive, dest)

    assert (dest / "inner" / "stockfish.exe").exists()


def test_extract_tar_gz(tmp_path):
    inner_file = tmp_path / "stockfish"
    inner_file.write_bytes(b"fake")
    archive = tmp_path / "a.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(inner_file, arcname="inner/stockfish")
    dest = tmp_path / "out"

    _extract(archive, dest)

    assert (dest / "inner" / "stockfish").exists()


def test_get_stockfish_path_reuses_cached_binary(tmp_path, monkeypatch):
    # Pre-place a "binary" where get_stockfish_path expects to find one already installed.
    version_dir = tmp_path / STOCKFISH_TAG_DIRNAME
    version_dir.mkdir(parents=True)
    fake_binary = version_dir / "stockfish.exe"
    fake_binary.write_bytes(b"fake")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not attempt a download when cached binary exists")

    monkeypatch.setattr("chesswhisper.engine_provision._download", fail_if_called)

    result = get_stockfish_path(app_data_dir=tmp_path)

    assert result == fake_binary
```

Add the missing constant import used by the last test:

```python
# add near the top of tests/test_engine_provision.py, alongside the other imports
from chesswhisper.engine_provision import STOCKFISH_TAG_DIRNAME
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engine_provision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.engine_provision'`

- [ ] **Step 3: Implement `engine_provision.py`**

```python
# src/chesswhisper/engine_provision.py
from __future__ import annotations

import os
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import requests
from platformdirs import user_data_dir

STOCKFISH_RELEASE_TAG = "sf_19"
STOCKFISH_TAG_DIRNAME = STOCKFISH_RELEASE_TAG
_RELEASE_BASE_URL = (
    f"https://github.com/official-stockfish/Stockfish/releases/download/{STOCKFISH_RELEASE_TAG}"
)


class StockfishProvisionError(Exception):
    pass


def asset_name_for_platform(system: str, machine: str) -> str:
    system = system.lower()
    machine = machine.lower()

    if system == "windows":
        if machine in ("amd64", "x86_64"):
            return "stockfish-windows-x86-64-universal.zip"
        if machine in ("arm64", "aarch64"):
            return "stockfish-windows-arm64-universal.zip"
    elif system == "linux":
        if machine in ("x86_64", "amd64"):
            return "stockfish-linux-x86-64-universal.tar.gz"
        if machine in ("aarch64", "arm64"):
            return "stockfish-linux-arm64-universal.tar.gz"
    elif system == "darwin":
        return "stockfish-macos-universal.tar.gz"

    raise StockfishProvisionError(
        f"No known Stockfish build for platform system={system!r} machine={machine!r}. "
        "Install Stockfish manually and point chesswhisper at it."
    )


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)


def _extract(archive_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
    else:
        with tarfile.open(archive_path) as tf:
            tf.extractall(dest_dir)


def _find_binary(root: Path) -> Path:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("stockfish") and (name.endswith(".exe") or "." not in name):
            return path
    raise StockfishProvisionError(f"No Stockfish binary found under {root}")


def _verify_binary(path: Path) -> bool:
    try:
        proc = subprocess.Popen(
            [str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
        )
        proc.stdin.write("uci\n")
        proc.stdin.flush()
        for _ in range(200):
            line = proc.stdout.readline()
            if "uciok" in line:
                proc.terminate()
                return True
        proc.terminate()
        return False
    except OSError:
        return False


def get_stockfish_path(app_data_dir: Path | None = None) -> Path:
    import platform

    base_dir = Path(app_data_dir) if app_data_dir else Path(user_data_dir("chesswhisper"))
    version_dir = base_dir / STOCKFISH_TAG_DIRNAME

    if version_dir.is_dir():
        try:
            return _find_binary(version_dir)
        except StockfishProvisionError:
            pass  # fall through and (re-)download

    asset_name = asset_name_for_platform(platform.system(), platform.machine())
    archive_path = base_dir / asset_name
    _download(f"{_RELEASE_BASE_URL}/{asset_name}", archive_path)
    _extract(archive_path, version_dir)
    archive_path.unlink(missing_ok=True)

    binary = _find_binary(version_dir)
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if not _verify_binary(binary):
        raise StockfishProvisionError(
            f"Downloaded Stockfish binary at {binary} did not respond to a UCI handshake."
        )

    return binary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine_provision.py -v`
Expected: PASS (9 tests). Note the cached-binary test never calls `_verify_binary`/`_download` for the happy cache-hit path, so it doesn't need network or a real binary.

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/engine_provision.py tests/test_engine_provision.py
git commit -m "feat: add Stockfish auto-provisioning (download, extract, locate, verify)"
```

---

### Task 6: `engine_analyzer`

**Files:**
- Create: `src/chesswhisper/engine_analyzer.py`
- Test: `tests/test_engine_analyzer.py`

**Interfaces:**
- Consumes: a `chess.pgn.Game` (from Task 2's `LoadedGame.game`) and any object exposing `.analyse(board, limit) -> {"score": chess.engine.PovScore}` (a real `chess.engine.SimpleEngine` or a test fake).
- Produces:
  ```python
  @dataclass
  class PositionEval:
      ply: int
      move_san: str
      color: bool         # chess.WHITE or chess.BLACK — who made this move
      cp_before: int
      cp_after: int
      fen_after: str
      clock_seconds: "float | None"  # remaining clock time after this move, parsed from a %clk PGN comment

  def parse_clock_seconds(comment: str) -> "float | None": ...
  def analyze_game(game: chess.pgn.Game, engine, movetime_ms: int = 300, depth: "int | None" = None) -> list[PositionEval]: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine_analyzer.py
import chess
import chess.engine
import chess.pgn
import io

from chesswhisper.engine_analyzer import analyze_game, parse_clock_seconds, PositionEval


class FakeEngine:
    """Returns a queued sequence of PovScore-like objects, ignoring the board/limit."""

    def __init__(self, scores: list[int]):
        # scores are plain White-relative centipawns, one per analyse() call
        self._scores = iter(scores)

    def analyse(self, board, limit):
        cp = next(self._scores)
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def _parse(pgn_text: str) -> chess.pgn.Game:
    return chess.pgn.read_game(io.StringIO(pgn_text))


SIMPLE_GAME = "1. e4 e5 2. Nf3 *"
GAME_WITH_CLOCK = "1. e4 {[%clk 0:09:58]} e5 {[%clk 0:00:25]} 2. Nf3 {[%clk 0:09:40]} *"


def test_analyze_game_returns_one_eval_per_ply():
    game = _parse(SIMPLE_GAME)
    # initial eval, after e4, after e5, after Nf3
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    assert len(evals) == 3
    assert [e.move_san for e in evals] == ["e4", "e5", "Nf3"]
    assert evals[0].color == chess.WHITE
    assert evals[1].color == chess.BLACK
    assert evals[2].color == chess.WHITE


def test_analyze_game_computes_cp_from_movers_perspective():
    game = _parse(SIMPLE_GAME)
    # White plays e4: eval goes from 0 (White's view) to 30 (White's view) -> mover gained, cp_loss 0
    # Black plays e5: eval was 30 (White's view) = -30 (Black's view);
    #   after e5 eval is 20 (White's view) = -20 (Black's view) -> Black IMPROVED from -30 to -20, cp_loss 0
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    e4_eval = evals[0]
    assert e4_eval.cp_before == 0
    assert e4_eval.cp_after == 30

    e5_eval = evals[1]
    assert e5_eval.cp_before == -30
    assert e5_eval.cp_after == -20


def test_analyze_game_records_fen_after():
    game = _parse(SIMPLE_GAME)
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    board = chess.Board()
    board.push_san("e4")
    assert evals[0].fen_after == board.fen()


def test_analyze_game_clock_seconds_none_without_comment():
    game = _parse(SIMPLE_GAME)
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    assert all(e.clock_seconds is None for e in evals)


def test_analyze_game_extracts_clock_seconds():
    game = _parse(GAME_WITH_CLOCK)
    engine = FakeEngine([0, 30, 20, 50])

    evals = analyze_game(game, engine, movetime_ms=10)

    assert evals[0].clock_seconds == 598.0
    assert evals[1].clock_seconds == 25.0
    assert evals[2].clock_seconds == 580.0


def test_parse_clock_seconds_various_formats():
    assert parse_clock_seconds("[%clk 0:05:23]") == 323.0
    assert parse_clock_seconds("[%clk 1:00:00]") == 3600.0
    assert parse_clock_seconds("no clock here") is None
    assert parse_clock_seconds("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_engine_analyzer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.engine_analyzer'`

- [ ] **Step 3: Implement `engine_analyzer.py`**

```python
# src/chesswhisper/engine_analyzer.py
from __future__ import annotations

import re
from dataclasses import dataclass

import chess
import chess.engine
import chess.pgn

_CLK_RE = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")


@dataclass
class PositionEval:
    ply: int
    move_san: str
    color: bool
    cp_before: int
    cp_after: int
    fen_after: str
    clock_seconds: float | None = None


def parse_clock_seconds(comment: str) -> float | None:
    if not comment:
        return None
    match = _CLK_RE.search(comment)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def analyze_game(
    game: chess.pgn.Game,
    engine,
    movetime_ms: int = 300,
    depth: int | None = None,
) -> list[PositionEval]:
    limit = chess.engine.Limit(depth=depth) if depth else chess.engine.Limit(time=movetime_ms / 1000)

    board = game.board()
    results: list[PositionEval] = []

    prev_score = engine.analyse(board, limit)["score"]

    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move
        mover = board.turn
        san = board.san(move)
        cp_before = prev_score.pov(mover).score(mate_score=100_000)

        board.push(move)
        info_after = engine.analyse(board, limit)
        current_score = info_after["score"]
        cp_after = current_score.pov(mover).score(mate_score=100_000)

        results.append(
            PositionEval(
                ply=ply,
                move_san=san,
                color=mover,
                cp_before=cp_before,
                cp_after=cp_after,
                fen_after=board.fen(),
                clock_seconds=parse_clock_seconds(node.comment),
            )
        )
        prev_score = current_score

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine_analyzer.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/engine_analyzer.py tests/test_engine_analyzer.py
git commit -m "feat: add per-move Stockfish evaluation with dependency-injected engine"
```

---

### Task 7: `stats_engine` report builders

**Files:**
- Modify: `src/chesswhisper/stats_engine.py` (append to the file from Task 4)
- Test: `tests/test_stats_engine_reports.py`

**Interfaces:**
- Consumes: `LoadedGame` (Task 2), `PositionEval` (Task 6), `EcoEntry`/`match_opening`/`load_eco_table` (Task 3).
- Produces:
  ```python
  @dataclass
  class ErrorCounts:
      inaccuracy: int = 0
      mistake: int = 0
      blunder: int = 0

  @dataclass
  class PlayerGameStats:
      acpl: float
      accuracy: float
      errors_by_phase: dict[str, ErrorCounts]  # keys: "opening","middlegame","endgame"

  @dataclass
  class TimeManagementStats:
      available: bool
      low_time_error_rate: "float | None" = None
      normal_time_error_rate: "float | None" = None

  LOW_TIME_THRESHOLD_SECONDS = 30  # clock_seconds below this counts as "time trouble"

  @dataclass
  class _TimeBuckets:
      low_total: int = 0
      low_errors: int = 0
      normal_total: int = 0
      normal_errors: int = 0
      # .merge(other) and .to_stats() -> TimeManagementStats

  @dataclass
  class GameReport:
      game_index: int
      your_color: str          # "white" | "black"
      opponent: str
      result: str              # "win" | "loss" | "draw"
      your_stats: PlayerGameStats
      opponent_stats: PlayerGameStats
      eco: str
      opening_name: str
      time_management: TimeManagementStats
      time_buckets: "_TimeBuckets"  # carried so build_aggregate_report can merge raw counts, not average rates
      top_mistakes: list  # list[PositionEval], your worst moves, sorted desc by cp loss, max 5

  @dataclass
  class OpeningStats:
      eco: str
      name: str
      games: int
      wins: int
      losses: int
      draws: int

  @dataclass
  class AggregateReport:
      player: str
      games: list[GameReport]
      overall_acpl: float
      overall_accuracy: float
      error_totals: dict[str, ErrorCounts]
      openings: list[OpeningStats]
      time_management: TimeManagementStats

  def detect_player(loaded_games: list) -> str: ...  # raises ValueError if ambiguous/empty
  def build_game_report(loaded: "LoadedGame", evals: list, player_name: str, eco_table: list) -> GameReport: ...
  def build_aggregate_report(player: str, game_reports: list) -> AggregateReport: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stats_engine_reports.py
import io

import chess.pgn
import pytest

from chesswhisper.engine_analyzer import PositionEval
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import LoadedGame
from chesswhisper.stats_engine import (
    detect_player, build_game_report, build_aggregate_report,
)

WHITE = True
BLACK = False


def _loaded_game(index, white, black, result, moves_text):
    text = (
        f'[White "{white}"]\n[Black "{black}"]\n[Result "{result}"]\n\n{moves_text} {result}\n'
    )
    game = chess.pgn.read_game(io.StringIO(text))
    return LoadedGame(index=index, game=game, headers=dict(game.headers))


def test_detect_player_picks_most_common_name():
    games = [
        _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5"),
        _loaded_game(1, "Carol", "Alice", "0-1", "1. d4 d5"),
        _loaded_game(2, "Alice", "Dave", "1/2-1/2", "1. c4 c5"),
    ]
    assert detect_player(games) == "Alice"


def test_detect_player_raises_when_no_repeated_name():
    games = [
        _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5"),
        _loaded_game(1, "Carol", "Dave", "0-1", "1. d4 d5"),
    ]
    with pytest.raises(ValueError):
        detect_player(games)


def _evals_no_errors():
    # e4 (White, no loss), e5 (Black, no loss)
    return [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=0, cp_after=20,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=-20, cp_after=-15,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"),
    ]


def test_build_game_report_assigns_your_color_and_result():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = _evals_no_errors()
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.your_color == "white"
    assert report.opponent == "Bob"
    assert report.result == "win"
    assert report.eco == "B20" or report.eco == "C20"  # depends on ECO table content for "e4 e5"


def test_build_game_report_counts_your_blunder():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=50, cp_after=-260,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=260, cp_after=250,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"),
    ]
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.your_stats.errors_by_phase["opening"].blunder == 1
    assert len(report.top_mistakes) == 1
    assert report.top_mistakes[0].move_san == "e4"


def test_build_aggregate_report_sums_across_games():
    loaded_a = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    loaded_b = _loaded_game(1, "Carol", "Alice", "0-1", "1. e4 e5")
    eco_table = load_eco_table()
    evals = _evals_no_errors()

    report_a = build_game_report(loaded_a, evals, "Alice", eco_table)
    report_b = build_game_report(loaded_b, evals, "Alice", eco_table)

    aggregate = build_aggregate_report("Alice", [report_a, report_b])

    assert aggregate.player == "Alice"
    assert len(aggregate.games) == 2
    assert aggregate.games[1].your_color == "black"
    assert aggregate.games[1].result == "win"  # Alice was Black in a 0-1 result


def test_build_game_report_time_management_unavailable_without_clock():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = _evals_no_errors()  # no clock_seconds set on these PositionEval fixtures
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.time_management.available is False


def test_build_game_report_time_management_flags_low_time_errors():
    loaded = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    evals = [
        # Alice (White) blunders with 10s left -> low-time error
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=50, cp_after=-260,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                     clock_seconds=10.0),
        # Bob (Black) response, not Alice's move, ignored for Alice's time management
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=260, cp_after=250,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                     clock_seconds=500.0),
    ]
    eco_table = load_eco_table()

    report = build_game_report(loaded, evals, player_name="Alice", eco_table=eco_table)

    assert report.time_management.available is True
    assert report.time_management.low_time_error_rate == 1.0
    assert report.time_management.normal_time_error_rate is None


def test_build_aggregate_report_merges_time_buckets_across_games():
    loaded_a = _loaded_game(0, "Alice", "Bob", "1-0", "1. e4 e5")
    loaded_b = _loaded_game(1, "Alice", "Carol", "1-0", "1. e4 e5")
    eco_table = load_eco_table()

    low_time_blunder = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=50, cp_after=-260,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                     clock_seconds=5.0),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=260, cp_after=250,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                     clock_seconds=500.0),
    ]
    clean_with_time = [
        PositionEval(ply=1, move_san="e4", color=WHITE, cp_before=0, cp_after=20,
                     fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                     clock_seconds=400.0),
        PositionEval(ply=2, move_san="e5", color=BLACK, cp_before=-20, cp_after=-15,
                     fen_after="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                     clock_seconds=390.0),
    ]

    report_a = build_game_report(loaded_a, low_time_blunder, "Alice", eco_table)
    report_b = build_game_report(loaded_b, clean_with_time, "Alice", eco_table)

    aggregate = build_aggregate_report("Alice", [report_a, report_b])

    assert aggregate.time_management.available is True
    assert aggregate.time_management.low_time_error_rate == 1.0    # 1 error / 1 low-time move
    assert aggregate.time_management.normal_time_error_rate == 0.0  # 0 errors / 1 normal-time move
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_stats_engine_reports.py -v`
Expected: FAIL — `ImportError` (names not yet defined in `stats_engine`)

- [ ] **Step 3: Append the report-building code to `stats_engine.py`**

```python
# append to src/chesswhisper/stats_engine.py
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from chesswhisper.openings import match_opening

PHASES = ("opening", "middlegame", "endgame")


@dataclass
class ErrorCounts:
    inaccuracy: int = 0
    mistake: int = 0
    blunder: int = 0

    def add(self, classification: str) -> None:
        if classification == "inaccuracy":
            self.inaccuracy += 1
        elif classification == "mistake":
            self.mistake += 1
        elif classification == "blunder":
            self.blunder += 1

    def merge(self, other: "ErrorCounts") -> None:
        self.inaccuracy += other.inaccuracy
        self.mistake += other.mistake
        self.blunder += other.blunder


@dataclass
class PlayerGameStats:
    acpl: float
    accuracy: float
    errors_by_phase: dict


@dataclass
class TimeManagementStats:
    available: bool
    low_time_error_rate: float | None = None
    normal_time_error_rate: float | None = None


LOW_TIME_THRESHOLD_SECONDS = 30


@dataclass
class _TimeBuckets:
    low_total: int = 0
    low_errors: int = 0
    normal_total: int = 0
    normal_errors: int = 0

    def merge(self, other: "_TimeBuckets") -> None:
        self.low_total += other.low_total
        self.low_errors += other.low_errors
        self.normal_total += other.normal_total
        self.normal_errors += other.normal_errors

    def to_stats(self) -> TimeManagementStats:
        if self.low_total == 0 and self.normal_total == 0:
            return TimeManagementStats(available=False)
        low_rate = (self.low_errors / self.low_total) if self.low_total else None
        normal_rate = (self.normal_errors / self.normal_total) if self.normal_total else None
        return TimeManagementStats(
            available=True, low_time_error_rate=low_rate, normal_time_error_rate=normal_rate
        )


def _time_buckets_for_color(evals, color) -> _TimeBuckets:
    buckets = _TimeBuckets()
    for ev in evals:
        if ev.color != color or ev.clock_seconds is None:
            continue
        cp_loss = max(0, ev.cp_before - ev.cp_after)
        is_error = classify_move(cp_loss) != "fine"
        if ev.clock_seconds < LOW_TIME_THRESHOLD_SECONDS:
            buckets.low_total += 1
            if is_error:
                buckets.low_errors += 1
        else:
            buckets.normal_total += 1
            if is_error:
                buckets.normal_errors += 1
    return buckets


@dataclass
class GameReport:
    game_index: int
    your_color: str
    opponent: str
    result: str
    your_stats: PlayerGameStats
    opponent_stats: PlayerGameStats
    eco: str
    opening_name: str
    time_management: TimeManagementStats
    time_buckets: _TimeBuckets = field(default_factory=_TimeBuckets)
    top_mistakes: list = field(default_factory=list)


@dataclass
class OpeningStats:
    eco: str
    name: str
    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0


@dataclass
class AggregateReport:
    player: str
    games: list
    overall_acpl: float
    overall_accuracy: float
    error_totals: dict
    openings: list
    time_management: TimeManagementStats


def detect_player(loaded_games) -> str:
    if not loaded_games:
        raise ValueError("Cannot detect player from an empty game list")

    counts = Counter()
    for lg in loaded_games:
        counts[lg.headers.get("White", "")] += 1
        counts[lg.headers.get("Black", "")] += 1
    counts.pop("", None)

    if not counts:
        raise ValueError("No player names found in PGN headers; pass --player explicitly")

    (top_name, top_count), *rest = counts.most_common()
    if rest and rest[0][1] == top_count:
        raise ValueError(
            f"Cannot auto-detect player: multiple names tied at {top_count} games. "
            "Pass --player explicitly."
        )
    if top_count < 1:
        raise ValueError("No repeated player name found; pass --player explicitly")
    return top_name


def _player_stats_for_color(evals, color, ply_to_phase) -> tuple[PlayerGameStats, list]:
    errors_by_phase = {p: ErrorCounts() for p in PHASES}
    cp_losses = []
    accuracies = []
    flagged = []

    for ev in evals:
        if ev.color != color:
            continue
        cp_loss = max(0, ev.cp_before - ev.cp_after)
        cp_losses.append(cp_loss)
        accuracies.append(move_accuracy(win_percent(ev.cp_before), win_percent(ev.cp_after)))

        classification = classify_move(cp_loss)
        if classification != "fine":
            phase = ply_to_phase[ev.ply]
            errors_by_phase[phase].add(classification)
            flagged.append((cp_loss, ev))

    acpl = sum(cp_losses) / len(cp_losses) if cp_losses else 0.0
    accuracy = sum(accuracies) / len(accuracies) if accuracies else 100.0
    stats = PlayerGameStats(acpl=acpl, accuracy=accuracy, errors_by_phase=errors_by_phase)

    flagged.sort(key=lambda pair: pair[0], reverse=True)
    top_mistakes = [ev for _, ev in flagged[:5]]
    return stats, top_mistakes


def build_game_report(loaded, evals, player_name: str, eco_table) -> GameReport:
    white = loaded.headers.get("White", "")
    black = loaded.headers.get("Black", "")
    result_header = loaded.headers.get("Result", "*")

    your_color = "white" if player_name == white else "black"
    opponent = black if your_color == "white" else white

    if result_header == "1-0":
        result = "win" if your_color == "white" else "loss"
    elif result_header == "0-1":
        result = "win" if your_color == "black" else "loss"
    else:
        result = "draw"

    ply_to_phase = {ev.ply: game_phase(ev.ply, ev.fen_after) for ev in evals}

    your_bool_color = your_color == "white"
    your_stats, top_mistakes = _player_stats_for_color(evals, your_bool_color, ply_to_phase)
    opp_stats, _ = _player_stats_for_color(evals, not your_bool_color, ply_to_phase)

    san_moves = [ev.move_san for ev in evals]
    opening = match_opening(san_moves, eco_table)

    time_buckets = _time_buckets_for_color(evals, your_bool_color)

    return GameReport(
        game_index=loaded.index,
        your_color=your_color,
        opponent=opponent,
        result=result,
        your_stats=your_stats,
        opponent_stats=opp_stats,
        eco=opening.eco,
        opening_name=opening.name,
        time_management=time_buckets.to_stats(),
        time_buckets=time_buckets,
        top_mistakes=top_mistakes,
    )


def build_aggregate_report(player: str, game_reports: list) -> AggregateReport:
    all_cp_losses_weight = []
    all_accuracies = []
    error_totals = {p: ErrorCounts() for p in PHASES}
    opening_map: dict = {}
    combined_time_buckets = _TimeBuckets()

    for gr in game_reports:
        all_accuracies.append(gr.your_stats.accuracy)
        all_cp_losses_weight.append(gr.your_stats.acpl)
        for phase in PHASES:
            error_totals[phase].merge(gr.your_stats.errors_by_phase[phase])
        combined_time_buckets.merge(gr.time_buckets)

        key = gr.eco
        if key not in opening_map:
            opening_map[key] = OpeningStats(eco=gr.eco, name=gr.opening_name)
        entry = opening_map[key]
        entry.games += 1
        if gr.result == "win":
            entry.wins += 1
        elif gr.result == "loss":
            entry.losses += 1
        else:
            entry.draws += 1

    overall_acpl = sum(all_cp_losses_weight) / len(all_cp_losses_weight) if all_cp_losses_weight else 0.0
    overall_accuracy = sum(all_accuracies) / len(all_accuracies) if all_accuracies else 100.0

    return AggregateReport(
        player=player,
        games=game_reports,
        overall_acpl=overall_acpl,
        overall_accuracy=overall_accuracy,
        error_totals=error_totals,
        openings=sorted(opening_map.values(), key=lambda o: o.games, reverse=True),
        time_management=combined_time_buckets.to_stats(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_stats_engine_reports.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full test suite so far**

Run: `pytest -v`
Expected: All tests from Tasks 2–7 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/chesswhisper/stats_engine.py tests/test_stats_engine_reports.py
git commit -m "feat: add per-game and aggregate report builders"
```

---

### Task 8: `i18n`

**Files:**
- Create: `src/chesswhisper/i18n.py`
- Test: `tests/test_i18n.py`

**Interfaces:**
- Produces:
  ```python
  def t(key: str, lang: str) -> str: ...  # lang is "tr" or "en"
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_i18n.py
import pytest
from chesswhisper.i18n import t


def test_known_key_turkish():
    assert t("report_title", "tr") == "chesswhisper Analiz Raporu"


def test_known_key_english():
    assert t("report_title", "en") == "chesswhisper Analysis Report"


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        t("does_not_exist", "tr")


def test_unsupported_lang_raises():
    with pytest.raises(KeyError):
        t("report_title", "fr")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_i18n.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.i18n'`

- [ ] **Step 3: Implement `i18n.py`**

```python
# src/chesswhisper/i18n.py
_STRINGS = {
    "report_title": {"tr": "chesswhisper Analiz Raporu", "en": "chesswhisper Analysis Report"},
    "player": {"tr": "Oyuncu", "en": "Player"},
    "overall_summary": {"tr": "Genel Özet", "en": "Overall Summary"},
    "acpl": {"tr": "Ortalama Centipawn Kaybı (ACPL)", "en": "Average Centipawn Loss (ACPL)"},
    "accuracy": {"tr": "Doğruluk", "en": "Accuracy"},
    "errors_by_phase": {"tr": "Faza Göre Hatalar", "en": "Errors by Phase"},
    "phase_opening": {"tr": "Açılış", "en": "Opening"},
    "phase_middlegame": {"tr": "Orta Oyun", "en": "Middlegame"},
    "phase_endgame": {"tr": "Final", "en": "Endgame"},
    "inaccuracy": {"tr": "Belirsizlik", "en": "Inaccuracy"},
    "mistake": {"tr": "Hata", "en": "Mistake"},
    "blunder": {"tr": "Gaf", "en": "Blunder"},
    "openings": {"tr": "Açılış Repertuarı", "en": "Opening Repertoire"},
    "opening_games": {"tr": "Oyun", "en": "Games"},
    "opening_record": {"tr": "G/M/B", "en": "W/L/D"},
    "time_management": {"tr": "Zaman Yönetimi", "en": "Time Management"},
    "time_management_unavailable": {
        "tr": "Bu PGN'de saat bilgisi bulunmadığı için zaman yönetimi analizi mevcut değil.",
        "en": "Time management analysis is not available because this PGN has no clock data.",
    },
    "low_time_error_rate": {
        "tr": "Zaman baskısında (<30sn) hata oranı", "en": "Error rate under time pressure (<30s)",
    },
    "normal_time_error_rate": {
        "tr": "Normal zamanda hata oranı", "en": "Error rate with normal time",
    },
    "not_enough_data": {"tr": "yeterli veri yok", "en": "not enough data"},
    "coaching_commentary": {"tr": "Koçluk Yorumu", "en": "Coaching Commentary"},
    "per_game_breakdown": {"tr": "Oyun Bazında Döküm", "en": "Per-Game Breakdown"},
    "game_result_win": {"tr": "Kazandı", "en": "Won"},
    "game_result_loss": {"tr": "Kaybetti", "en": "Lost"},
    "game_result_draw": {"tr": "Berabere", "en": "Draw"},
    "top_mistakes": {"tr": "Kritik Hamleler", "en": "Critical Moves"},
}


def t(key: str, lang: str) -> str:
    entry = _STRINGS[key]
    return entry[lang]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_i18n.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/i18n.py tests/test_i18n.py
git commit -m "feat: add TR/EN report label strings"
```

---

### Task 9: `report_writer`

**Files:**
- Create: `src/chesswhisper/report_writer.py`
- Test: `tests/test_report_writer.py`

**Interfaces:**
- Consumes: `AggregateReport` (Task 7), `t()` (Task 8).
- Produces:
  ```python
  def render_markdown(report, commentary: str, lang: str) -> str: ...
  def write_report(report, commentary: str, lang: str, output_path: str) -> None: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report_writer.py
from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.report_writer import render_markdown, write_report


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    errors["middlegame"].blunder = 1
    your_stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    opp_stats = PlayerGameStats(acpl=30.0, accuracy=90.0, errors_by_phase={p: ErrorCounts() for p in PHASES})

    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=your_stats, opponent_stats=opp_stats,
        eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False),
        top_mistakes=[],
    )

    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors,
        openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1, losses=0, draws=0)],
        time_management=TimeManagementStats(available=False),
    )


def test_render_markdown_turkish_contains_key_sections():
    md = render_markdown(_sample_report(), commentary="Test yorumu.", lang="tr")

    assert "chesswhisper Analiz Raporu" in md
    assert "Alice" in md
    assert "Ruy Lopez" in md
    assert "42.5" in md or "42.50" in md
    assert "Test yorumu." in md


def test_render_markdown_english_uses_english_labels():
    md = render_markdown(_sample_report(), commentary="Test comment.", lang="en")

    assert "chesswhisper Analysis Report" in md
    assert "Opening Repertoire" in md


def test_write_report_creates_file(tmp_path):
    out = tmp_path / "report.md"
    write_report(_sample_report(), commentary="Hi.", lang="en", output_path=str(out))

    assert out.exists()
    assert "Hi." in out.read_text(encoding="utf-8")


def test_render_markdown_handles_partial_time_management_data():
    report = _sample_report()
    # available=True but only the low-time bucket has data (normal_time_error_rate is None)
    report.time_management = TimeManagementStats(available=True, low_time_error_rate=0.5, normal_time_error_rate=None)

    md = render_markdown(report, commentary="", lang="en")

    assert "50.0%" in md
    assert "not enough data" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.report_writer'`

- [ ] **Step 3: Implement `report_writer.py`**

```python
# src/chesswhisper/report_writer.py
from chesswhisper.i18n import t
from chesswhisper.stats_engine import PHASES

_RESULT_KEY = {"win": "game_result_win", "loss": "game_result_loss", "draw": "game_result_draw"}
_PHASE_KEY = {"opening": "phase_opening", "middlegame": "phase_middlegame", "endgame": "phase_endgame"}


def _errors_table(errors_by_phase: dict, lang: str) -> str:
    lines = [
        "| Faza / Phase | " + t("inaccuracy", lang) + " | " + t("mistake", lang) + " | " + t("blunder", lang) + " |",
        "| --- | --- | --- | --- |",
    ]
    for phase in PHASES:
        counts = errors_by_phase[phase]
        lines.append(
            f"| {t(_PHASE_KEY[phase], lang)} | {counts.inaccuracy} | {counts.mistake} | {counts.blunder} |"
        )
    return "\n".join(lines)


def render_markdown(report, commentary: str, lang: str) -> str:
    parts = [f"# {t('report_title', lang)}", ""]
    parts.append(f"**{t('player', lang)}:** {report.player}")
    parts.append("")
    parts.append(f"## {t('overall_summary', lang)}")
    parts.append(f"- {t('acpl', lang)}: {report.overall_acpl:.1f}")
    parts.append(f"- {t('accuracy', lang)}: {report.overall_accuracy:.1f}%")
    parts.append("")
    parts.append(f"### {t('errors_by_phase', lang)}")
    parts.append(_errors_table(report.error_totals, lang))
    parts.append("")

    parts.append(f"## {t('openings', lang)}")
    parts.append(f"| Eco | {t('openings', lang)} | {t('opening_games', lang)} | {t('opening_record', lang)} |")
    parts.append("| --- | --- | --- | --- |")
    for op in report.openings:
        parts.append(f"| {op.eco} | {op.name} | {op.games} | {op.wins}/{op.losses}/{op.draws} |")
    parts.append("")

    parts.append(f"## {t('time_management', lang)}")
    if report.time_management.available:
        low_rate = report.time_management.low_time_error_rate
        normal_rate = report.time_management.normal_time_error_rate
        low_str = f"{low_rate:.1%}" if low_rate is not None else t("not_enough_data", lang)
        normal_str = f"{normal_rate:.1%}" if normal_rate is not None else t("not_enough_data", lang)
        parts.append(f"- {t('low_time_error_rate', lang)}: {low_str}")
        parts.append(f"- {t('normal_time_error_rate', lang)}: {normal_str}")
    else:
        parts.append(t("time_management_unavailable", lang))
    parts.append("")

    parts.append(f"## {t('coaching_commentary', lang)}")
    parts.append(commentary if commentary else "-")
    parts.append("")

    parts.append(f"## {t('per_game_breakdown', lang)}")
    for game in report.games:
        result_str = t(_RESULT_KEY[game.result], lang)
        parts.append(
            f"### {game.game_index + 1}. vs {game.opponent} ({result_str}, {game.your_color})"
        )
        parts.append(f"- {game.opening_name} ({game.eco})")
        parts.append(f"- {t('acpl', lang)}: {game.your_stats.acpl:.1f}")
        parts.append(f"- {t('accuracy', lang)}: {game.your_stats.accuracy:.1f}%")
        if game.top_mistakes:
            parts.append(f"- {t('top_mistakes', lang)}: " + ", ".join(m.move_san for m in game.top_mistakes))
        parts.append("")

    return "\n".join(parts)


def write_report(report, commentary: str, lang: str, output_path: str) -> None:
    markdown = render_markdown(report, commentary, lang)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report_writer.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/report_writer.py tests/test_report_writer.py
git commit -m "feat: render aggregate report as TR/EN Markdown"
```

---

### Task 10: `llm` base + none provider

**Files:**
- Create: `src/chesswhisper/llm/base.py`
- Create: `src/chesswhisper/llm/none_provider.py`
- Test: `tests/test_llm_none_provider.py`

**Interfaces:**
- Produces:
  ```python
  class LLMProviderError(Exception): ...

  class LLMProvider(ABC):
      @abstractmethod
      def generate_commentary(self, report, lang: str) -> str: ...

  class NoneProvider(LLMProvider):
      def generate_commentary(self, report, lang: str) -> str: ...  # always ""
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_none_provider.py
from chesswhisper.llm.none_provider import NoneProvider


def test_none_provider_returns_empty_string():
    provider = NoneProvider()
    assert provider.generate_commentary(report=None, lang="tr") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_none_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.llm.none_provider'`

- [ ] **Step 3: Implement `base.py` and `none_provider.py`**

```python
# src/chesswhisper/llm/base.py
from abc import ABC, abstractmethod


class LLMProviderError(Exception):
    pass


class LLMProvider(ABC):
    @abstractmethod
    def generate_commentary(self, report, lang: str) -> str:
        raise NotImplementedError
```

```python
# src/chesswhisper/llm/none_provider.py
from chesswhisper.llm.base import LLMProvider


class NoneProvider(LLMProvider):
    def generate_commentary(self, report, lang: str) -> str:
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_llm_none_provider.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/llm/base.py src/chesswhisper/llm/none_provider.py tests/test_llm_none_provider.py
git commit -m "feat: add LLMProvider interface and no-op provider"
```

---

### Task 11: `llm/claude_provider`

**Files:**
- Create: `src/chesswhisper/llm/claude_provider.py`
- Test: `tests/test_llm_claude_provider.py`

**Interfaces:**
- Consumes: `AggregateReport` (Task 7), `LLMProvider`/`LLMProviderError` (Task 10).
- Produces:
  ```python
  class ClaudeProvider(LLMProvider):
      def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5"): ...
      def generate_commentary(self, report, lang: str) -> str: ...

  def build_prompt(report, lang: str) -> str: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_claude_provider.py
import pytest

from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.claude_provider import ClaudeProvider, build_prompt


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=stats, opponent_stats=stats, eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False), top_mistakes=[],
    )
    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors, openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1)],
        time_management=TimeManagementStats(available=False),
    )


def test_build_prompt_includes_key_numbers():
    prompt = build_prompt(_sample_report(), lang="tr")
    assert "Alice" in prompt
    assert "42.5" in prompt
    assert "Ruy Lopez" in prompt
    assert "tr" not in prompt.split()  # sanity: lang code itself isn't dumped verbatim into prose


def test_generate_commentary_returns_text(mocker):
    fake_response = mocker.Mock()
    fake_response.content = [mocker.Mock(text="Harika bir açılış oynadın!")]

    fake_client = mocker.Mock()
    fake_client.messages.create.return_value = fake_response

    provider = ClaudeProvider(api_key="fake-key")
    provider._client = fake_client  # inject the mocked SDK client

    result = provider.generate_commentary(_sample_report(), lang="tr")

    assert result == "Harika bir açılış oynadın!"
    fake_client.messages.create.assert_called_once()


def test_generate_commentary_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.messages.create.side_effect = RuntimeError("boom")

    provider = ClaudeProvider(api_key="fake-key")
    provider._client = fake_client

    with pytest.raises(LLMProviderError):
        provider.generate_commentary(_sample_report(), lang="tr")


def test_missing_api_key_raises_at_construction(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMProviderError):
        ClaudeProvider(api_key=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_claude_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.llm.claude_provider'`

- [ ] **Step 3: Implement `claude_provider.py`**

```python
# src/chesswhisper/llm/claude_provider.py
import os

import anthropic

from chesswhisper.llm.base import LLMProvider, LLMProviderError

_LANG_INSTRUCTION = {
    "tr": "Yanıtını Türkçe yaz.",
    "en": "Write your response in English.",
}


def build_prompt(report, lang: str) -> str:
    lines = [
        f"Player: {report.player}",
        f"Overall ACPL: {report.overall_acpl:.1f}",
        f"Overall accuracy: {report.overall_accuracy:.1f}%",
        "Error totals by phase:",
    ]
    for phase, counts in report.error_totals.items():
        lines.append(f"  {phase}: inaccuracies={counts.inaccuracy}, mistakes={counts.mistake}, blunders={counts.blunder}")

    lines.append("Opening repertoire:")
    for op in report.openings:
        lines.append(f"  {op.name} ({op.eco}): {op.games} games, {op.wins}W/{op.losses}L/{op.draws}D")

    lines.append("Most critical mistakes across games:")
    for game in report.games:
        for mistake in game.top_mistakes:
            lines.append(
                f"  Game {game.game_index + 1}, move {mistake.move_san} (ply {mistake.ply}): "
                f"lost {mistake.cp_before - mistake.cp_after} cp"
            )

    instruction = _LANG_INSTRUCTION[lang]
    lines.append("")
    lines.append(
        f"You are a supportive chess coach. Using only the data above, write a short, concrete "
        f"coaching summary (3-5 short paragraphs) explaining this player's main strengths, their "
        f"most important weaknesses, and 1-2 specific things to practice next. {instruction}"
    )
    return "\n".join(lines)


class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5"):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMProviderError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY or pass --llm none."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model

    def generate_commentary(self, report, lang: str) -> str:
        prompt = build_prompt(report, lang)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - any SDK failure becomes a provider error
            raise LLMProviderError(f"Claude API call failed: {exc}") from exc

        return response.content[0].text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_claude_provider.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/llm/claude_provider.py tests/test_llm_claude_provider.py
git commit -m "feat: add Claude LLM provider"
```

---

### Task 12: `llm/openai_provider`

**Files:**
- Create: `src/chesswhisper/llm/openai_provider.py`
- Test: `tests/test_llm_openai_provider.py`

**Interfaces:**
- Consumes: `AggregateReport` (Task 7), `LLMProvider`/`LLMProviderError` (Task 10), `build_prompt` (Task 11, reused as-is).
- Produces:
  ```python
  class OpenAIProvider(LLMProvider):
      def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini"): ...
      def generate_commentary(self, report, lang: str) -> str: ...
  ```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_openai_provider.py
import pytest

from chesswhisper.stats_engine import (
    AggregateReport, GameReport, PlayerGameStats, ErrorCounts,
    TimeManagementStats, OpeningStats, PHASES,
)
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.openai_provider import OpenAIProvider


def _sample_report():
    errors = {p: ErrorCounts() for p in PHASES}
    stats = PlayerGameStats(acpl=42.5, accuracy=87.3, errors_by_phase=errors)
    game = GameReport(
        game_index=0, your_color="white", opponent="Bob", result="win",
        your_stats=stats, opponent_stats=stats, eco="C60", opening_name="Ruy Lopez",
        time_management=TimeManagementStats(available=False), top_mistakes=[],
    )
    return AggregateReport(
        player="Alice", games=[game], overall_acpl=42.5, overall_accuracy=87.3,
        error_totals=errors, openings=[OpeningStats(eco="C60", name="Ruy Lopez", games=1, wins=1)],
        time_management=TimeManagementStats(available=False),
    )


def test_generate_commentary_returns_text(mocker):
    fake_message = mocker.Mock()
    fake_message.content = "İyi oynadın!"
    fake_choice = mocker.Mock()
    fake_choice.message = fake_message
    fake_response = mocker.Mock()
    fake_response.choices = [fake_choice]

    fake_client = mocker.Mock()
    fake_client.chat.completions.create.return_value = fake_response

    provider = OpenAIProvider(api_key="fake-key")
    provider._client = fake_client

    result = provider.generate_commentary(_sample_report(), lang="tr")

    assert result == "İyi oynadın!"


def test_generate_commentary_wraps_api_errors(mocker):
    fake_client = mocker.Mock()
    fake_client.chat.completions.create.side_effect = RuntimeError("boom")

    provider = OpenAIProvider(api_key="fake-key")
    provider._client = fake_client

    with pytest.raises(LLMProviderError):
        provider.generate_commentary(_sample_report(), lang="tr")


def test_missing_api_key_raises_at_construction(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMProviderError):
        OpenAIProvider(api_key=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_openai_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.llm.openai_provider'`

- [ ] **Step 3: Implement `openai_provider.py`**

```python
# src/chesswhisper/llm/openai_provider.py
import os

import openai

from chesswhisper.llm.base import LLMProvider, LLMProviderError
from chesswhisper.llm.claude_provider import build_prompt


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini"):
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMProviderError(
                "No OpenAI API key found. Set OPENAI_API_KEY or pass --llm none."
            )
        self._client = openai.OpenAI(api_key=key)
        self._model = model

    def generate_commentary(self, report, lang: str) -> str:
        prompt = build_prompt(report, lang)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"OpenAI API call failed: {exc}") from exc

        return response.choices[0].message.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_openai_provider.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/llm/openai_provider.py tests/test_llm_openai_provider.py
git commit -m "feat: add OpenAI LLM provider"
```

---

### Task 13: `cli`

**Files:**
- Create: `src/chesswhisper/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes everything from Tasks 2–12.
- Produces: `cli` (a `click.Group`), console-script entry point `chesswhisper` (already declared in `pyproject.toml`, Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import textwrap

from click.testing import CliRunner

from chesswhisper.cli import cli

SAMPLE_PGN = textwrap.dedent("""
    [Event "Test"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


class FakeEngine:
    def __init__(self):
        self._scores = [0, 20, 10, 15]
        self._i = 0

    def analyse(self, board, limit):
        import chess.engine
        cp = self._scores[self._i]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def test_analyze_produces_report_with_none_llm(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda: FakeEngine())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "analyze", str(pgn_path),
            "--player", "Alice",
            "--llm", "none",
            "--lang", "en",
            "-o", str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "chesswhisper Analysis Report" in content
    assert "Alice" in content


def test_analyze_missing_pgn_file_fails_fast(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(tmp_path / "missing.pgn"), "--llm", "none"],
    )

    assert result.exit_code != 0


def test_analyze_llm_failure_falls_back_to_stats_only(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda: FakeEngine())

    def boom(*args, **kwargs):
        from chesswhisper.llm.base import LLMProviderError
        raise LLMProviderError("no key")

    monkeypatch.setattr("chesswhisper.cli._build_llm_provider", lambda name: type(
        "P", (), {"generate_commentary": staticmethod(boom)}
    )())

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Alice", "--llm", "claude", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert "no key" in result.output or "warning" in result.output.lower()
    assert output_path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chesswhisper.cli'`

- [ ] **Step 3: Implement `cli.py`**

```python
# src/chesswhisper/cli.py
import os
import sys

import click

from chesswhisper.engine_analyzer import analyze_game
from chesswhisper.engine_provision import get_stockfish_path, StockfishProvisionError
from chesswhisper.llm.base import LLMProviderError
from chesswhisper.llm.none_provider import NoneProvider
from chesswhisper.openings import load_eco_table
from chesswhisper.pgn_loader import load_pgn
from chesswhisper.report_writer import write_report
from chesswhisper.stats_engine import build_aggregate_report, build_game_report, detect_player


def _open_engine():
    import chess.engine

    path = get_stockfish_path()
    return chess.engine.SimpleEngine.popen_uci(str(path))


def _build_llm_provider(name: str):
    if name == "none":
        return NoneProvider()
    if name == "claude":
        from chesswhisper.llm.claude_provider import ClaudeProvider
        return ClaudeProvider()
    if name == "openai":
        from chesswhisper.llm.openai_provider import OpenAIProvider
        return OpenAIProvider()
    raise ValueError(f"Unknown LLM provider: {name}")


@click.group()
def cli():
    pass


@cli.command()
@click.argument("pgn_path", type=click.Path(exists=False))
@click.option("--player", default=None, help="Player name to analyze (matches PGN White/Black headers).")
@click.option("--lang", type=click.Choice(["tr", "en"]), default="tr")
@click.option("--llm", "llm_name", type=click.Choice(["claude", "openai", "none"]), default="none")
@click.option("-o", "--output", "output_path", default="report.md", type=click.Path())
@click.option("--movetime", default=300, help="Engine think time per position, in milliseconds.")
@click.option("--depth", default=None, type=int, help="Fixed search depth (overrides --movetime).")
def analyze(pgn_path, player, lang, llm_name, output_path, movetime, depth):
    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    if not os.access(output_dir, os.W_OK):
        click.echo(f"Error: cannot write to output directory {output_dir}", err=True)
        sys.exit(1)

    try:
        load_result = load_pgn(pgn_path)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    for warning in load_result.warnings:
        click.echo(f"Warning: {warning}", err=True)

    if not load_result.games:
        click.echo("Error: no valid games found in PGN file", err=True)
        sys.exit(1)

    if player is None:
        try:
            player = detect_player(load_result.games)
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    eco_table = load_eco_table()

    try:
        engine = _open_engine()
    except StockfishProvisionError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    try:
        game_reports = []
        for loaded in load_result.games:
            evals = analyze_game(loaded.game, engine, movetime_ms=movetime, depth=depth)
            game_reports.append(build_game_report(loaded, evals, player, eco_table))
    finally:
        if hasattr(engine, "quit"):
            engine.quit()

    aggregate = build_aggregate_report(player, game_reports)

    commentary = ""
    if llm_name != "none":
        try:
            provider = _build_llm_provider(llm_name)
            commentary = provider.generate_commentary(aggregate, lang)
        except LLMProviderError as exc:
            click.echo(f"Warning: LLM commentary unavailable ({exc}); writing stats-only report.", err=True)

    write_report(aggregate, commentary, lang, output_path)
    click.echo(f"Report written to {output_path}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the entire test suite**

Run: `pytest -v`
Expected: every test from Tasks 2–13 PASSES.

- [ ] **Step 6: Commit**

```bash
git add src/chesswhisper/cli.py tests/test_cli.py
git commit -m "feat: wire pipeline into the analyze CLI command"
```

---

### Task 14: End-to-end smoke test + README update

**Files:**
- Create: `tests/test_smoke_real_stockfish.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the real `chesswhisper analyze` CLI, a real Stockfish binary if present on the system.

- [ ] **Step 1: Write the smoke test (auto-skips without a real Stockfish binary)**

```python
# tests/test_smoke_real_stockfish.py
import shutil
import textwrap

import pytest
from click.testing import CliRunner

from chesswhisper.cli import cli

SAMPLE_PGN = textwrap.dedent("""
    [Event "Smoke"]
    [White "Alice"]
    [Black "Bob"]
    [Result "1-0"]

    1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
""").strip() + "\n"


def _system_stockfish_available() -> bool:
    return shutil.which("stockfish") is not None


@pytest.mark.skipif(not _system_stockfish_available(), reason="no system Stockfish binary on PATH")
def test_end_to_end_with_real_stockfish(tmp_path, monkeypatch):
    import chess.engine

    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")
    output_path = tmp_path / "report.md"

    monkeypatch.setattr(
        "chesswhisper.cli._open_engine",
        lambda: chess.engine.SimpleEngine.popen_uci(shutil.which("stockfish")),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["analyze", str(pgn_path), "--player", "Alice", "--llm", "none",
         "--movetime", "50", "-o", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip() != ""
```

- [ ] **Step 2: Run it to confirm it skips cleanly without Stockfish on PATH**

Run: `pytest tests/test_smoke_real_stockfish.py -v`
Expected: `SKIPPED` (unless `stockfish` happens to already be on PATH, in which case it should PASS).

- [ ] **Step 3: Update `README.md` with real usage instructions**

Replace the "Planned usage" section's contents with:

```markdown
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
```

Also update the "Status" section to say implementation is in progress rather than not started.

- [ ] **Step 4: Run the full test suite one last time**

Run: `pytest -v`
Expected: all non-skipped tests PASS.

- [ ] **Step 5: Commit and push**

```bash
git add tests/test_smoke_real_stockfish.py README.md
git commit -m "test: add real-Stockfish smoke test; document CLI usage"
git push
```

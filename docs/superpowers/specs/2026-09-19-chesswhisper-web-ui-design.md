# chesswhisper Web UI — Design Spec

Date: 2026-09-19
Status: Approved (pending implementation plan)

## 1. Overview

Add a simple, local, single-user web interface to `chesswhisper` so a user
can upload a `.pgn` file in a browser and see the analysis report rendered
on a page, instead of using the CLI. The web layer is a thin wrapper around
the existing pipeline (`pgn_loader`, `engine_analyzer`, `stats_engine`,
`llm` providers, `report_writer`) — no analysis logic is duplicated.

## 2. Goals

- A `chesswhisper serve [--host HOST] [--port PORT]` command that starts a
  local web server.
- A single upload page (`GET /`) with: a `.pgn` file input, an optional
  player-name text field (auto-detected if left blank, same semantics as
  the CLI's `--player`), a language selector (TR/EN), and an LLM provider
  selector (Claude / OpenAI / None).
- Submitting the form (`POST /analyze`) runs the full analysis pipeline
  synchronously and renders the resulting report as an HTML page in the
  same request/response cycle (a loading indicator on the client side
  covers the wait; no background jobs, no polling).
- The report is produced by converting `report_writer`'s existing Markdown
  output to HTML for display — the web UI never re-implements report
  formatting.
- Clean, non-traceback error pages for the same failure modes the CLI
  already handles (missing/invalid PGN, unrecognized player, Stockfish
  provisioning failure, LLM failure — the last one degrades to a
  stats-only report with a visible warning, same as the CLI).

## 3. Non-goals (v1)

- No authentication, no multi-user support, no persistent storage of past
  reports — this is a local, single-user tool.
- No background job queue / polling / progress bar with real percentage —
  synchronous request with a simple "analyzing..." indicator only.
- No exposure of advanced engine-tuning flags (`--movetime`, `--depth`,
  `--engine-path`) in the web form — these keep their CLI defaults for the
  web path. (`serve`'s own `--host`/`--port` are server startup flags, not
  part of this list.)
- Not intended to be exposed on a public network; default bind address is
  `127.0.0.1`.

## 4. Architecture

```
Browser                          chesswhisper (FastAPI app)
   |  GET /                              |
   |------------------------------------>|  renders upload form (index.html)
   |  POST /analyze (multipart: pgn +    |
   |  player/lang/llm fields)            |
   |------------------------------------>|
   |                                      |  save PGN to temp file
   |                                      |  run_analysis(...) [shared with CLI]
   |                                      |  -> AggregateReport, commentary, warnings
   |                                      |  render_markdown(...) -> markdown -> HTML
   |  <-----------------------------------|  renders result.html (report + warnings)
```

`run_analysis` is a new pure function extracted from `cli.py`'s `analyze`
command, used by both the CLI and the web route — see section 5.1.

## 5. Components

### 5.1 `cli.py` refactor: `run_analysis`

Extract the orchestration core of the existing `analyze` Click command into:

```python
def run_analysis(
    pgn_path: str,
    player: str | None,
    lang: str,
    llm_name: str,
    movetime: int = 300,
    depth: int | None = None,
    engine_path: str | None = None,
) -> tuple[AggregateReport, str, list[str]]:
    """Runs the full pipeline. Returns (aggregate_report, commentary, warnings).

    Raises FileNotFoundError, ValueError, or StockfishProvisionError on
    unrecoverable input/engine problems — callers (CLI, web route) decide
    how to present these. Does NOT write any output file. LLM failures are
    caught internally and folded into the returned `warnings` list with an
    empty `commentary` instead of raising.
    """
```

`warnings` accumulates: PGN per-game parse warnings (from `PgnLoadResult`)
and an LLM-failure warning (if `llm_name != "none"` and the provider
raised `LLMProviderError`) — the same two warning sources the CLI already
surfaces via `click.echo(..., err=True)`, now returned as data instead of
printed directly, so both callers can present them their own way.

The `analyze` Click command becomes a thin wrapper: it keeps its own
fail-fast output-path check (file-writing is CLI-specific), calls
`run_analysis`, prints any `warnings` to stderr, catches
`FileNotFoundError`/`ValueError`/`StockfishProvisionError` for its
existing `click.echo` + `sys.exit(1)` behavior, and on success calls
`write_report(...)` as before. Its observable behavior and existing tests
do not change.

### 5.2 `src/chesswhisper/web/app.py`

A FastAPI app with two routes:

- `GET /` — renders `templates/index.html` (the upload form).
- `POST /analyze` — accepts the multipart form (`pgn` file, `player`,
  `lang`, `llm` fields), saves the upload to a `tempfile.NamedTemporaryFile`
  (cleaned up in a `finally`), calls `run_analysis(...)`, converts the
  result via `render_markdown(...)` (from `report_writer`) and the
  `markdown` library into HTML, and renders `templates/result.html` with
  the report HTML and any warnings.

A small exception-to-HTTP mapping in the route:
- `FileNotFoundError` / `ValueError` → HTTP 400, renders `templates/error.html`
  with the exception's message.
- `StockfishProvisionError` → HTTP 500, same error template (this is an
  environment problem, not a user input problem).
- Any other unhandled exception → a generic FastAPI exception handler
  registered on the app renders `templates/error.html` with a generic
  "something went wrong" message (no raw traceback is ever sent to the
  browser).

### 5.3 Templates

- `templates/base.html` — shared layout with a single inline `<style>`
  block (no external CSS framework), used by the other three templates.
- `templates/index.html` — the upload form (`enctype="multipart/form-data"`),
  a submit button, and a small inline script that shows a simple
  "Analyzing… this can take a few minutes" message and disables the
  button on submit (no real progress percentage — just prevents a
  confused double-submit while the synchronous request is in flight).
- `templates/result.html` — a warnings banner (if any) above the
  `{{ report_html | safe }}` block.
- `templates/error.html` — a simple message + a link back to `/`.

### 5.4 `cli.py`: `serve` command

```
chesswhisper serve [--host 127.0.0.1] [--port 8000]
```

Imports `uvicorn` and `chesswhisper.web.app:app` only inside the command
function body (not at module top level), so a base `pip install -e .`
(without the `web` extra) doesn't fail to import `cli.py` at all — it only
fails when `serve` is actually invoked, with a clear error message telling
the user to `pip install -e ".[web]"`.

## 6. Dependencies

New `pyproject.toml` optional-dependency group, kept separate from the
base install and from `dev`:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.14"]
web = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9", "jinja2>=3.1", "markdown>=3.6"]
```

## 7. Error Handling

| Situation | Behavior |
|---|---|
| No `.pgn` file in the form submission | HTTP 400, `error.html`, clear message |
| PGN has malformed games (some valid) | Analysis proceeds on valid games; warnings shown in `result.html` |
| `player` doesn't match any header / ambiguous auto-detect | HTTP 400, `error.html` with the same message `run_analysis` raises |
| Stockfish provisioning fails | HTTP 500, `error.html`, generic engine-setup message |
| LLM call fails | Report still renders (stats-only), a warning banner explains commentary is unavailable |
| Any other unhandled exception | HTTP 500, generic `error.html`, no traceback exposed |
| `web` extra not installed, user runs `serve` | Clear CLI error message naming `pip install -e ".[web]"` |

## 8. Testing Strategy

- Unit tests for `run_analysis` in `tests/test_cli.py` (or a new
  `tests/test_run_analysis.py`), reusing the existing `FakeEngine`/
  `_build_llm_provider` monkeypatch patterns already established for the
  CLI's tests — verifies it returns the right tuple shape and raises the
  right exceptions, without needing a real Stockfish or LLM call.
- Route tests using `fastapi.testclient.TestClient`: `GET /` returns 200
  with the form; `POST /analyze` with a valid small PGN (mocked engine)
  returns 200 with report content in the HTML body; `POST /analyze` with
  a mismatched player returns 400 with a clear message; `POST /analyze`
  with no file returns 400.
- No test starts a real `uvicorn` server process or hits a real network/
  LLM/Stockfish — same mocking boundary discipline as the existing CLI
  test suite.

## 9. Open Items for Implementation Planning

- Exact Jinja2 template file organization (single dir vs. `web/templates/`
  nested further) — implementation detail, not a design question.

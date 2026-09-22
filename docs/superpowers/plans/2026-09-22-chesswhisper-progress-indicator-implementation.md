# Web Progress Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /analyze` start the Stockfish analysis pipeline in a background thread and redirect to a progress page that polls for real per-game progress, landing on the existing `/history/{id}` page when done, instead of blocking the request for the whole analysis.

**Architecture:** `run_analysis` (shared by the CLI and web UI) gains an optional `on_progress` callback. A new in-memory, thread-safe job registry (`web/jobs.py`) tracks each analysis's status. `POST /analyze` creates a job, starts a background thread running the existing pipeline, and redirects immediately; a new progress page polls a JSON status endpoint until the job is done or errors.

**Tech Stack:** Python 3.11, stdlib `threading` (no new dependencies), FastAPI + Jinja2, `pytest` / `pytest-mock`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-09-22-chesswhisper-progress-indicator-design.md`

## Global Constraints

- No new dependencies (no Redis, Celery, task queue). The job registry is a plain `dict` guarded by `threading.Lock`.
- `run_analysis`'s new `on_progress` parameter is optional and defaults to `None`; the CLI never passes it, so CLI behavior and its existing tests are unchanged.
- `POST /analyze` keeps every synchronous validation check it has today (source, username/max_games, missing-API-key redirect, `fetch_pgn` call) in the same order, before any job is created.
- Only a job's owning user (`job.user_id == current_user.id`) may view its progress page or status; otherwise 403 (`Forbidden`). An unknown `job_id` is 404.
- Error messages shown on the progress page are the same text the old synchronous path used (`str(exc)` for `OSError`/`ValueError`; `f"Motor kurulum hatası: {exc}"` for `StockfishProvisionError`) — never a raw traceback.
- On success, the browser lands on `/history/{record_id}?job={job_id}`; that query param is how the finished job's `warnings` reach the page (via a one-time lookup in the job registry, not a new DB column).
- Job registry entries older than one hour are pruned on each `create_job` call.
- Tests must be deterministic: no real background threads. `web/app.py` starts background work through one seam, `_start_background`, which tests monkeypatch to run synchronously.
- Code style: `ruff` (line length 110, rules E,F,I,UP,B) and `mypy` (`check_untyped_defs`) must pass; `pytest` must stay green, including every pre-existing test in the whole suite.
- Commit messages: plain `type: subject` lines, no `Co-Authored-By` trailer and no AI/tool attribution of any kind (the repo owner's standing rule for every commit).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/chesswhisper/cli.py` (modify) | `run_analysis` gains `on_progress`; CLI command unaffected. |
| `tests/test_cli.py` (modify) | New test for `on_progress`. |
| `src/chesswhisper/web/jobs.py` (create) | In-memory, thread-safe job registry: create/get/update/mark-done/mark-error. |
| `tests/test_web_jobs.py` (create) | Unit tests for `jobs.py`. |
| `src/chesswhisper/web/app.py` (modify) | `POST /analyze` starts a background job and redirects; new `GET /analyze/progress/{id}` and `GET /analyze/status/{id}` routes; `history_detail` reads an optional `?job=` param. |
| `src/chesswhisper/web/templates/progress.html` (create) | Progress bar page; polls the status endpoint. |
| `src/chesswhisper/web/templates/history_detail.html` (modify) | Gains the same warnings banner `result.html` already has. |
| `src/chesswhisper/web/templates/base.html` (modify) | Progress-bar CSS, reusing existing color tokens. |
| `tests/conftest.py` (modify) | Autouse fixture that runs background jobs inline, scoped so CLI-only test runs never import the web app. |
| `tests/test_web_progress.py` (create) | Tests for the new job-based `/analyze` flow and the new routes. |
| `tests/test_web_app.py` (modify) | Two tests updated to the new redirect-then-poll contract; fixture gains `follow_redirects=False`. |
| `tests/test_web_analyze_authenticated.py` (modify) | Three tests updated to the new contract. |
| `tests/test_web_fetch.py` (modify) | Two tests updated to the new contract (two others need no change — see Task 4). |
| `README.md` (modify) | One sentence noting the progress page. |

---

### Task 1: `run_analysis` gains `on_progress`

**Files:**
- Modify: `src/chesswhisper/cli.py:60-128` (the `run_analysis` function)
- Modify: `tests/test_cli.py` (append one test)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 3): `run_analysis(..., on_progress: Callable[[int, int], None] | None = None)`. When given, `on_progress(processed, total)` is called once per game in the PGN (both skipped-for-player and analyzed games), with `processed` a 1-based counter and `total = len(load_result.games)` fixed before the loop starts.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`, directly after the existing `test_analyze_no_games_feature_player_fails_cleanly` function (which ends with `assert not output_path.exists()`):

```python
def test_run_analysis_calls_on_progress_after_each_game(tmp_path, monkeypatch):
    pgn_path = tmp_path / "games.pgn"
    pgn_path.write_text(MULTI_GAME_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    progress_calls = []
    run_analysis(
        str(pgn_path), "Alice", "en", "none",
        on_progress=lambda completed, total: progress_calls.append((completed, total)),
    )

    # MULTI_GAME_PGN has two games (Alice-Bob, Carol-Dave); with player="Alice"
    # the first is analyzed and the second is skipped, but on_progress fires
    # for both, in order, each reporting the fixed total of 2.
    assert progress_calls == [(1, 2), (2, 2)]


def test_run_analysis_without_on_progress_is_unaffected(tmp_path, monkeypatch):
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(SAMPLE_PGN, encoding="utf-8")

    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    aggregate, commentary, warnings = run_analysis(str(pgn_path), "Alice", "en", "none")

    assert aggregate.player == "Alice"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -k "on_progress" -v`
Expected: `test_run_analysis_calls_on_progress_after_each_game` FAILS with `TypeError: run_analysis() got an unexpected keyword argument 'on_progress'`. `test_run_analysis_without_on_progress_is_unaffected` PASSES already (it doesn't use the new parameter) — that's fine, it's here as a safety net for Step 4.

- [ ] **Step 3: Implement `on_progress`**

In `src/chesswhisper/cli.py`, add `from collections.abc import Callable` to the stdlib import group at the top of the file (exact position doesn't matter — `ruff check --fix` will correct it in Step 4 regardless):

```python
import os
import sys
import tempfile
from collections.abc import Callable
```

Replace the `run_analysis` function signature and its docstring:

```python
def run_analysis(
    pgn_path,
    player,
    lang,
    llm_name,
    movetime=300,
    depth=None,
    engine_path=None,
    llm_api_key=None,
    on_progress: Callable[[int, int], None] | None = None,
):
    """Runs the full analysis pipeline and returns (aggregate_report, commentary, warnings).

    Writes no output file. Raises FileNotFoundError/OSError, ValueError, or
    StockfishProvisionError on unrecoverable input/engine problems -- callers
    (the CLI, the web UI) decide how to present these. LLM failures are caught
    internally and folded into the returned `warnings` list with an empty
    `commentary` instead of raising.

    llm_api_key, if given, is passed straight to the chosen LLM provider
    instead of letting it fall back to reading from the environment --
    used by the web UI to supply a specific logged-in user's own key.

    on_progress, if given, is called once per game in the PGN -- whether
    that game is analyzed or skipped for not featuring the tracked player --
    as on_progress(games_processed, total_games), with total_games fixed
    before the loop starts. The CLI does not pass this; the web UI uses it
    to report progress on a background job.
    """
```

Replace the loop body (from `skipped_for_player = 0` through the `finally` block):

```python
    total_games = len(load_result.games)
    skipped_for_player = 0
    try:
        game_reports = []
        for processed, loaded in enumerate(load_result.games, start=1):
            white = loaded.headers.get("White", "")
            black = loaded.headers.get("Black", "")
            if player not in (white, black):
                skipped_for_player += 1
                if on_progress is not None:
                    on_progress(processed, total_games)
                continue
            evals = analyze_game(loaded.game, engine, movetime_ms=movetime, depth=depth)
            game_reports.append(build_game_report(loaded, evals, player, eco_table))
            if on_progress is not None:
                on_progress(processed, total_games)
    finally:
        if hasattr(engine, "quit"):
            engine.quit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: all PASS, including every pre-existing `test_cli.py` test (the parameter is additive and optional).

Run: `ruff check src tests && mypy src`
Expected: no errors. (If `ruff` flags import ordering, run `ruff check --fix src/chesswhisper/cli.py` and re-run.)

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/cli.py tests/test_cli.py
git commit -m "feat: add an on_progress callback to run_analysis"
```

---

### Task 2: `web/jobs.py` — in-memory job registry

**Files:**
- Create: `src/chesswhisper/web/jobs.py`
- Create: `tests/test_web_jobs.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces (used by Task 3): `Job` dataclass with fields `id: str`, `user_id: int`, `status: str` (`"running"`/`"done"`/`"error"`), `completed: int`, `total: int`, `record_id: int | None`, `warnings: list[str]`, `error_message: str | None`, `created_at: float`. Functions `create_job(user_id: int) -> Job`, `get_job(job_id: str) -> Job | None`, `update_progress(job_id: str, completed: int, total: int) -> None`, `mark_done(job_id: str, record_id: int, warnings: list[str]) -> None`, `mark_error(job_id: str, message: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_jobs.py`:

```python
from chesswhisper.web import jobs


def test_create_job_starts_running_with_zero_progress():
    job = jobs.create_job(user_id=1)

    assert job.status == "running"
    assert job.completed == 0
    assert job.total == 0
    assert job.user_id == 1
    assert job.record_id is None
    assert job.warnings == []
    assert job.error_message is None
    assert jobs.get_job(job.id) is job


def test_get_job_returns_none_for_unknown_id():
    assert jobs.get_job("does-not-exist") is None


def test_two_created_jobs_have_different_ids():
    first = jobs.create_job(user_id=1)
    second = jobs.create_job(user_id=1)

    assert first.id != second.id


def test_update_progress_sets_completed_and_total():
    job = jobs.create_job(user_id=1)

    jobs.update_progress(job.id, 3, 10)

    updated = jobs.get_job(job.id)
    assert updated.completed == 3
    assert updated.total == 10
    assert updated.status == "running"


def test_update_progress_on_unknown_id_does_not_raise():
    jobs.update_progress("does-not-exist", 1, 2)


def test_mark_done_sets_status_record_id_and_warnings():
    job = jobs.create_job(user_id=1)

    jobs.mark_done(job.id, record_id=42, warnings=["only 1 game(s) found."])

    updated = jobs.get_job(job.id)
    assert updated.status == "done"
    assert updated.record_id == 42
    assert updated.warnings == ["only 1 game(s) found."]


def test_mark_done_on_unknown_id_does_not_raise():
    jobs.mark_done("does-not-exist", record_id=1, warnings=[])


def test_mark_error_sets_status_and_message():
    job = jobs.create_job(user_id=1)

    jobs.mark_error(job.id, "Motor kurulum hatası: boom")

    updated = jobs.get_job(job.id)
    assert updated.status == "error"
    assert updated.error_message == "Motor kurulum hatası: boom"


def test_mark_error_on_unknown_id_does_not_raise():
    jobs.mark_error("does-not-exist", "boom")


def test_create_job_prunes_entries_older_than_one_hour(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr("chesswhisper.web.jobs.time.monotonic", lambda: fake_now[0])

    old_job = jobs.create_job(user_id=1)
    fake_now[0] += jobs._JOB_TTL_SECONDS + 1

    new_job = jobs.create_job(user_id=2)

    assert jobs.get_job(old_job.id) is None
    assert jobs.get_job(new_job.id) is new_job


def test_create_job_does_not_prune_entries_within_the_ttl(monkeypatch):
    fake_now = [2000.0]
    monkeypatch.setattr("chesswhisper.web.jobs.time.monotonic", lambda: fake_now[0])

    job = jobs.create_job(user_id=1)
    fake_now[0] += jobs._JOB_TTL_SECONDS - 1

    jobs.create_job(user_id=2)

    assert jobs.get_job(job.id) is job
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_jobs.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'chesswhisper.web.jobs'`.

- [ ] **Step 3: Implement `jobs.py`**

Create `src/chesswhisper/web/jobs.py`:

```python
"""In-memory registry of the web UI's background analysis jobs.

Job state does not survive a server restart -- this mirrors the rest of
the app's local, single-process design (no migrations tooling, no
public-internet hardening). Every function here takes the module-level
lock, so callers never need to know the registry is shared across the
request thread and whichever thread is running the analysis.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

_JOB_TTL_SECONDS = 60 * 60  # one hour


@dataclass
class Job:
    id: str
    user_id: int
    status: str = "running"  # "running" | "done" | "error"
    completed: int = 0
    total: int = 0
    record_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    error_message: str | None = None
    created_at: float = field(default_factory=time.monotonic)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _prune_expired(now: float) -> None:
    expired = [job_id for job_id, job in _jobs.items() if now - job.created_at > _JOB_TTL_SECONDS]
    for job_id in expired:
        del _jobs[job_id]


def create_job(user_id: int) -> Job:
    job = Job(id=str(uuid.uuid4()), user_id=user_id)
    with _lock:
        _prune_expired(time.monotonic())
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def update_progress(job_id: str, completed: int, total: int) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.completed = completed
            job.total = total


def mark_done(job_id: str, record_id: int, warnings: list[str]) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.status = "done"
            job.record_id = record_id
            job.warnings = warnings


def mark_error(job_id: str, message: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.status = "error"
            job.error_message = message
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_web_jobs.py -v`
Expected: all PASS.

Run: `ruff check src tests && mypy src`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/web/jobs.py tests/test_web_jobs.py
git commit -m "feat: add an in-memory job registry for background analysis"
```

---

### Task 3: Background analysis job + progress routes

**Files:**
- Modify: `src/chesswhisper/web/app.py` (imports; the `analyze` handler at lines 308-406; the `history_detail` handler at lines 265-280; two new routes)
- Create: `src/chesswhisper/web/templates/progress.html`
- Modify: `src/chesswhisper/web/templates/history_detail.html`
- Modify: `src/chesswhisper/web/templates/base.html` (progress-bar CSS)
- Modify: `tests/conftest.py` (autouse fixture)
- Create: `tests/test_web_progress.py`
- Modify: `README.md`

**Interfaces:**
- Consumes (from Tasks 1-2): `run_analysis(..., on_progress=...)`; `jobs.create_job`, `jobs.get_job`, `jobs.update_progress`, `jobs.mark_done`, `jobs.mark_error`, `jobs.Job`.
- Produces (used by Task 4): `POST /analyze` now returns `RedirectResponse("/analyze/progress/{job_id}", status_code=303)` on every success/validation-passed path (never a direct 200/400/500 report). `GET /analyze/status/{job_id}` returns JSON `{"status": ..., "completed": ..., "total": ...}` plus `"redirect"` (done) or `"message"` (error). `chesswhisper.web.app._start_background(target)` is the seam tests monkeypatch.

- [ ] **Step 1: Write the failing tests**

First, modify `tests/conftest.py` (full replacement — the file is currently 37 lines):

```python
import re
import sys

import pytest
from fastapi.testclient import TestClient

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]*)"')

# Pages that always render a csrf_token hidden field, tried in order until
# one doesn't 30x (anonymous sessions redirect off "/", logged-in sessions
# redirect off "/login").
_TOKEN_SOURCE_PATHS = ("/login", "/", "/settings")


class CsrfClient(TestClient):
    """A TestClient that transparently attaches a valid csrf_token to POSTs.

    Every state-changing route requires a csrf_token form field matching the
    one chesswhisper stashes in the session. Tests exercise those routes
    directly without ever rendering the page that would normally carry the
    token, so this client fetches one over a plain GET and merges it into
    the POST body unless the test already supplied its own.
    """

    def post(self, url, *args, **kwargs):
        data = dict(kwargs.get("data") or {})
        if "csrf_token" not in data:
            data["csrf_token"] = self._fetch_csrf_token()
        kwargs["data"] = data
        return super().post(url, *args, **kwargs)

    def _fetch_csrf_token(self) -> str:
        for path in _TOKEN_SOURCE_PATHS:
            response = super().get(path)
            match = _CSRF_RE.search(response.text)
            if match:
                return match.group(1)
        raise AssertionError("could not obtain a csrf_token from any known page")


@pytest.fixture(autouse=True)
def _run_background_jobs_inline(monkeypatch):
    """chesswhisper.web.app starts analysis jobs on a background thread;
    tests need deterministic, synchronous execution instead of racing a
    real thread. This patches that one seam -- but only if the module is
    already imported (checked via sys.modules, which never triggers an
    import itself), so a CLI-only test run never has to import or
    initialize the FastAPI app.
    """
    module = sys.modules.get("chesswhisper.web.app")
    if module is not None:
        monkeypatch.setattr(module, "_start_background", lambda target: target())
```

Then create `tests/test_web_progress.py`:

```python
import textwrap

import chess.engine
import pytest
from conftest import CsrfClient

from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.web.app import app
from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import AnalysisRecord

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

    def analyse(self, board, limit, **kwargs):
        cp = self._scores[self._i % len(self._scores)]
        self._i += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)}


def _fake_aggregate_report(warnings=None):
    from chesswhisper.stats_engine import PHASES, AggregateReport, ErrorCounts, TimeManagementStats
    report = AggregateReport(
        player="Alice", games=[], overall_acpl=0.0, overall_accuracy=100.0,
        error_totals={p: ErrorCounts() for p in PHASES}, openings=[],
        time_management=TimeManagementStats(available=False),
    )
    return report, "", (warnings or [])


@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "progress_test.db")
    c = CsrfClient(app, follow_redirects=False)
    c.post("/register", data={"username": "alice", "password": "correct-horse"})
    return c


def _job_id_from_redirect(response):
    return response.headers["location"].rsplit("/", 1)[-1]


def _fail_if_job_created(*args, **kwargs):
    raise AssertionError("a job should not have been created")


def test_analyze_redirects_to_progress_page(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/analyze/progress/")


def test_analyze_job_completes_and_status_redirects_to_history(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["completed"] == body["total"] == 1

    db = next(get_db())
    records = db.query(AnalysisRecord).all()
    assert len(records) == 1
    assert body["redirect"] == f"/history/{records[0].id}?job={job_id}"


def test_history_detail_shows_warnings_only_via_the_job_query_param(client, monkeypatch):
    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, on_progress=None, **kwargs):
        if on_progress is not None:
            on_progress(1, 1)
        return _fake_aggregate_report(warnings=["only 1 game(s) found (requested 7)."])

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)
    status = client.get(f"/analyze/status/{job_id}")
    redirect = status.json()["redirect"]
    record_id = redirect.split("/history/")[1].split("?")[0]

    with_job = client.get(redirect)
    without_job = client.get(f"/history/{record_id}")

    assert with_job.status_code == 200
    assert "only 1 game(s) found" in with_job.text
    assert without_job.status_code == 200
    assert "only 1 game(s) found" not in without_job.text


def test_analyze_engine_failure_marks_the_job_as_an_error(client, monkeypatch):
    def fail_provisioning(**kwargs):
        raise StockfishProvisionError("no engine available")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_provisioning)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "error"
    assert "Motor kurulum hatası" in body["message"]


def test_analyze_unknown_player_marks_the_job_as_an_error(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Nobody", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    body = status.json()
    assert body["status"] == "error"
    assert "Nobody" in body["message"]


def test_analyze_unexpected_exception_marks_the_job_as_an_error_not_stuck_running(client, monkeypatch):
    def fake_run_analysis(*args, **kwargs):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    status = client.get(f"/analyze/status/{job_id}")

    body = status.json()
    assert body["status"] == "error"
    assert body["message"] == "Beklenmeyen bir hata oluştu."


def test_on_progress_updates_the_job_before_it_completes(client, monkeypatch):
    from chesswhisper.web import jobs

    created_jobs = []
    original_create_job = jobs.create_job

    def spy_create_job(user_id):
        job = original_create_job(user_id)
        created_jobs.append(job)
        return job

    monkeypatch.setattr("chesswhisper.web.app.jobs.create_job", spy_create_job)

    captured = {}

    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, on_progress=None, **kwargs):
        if on_progress is not None:
            on_progress(1, 2)
        job = created_jobs[0]
        # Snapshot state right after the callback but before this function
        # returns -- a real concurrent status poll mid-run would see exactly
        # this: progress recorded, but not yet marked done.
        captured["mid_run"] = (job.completed, job.total, job.status)
        return _fake_aggregate_report()

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert captured["mid_run"] == (1, 2, "running")


def test_analyze_missing_api_key_does_not_create_a_job(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.web.app.jobs.create_job", _fail_if_job_created)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings")


def test_analyze_progress_page_requires_login():
    anon = CsrfClient(app, follow_redirects=False)

    response = anon.get("/analyze/progress/some-job-id")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_analyze_progress_page_unknown_job_returns_404(client):
    response = client.get("/analyze/progress/does-not-exist")

    assert response.status_code == 404


def test_analyze_status_unknown_job_returns_404(client):
    response = client.get("/analyze/status/does-not-exist")

    assert response.status_code == 404


def test_analyze_progress_and_status_forbidden_for_another_users_job(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    other = CsrfClient(app, follow_redirects=False)
    other.post("/register", data={"username": "mallory", "password": "correct-horse"})

    progress = other.get(f"/analyze/progress/{job_id}")
    status = other.get(f"/analyze/status/{job_id}")

    assert progress.status_code == 403
    assert status.status_code == 403


def test_analyze_progress_page_renders_for_the_owning_user(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )
    job_id = _job_id_from_redirect(response)

    progress = client.get(f"/analyze/progress/{job_id}")

    assert progress.status_code == 200
    assert job_id in progress.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_web_progress.py -v`
Expected: FAIL. Every test either 404s (no `/analyze/progress/*` or `/analyze/status/*` routes exist yet) or gets a 200 with a report body directly from `POST /analyze` instead of a 303 (the old synchronous behavior is still in place).

- [ ] **Step 3: Implement the routes and templates**

In `src/chesswhisper/web/app.py`, replace the import block (from `import os` through the closing `)` of the `chesswhisper.web.security` import) with:

```python
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import timedelta

import bleach
import markdown as markdown_lib
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from chesswhisper.cli import run_analysis
from chesswhisper.engine_provision import StockfishProvisionError
from chesswhisper.game_sources import (
    DEFAULT_MAX_GAMES,
    MAX_GAMES_LIMIT,
    GameFetchError,
    GameSourceUnavailable,
    fetch_pgn,
)
from chesswhisper.report_writer import render_markdown
from chesswhisper.web import jobs
from chesswhisper.web.auth import (
    Forbidden,
    RedirectToLogin,
    get_csrf_token,
    get_current_user,
    get_optional_current_user,
    require_admin,
    verify_csrf,
)
from chesswhisper.web.db import get_db, utcnow
from chesswhisper.web.models import AnalysisRecord, User, UserApiKey
from chesswhisper.web.security import (
    decrypt_api_key,
    encrypt_api_key,
    get_session_secret,
    hash_password,
    verify_password,
)
```

(If `ruff` later reports import-order issues on this block, run `ruff check --fix src/chesswhisper/web/app.py` and re-verify with `pytest` — auto-fixing import order never changes runtime behavior.)

Add this function directly above the `@app.get("/register", ...)` route (after `_report_to_safe_html`):

```python
def _start_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def _run_analysis_job(
    job_id: str,
    user_id: int,
    tmp_path: str,
    player: str | None,
    lang: str,
    llm: str,
    llm_api_key: str | None,
    fetch_warnings: list[str],
) -> None:
    db = next(get_db())
    try:
        try:
            aggregate, commentary, warnings = run_analysis(
                tmp_path, player, lang, llm,
                llm_api_key=llm_api_key,
                on_progress=lambda completed, total: jobs.update_progress(job_id, completed, total),
            )
        except (OSError, ValueError) as exc:
            jobs.mark_error(job_id, str(exc))
            return
        except StockfishProvisionError as exc:
            jobs.mark_error(job_id, f"Motor kurulum hatası: {exc}")
            return

        report_markdown = render_markdown(aggregate, commentary, lang)
        record = AnalysisRecord(
            user_id=user_id,
            player_name=aggregate.player,
            lang=lang,
            llm_used=llm,
            games_count=len(aggregate.games),
            overall_acpl=aggregate.overall_acpl,
            overall_accuracy=aggregate.overall_accuracy,
            report_markdown=report_markdown,
        )
        db.add(record)
        db.commit()
        jobs.mark_done(job_id, record.id, fetch_warnings + warnings)
    except Exception:
        # Last-resort safety net: without this, an exception type this
        # function doesn't already handle (e.g. a database error on commit)
        # would propagate out of the background thread silently, leaving
        # the job stuck in "running" forever from the client's point of
        # view. Message text matches the existing top-level
        # _unhandled_exception_handler for the same failure class.
        jobs.mark_error(job_id, "Beklenmeyen bir hata oluştu.")
    finally:
        db.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
```

Replace the whole `history_detail` handler (from `@app.get("/history/{record_id}", ...)` through its closing `)`) with:

```python
@app.get("/history/{record_id}", response_class=HTMLResponse)
async def history_detail(
    request: Request,
    record_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    record = db.get(AnalysisRecord, record_id)
    if record is None or record.user_id != current_user.id:
        return _render_error(request, "Kayıt bulunamadı.", 404, current_user)

    warnings: list[str] = []
    job_id = request.query_params.get("job")
    if job_id is not None:
        job = jobs.get_job(job_id)
        if (
            job is not None
            and job.user_id == current_user.id
            and job.status == "done"
            and job.record_id == record_id
        ):
            warnings = job.warnings

    report_html = _report_to_safe_html(record.report_markdown)
    return _render(
        request, "history_detail.html",
        {
            "current_user": current_user, "report_html": report_html,
            "back_url": "/history", "warnings": warnings,
        },
    )
```

Replace the whole `analyze` handler (from `@app.post("/analyze", ...)` through its final `return _render(request, "result.html", ...)`) with:

```python
@app.post("/analyze", response_class=HTMLResponse, response_model=None)
async def analyze(
    request: Request,
    pgn: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI's own idiom
    source: str = Form(default="file"),
    username: str = Form(default=""),
    max_games: str = Form(default=str(DEFAULT_MAX_GAMES)),
    player: str = Form(default=""),
    lang: str = Form(default="tr"),
    llm: str = Form(default="none"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse | RedirectResponse:
    fetching = source in FETCH_SOURCES
    if source != "file" and not fetching:
        return _render_error(request, "Geçersiz oyun kaynağı.", 400, current_user)

    contents = b""
    game_count = DEFAULT_MAX_GAMES
    if fetching:
        username = username.strip()
        if not username:
            return _render_error(request, "Lütfen bir kullanıcı adı girin.", 400, current_user)
        try:
            game_count = int(max_games)
        except ValueError:
            return _render_error(request, "Oyun sayısı geçerli bir tam sayı olmalı.", 400, current_user)
    else:
        if pgn is None or not pgn.filename:
            return _render_error(request, "Lütfen analiz edilecek bir PGN dosyası seçin.", 400, current_user)
        contents = await pgn.read()

    llm_api_key = None
    if llm != "none":
        key_row = db.get(UserApiKey, current_user.id)
        encrypted = None
        if key_row is not None:
            encrypted = key_row.anthropic_key_encrypted if llm == "claude" else key_row.openai_key_encrypted
        if not encrypted:
            return RedirectResponse("/settings?message=missing_key", status_code=303)
        llm_api_key = decrypt_api_key(encrypted)

    fetch_warnings: list[str] = []
    if fetching:
        try:
            fetched = await run_in_threadpool(fetch_pgn, source, username, game_count)
        except GameSourceUnavailable as exc:
            return _render_error(request, f"Oyunlar alınamadı: {exc}", 502, current_user)
        except GameFetchError as exc:
            return _render_error(request, str(exc), 400, current_user)
        contents = fetched.pgn_text.encode("utf-8")
        fetch_warnings = fetched.warnings
        player = player or fetched.player

    with tempfile.NamedTemporaryFile(suffix=".pgn", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    job = jobs.create_job(current_user.id)
    _start_background(
        lambda: _run_analysis_job(
            job.id, current_user.id, tmp_path, player or None, lang, llm, llm_api_key, fetch_warnings
        )
    )
    return RedirectResponse(f"/analyze/progress/{job.id}", status_code=303)


@app.get("/analyze/progress/{job_id}", response_class=HTMLResponse, response_model=None)
async def analyze_progress(
    request: Request,
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    job = jobs.get_job(job_id)
    if job is None:
        return _render_error(request, "Analiz bulunamadı.", 404, current_user)
    if job.user_id != current_user.id:
        raise Forbidden()
    return _render(request, "progress.html", {"current_user": current_user, "job_id": job_id})


@app.get("/analyze/status/{job_id}", response_model=None)
async def analyze_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    job = jobs.get_job(job_id)
    if job is None:
        return JSONResponse({"status": "error", "message": "Analiz bulunamadı."}, status_code=404)
    if job.user_id != current_user.id:
        return JSONResponse({"status": "error", "message": "Yetkisiz."}, status_code=403)

    body: dict[str, object] = {"status": job.status, "completed": job.completed, "total": job.total}
    if job.status == "done":
        body["redirect"] = f"/history/{job.record_id}?job={job.id}"
    elif job.status == "error":
        body["message"] = job.error_message
    return JSONResponse(body)
```

Create `src/chesswhisper/web/templates/progress.html`:

```html
{% extends "base.html" %}
{% block title %}chesswhisper — Analiz sürüyor{% endblock %}
{% block content %}
<div class="card">
  <p id="progress-text">Analiz başlıyor…</p>
  <div class="progress-track">
    <div class="progress-fill" id="progress-fill" style="width: 0%;"></div>
  </div>
  <div class="banner error" id="error-banner" style="display: none;">
    <strong>Hata:</strong> <span id="error-message"></span>
    <p><a class="back-link" href="/">&larr; Yeni analiz</a></p>
  </div>
</div>
<script>
  var jobId = "{{ job_id }}";

  function poll() {
    fetch("/analyze/status/" + jobId)
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.status === "running") {
          document.getElementById("progress-text").textContent =
            data.total > 0 ? data.completed + " / " + data.total + " oyun analiz edildi" : "Analiz başlıyor…";
          var pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;
          document.getElementById("progress-fill").style.width = pct + "%";
          setTimeout(poll, 1000);
        } else if (data.status === "done") {
          window.location = data.redirect;
        } else if (data.status === "error") {
          document.getElementById("progress-text").style.display = "none";
          document.getElementById("error-banner").style.display = "block";
          document.getElementById("error-message").textContent = data.message;
        }
      })
      .catch(function () {
        setTimeout(poll, 1000);
      });
  }
  poll();
</script>
{% endblock %}
```

In `src/chesswhisper/web/templates/history_detail.html`, replace the whole file:

```html
{% extends "base.html" %}
{% block title %}chesswhisper — Rapor{% endblock %}
{% block content %}
{% if warnings %}
<div class="banner warning">
  <strong>Uyarılar:</strong>
  <ul>
    {% for w in warnings %}<li>{{ w }}</li>{% endfor %}
  </ul>
</div>
{% endif %}
<div class="card report">
  {{ report_html | safe }}
</div>
<a class="back-link" href="{{ back_url }}">&larr; Geçmişe dön</a>
{% endblock %}
```

In `src/chesswhisper/web/templates/base.html`, add this CSS block directly after the existing `.status-line { ... }` rule (which ends `display: none; }`):

```css
  .progress-track {
    margin-top: 16px;
    height: 10px;
    border-radius: 6px;
    background: var(--border);
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s ease;
  }
```

In `README.md`, insert this sentence directly after the paragraph ending "...delete their own past records." (in the **Web UI** section):

```markdown
Submitting the analyze form takes you to a progress page showing how many
of your games have been analyzed so far, then lands you on the finished
report once it's done — the page is safe to leave and revisit from
**History** if you navigate away.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_web_progress.py -v`
Expected: all PASS.

Run: `pytest -v`
Expected: `test_web_progress.py` and `test_web_jobs.py` pass; the pre-existing tests that assert directly on `POST /analyze`'s old synchronous 200/400/500 response now FAIL (expected — Task 4 fixes them) or, in files where the test only checks response.status_code for error paths that fail *before* job creation (e.g. missing-PGN, unknown-source, missing-API-key), still PASS. Do not attempt to fix those in this task.

Run: `ruff check src tests && mypy src`
Expected: no errors (aside from the `history_detail.html`/`progress.html` files, which aren't linted).

- [ ] **Step 5: Commit**

```bash
git add src/chesswhisper/web/app.py src/chesswhisper/web/templates/progress.html \
        src/chesswhisper/web/templates/history_detail.html src/chesswhisper/web/templates/base.html \
        tests/conftest.py tests/test_web_progress.py README.md
git commit -m "feat: run web analysis in a background job with a progress page"
```

---

### Task 4: Update pre-existing tests to the new contract

**Files:**
- Modify: `tests/test_web_app.py`
- Modify: `tests/test_web_analyze_authenticated.py`
- Modify: `tests/test_web_fetch.py`

**Interfaces:**
- Consumes (from Task 3): the new `303` + `/analyze/progress/{id}` + `/analyze/status/{id}` contract; the autouse `_run_background_jobs_inline` fixture (already active, no per-test changes needed for it).
- Produces: nothing new; this task only brings existing tests back to green.

- [ ] **Step 1: Update `tests/test_web_app.py`**

Replace the `client` fixture (to add `follow_redirects=False`, matching the other web test files — without it, this client auto-follows the new 303 and none of the rewritten assertions below would see the redirect):

```python
@pytest.fixture
def client(tmp_path):
    init_db(tmp_path / "web_app_test.db")
    return CsrfClient(app, raise_server_exceptions=False, follow_redirects=False)
```

Replace `test_analyze_sanitizes_html_injected_via_pgn_headers`:

```python
def test_analyze_sanitizes_html_injected_via_pgn_headers(client, monkeypatch):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    hostile_pgn = textwrap.dedent("""
        [Event "Test"]
        [White "<script>alert(1)</script>"]
        [Black "Bob"]
        [Result "1-0"]

        1. e4 e5 2. Nf3 Nc6 3. Bb5 1-0
    """).strip() + "\n"

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", hostile_pgn, "application/octet-stream")},
        data={"player": "<script>alert(1)</script>", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    status = client.get(f"/analyze/status/{job_id}")
    report = client.get(status.json()["redirect"])

    assert report.status_code == 200
    assert "<script>" not in report.text
```

Replace `test_analyze_engine_provisioning_failure_returns_500`:

```python
def test_analyze_engine_provisioning_failure_marks_the_job_as_an_error(client, monkeypatch):
    client.post("/register", data={"username": "alice", "password": "correct-horse"})
    from chesswhisper.engine_provision import StockfishProvisionError

    def fail_provisioning(**kwargs):
        raise StockfishProvisionError("no engine available")

    monkeypatch.setattr("chesswhisper.cli._open_engine", fail_provisioning)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    status = client.get(f"/analyze/status/{job_id}")

    assert status.json()["status"] == "error"
    assert "Motor kurulum hatası" in status.json()["message"]
```

`test_analyze_without_file_returns_400` is unchanged — leave it exactly as it is (it still fails before any job is created).

- [ ] **Step 2: Update `tests/test_web_analyze_authenticated.py`**

Replace `test_analyze_saves_history_record_for_current_user`:

```python
def test_analyze_saves_history_record_for_current_user(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    status = client.get(f"/analyze/status/{job_id}")
    assert status.json()["status"] == "done"

    db = next(get_db())
    records = db.query(AnalysisRecord).all()
    assert len(records) == 1
    assert records[0].player_name == "Alice"
    assert records[0].games_count == 1

    report = client.get(status.json()["redirect"])
    assert report.status_code == 200
    assert "chesswhisper Analysis Report" in report.text
```

Replace `test_analyze_uses_current_users_decrypted_key`:

```python
def test_analyze_uses_current_users_decrypted_key(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    db = next(get_db())
    from chesswhisper.web.models import User
    user = db.query(User).filter_by(username="alice").one()
    db.add(UserApiKey(user_id=user.id, anthropic_key_encrypted=encrypt_api_key("sk-alices-key")))
    db.commit()

    captured = {}

    def fake_run_analysis(pgn_path, player, lang, llm_name, llm_api_key=None, on_progress=None, **kwargs):
        captured["llm_api_key"] = llm_api_key
        from chesswhisper.stats_engine import PHASES, AggregateReport, ErrorCounts, TimeManagementStats
        return (
            AggregateReport(
                player="Alice", games=[], overall_acpl=0.0, overall_accuracy=100.0,
                error_totals={p: ErrorCounts() for p in PHASES}, openings=[],
                time_management=TimeManagementStats(available=False),
            ),
            "",
            [],
        )

    monkeypatch.setattr("chesswhisper.web.app.run_analysis", fake_run_analysis)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Alice", "lang": "en", "llm": "claude"},
    )

    assert response.status_code == 303
    assert captured["llm_api_key"] == "sk-alices-key"
```

Replace `test_analyze_unknown_player_returns_400`:

```python
def test_analyze_unknown_player_marks_the_job_as_an_error(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"player": "Nobody", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    status = client.get(f"/analyze/status/{job_id}")

    assert status.json()["status"] == "error"
    assert "Nobody" in status.json()["message"]
```

`test_index_redirects_when_logged_out`, `test_index_shows_form_when_logged_in`, and `test_analyze_without_configured_key_redirects_to_settings` are unchanged.

- [ ] **Step 3: Update `tests/test_web_fetch.py`**

Replace `test_analyze_fetches_lichess_games_and_saves_history`:

```python
def test_analyze_fetches_lichess_games_and_saves_history(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    calls = {}

    def fake_fetch(platform, username, max_games):
        calls["args"] = (platform, username, max_games)
        return FetchResult(pgn_text=SAMPLE_PGN, player="Alice", warnings=["only 1 game(s) found (requested 7)."])

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    response = client.post(
        "/analyze",
        data={"source": "lichess", "username": " alice ", "max_games": "7", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    assert calls["args"] == ("lichess", "alice", 7)
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    status = client.get(f"/analyze/status/{job_id}")
    assert status.json()["status"] == "done"

    records = next(get_db()).query(AnalysisRecord).all()
    assert len(records) == 1
    assert records[0].player_name == "Alice"

    report = client.get(status.json()["redirect"])
    assert report.status_code == 200
    assert "chesswhisper Analysis Report" in report.text
    assert "only 1 game(s) found" in report.text
```

Replace `test_analyze_file_upload_still_works_when_source_is_file`:

```python
def test_analyze_file_upload_still_works_when_source_is_file(client, monkeypatch):
    monkeypatch.setattr("chesswhisper.cli._open_engine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fail_if_called)

    response = client.post(
        "/analyze",
        files={"pgn": ("game.pgn", SAMPLE_PGN, "application/octet-stream")},
        data={"source": "file", "player": "Alice", "lang": "en", "llm": "none"},
    )

    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    status = client.get(f"/analyze/status/{job_id}")
    report = client.get(status.json()["redirect"])

    assert report.status_code == 200
    assert "chesswhisper Analysis Report" in report.text
```

Append one new test, directly after `test_analyze_source_unavailable_returns_502`, confirming a fetch failure never creates a job (the spec explicitly calls for this, mirroring the missing-API-key spy test in Task 3):

```python
def test_analyze_fetch_error_does_not_create_a_job(client, monkeypatch):
    def fake_fetch(platform, username, max_games):
        raise GameFetchError("Lichess user 'alice' not found.")

    monkeypatch.setattr("chesswhisper.web.app.fetch_pgn", fake_fetch)

    def fail_if_job_created(*args, **kwargs):
        raise AssertionError("a job should not have been created")

    monkeypatch.setattr("chesswhisper.web.app.jobs.create_job", fail_if_job_created)

    response = client.post("/analyze", data={"source": "lichess", "username": "alice", "max_games": "5", "llm": "none"})

    assert response.status_code == 400
```

Every other test in this file is unchanged:
- `test_index_offers_game_source_choices`, `test_index_max_games_field_matches_the_shared_constants` — don't touch `/analyze` at all.
- `test_analyze_fetch_defaults_player_to_the_fetched_spelling`, `test_analyze_fetch_form_player_overrides_the_fetched_spelling` — their `fake_run_analysis(pgn_path, player, *args, **kwargs)` already accepts the new `on_progress` kwarg via `**kwargs`, and they never assert on the `POST /analyze` response itself, only on `captured["player"]` — nothing to change.
- `test_analyze_fetch_requires_a_username`, `test_analyze_fetch_rejects_a_non_numeric_game_count`, `test_analyze_rejects_an_unknown_source`, `test_analyze_fetch_error_returns_400_with_the_message`, `test_analyze_source_unavailable_returns_502`, `test_analyze_fetch_with_missing_api_key_redirects_before_fetching` — all fail before a job is ever created; their assertions (400/502/303-to-/settings) are unaffected by this feature.

- [ ] **Step 4: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS — the full suite, including `test_web_progress.py`, `test_web_jobs.py`, `test_cli.py`, and every file touched in this task.

Run: `ruff check src tests && mypy src`
Expected: no errors.

- [ ] **Step 5: Manual smoke check (optional, needs a real or cached Stockfish binary)**

Run:
```bash
chesswhisper serve --host 127.0.0.1 --port 8000
```
In a browser: register, upload a multi-game PGN (or use a Lichess/chess.com username), submit, and confirm the progress page shows an increasing "X / Y oyun analiz edildi" count before landing on the finished report at `/history/{id}`. Stop the server afterward (Ctrl+C); this step produces no commit.

- [ ] **Step 6: Commit**

```bash
git add tests/test_web_app.py tests/test_web_analyze_authenticated.py tests/test_web_fetch.py
git commit -m "test: update web analyze tests for the background-job progress flow"
```

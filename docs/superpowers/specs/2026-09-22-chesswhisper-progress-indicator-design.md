# chesswhisper Web Progress Indicator — Design Spec

Date: 2026-09-22
Status: Draft (pending user review)

## 1. Overview

`POST /analyze` currently runs the whole analysis pipeline synchronously
inside the request: the browser sends the form, the server blocks until
every game is analyzed by Stockfish, and only then responds with the
report HTML. For a handful of games this is a few seconds; for the tens of
games the new Lichess/chess.com fetch feature makes easy to request, it
can be minutes with no feedback beyond a static "analyzing..." line.

This feature makes `POST /analyze` start the analysis in the background
and redirect immediately to a progress page that polls for status, then
lands the user on the existing history-detail page once the analysis is
saved. It explicitly supersedes the original web UI spec's v1 non-goal
("No background job queue / polling / progress bar with real
percentage").

## 2. Goals

- `POST /analyze` (both the file-upload and the Lichess/chess.com-fetch
  paths) validates input synchronously exactly as today (missing PGN,
  missing username, missing API key -> same redirects/error pages as
  now), then starts the pipeline in a background thread and redirects to
  a new progress page instead of blocking for the whole analysis.
- The progress page (`GET /analyze/progress/{job_id}`) shows real,
  per-game progress ("7 / 23 oyun analiz edildi") by polling a JSON status
  endpoint (`GET /analyze/status/{job_id}`) roughly once a second.
- On completion, the browser is sent to the existing `/history/{id}` page
  — no new "result" view is built; the finished `AnalysisRecord` is
  already what that page renders.
- On failure, the same error message the synchronous path would have
  shown is displayed on the progress page itself (no page navigation),
  with a link back to start a new analysis.
- Only the job's owning user can view its progress or status.
- No new runtime dependencies (no Redis, Celery, or task queue). Progress
  state lives in an in-memory, thread-safe registry inside the running
  server process — consistent with this app's existing "local,
  single-process" design (SQLite, no migrations tooling, no
  public-internet hardening).

## 3. Non-goals (v1)

- No job persistence across a server restart — an in-flight job is lost
  if the process restarts, same as any other in-memory server state here.
- No cancel button — a started analysis runs to completion or failure.
- No progress for the fetch step itself (fetching Lichess/chess.com games
  is comparatively fast and already runs off the event loop via
  `run_in_threadpool`); progress only covers the per-game Stockfish
  analysis loop.
- No change to the CLI (`chesswhisper analyze`) — it stays synchronous
  with its existing stdout warnings; `on_progress` is an optional
  parameter CLI callers simply don't pass.
- No multi-server / multi-process deployment support for the job
  registry (out of scope for this local-only tool, same as the rest of
  the app).

## 4. Architecture

```
POST /analyze (validate as today; fetch games if needed)
   |
   v
create Job(user_id) in the in-memory registry
   |
   v
start background thread: run_analysis(..., on_progress=job.update)
   |                                  |
   v                                  v
303 -> GET /analyze/progress/{id}     thread: on success, save
   |                                  AnalysisRecord + job.done(record_id);
   v                                  on failure, job.fail(message)
polls GET /analyze/status/{id}
every ~1s (JS) until status != "running"
   |
   v
"done"  -> redirect browser to /history/{record_id}
"error" -> show error_message in place, no navigation
```

`run_analysis` (in `cli.py`, shared by the CLI and the web UI) gains one
new optional parameter, `on_progress`, called after each game in its main
loop — whether that game was analyzed or skipped for not featuring the
tracked player — with `(games_processed, total_games)`. This is a
narrower, cheaper signal than "moves analyzed" but needs no restructuring
of the existing loop, and matches what a user watching the page actually
cares about ("how many of my games are done").

## 5. Components

### 5.1 `run_analysis` (`cli.py`)

```python
def run_analysis(
    pgn_path, player, lang, llm_name,
    movetime=300, depth=None, engine_path=None, llm_api_key=None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[AggregateReport, str, list[str]]
```

Inside the existing `for loaded in load_result.games:` loop, after each
iteration (both the `continue`-for-wrong-player branch and the
analyzed branch), call `on_progress(processed, total)` if `on_progress` is
not `None`, where `total = len(load_result.games)` computed once before
the loop and `processed` is a 1-based counter incremented every
iteration. The CLI command passes nothing (default `None`); behavior and
its existing tests are unaffected.

### 5.2 `web/jobs.py` (new)

```python
@dataclass
class Job:
    id: str
    user_id: int
    status: str  # "running" | "done" | "error"
    completed: int = 0
    total: int = 0
    record_id: int | None = None      # set when status == "done"
    warnings: list[str] = field(default_factory=list)  # set when status == "done"
    error_message: str | None = None  # set when status == "error"
    created_at: float = field(default_factory=time.monotonic)

def create_job(user_id: int) -> Job
def get_job(job_id: str) -> Job | None
def update_progress(job_id: str, completed: int, total: int) -> None
def mark_done(job_id: str, record_id: int, warnings: list[str]) -> None
def mark_error(job_id: str, message: str) -> None
```

`warnings` closes a gap the naive redirect-to-history design would
otherwise create: today, `result.html` shows a warnings banner (e.g. "LLM
commentary unavailable", "only 3 game(s) found") immediately after
analysis. `history_detail.html` has no such banner and `AnalysisRecord`
has no warnings column, so landing straight on `/history/{id}` would
silently drop them. Instead, `mark_done` stores the finished warnings on
the job (which already lives in the registry for up to an hour), and
`/history/{record_id}` is reached via a one-time `?job={job_id}` query
parameter — see §5.3.

A module-level `dict[str, Job]` guarded by a single `threading.Lock`
backs all five functions — every read and write takes the lock, so no
caller needs to know about it. `create_job` also prunes entries older
than one hour (via `created_at`) so long-running server processes don't
accumulate unbounded state; this is a plain size-bounding sweep, not a
scheduled task. `error_message` is always a chesswhisper-authored string
(from the same exception types the synchronous path already catches),
never a raw traceback.

### 5.3 `web/app.py`

`POST /analyze` keeps every synchronous check it has today — source
validation, username/max_games validation, the missing-API-key redirect
to `/settings`, and (for a fetch source) the `fetch_pgn` call via
`run_in_threadpool` — in the same order, so a bad username or a missing
key still fails immediately on the form, before any job exists. Once
those checks pass and the PGN bytes (uploaded or fetched) are written to
a temp file, instead of calling `run_analysis` inline it:

1. Creates a `Job` via `jobs.create_job(current_user.id)`.
2. Starts a background thread (via an injectable `_start_background`
   function — see §8 — defaulting to `threading.Thread(..., daemon=True).start()`)
   running a new `_run_analysis_job(job_id, tmp_path, player, lang, llm,
   llm_api_key, fetch_warnings)` function that:
   - Opens its own DB session (`next(get_db())`) — a request-scoped
     session must never be used from another thread.
   - Calls `run_analysis(..., on_progress=lambda done, total:
     jobs.update_progress(job_id, done, total))`.
   - On success: builds `report_markdown`/`report_html` exactly as today,
     creates and commits the `AnalysisRecord`, then calls
     `jobs.mark_done(job_id, record.id, fetch_warnings + warnings)` (same
     warnings this thread already has in scope — `fetch_warnings` is `[]`
     for the file-upload path, exactly as today).
   - On `(OSError, ValueError)` / `StockfishProvisionError`: calls
     `jobs.mark_error(job_id, str(exc))` / `jobs.mark_error(job_id, f"Motor
     kurulum hatası: {exc}")` — the same message text the synchronous path
     used for `_render_error`.
   - Always removes the temp file in a `finally`, and always closes the DB
     session in a `finally`.
3. Returns `RedirectResponse(f"/analyze/progress/{job.id}", status_code=303)`.

New routes:

```python
@app.get("/analyze/progress/{job_id}", response_class=HTMLResponse, response_model=None)
async def analyze_progress(request, job_id, current_user=Depends(get_current_user)):
    job = jobs.get_job(job_id)
    if job is None:
        return _render_error(request, "Analiz bulunamadı.", 404, current_user)
    if job.user_id != current_user.id:
        raise Forbidden()
    return _render(request, "progress.html", {"current_user": current_user, "job_id": job_id})

@app.get("/analyze/status/{job_id}", response_model=None)
async def analyze_status(job_id, current_user=Depends(get_current_user)):
    job = jobs.get_job(job_id)
    if job is None:
        return JSONResponse({"status": "error", "message": "Analiz bulunamadı."}, status_code=404)
    if job.user_id != current_user.id:
        return JSONResponse({"status": "error", "message": "Yetkisiz."}, status_code=403)
    body = {"status": job.status, "completed": job.completed, "total": job.total}
    if job.status == "done":
        body["redirect"] = f"/history/{job.record_id}?job={job.id}"
    elif job.status == "error":
        body["message"] = job.error_message
    return JSONResponse(body)
```

`Forbidden` and `_render_error`/`RedirectToLogin` reuse the existing
exception handlers already registered on `app` (`web/auth.py`,
`web/app.py`); no new error-handling machinery.

`GET /history/{record_id}` gains one optional query parameter, `job:
str | None = None`. When present, it looks the job up
(`jobs.get_job(job)`); if that job exists, belongs to `current_user`,
has `status == "done"`, and its `record_id` matches the `record_id` in
the URL, its `warnings` are passed into the template's existing
warnings-banner block (the same `.banner.warning` markup `result.html`
already uses, added to `history_detail.html`). Any mismatch (wrong user,
job not found — e.g. it aged out of the registry, wrong record) is not an
error; the page just renders without the banner, identical to a normal
`/history/{id}` visit. This keeps `/history/{id}` itself parameter-free
for every other caller (the history list, the admin view) and makes the
banner strictly a one-time echo of a job the current request just
finished — not a persisted feature of the history page.

### 5.4 `web/templates/progress.html` (new)

Extends `base.html` like every other page. A simple bar (a `<div>` whose
width is set by JS from `completed/total`), a text line ("7 / 23 oyun
analiz edildi"), and an error banner (hidden by default, matching the
existing `.banner.error` style) shown in place on failure, with a
"&larr; Yeni analiz" link identical in style to `result.html`'s. Inline
JS (module-scope, no new script files, matching `index.html`'s existing
pattern) polls `/analyze/status/{job_id}` with `fetch()` every 1000 ms;
on `"done"` it sets `window.location = data.redirect`; on `"error"` it
stops polling and shows `data.message` in the error banner; any polling
`fetch()` failure (network hiccup) is retried on the next tick rather
than surfaced, since a single dropped poll isn't a real failure.

### 5.5 `web/templates/index.html`

No changes beyond what Task 4 of the previous feature already added
(source selector, username/max_games fields). The status line's current
copy ("Analiz ediliyor… bu birkaç dakika sürebilir…") is no longer shown
during analysis itself — since the browser is redirected off this page
almost immediately, only the brief validation/fetch window — but it
still communicates useful expectation-setting for that window, so it
stays as-is.

## 6. Dependencies

None added. `threading` (stdlib) replaces the current in-request
synchronous call; the job registry is a plain `dict` + `threading.Lock`.

## 7. Error Handling

| Situation | Behavior |
|---|---|
| Bad source/username/max_games, missing API key | Unchanged: same redirect/400 the synchronous path gives today, before any job is created. |
| `fetch_pgn` fails (`GameFetchError`/`GameSourceUnavailable`) | Unchanged: 400/502 error page, before any job is created. |
| `run_analysis` raises `OSError`/`ValueError` in the background thread | Job -> `"error"`, message = `str(exc)`; shown inline on the progress page. |
| `run_analysis` raises `StockfishProvisionError` in the background thread | Job -> `"error"`, message = `f"Motor kurulum hatası: {exc}"`; shown inline. |
| `job_id` doesn't exist (typo'd URL, server restarted) | `/analyze/progress/{id}`: 404 error page. `/analyze/status/{id}`: 404 JSON. |
| `job_id` exists but belongs to another user | `/analyze/progress/{id}`: 403 (via `Forbidden`). `/analyze/status/{id}`: 403 JSON. |
| A single status poll fails (network blip) | Client retries on the next tick; no user-visible error. |

## 8. Testing Strategy

Tests must be deterministic — no real background threads racing a test's
assertions against sleeps. `web/app.py` calls background-thread startup
through one seam:

```python
def _start_background(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()
```

Tests monkeypatch `chesswhisper.web.app._start_background` to call
`target()` synchronously (inline, in the test's own thread) instead of
spawning a real thread. Because the DB session the job function opens is
its own (`next(get_db())`), running it inline in the test thread is safe
and behaves identically to the real background-thread path, just without
concurrency — this is the same "swap the seam, not the logic" approach
the existing `_open_engine` monkeypatch already uses throughout the test
suite.

- `tests/test_web_jobs.py` (new): unit tests for `jobs.py` — create/get,
  progress updates, done/error transitions, per-user isolation is a
  contract enforced by the caller (routes), not by `jobs.py` itself, and
  the one-hour pruning sweep (using a monkeypatched `time.monotonic`).
- `tests/test_web_progress.py` (new): with `_start_background`
  monkeypatched to run inline —
  - `POST /analyze` (file upload) redirects to `/analyze/progress/{id}`;
    following that job's status shows `"done"` and a `redirect` to
    `/history/{id}`, and that history record matches what the old
    synchronous test asserted (`test_analyze_saves_history_record_for_current_user`'s
    fixture data).
  - Same for the Lichess/chess.com fetch path (`fetch_pgn` monkeypatched
    as in `test_web_fetch.py`), including that `fetch_warnings` still
    reach the user: follow the status response's `redirect` (which
    includes `?job={id}`) and assert the warning text appears on that
    page.
  - A direct `/history/{id}` visit (no `?job=` param, e.g. from the
    history list later) does NOT show the warnings banner — confirms the
    banner is a one-time echo, not persisted, distinguishing it from a
    dedicated warnings-on-`/history/{id}?job=...` visit right after the
    job completes.
  - `run_analysis` raising `ValueError`/`StockfishProvisionError` ->
    job status `"error"` with the expected message text.
  - `/analyze/progress/{id}` and `/analyze/status/{id}` for another
    user's job -> 403; for an unknown id -> 404.
  - `/analyze/status/{id}` while still running -> `{"status": "running",
    "completed": <n>, "total": <n>}` with plausible values (achievable by
    monkeypatching `run_analysis` itself to call `on_progress` a couple of
    times before returning, then asserting the job's state via
    `jobs.get_job` rather than racing a real poll).
  - The missing-API-key redirect and fetch-error paths still fire
    *before* any job is created (assert via a spy that `jobs.create_job`
    was never called), preserving today's behavior.
- Existing tests that currently assert on `POST /analyze`'s direct 200
  response — in `test_web_app.py`, `test_web_analyze_authenticated.py`,
  and `test_web_fetch.py` (the exact call sites §9 enumerates) — must be
  updated to the new redirect-then-poll-then-land-on-history contract.
  `test_web_history.py` and `test_web_admin.py` seed their
  `AnalysisRecord` rows directly via the DB (matching their current
  pattern, e.g. `UserApiKey` insertion in
  `test_web_analyze_authenticated.py`), not through `POST /analyze`, so
  they need no changes. This is the bulk of
  the implementation risk: every test that used to do
  `response = client.post("/analyze", ...); assert response.status_code == 200; assert "chesswhisper Analysis Report" in response.text`
  needs rewriting to follow the redirect, poll (with `_start_background`
  patched inline, this is a same-tick, not a real poll loop), and check
  `/history/{record_id}` instead. No test's *intent* changes — only the
  mechanics of getting from "submitted the form" to "here's the report" do.
- `tests/test_cli.py`: one small addition confirming `run_analysis`
  accepts and calls `on_progress` (a fake collecting `(completed, total)`
  tuples across a two-game PGN), and that omitting it changes nothing
  (all existing CLI tests keep passing unmodified, since the parameter is
  optional and defaults to `None`).

The existing `ruff`, `mypy`, and `pytest` CI checks must stay green.

## 9. Open Items for Implementation Planning

- Confirmed via `grep -rn '"/analyze"' tests/` (as of this spec): three
  files call `POST /analyze` and need their assertions updated to the
  new redirect-then-poll contract — `tests/test_web_app.py` (3 call
  sites, including the "missing PGN file" 400 case, which does NOT change
  since that check still runs before any job exists),
  `tests/test_web_analyze_authenticated.py` (4 call sites — the file's
  entire subject is this behavior change), and `tests/test_web_fetch.py`
  (9 call sites — several are error-path tests, e.g. missing username,
  unknown source, that also don't change since they fail before job
  creation; only the success-path and `GameSourceUnavailable`/`GameFetchError`-after-fetch
  tests actually need rewriting to follow the new redirect). The
  implementation plan's task list must enumerate each call site by name,
  not just the file.
- Decide the progress bar's exact visual treatment (CSS) within
  `base.html`'s existing `:root` custom-property palette — no new colors,
  reuse `--accent`/`--border`/`--bg`.
- Decide polling backoff, if any, for a job that has been `"running"` far
  longer than expected (e.g. stop polling and show a "bu uzun sürüyor"
  hint after N minutes) — nice-to-have, not required for v1; default to
  no backoff/timeout unless the plan finds it trivial to add.

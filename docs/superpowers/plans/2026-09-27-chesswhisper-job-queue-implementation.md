# Persistent Job Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store web analysis jobs in the database and run them from a bounded worker pool that recovers after a restart.

**Architecture:** An `analysis_jobs` table (migration 0007) replaces the in-memory dict behind `web/jobs.py`, whose public functions keep their names and return plain `Job` snapshots. A new `web/worker.py` runs job ids from a `queue.Queue` on N daemon threads and, on start, requeues interrupted jobs. `app.py` stores the job's inputs and enqueues its id; a lifespan starts the worker.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-27-chesswhisper-job-queue-design.md`

## Global Constraints

- Worker count from `CHESSWHISPER_WORKERS`, default 2, minimum 1.
- Finished jobs kept 7 days; PGN text cleared when a job finishes.
- A job is failed instead of retried once it has been started 3 times.
- No LLM key stored in a job; read the user's saved key when the job runs.
- Statuses: `queued`, `running`, `done`, `error`. Queued counts as "in progress" for the one-per-user rule.
- Coverage stays at or above 90%; the full suite passes on Windows and Linux.

## Review Focus

- The server restarts while a job is running → after start-up it runs again and finishes (test in Task 3).
- The user deletes their LLM key between submitting and the job starting → the job fails with the missing-key message rather than crashing (Task 3).
- The user account is deleted while a job is queued → the worker skips it without an error (Task 2).
- Two submissions in quick succession → only one job is created (Task 1).
- A progress write fails (database locked) → the analysis continues (Task 1, `update_progress` swallows and logs).

---

### Task 1: `AnalysisJob` model, migration 0007 and DB-backed `jobs.py`

**Files:**
- Modify: `src/chesswhisper/web/models.py`, `src/chesswhisper/web/db.py`, `src/chesswhisper/web/jobs.py`, `tests/conftest.py`
- Create: `src/chesswhisper/web/migrations/versions/0007_analysis_jobs.py`
- Test: `tests/test_web_jobs.py` (rewritten), `tests/test_web_migrations.py`

**Interfaces:**
- Produces: `db.session_scope()` context manager (commit on success, rollback on error). `jobs.JobInputs(pgn_text, player, lang, llm, ui_language, fetch_warnings)`; `jobs.create_job(user_id, inputs=None) -> Job`; `create_job_unless_running(user_id, inputs) -> tuple[Job, bool]`; `running_job_for(user_id) -> Job | None`; `get_job(job_id) -> Job | None`; `mark_running(job_id) -> bool`; `job_inputs(job_id) -> JobInputs | None`; `update_progress`; `mark_done`; `mark_error`; `queue_position(job_id) -> int` (jobs queued before it); `recover() -> list[str]` (ids to run, oldest first); `prune(now=None)`; constants `JOB_RETENTION = timedelta(days=7)`, `MAX_ATTEMPTS = 3`.

- [ ] Rewrite `tests/test_web_jobs.py` against a `tmp_path` database (`init_db(tmp_path / "jobs.db")` plus a user row): snapshot fields, `queued` initial status, one-active-job rule for queued and running, queue position, `mark_running` increments attempts, `mark_done` clears the PGN, `recover` requeues running jobs and fails the third-attempt one, `prune` deletes finished jobs older than 7 days only, deleting the user deletes their jobs.
- [ ] Run `pytest tests/test_web_jobs.py -v`; expect failures.
- [ ] Add the model, the migration (same style as 0006), `session_scope`, and the new `jobs.py`. `create_job_unless_running` runs its check and insert in one session under a module lock (single process) so two threads cannot both insert.
- [ ] Replace the conftest `_isolate_job_registry` fixture (the registry is now per test database) and extend the migration test's expected table list.
- [ ] Run the new tests, then the full suite; fix callers only as far as needed to keep it green (Task 3 does the real rewiring).
- [ ] Commit `feat: store analysis jobs in the database`.

### Task 2: `JobWorker`

**Files:**
- Create: `src/chesswhisper/web/worker.py`
- Test: `tests/test_web_worker.py`

**Interfaces:**
- Consumes: `jobs.recover`, `jobs.get_job`.
- Produces: `JobWorker(runner: Callable[[str], None], workers: int)` with `start()`, `submit(job_id)`, `stop(timeout)` and `worker_count_from_env() -> int`.

- [ ] Tests with a fake runner and `threading.Event`s: jobs submitted run in order with one worker; with two workers two run at once and a third waits; `start()` runs jobs `recover()` returns; a runner exception does not stop the thread; `submit` before `start` is held until start; env parsing (`"3"` → 3, `"0"`/`"x"`/unset → default 2 with 0 clamped to 1).
- [ ] Implement; each thread loops on `queue.get()`, skips ids whose job no longer exists or is not queued, and logs runner exceptions.
- [ ] Run tests, commit `feat: a worker pool that runs queued analyses`.

### Task 3: Wire the web app and API to the queue

**Files:**
- Modify: `src/chesswhisper/web/app.py`, `src/chesswhisper/web/api.py`, `src/chesswhisper/web/templates/progress.html`, `src/chesswhisper/web/ui_text.py`, `tests/conftest.py`
- Test: `tests/test_web_progress.py`, `tests/test_api.py`, `tests/test_web_hardening.py`, new `tests/test_web_queue.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: `app._enqueue(job_id)` (the test seam, patched in conftest to call `_run_analysis_job(job_id)` inline); `_run_analysis_job(job_id)`; status JSON `position` for queued jobs.

- [ ] Tests: status JSON for a queued job has `status: queued` and `position`; progress page renders the queued text; a job created, then the worker "restarted" (new `JobWorker` started against the same database) finishes; LLM key deleted after submit → job error with the missing-key text; API `JobStatus` accepts `queued`.
- [ ] `start_analysis` builds `JobInputs` instead of a temp file and calls `create_job_unless_running` then `_enqueue`. `_run_analysis_job(job_id)` calls `mark_running`, loads inputs, looks up the key if `llm != "none"`, writes a temp file, runs, saves, marks done/error, deletes the temp file. A lifespan creates and starts the module's `JobWorker`.
- [ ] Progress template handles `queued`; new UI strings in both languages.
- [ ] Full suite with coverage; commit `feat: run analyses from a persistent queue`.

### Task 4: Docs and release 0.4.0

- [ ] README env var table (`CHESSWHISPER_WORKERS`), deployment guide (jobs survive restarts; remove "background jobs live in memory"), CHANGELOG 0.4.0, version bump in `pyproject.toml`.
- [ ] Full suite; commit `docs: document the job queue; release 0.4.0`; tag `v0.4.0`; push master and the tag; watch CI and the Release workflow.

# Persistent job queue (0.4.0)

## Goal

Web analyses currently live in an in-memory dict and each one gets its own
thread. A server restart loses every job (a running analysis just vanishes
and its progress page says "not found"), and any number of analyses can run
Stockfish at once. After this change jobs are rows in the database, a small
worker pool runs them in order, and a restart picks up where it left off.

Success criteria:

- A job survives a restart: finished jobs keep their status, warnings and
  result link; queued or interrupted jobs are run again automatically.
- At most `CHESSWHISPER_WORKERS` analyses (default 2) run at once; the rest
  wait, and the progress page says how many are ahead.
- The web form, the JSON API and their existing responses keep working; the
  one-analysis-per-user rule still holds (queued counts as in progress).

## Decisions

- **Storage: a new `analysis_jobs` table** (migration 0007), not a separate
  queue library or file. SQLite is already there and the app is documented
  as single-process, so a table plus an in-process worker pool is enough.
  No Redis, Celery or extra service.
- **Inputs are stored with the job** so it can be re-run: the PGN text
  (uploaded or fetched; uploads are capped at the existing limit), player,
  report language, LLM choice, UI language and the fetch warnings. The PGN
  is cleared once the job finishes.
- **No LLM key is stored in the job.** The worker reads the user's saved,
  encrypted key when the job starts. If it has been deleted in between, the
  job fails with the existing "missing key" message.
- **Interrupted jobs restart from scratch.** Games already analyzed come
  back from the evaluation cache, so a re-run only pays for the rest. Each
  start increments `attempts`; a job interrupted on its third attempt is
  marked failed instead of being retried forever (for example if the
  analysis itself keeps crashing the process).
- **Retention: finished jobs are kept for 7 days** (was one hour in memory),
  pruned when a job is created and at startup.

## Components

- `web/models.py`: `AnalysisJob` with `id` (UUID string), `user_id`,
  `status` (`queued` | `running` | `done` | `error`), `completed`, `total`,
  `record_id`, `warnings_json`, `error_message`, `attempts`, the inputs
  above, `created_at`, `started_at`, `finished_at`. Deleting a user deletes
  their jobs.
- `web/jobs.py`: same public functions as today (`create_job`,
  `create_job_unless_running`, `running_job_for`, `get_job`,
  `update_progress`, `mark_done`, `mark_error`) plus `mark_running`,
  `job_inputs`, `queue_position`, `recover` and `prune`. Each opens its own
  short session, so callers stay unaware of the storage. `get_job` returns a
  plain `Job` snapshot as before, with `queued` as a new status value.
  `create_job_unless_running` checks and inserts in one `BEGIN IMMEDIATE`
  transaction so two quick submissions cannot both create a job.
- `web/worker.py`: `JobWorker` owns a `queue.Queue` of job ids and N daemon
  threads that call a runner for each id. `start()` recovers the database
  (running jobs become queued again, or failed after three attempts), then
  enqueues every queued job oldest first.
- `web/app.py`: `start_analysis` stores the inputs and enqueues the job id;
  `_run_analysis_job(job_id)` loads the inputs, writes a temp PGN, runs the
  analysis and records the outcome as it does now. A FastAPI lifespan starts
  the worker. The status JSON gains `position` for queued jobs.
- `progress.html`: a queued job shows "Waiting: N analyses ahead" instead of
  the progress bar until it starts.
- API: `JobStatus.status` accepts `queued`; the 404 text says finished jobs
  are kept for 7 days.

## Error handling

- Anything the runner raises is caught and recorded as the job's error, as
  today; the worker thread keeps going.
- A job whose row disappeared (user deleted mid-run) is skipped.
- A database error while saving progress is logged and ignored so it cannot
  kill the analysis; the final `mark_done`/`mark_error` still runs.

## Testing

- `jobs` functions against a temporary database: create, snapshot, the
  one-active-job rule, queue position, recover (requeue and the three-attempt
  cap), prune after 7 days, PGN cleared when finished.
- `JobWorker` with a fake runner: runs jobs in order, respects the worker
  count, recovers queued jobs on start, survives a runner exception.
- Web and API: queued status and position in the status JSON and the
  progress page; a job created before a simulated restart finishes after
  it; the LLM key read at run time.
- The existing test seam (run the job inline) moves from `_start_background`
  to the enqueue function.

# chesswhisper Alembic Migrations — Design Spec

Date: 2026-09-25
Status: Draft (pending user review)

## 1. Overview

The web UI's SQLite schema is created by `Base.metadata.create_all()`
inside `chesswhisper.web.db.init_db()`. `create_all` only creates missing
tables; it never alters an existing one. The multiuser spec explicitly
deferred migrations tooling "until the schema changes". Upcoming work
(showing blunders on a board) is expected to add data to
`analysis_records`, so the schema is about to change for users who
already have a populated `chesswhisper.db`.

This feature replaces `create_all` with Alembic migrations, ships the
migrations inside the package, and upgrades existing databases
automatically and without data loss. It adds no new columns or tables:
the only migration is a baseline that reproduces today's schema. It
supersedes the multiuser spec's non-goal "No database migrations tooling".

## 2. Goals

- `init_db(db_path)` brings any database to the latest schema revision:
  - **Empty / new database:** runs all migrations from scratch
    (`upgrade head`), creating the three tables and `alembic_version`.
  - **Legacy database** (created by the old `create_all`: the `users`
    table exists but `alembic_version` does not): stamps it at the
    baseline revision, then runs `upgrade head`. Existing rows are left
    untouched.
  - **Already-migrated database:** runs `upgrade head`, which is a no-op
    when it is already at head.
- `init_db` stays idempotent and keeps its current signature, so every
  existing caller (`cli.py` `web` command, `get_db()` lazy init, tests)
  keeps working unchanged.
- Migrations work from an installed wheel (`pip install
  chesswhisper[web]`), not only from a repo checkout.
- A developer can generate a new migration from the repo root with
  `alembic revision --autogenerate -m "..."`.
- CI fails if the models and the migrations drift apart.

## 3. Non-Goals

- No new columns or tables (those come with the feature that needs them).
- No `chesswhisper db ...` CLI subcommands; upgrades are automatic on
  startup.
- No supported downgrade path. The baseline's `downgrade()` drops the
  tables for completeness, but nothing in the app or docs exposes it.
- No support for databases other than SQLite.

## 4. Design

### 4.1 Layout

```
alembic.ini                                  # dev-only: points at the package's migrations
src/chesswhisper/web/migrations/
    env.py
    script.py.mako
    versions/
        0001_baseline.py
```

`alembic` is added to the `web` optional-dependency group in
`pyproject.toml`. Hatch already packages everything under
`src/chesswhisper`, so `env.py`, `script.py.mako`, and `versions/` ship in
the wheel.

### 4.2 `env.py`

- `target_metadata = Base.metadata`, after importing
  `chesswhisper.web.models` so all tables are registered.
- Online mode only matters at runtime. If the caller put a live
  `Connection` in `config.attributes["connection"]`, `env.py` uses it.
  Otherwise it builds an engine from `alembic.ini`'s `sqlalchemy.url`,
  which points at a gitignored scratch `dev.db` in the repo root. If no
  URL is set it raises `RuntimeError`. It never falls back to the app's
  real database, so developer CLI commands can't create or touch a
  user's data.
- `context.configure(..., render_as_batch=True)` so future
  `ALTER`-style migrations work on SQLite.
- Offline mode (`--sql`) is supported in the standard way for
  completeness.

### 4.3 `0001_baseline`

Creates `users`, `user_api_keys`, and `analysis_records` exactly as the
current models define them: same column types, nullability, defaults,
primary/foreign keys, the unique index on `users.username`, and the index
on `analysis_records.user_id`. `downgrade()` drops them in reverse
dependency order. The revision id is the fixed string `"0001_baseline"`
so the stamp code can refer to it by name.

### 4.4 `init_db` flow

```
engine = get_engine(db_path)
with engine.begin() as conn:
    cfg = Config()                                  # no ini file needed
    cfg.set_main_option("script_location", <package path to migrations>)
    cfg.attributes["connection"] = conn
    tables = inspect(conn).get_table_names()
    if "users" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "0001_baseline")
    command.upgrade(cfg, "head")
_SessionLocal = sessionmaker(bind=engine, ...)
```

The migrations directory is located with
`importlib.resources.files("chesswhisper.web") / "migrations"` (a real
filesystem path for both editable and wheel installs), so there is no
dependency on the working directory. The logic lives in a small helper
(`run_migrations(engine)`) in `db.py`. `Base.metadata.create_all`
is removed.

Alembic's default `fileConfig` logging setup is skipped when there is no
ini file, so calling `init_db` does not reconfigure the app's logging.

### 4.5 Error handling and concurrency

A failing migration raises out of `init_db`, so the app fails to start
with Alembic's traceback, and the database is left exactly as it was.
Python's `sqlite3` driver never emits BEGIN before DDL, so migrations run
on a private, NullPool engine that takes over transaction control
(SQLAlchemy's documented pysqlite recipe: driver `isolation_level = None`
plus `BEGIN IMMEDIATE` on begin). The stamp and the whole upgrade then
form one real transaction. `BEGIN IMMEDIATE` also makes a second process
that starts at the same moment wait for the first one's migration.

Within one process, Alembic's command API is not thread-safe. `init_db`
and `get_db()`'s lazy init therefore share a lock, and `chesswhisper
serve` calls `init_db()` before starting uvicorn, so migrations normally
run once at startup and never inside a request. No retry or repair logic.

## 5. Testing

New tests in `tests/test_web_migrations.py` (existing `test_web_db.py`
and every test that calls `init_db` keep passing unchanged):

1. **Fresh DB:** `init_db(tmp)` creates the three tables and
   `alembic_version`, whose single row is the head revision.
2. **Legacy DB:** build a DB the old way (`Base.metadata.create_all` on a
   raw engine), insert a user and an analysis record, then call
   `init_db`. Assert `alembic_version` is at head and both rows are still
   there.
3. **Idempotent:** calling `init_db` twice on the same path succeeds and
   leaves one `alembic_version` row.
4. **No drift:** after `init_db`, `alembic.autogenerate.compare_metadata`
   between the migrated DB and `Base.metadata` returns an empty diff.
5. **Packaged location:** the resolved migrations directory exists and
   contains `env.py` and at least one file in `versions/`.

`ruff` and `mypy src` must pass. `env.py` and the versions files may need
per-file lint/type adjustments; keep them minimal.

## 6. Documentation

A short "Schema changes" section in `README.md`: edit the models, run
`alembic revision --autogenerate -m "<what>"` from the repo root, review
the generated file, and commit it. Existing databases upgrade
automatically the next time the app starts.

# Alembic Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `Base.metadata.create_all()` in `chesswhisper.web.db.init_db()` with Alembic migrations that ship inside the package, upgrading new, legacy (pre-Alembic), and already-migrated SQLite databases automatically on startup without data loss.

**Architecture:** Migrations live in `src/chesswhisper/web/migrations/` (`env.py`, `script.py.mako`, `versions/0001_baseline.py`). A new `run_migrations(engine)` in `db.py` builds an Alembic `Config` in code, hands `env.py` an open connection, stamps legacy databases at `0001_baseline`, then runs `upgrade head`. A dev-only `alembic.ini` in the repo root points the Alembic CLI at a gitignored scratch `dev.db` so developers can autogenerate future migrations. It never points at a user's real database.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic (≥1.13), SQLite, `pytest`, `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-09-25-chesswhisper-alembic-design.md`

## Global Constraints

- `alembic>=1.13` is added to the `web` optional-dependency group in `pyproject.toml`. No other new dependencies.
- `init_db(db_path: Path | None = None) -> None` keeps its exact signature and stays idempotent. Every existing caller (`cli.py` `web` command, `get_db()` lazy init, all existing tests) keeps working unchanged.
- The baseline revision id is the fixed string `"0001_baseline"`, exposed as `BASELINE_REVISION` in `db.py`.
- Legacy detection rule: `"users"` table present and `"alembic_version"` table absent → stamp `0001_baseline`, then `upgrade head`.
- `env.py` uses `render_as_batch=True` (online and offline) so future ALTER-style migrations work on SQLite.
- Migrations are located via `importlib.resources.files("chesswhisper.web") / "migrations"`, never relative to the working directory.
- `init_db` must not reconfigure logging: no `fileConfig` call anywhere in `env.py`.
- The Alembic CLI never falls back to the app's real database. Without `sqlalchemy.url` and without a passed-in connection, `env.py` raises `RuntimeError`.
- No new CLI subcommands, no new columns/tables, no exposed downgrade path.
- Code style: `ruff check src tests` (line length 110, rules E,F,I,UP,B) and `mypy src` must pass. `pytest` must stay green, including every pre-existing test.
- Commit messages: plain `type: subject` lines, no `Co-Authored-By` trailer and no AI/tool attribution of any kind (the repo owner's standing rule for every commit).

## Review Focus

- **Database file holding only an empty `alembic_version` table** (left behind by an interrupted first start or a stray Alembic CLI run): `init_db` should treat it as fresh and migrate to head, not stamp or crash. Test: `test_database_with_only_empty_version_table_is_migrated` (Task 1).
- **App started from a different working directory** (e.g. `chesswhisper web` from `~`): migrations must still be found. Test: `test_init_db_works_from_any_working_directory` (Task 1).
- **Logging configuration after startup:** Alembic's usual `fileConfig` would silently replace the app's handlers. Test: `test_init_db_leaves_logging_configuration_alone` (Task 1).
- **Alembic CLI run with no URL configured:** must refuse instead of creating or touching the real `~/AppData/.../chesswhisper.db`. Test: `test_cli_mode_without_url_refuses_to_guess_a_database` (Task 1).
- **A developer adds a model column and forgets the migration:** CI must fail. Test: `test_migrated_schema_matches_models` (Task 1).

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` (modify) | Add `alembic>=1.13` to the `web` extra. |
| `src/chesswhisper/web/migrations/env.py` (create) | Alembic environment: uses a passed-in connection at runtime, `alembic.ini`'s URL from the CLI. |
| `src/chesswhisper/web/migrations/script.py.mako` (create) | Template for new revisions, emitting ruff/pyupgrade-clean code. |
| `src/chesswhisper/web/migrations/versions/0001_baseline.py` (create) | Creates today's three tables exactly as the models define them. |
| `src/chesswhisper/web/db.py` (modify) | `migrations_dir()`, `run_migrations(engine)`, `BASELINE_REVISION`; `init_db` calls `run_migrations` instead of `create_all`. |
| `tests/test_web_migrations.py` (create) | Fresh / legacy / stray / idempotent / drift / packaging / cwd / logging / CLI-guard tests. |
| `alembic.ini` (create) | Dev-only CLI config pointing at the package migrations and `sqlite:///dev.db`. |
| `.gitignore` (modify) | Ignore `/dev.db`. |
| `README.md` (modify) | "Schema changes" subsection under Development. |

---

### Task 1: Alembic migrations run by `init_db`

**Files:**
- Modify: `pyproject.toml` (`web = [...]` line in `[project.optional-dependencies]`)
- Create: `src/chesswhisper/web/migrations/env.py`
- Create: `src/chesswhisper/web/migrations/script.py.mako`
- Create: `src/chesswhisper/web/migrations/versions/0001_baseline.py`
- Modify: `src/chesswhisper/web/db.py` (whole file shown below)
- Test: `tests/test_web_migrations.py`

**Interfaces:**
- Consumes: `Base`, `get_engine`, `init_db`, `get_db` from `chesswhisper.web.db`; models `User`, `UserApiKey`, `AnalysisRecord` from `chesswhisper.web.models` (unchanged).
- Produces:
  - `chesswhisper.web.db.BASELINE_REVISION: str = "0001_baseline"`
  - `chesswhisper.web.db.migrations_dir() -> pathlib.Path`
  - `chesswhisper.web.db.run_migrations(engine: sqlalchemy.engine.Engine) -> None`
  - `init_db(db_path: Path | None = None) -> None`: same signature, now migrates.

- [ ] **Step 1: Add the dependency and install it**

In `pyproject.toml`, append `"alembic>=1.13"` to the end of the `web = [...]` list, so the line becomes:

```toml
web = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9", "jinja2>=3.1", "markdown>=3.6", "bleach>=6.1", "sqlalchemy>=2.0", "bcrypt>=4.0", "cryptography>=42", "itsdangerous>=2.1", "alembic>=1.13"]
```

Run: `pip install -e ".[dev,web]"`
Expected: installs `alembic` (and `Mako`); `python -c "import alembic"` succeeds.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_web_migrations.py`:

```python
import logging

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata
from chesswhisper.web.db import BASELINE_REVISION, Base, get_db, init_db, migrations_dir

HEAD = BASELINE_REVISION


def _engine(db_path):
    return create_engine(f"sqlite:///{db_path}")


def _versions(engine):
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]


def test_fresh_database_is_migrated_to_head(tmp_path):
    db_path = tmp_path / "fresh.db"
    init_db(db_path)

    engine = _engine(db_path)
    tables = set(inspect(engine).get_table_names())
    assert {"users", "user_api_keys", "analysis_records", "alembic_version"} <= tables
    assert _versions(engine) == [HEAD]


def test_legacy_create_all_database_is_stamped_and_keeps_its_rows(tmp_path):
    db_path = tmp_path / "legacy.db"
    legacy = _engine(db_path)
    Base.metadata.create_all(legacy)
    with legacy.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, username, password_hash, is_admin, created_at, "
                "failed_login_attempts) VALUES (1, 'alice', 'h', 0, '2026-01-01 00:00:00', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO analysis_records (user_id, created_at, player_name, lang, llm_used, "
                "games_count, overall_acpl, overall_accuracy, report_markdown) VALUES "
                "(1, '2026-01-01 00:00:00', 'alice', 'tr', 'none', 3, 42.0, 80.5, '# r')"
            )
        )
    legacy.dispose()

    init_db(db_path)

    assert _versions(_engine(db_path)) == [HEAD]
    db_gen = get_db()
    db = next(db_gen)
    try:
        user = db.query(models.User).filter_by(username="alice").one()
        assert len(user.analyses) == 1
        assert user.analyses[0].overall_acpl == 42.0
    finally:
        db_gen.close()


def test_database_with_only_empty_version_table_is_migrated(tmp_path):
    db_path = tmp_path / "stray.db"
    stray = _engine(db_path)
    with stray.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
    stray.dispose()

    init_db(db_path)

    engine = _engine(db_path)
    assert {"users", "user_api_keys", "analysis_records"} <= set(inspect(engine).get_table_names())
    assert _versions(engine) == [HEAD]


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "twice.db"
    init_db(db_path)
    init_db(db_path)

    assert _versions(_engine(db_path)) == [HEAD]


def test_migrated_schema_matches_models(tmp_path):
    db_path = tmp_path / "drift.db"
    init_db(db_path)

    with _engine(db_path).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_migrations_ship_inside_the_package():
    directory = migrations_dir()
    assert (directory / "env.py").is_file()
    assert (directory / "script.py.mako").is_file()
    assert any((directory / "versions").glob("*.py"))


def test_init_db_works_from_any_working_directory(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    init_db(tmp_path / "cwd.db")

    assert _versions(_engine(tmp_path / "cwd.db")) == [HEAD]


def test_init_db_leaves_logging_configuration_alone(tmp_path):
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level

    init_db(tmp_path / "logging.db")

    assert root.handlers == handlers_before
    assert root.level == level_before


def test_cli_mode_without_url_refuses_to_guess_a_database():
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))

    with pytest.raises(RuntimeError, match="sqlalchemy.url"):
        command.upgrade(config, "head")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_web_migrations.py -v`
Expected: collection error, `ImportError: cannot import name 'BASELINE_REVISION' from 'chesswhisper.web.db'`.

- [ ] **Step 4: Create the migrations environment**

Create `src/chesswhisper/web/migrations/env.py`:

```python
"""Alembic environment for chesswhisper's web database.

At runtime chesswhisper.web.db.run_migrations() hands this script an open
connection via config.attributes["connection"]. From the command line
(`alembic ...` in the repo root) there is no connection, so one is built
from alembic.ini's sqlalchemy.url -- deliberately never the app's real
database, so developer commands can't touch a user's data.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata
from chesswhisper.web.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("set sqlalchemy.url in alembic.ini to run Alembic from the command line")
    return url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, render_as_batch=True, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with create_engine(_url()).connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `src/chesswhisper/web/migrations/script.py.mako` (Alembic's generic template, rewritten so generated files already satisfy ruff's `UP`/`I` rules):

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `src/chesswhisper/web/migrations/versions/0001_baseline.py` (this is Alembic's autogenerate output against the current models, verified to produce an empty `compare_metadata` diff):

```python
"""baseline: users, user_api_keys, analysis_records

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "analysis_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("player_name", sa.String(length=200), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("llm_used", sa.String(length=20), nullable=False),
        sa.Column("games_count", sa.Integer(), nullable=False),
        sa.Column("overall_acpl", sa.Float(), nullable=False),
        sa.Column("overall_accuracy", sa.Float(), nullable=False),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_records_user_id"), "analysis_records", ["user_id"], unique=False)
    op.create_table(
        "user_api_keys",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("anthropic_key_encrypted", sa.Text(), nullable=True),
        sa.Column("openai_key_encrypted", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_api_keys")
    op.drop_index(op.f("ix_analysis_records_user_id"), table_name="analysis_records")
    op.drop_table("analysis_records")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
```

No `__init__.py` is needed in `migrations/` or `versions/`: Alembic loads them by path, and hatch packages every file under `src/chesswhisper` into the wheel (verified: the built wheel contains `env.py`, `script.py.mako`, and `versions/0001_baseline.py`).

- [ ] **Step 5: Switch `init_db` to migrations**

Replace `src/chesswhisper/web/db.py` with:

```python
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config
from platformdirs import user_data_dir
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Revision that reproduces the schema the pre-Alembic create_all() built.
# Databases created before migrations existed are stamped at this revision.
BASELINE_REVISION = "0001_baseline"


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def _default_db_path() -> Path:
    return Path(user_data_dir("chesswhisper")) / "chesswhisper.db"


def get_engine(db_path: Path | None = None) -> Engine:
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def migrations_dir() -> Path:
    return Path(str(files("chesswhisper.web") / "migrations"))


def run_migrations(engine: Engine) -> None:
    """Bring the database behind *engine* to the latest schema revision.

    A database created by the pre-Alembic create_all() has the tables but
    no alembic_version table; it is stamped at the baseline first so the
    baseline migration doesn't try to recreate tables that already exist.
    """
    with engine.begin() as connection:
        config = Config()
        config.set_main_option("script_location", str(migrations_dir()))
        config.attributes["connection"] = connection
        tables = inspect(connection).get_table_names()
        if "users" in tables and "alembic_version" not in tables:
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(db_path: Path | None = None) -> None:
    global _engine, _SessionLocal
    _engine = get_engine(db_path)
    run_migrations(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

(`models` is no longer imported here: `env.py` imports it before Alembic reads `Base.metadata`, and `db.py` never needs the metadata itself anymore.)

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `pytest tests/test_web_migrations.py -v`
Expected: 9 passed.

- [ ] **Step 7: Run the full suite, lint, and type-check**

Run: `pytest -q && ruff check src tests && mypy src`
Expected: all tests pass (the prior count plus 9), `All checks passed!`, `Success: no issues found`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/chesswhisper/web/db.py src/chesswhisper/web/migrations tests/test_web_migrations.py
git commit -m "feat: run Alembic migrations from init_db instead of create_all"
```

---

### Task 2: Developer tooling and docs for new migrations

**Files:**
- Create: `alembic.ini`
- Modify: `.gitignore`
- Modify: `README.md` (Development section, after the CI paragraph, before `## License`)

**Interfaces:**
- Consumes: `src/chesswhisper/web/migrations/` from Task 1 (`env.py` reads `sqlalchemy.url` from this ini file when run from the CLI).
- Produces: a working `alembic` CLI in the repo root that targets `./dev.db`.

- [ ] **Step 1: Verify the CLI doesn't work yet**

Run from the repo root: `alembic heads`
Expected: FAIL. Alembic reports that no config file `alembic.ini` was found.

- [ ] **Step 2: Create `alembic.ini`**

```ini
# Developer-only Alembic config for generating new migrations. The app never
# reads this file: chesswhisper.web.db.run_migrations() configures Alembic in
# code at startup. dev.db is a scratch database (gitignored), never a user's
# real one:
#   alembic upgrade head
#   alembic revision --autogenerate -m "add blunders to analysis_records"
[alembic]
script_location = %(here)s/src/chesswhisper/web/migrations
sqlalchemy.url = sqlite:///dev.db
```

- [ ] **Step 3: Ignore the scratch database**

Append to `.gitignore`:

```gitignore

# Scratch database used by the Alembic CLI (see alembic.ini)
/dev.db
```

- [ ] **Step 4: Document the workflow**

In `README.md`, insert after the paragraph ending "across Python 3.11/3.12 on Linux and Windows." and before `## License`:

````markdown
### Schema changes

The web UI's SQLite schema is managed by Alembic; migrations live in
`src/chesswhisper/web/migrations/versions/` and run automatically every
time the app starts, so existing databases upgrade themselves. To change
the schema:

```
alembic upgrade head                           # bring the scratch dev.db up to date
# edit src/chesswhisper/web/models.py
alembic revision --autogenerate -m "what changed"
```

Review the generated file in `versions/` before committing it. `pytest`
fails if the models and the migrations disagree.
````

- [ ] **Step 5: Verify the CLI workflow**

Run from the repo root:

```bash
alembic heads
alembic upgrade head
alembic check
git status --short
```

Expected: `0001_baseline (head)`; the upgrade runs `0001_baseline`; `No new upgrade operations detected.`; `git status` shows only `alembic.ini`, `.gitignore`, and `README.md` (`dev.db` is ignored). Then delete the scratch file: `rm dev.db`.

- [ ] **Step 6: Run the full suite and lint**

Run: `pytest -q && ruff check src tests`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini .gitignore README.md
git commit -m "docs: add dev Alembic config and schema-change workflow"
```

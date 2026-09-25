import logging
import shutil
import textwrap
import threading

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from chesswhisper.web import db as db_module
from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata
from chesswhisper.web.db import BASELINE_REVISION, Base, get_db, init_db, migrations_dir

HEAD = "0002_critical_moves"


def _engine(db_path):
    return create_engine(f"sqlite:///{db_path}")


def _versions(engine):
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))]


def _build_schema_at(engine, revision):
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(migrations_dir()))
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


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
    # The schema the pre-Alembic create_all() built is exactly the baseline's.
    _build_schema_at(legacy, BASELINE_REVISION)
    with legacy.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
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


def _migrations_with_failing_revision(tmp_path):
    """A copy of the real migrations plus a revision on top that does DDL, then raises."""
    directory = tmp_path / "migrations"
    shutil.copytree(migrations_dir(), directory, ignore=shutil.ignore_patterns("__pycache__"))
    (directory / "versions" / "0099_boom.py").write_text(
        textwrap.dedent(
            '''
            import sqlalchemy as sa
            from alembic import op

            revision = "0099_boom"
            down_revision = "0002_critical_moves"
            branch_labels = None
            depends_on = None


            def upgrade() -> None:
                with op.batch_alter_table("analysis_records") as batch_op:
                    batch_op.add_column(sa.Column("blunders", sa.Text(), nullable=True))
                op.create_table("extra", sa.Column("id", sa.Integer(), primary_key=True))
                raise RuntimeError("boom")


            def downgrade() -> None:
                pass
            '''
        ),
        encoding="utf-8",
    )
    return directory


def test_failed_migration_rolls_back_its_ddl(tmp_path, monkeypatch):
    db_path = tmp_path / "rollback.db"
    init_db(db_path)
    failing = _migrations_with_failing_revision(tmp_path)
    monkeypatch.setattr(db_module, "migrations_dir", lambda: failing)

    with pytest.raises(RuntimeError, match="boom"):
        init_db(db_path)

    engine = _engine(db_path)
    assert "extra" not in inspect(engine).get_table_names()
    assert "blunders" not in {c["name"] for c in inspect(engine).get_columns("analysis_records")}
    assert _versions(engine) == [HEAD]


def test_failed_first_start_leaves_the_database_startable(tmp_path, monkeypatch):
    db_path = tmp_path / "first.db"
    failing = _migrations_with_failing_revision(tmp_path)
    monkeypatch.setattr(db_module, "migrations_dir", lambda: failing)
    with pytest.raises(RuntimeError, match="boom"):
        init_db(db_path)
    monkeypatch.undo()

    init_db(db_path)

    assert _versions(_engine(db_path)) == [HEAD]


def _open_session_after(barrier, errors):
    try:
        barrier.wait()
        db_gen = get_db()
        next(db_gen)
        db_gen.close()
    except Exception as exc:  # collected for the caller's assertion
        errors.append(exc)


def test_concurrent_lazy_get_db_migrates_exactly_once(tmp_path, monkeypatch):
    for trial in range(5):
        db_path = tmp_path / f"race{trial}.db"
        monkeypatch.setattr(db_module, "_default_db_path", lambda path=db_path: path)
        monkeypatch.setattr(db_module, "_SessionLocal", None)
        barrier = threading.Barrier(4)
        errors: list[Exception] = []
        threads = [threading.Thread(target=_open_session_after, args=(barrier, errors)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert _versions(_engine(db_path)) == [HEAD]


def test_baseline_database_gains_critical_moves_column_and_keeps_rows(tmp_path):
    db_path = tmp_path / "at_baseline.db"
    engine = _engine(db_path)
    _build_schema_at(engine, BASELINE_REVISION)
    with engine.begin() as conn:
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
    engine.dispose()

    init_db(db_path)

    engine = _engine(db_path)
    assert _versions(engine) == [HEAD]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT report_markdown, critical_moves_json FROM analysis_records")).all()
    assert [tuple(r) for r in rows] == [("# r", None)]

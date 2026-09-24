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

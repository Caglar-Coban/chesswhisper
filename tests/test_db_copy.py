import os

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from chesswhisper.cli import cli
from chesswhisper.web import db_copy
from chesswhisper.web.db import Base, create_engine_for, run_migrations
from chesswhisper.web.models import (
    AnalysisJob,
    AnalysisRecord,
    GameSync,
    PuzzleAttempt,
    PuzzleRushRun,
    User,
    UserApiKey,
)

TEST_DATABASE_URL = os.environ.get("CHESSWHISPER_TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(not TEST_DATABASE_URL, reason="set CHESSWHISPER_TEST_DATABASE_URL")


def _sqlite(path):
    return create_engine_for(f"sqlite:///{path}")


def _filled_source(path):
    engine = _sqlite(path)
    run_migrations(engine)
    with Session(engine) as db:
        alice = User(username="alice", password_hash="h", is_admin=True)
        bob = User(username="bob", password_hash="h")
        db.add_all([alice, bob])
        db.flush()
        record = AnalysisRecord(
            user_id=alice.id, player_name="Alice", lang="en", llm_used="none", games_count=1,
            overall_acpl=12.5, overall_accuracy=91.0, report_markdown="# R", unseen=True,
        )
        db.add(record)
        db.flush()
        db.add_all([
            UserApiKey(user_id=alice.id, anthropic_key_encrypted="enc"),
            PuzzleAttempt(user_id=alice.id, record_id=record.id, move_index=0, move_uci="e2e4", solved=True),
            PuzzleRushRun(user_id=bob.id, minutes=3, solved=7, misses=3),
            GameSync(user_id=bob.id, platform="lichess", username="bob", lang="tr", seen_keys_json="[]"),
            AnalysisJob(id="job-1", user_id=alice.id, status="done", record_id=record.id, origin="sync"),
        ])
        db.commit()
    return engine


def _rows(engine):
    with engine.connect() as conn:
        return {
            table.name: sorted(tuple(row) for row in conn.execute(select(table)))
            for table in Base.metadata.sorted_tables
        }


def test_every_table_is_copied_as_it_was(tmp_path):
    source = _filled_source(tmp_path / "source.db")
    target = _sqlite(tmp_path / "target.db")

    counts = db_copy.copy_database(source, target)

    assert counts["users"] == 2 and counts["analysis_records"] == 1 and counts["game_syncs"] == 1
    assert _rows(target) == _rows(source)


def test_a_target_with_data_is_refused(tmp_path):
    source = _filled_source(tmp_path / "source.db")
    target = _filled_source(tmp_path / "target.db")

    with pytest.raises(db_copy.CopyError, match="already has data"):
        db_copy.copy_database(source, target)


def test_a_failed_copy_leaves_the_target_empty(tmp_path, monkeypatch):
    source = _filled_source(tmp_path / "source.db")
    target = _sqlite(tmp_path / "target.db")
    real_select = db_copy.select

    def failing_select(table):
        if table.name == "puzzle_attempts":  # after users and records went in
            raise RuntimeError("disk full")
        return real_select(table)

    monkeypatch.setattr(db_copy, "select", failing_select)
    with pytest.raises(RuntimeError):
        db_copy.copy_database(source, target)

    with target.connect() as conn:
        assert conn.execute(real_select(func.count()).select_from(User.__table__)).scalar() == 0


def test_the_command_wants_a_postgres_url_and_a_source(tmp_path):
    runner = CliRunner()

    bad_url = runner.invoke(cli, ["copy-to-postgres", "sqlite:///x.db"])
    no_source = runner.invoke(cli, ["copy-to-postgres", "postgresql://u@h/db"])

    assert bad_url.exit_code == 1 and "must be a PostgreSQL URL" in bad_url.output
    assert no_source.exit_code == 1 and "no database to copy" in no_source.output


@needs_postgres
def test_the_command_copies_into_postgres_and_ids_carry_on(tmp_path):
    _filled_source(tmp_path / "source.db").dispose()

    result = CliRunner().invoke(cli, ["copy-to-postgres", TEST_DATABASE_URL, "--from", str(tmp_path / "source.db")])

    assert result.exit_code == 0, result.output
    assert "users: 2" in result.output
    assert "CHESSWHISPER_DATABASE_URL=postgresql://" in result.output
    from chesswhisper.web.db import postgres_url

    target = create_engine_for(postgres_url(TEST_DATABASE_URL))
    try:
        assert _rows(target) == _rows(_sqlite(tmp_path / "source.db"))
        with Session(target) as db:
            carol = User(username="carol", password_hash="h")
            db.add(carol)
            db.commit()
            assert carol.id == 3
    finally:
        target.dispose()

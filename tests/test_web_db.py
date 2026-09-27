import pytest

from chesswhisper.web.db import get_db, init_db
from chesswhisper.web.models import User


@pytest.mark.sqlite_only
def test_init_db_creates_tables_and_supports_crud(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    db_gen = get_db()
    db = next(db_gen)
    try:
        db.add(User(username="alice", password_hash="hash", is_admin=False))
        db.commit()

        found = db.query(User).filter_by(username="alice").one()
        assert found.username == "alice"
        assert found.is_admin is False
        assert found.created_at is not None
    finally:
        db_gen.close()

    assert db_path.exists()


# --- PostgreSQL through CHESSWHISPER_DATABASE_URL ----------------------------


def test_without_a_database_url_the_sqlite_file_is_used(monkeypatch):
    from chesswhisper.web import db as web_db

    monkeypatch.delenv(web_db.DATABASE_URL_ENV, raising=False)

    assert web_db.database_url_from_env() is None


@pytest.mark.parametrize("raw", [
    "postgres://u:p@db.example:5432/cw",
    "postgresql://u:p@db.example:5432/cw",
    "postgresql+psycopg://u:p@db.example:5432/cw",
])
def test_postgres_urls_use_the_psycopg_driver(monkeypatch, raw):
    from chesswhisper.web import db as web_db

    monkeypatch.setenv(web_db.DATABASE_URL_ENV, raw)
    url = web_db.database_url_from_env()

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("db.example", 5432, "cw", "u")


@pytest.mark.parametrize("raw", ["sqlite:///x.db", "mysql://u@h/db", "not a url"])
def test_other_database_urls_are_refused(monkeypatch, raw):
    from chesswhisper.web import db as web_db

    monkeypatch.setenv(web_db.DATABASE_URL_ENV, raw)

    with pytest.raises(ValueError, match="CHESSWHISPER_DATABASE_URL"):
        web_db.database_url_from_env()


@pytest.mark.sqlite_only
def test_an_explicit_path_wins_over_the_database_url(tmp_path, monkeypatch):
    from chesswhisper.web import db as web_db

    monkeypatch.setenv(web_db.DATABASE_URL_ENV, "postgresql://u:p@db.example/cw")
    engine = web_db.get_engine(tmp_path / "explicit.db")

    assert web_db.is_sqlite(engine)
    engine.dispose()


def test_the_migrated_schema_matches_the_models_on_the_database_in_use(tmp_path):
    """Checked on whichever backend the tests run against (SQLite, or PostgreSQL in its mode)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from chesswhisper.web import db as web_db
    from chesswhisper.web.db import Base

    init_db(tmp_path / "in_use.db")

    with web_db._engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []

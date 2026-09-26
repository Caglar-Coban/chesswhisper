from __future__ import annotations

import threading
from collections.abc import Generator
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from chesswhisper.paths import data_dir

# Revision that reproduces the schema the pre-Alembic create_all() built.
# Databases created before migrations existed are stamped at this revision.
BASELINE_REVISION = "0001_baseline"


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def _default_db_path() -> Path:
    return data_dir() / "chesswhisper.db"


def get_engine(db_path: Path | None = None) -> Engine:
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


def migrations_dir() -> Path:
    return Path(str(files("chesswhisper.web") / "migrations"))


def _migration_engine(engine: Engine) -> Engine:
    """A private engine on *engine*'s database whose transactions cover DDL.

    Python's sqlite3 driver never emits BEGIN before DDL, so every CREATE or
    ALTER a migration runs would autocommit and survive a failed migration.
    Taking over transaction control (SQLAlchemy's documented pysqlite
    recipe) makes a failed migration roll back completely; BEGIN IMMEDIATE
    also makes a second process starting at the same time wait for the
    first one's migration instead of racing it.
    """
    migration_engine = create_engine(
        engine.url, connect_args={"check_same_thread": False}, poolclass=NullPool
    )

    @event.listens_for(migration_engine, "connect")
    def _disable_driver_transactions(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(migration_engine, "begin")
    def _begin_immediate(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    return migration_engine


def run_migrations(engine: Engine) -> None:
    """Bring the database behind *engine* to the latest schema revision.

    A database created by the pre-Alembic create_all() has the tables but
    no alembic_version table; it is stamped at the baseline first so the
    baseline migration doesn't try to recreate tables that already exist.
    The whole run is one transaction: if any migration fails, the database
    is left exactly as it was.
    """
    migration_engine = _migration_engine(engine)
    try:
        with migration_engine.begin() as connection:
            config = Config()
            config.set_main_option("script_location", str(migrations_dir()))
            config.attributes["connection"] = connection
            tables = inspect(connection).get_table_names()
            if "users" in tables and "alembic_version" not in tables:
                command.stamp(config, BASELINE_REVISION)
            command.upgrade(config, "head")
    finally:
        migration_engine.dispose()


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
# Alembic's command API is not thread-safe, and the first requests after
# startup can all reach get_db()'s lazy init at once.
_init_lock = threading.Lock()


def _init_db(db_path: Path | None) -> None:
    global _engine, _SessionLocal
    _engine = get_engine(db_path)
    run_migrations(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def init_db(db_path: Path | None = None) -> None:
    with _init_lock:
        _init_db(db_path)


def get_db() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        with _init_lock:
            if _SessionLocal is None:
                _init_db(None)
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()

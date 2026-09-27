from __future__ import annotations

import os
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, make_url
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from chesswhisper.paths import data_dir

# Revision that reproduces the schema the pre-Alembic create_all() built.
# Databases created before migrations existed are stamped at this revision.
BASELINE_REVISION = "0001_baseline"

# A PostgreSQL database to use instead of the SQLite file in the data directory.
DATABASE_URL_ENV = "CHESSWHISPER_DATABASE_URL"
# Any fixed number: the advisory lock that keeps two processes from migrating at once.
_MIGRATION_LOCK_ID = 7_415_311


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def _default_db_path() -> Path:
    return data_dir() / "chesswhisper.db"


def database_url_from_env() -> URL | None:
    """CHESSWHISPER_DATABASE_URL as a SQLAlchemy URL, or None when unset.

    Only PostgreSQL is accepted; postgres:// and postgresql:// (as hosting
    services hand them out) use the psycopg 3 driver.
    """
    raw = os.environ.get(DATABASE_URL_ENV, "").strip()
    if not raw:
        return None
    try:
        url = make_url(raw)
    except Exception as exc:
        raise ValueError(f"{DATABASE_URL_ENV} is not a database URL.") from exc
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"{DATABASE_URL_ENV} must be a PostgreSQL URL (postgresql://user:password@host/db).")
    return url


def is_sqlite(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def _create_engine(url: URL | str, **kwargs) -> Engine:
    if make_url(url).get_backend_name() == "sqlite":
        return create_engine(url, connect_args={"check_same_thread": False}, **kwargs)
    # Timestamps are stored without a zone and meant as UTC, whatever the server's zone is.
    # An offset rather than "UTC", which a server built without time zone data rejects.
    return create_engine(url, connect_args={"options": "-c timezone=+00:00"}, pool_pre_ping=True, **kwargs)


def get_engine(db_path: Path | None = None) -> Engine:
    """The engine for *db_path*, else CHESSWHISPER_DATABASE_URL, else the data directory's SQLite file."""
    url = database_url_from_env() if db_path is None else None
    if url is not None:
        return _create_engine(url)
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return _create_engine(f"sqlite:///{path}")


def migrations_dir() -> Path:
    return Path(str(files("chesswhisper.web") / "migrations"))


def _migration_engine(engine: Engine) -> Engine:
    """A private engine on *engine*'s database whose transactions cover DDL.

    PostgreSQL's DDL is transactional already. Python's sqlite3 driver never emits BEGIN before DDL, so every CREATE or
    ALTER a migration runs would autocommit and survive a failed migration.
    Taking over transaction control (SQLAlchemy's documented pysqlite
    recipe) makes a failed migration roll back completely; BEGIN IMMEDIATE
    also makes a second process starting at the same time wait for the
    first one's migration instead of racing it.
    """
    migration_engine = _create_engine(engine.url, poolclass=NullPool)
    if not is_sqlite(engine):
        return migration_engine

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
    is left exactly as it was. Processes starting together take turns.
    """
    migration_engine = _migration_engine(engine)
    try:
        with migration_engine.begin() as connection:
            if not is_sqlite(migration_engine):
                connection.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_MIGRATION_LOCK_ID})")
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


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session for code outside a request: commits on success, rolls back on error."""
    db_gen = get_db()
    db = next(db_gen)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db_gen.close()

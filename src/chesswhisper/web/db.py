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

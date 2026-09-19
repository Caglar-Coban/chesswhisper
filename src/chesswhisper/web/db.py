from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

from platformdirs import user_data_dir
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


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


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(db_path: Path | None = None) -> None:
    global _engine, _SessionLocal
    _engine = get_engine(db_path)
    from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata

    Base.metadata.create_all(_engine)
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

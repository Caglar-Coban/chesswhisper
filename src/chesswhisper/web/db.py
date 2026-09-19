from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from platformdirs import user_data_dir
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_db_path() -> Path:
    return Path(user_data_dir("chesswhisper")) / "chesswhisper.db"


def get_engine(db_path: Path | None = None):
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})


_engine = None
_SessionLocal = None


def init_db(db_path: Path | None = None) -> None:
    global _engine, _SessionLocal
    _engine = get_engine(db_path)
    from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata

    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    if _SessionLocal is None:
        init_db()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chesswhisper.web.db import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(default=None)

    api_key: Mapped[UserApiKey | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    analyses: Mapped[list[AnalysisRecord]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserApiKey(Base):
    __tablename__ = "user_api_keys"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    anthropic_key_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    openai_key_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="api_key")


class AnalysisRecord(Base):
    __tablename__ = "analysis_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    player_name: Mapped[str] = mapped_column(String(200))
    lang: Mapped[str] = mapped_column(String(8))
    llm_used: Mapped[str] = mapped_column(String(20))
    games_count: Mapped[int]
    overall_acpl: Mapped[float]
    overall_accuracy: Mapped[float]
    report_markdown: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="analyses")

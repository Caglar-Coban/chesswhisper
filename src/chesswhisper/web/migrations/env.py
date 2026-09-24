"""Alembic environment for chesswhisper's web database.

At runtime chesswhisper.web.db.run_migrations() hands this script an open
connection via config.attributes["connection"]. From the command line
(`alembic ...` in the repo root) there is no connection, so one is built
from alembic.ini's sqlalchemy.url -- deliberately never the app's real
database, so developer commands can't touch a user's data.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata
from chesswhisper.web.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("set sqlalchemy.url in alembic.ini to run Alembic from the command line")
    return url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, render_as_batch=True, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with create_engine(_url()).connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

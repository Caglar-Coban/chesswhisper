"""Copy the web UI's SQLite database into an empty PostgreSQL database.

For moving an existing install to CHESSWHISPER_DATABASE_URL: the source is
brought to the latest schema, the target gets the same schema through the
migrations, then every table is copied in foreign-key order inside one
transaction on the target, so a failure leaves it empty. PostgreSQL's id
sequences are moved past the copied ids. The source is only read.
"""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

from chesswhisper.web import models  # noqa: F401 -- registers tables on Base.metadata
from chesswhisper.web.db import Base, is_sqlite, run_migrations

BATCH_ROWS = 500


class CopyError(Exception):
    """The copy can't go ahead; the message is for the user."""


def copy_database(source: Engine, target: Engine) -> dict[str, int]:
    """Copy every table from *source* into the empty database behind *target*; returns rows per table."""
    run_migrations(source)
    run_migrations(target)
    tables = Base.metadata.sorted_tables  # parents before children
    with target.connect() as conn:
        filled = [table.name for table in tables if conn.execute(select(func.count()).select_from(table)).scalar()]
    if filled:
        raise CopyError(
            f"The target database already has data (in {', '.join(filled)}); copy only into an empty one."
        )

    counts: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        for table in tables:
            counts[table.name] = 0
            result = src.execution_options(stream_results=True).execute(select(table))
            while batch := result.fetchmany(BATCH_ROWS):
                dst.execute(table.insert(), [dict(row._mapping) for row in batch])
                counts[table.name] += len(batch)
        if not is_sqlite(target):
            _move_sequences(dst, tables)
    return counts


def _move_sequences(conn, tables) -> None:
    """Rows were inserted with their ids, so each id sequence must continue after the largest."""
    for table in tables:
        for column in table.primary_key.columns:
            if not column.autoincrement or column.type.python_type is not int:
                continue
            sequence = conn.execute(
                text("SELECT pg_get_serial_sequence(:table, :column)"), {"table": table.name, "column": column.name}
            ).scalar()
            if sequence is None:
                continue
            conn.execute(
                text(f"SELECT setval(:sequence, COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, false)"),
                {"sequence": sequence},
            )

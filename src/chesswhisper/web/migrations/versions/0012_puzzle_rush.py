"""keep the results of timed puzzle rounds

Revision ID: 0012_puzzle_rush
Revises: 0011_unseen_analyses
Create Date: 2026-09-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_puzzle_rush"
down_revision: str | Sequence[str] | None = "0011_unseen_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "puzzle_rush_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("theme", sa.String(length=20), nullable=True),
        sa.Column("solved", sa.Integer(), nullable=False),
        sa.Column("misses", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("puzzle_rush_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_puzzle_rush_runs_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("puzzle_rush_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_puzzle_rush_runs_user_id"))
    op.drop_table("puzzle_rush_runs")

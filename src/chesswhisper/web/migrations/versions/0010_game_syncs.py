"""analyse new games from a linked account automatically

Revision ID: 0010_game_syncs
Revises: 0009_analysis_summary
Create Date: 2026-09-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_game_syncs"
down_revision: str | Sequence[str] | None = "0009_analysis_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_syncs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=10), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("seen_keys_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_new_games", sa.Integer(), nullable=False),
        sa.Column("last_job_id", sa.String(length=36), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("game_syncs")

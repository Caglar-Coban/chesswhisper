"""add puzzle_attempts

Revision ID: 0006_puzzle_attempts
Revises: 0005_api_tokens
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_puzzle_attempts"
down_revision: str | Sequence[str] | None = "0005_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "puzzle_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=False),
        sa.Column("move_index", sa.Integer(), nullable=False),
        sa.Column("move_uci", sa.String(length=5), nullable=False),
        sa.Column("solved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["record_id"], ["analysis_records.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("puzzle_attempts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_puzzle_attempts_record_id"), ["record_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_puzzle_attempts_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("puzzle_attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_puzzle_attempts_user_id"))
        batch_op.drop_index(batch_op.f("ix_puzzle_attempts_record_id"))
    op.drop_table("puzzle_attempts")

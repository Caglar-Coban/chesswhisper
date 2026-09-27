"""add analysis_jobs

Revision ID: 0007_analysis_jobs
Revises: 0006_puzzle_attempts
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_analysis_jobs"
down_revision: str | Sequence[str] | None = "0006_puzzle_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.Integer(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("pgn_text", sa.Text(), nullable=False),
        sa.Column("player", sa.String(length=200), nullable=True),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("llm", sa.String(length=20), nullable=False),
        sa.Column("ui_language", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("analysis_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_analysis_jobs_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_analysis_jobs_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_analysis_jobs_user_id"))
        batch_op.drop_index(batch_op.f("ix_analysis_jobs_status"))
    op.drop_table("analysis_jobs")

"""baseline: users, user_api_keys, analysis_records

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "analysis_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("player_name", sa.String(length=200), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("llm_used", sa.String(length=20), nullable=False),
        sa.Column("games_count", sa.Integer(), nullable=False),
        sa.Column("overall_acpl", sa.Float(), nullable=False),
        sa.Column("overall_accuracy", sa.Float(), nullable=False),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_records_user_id"), "analysis_records", ["user_id"], unique=False)
    op.create_table(
        "user_api_keys",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("anthropic_key_encrypted", sa.Text(), nullable=True),
        sa.Column("openai_key_encrypted", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_api_keys")
    op.drop_index(op.f("ix_analysis_records_user_id"), table_name="analysis_records")
    op.drop_table("analysis_records")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")

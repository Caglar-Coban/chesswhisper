"""link several accounts per user: game_syncs becomes linked_accounts

Revision ID: 0013_linked_accounts
Revises: 0012_puzzle_rush
Create Date: 2026-09-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_linked_accounts"
down_revision: str | Sequence[str] | None = "0012_puzzle_rush"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "user_id, platform, username, lang, seen_keys_json, created_at, last_checked_at, "
    "last_new_games, last_job_id, last_error"
)


def _account_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "linked_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        *_account_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", "username"),
    )
    with op.batch_alter_table("linked_accounts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_linked_accounts_user_id"), ["user_id"], unique=False)
    # Each user's one account so far comes along, with what it had already seen.
    op.execute(f"INSERT INTO linked_accounts ({_COLUMNS}) SELECT {_COLUMNS} FROM game_syncs")
    op.drop_table("game_syncs")


def downgrade() -> None:
    op.create_table(
        "game_syncs",
        *_account_columns(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )
    # One account per user again: the first each user linked.
    op.execute(
        f"INSERT INTO game_syncs ({_COLUMNS}) SELECT {_COLUMNS} FROM linked_accounts "
        "WHERE id IN (SELECT MIN(id) FROM linked_accounts GROUP BY user_id)"
    )
    with op.batch_alter_table("linked_accounts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_linked_accounts_user_id"))
    op.drop_table("linked_accounts")

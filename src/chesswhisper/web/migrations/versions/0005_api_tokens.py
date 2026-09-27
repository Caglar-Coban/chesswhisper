"""add users.api_token_hash

Revision ID: 0005_api_tokens
Revises: 0004_session_version
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_api_tokens"
down_revision: str | Sequence[str] | None = "0004_session_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_token_hash", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_users_api_token_hash"), ["api_token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_api_token_hash"))
        batch_op.drop_column("api_token_hash")

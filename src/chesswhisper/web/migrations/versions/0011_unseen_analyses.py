"""mark analyses the automatic sync added until they are looked at

Revision ID: 0011_unseen_analyses
Revises: 0010_game_syncs
Create Date: 2026-09-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_unseen_analyses"
down_revision: str | Sequence[str] | None = "0010_game_syncs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("unseen", sa.Boolean(), server_default=sa.false(), nullable=False))
    with op.batch_alter_table("analysis_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(length=10), server_default="manual", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs", schema=None) as batch_op:
        batch_op.drop_column("origin")
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.drop_column("unseen")

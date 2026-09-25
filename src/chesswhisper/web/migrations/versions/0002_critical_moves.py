"""add analysis_records.critical_moves_json

Revision ID: 0002_critical_moves
Revises: 0001_baseline
Create Date: 2026-09-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_critical_moves"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("critical_moves_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.drop_column("critical_moves_json")

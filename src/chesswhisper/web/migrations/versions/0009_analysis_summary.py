"""keep a summary of each analysis

Revision ID: 0009_analysis_summary
Revises: 0008_game_filters
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_analysis_summary"
down_revision: str | Sequence[str] | None = "0008_game_filters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("summary_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.drop_column("summary_json")

"""keep analyzed games and filters

Revision ID: 0008_game_filters
Revises: 0007_analysis_jobs
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_game_filters"
down_revision: str | Sequence[str] | None = "0007_analysis_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pgn_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("filters_json", sa.Text(), nullable=True))
    with op.batch_alter_table("analysis_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("filters_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs", schema=None) as batch_op:
        batch_op.drop_column("filters_json")
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.drop_column("filters_json")
        batch_op.drop_column("pgn_text")

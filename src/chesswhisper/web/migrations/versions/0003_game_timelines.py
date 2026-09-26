"""add analysis_records.games_json

Revision ID: 0003_game_timelines
Revises: 0002_critical_moves
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_game_timelines"
down_revision: str | Sequence[str] | None = "0002_critical_moves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("games_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("analysis_records", schema=None) as batch_op:
        batch_op.drop_column("games_json")

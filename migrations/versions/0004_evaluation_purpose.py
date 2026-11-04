"""evaluation run purpose (release gate vs. model comparison)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.add_column(sa.Column("purpose", sa.String(length=20), nullable=False, server_default="release"))


def downgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_column("purpose")

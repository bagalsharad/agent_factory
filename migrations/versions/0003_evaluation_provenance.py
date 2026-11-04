"""evaluation run provenance and regression baseline

Adds what each evaluation run evaluated (tool, component and prompt versions, environment) and which run of the
published version it was compared with. Additive: existing runs get an empty provenance and no baseline.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.add_column(sa.Column("provenance", JSON, nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("baseline_run_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_column("baseline_run_id")
        batch.drop_column("provenance")

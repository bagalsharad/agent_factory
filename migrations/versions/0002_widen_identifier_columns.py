"""widen correlation and audit resource identifier columns

PostgreSQL enforces VARCHAR lengths (SQLite does not): evaluation correlation ids and some audit resource ids
exceed 64 characters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("executions") as batch:
        batch.alter_column("correlation_id", type_=sa.String(length=200), existing_type=sa.String(length=64))
    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column("resource_id", type_=sa.String(length=200), existing_type=sa.String(length=64))


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column("resource_id", type_=sa.String(length=64), existing_type=sa.String(length=200))
    with op.batch_alter_table("executions") as batch:
        batch.alter_column("correlation_id", type_=sa.String(length=64), existing_type=sa.String(length=200))

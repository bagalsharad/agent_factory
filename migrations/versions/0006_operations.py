"""operations: execution trace ids, SLO definitions and cost budgets

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import agent_factory.storage.db

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = agent_factory.storage.db.UTCDateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("executions") as batch:
        batch.add_column(sa.Column("trace_id", sa.String(length=32), nullable=True))
        batch.create_index(batch.f("ix_executions_trace_id"), ["trace_id"], unique=False)
    op.create_table(
        "slo_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("objective", sa.Float(), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name"),
    )
    with op.batch_alter_table("slo_definitions") as batch:
        batch.create_index(batch.f("ix_slo_definitions_workspace_id"), ["workspace_id"], unique=False)
    op.create_table(
        "cost_budgets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("max_cost_usd", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("warn_at", sa.Float(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cost_budgets") as batch:
        batch.create_index(batch.f("ix_cost_budgets_workspace_id"), ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_table("cost_budgets")
    op.drop_table("slo_definitions")
    with op.batch_alter_table("executions") as batch:
        batch.drop_index(batch.f("ix_executions_trace_id"))
        batch.drop_column("trace_id")

"""agentic continuity engineering: intent ledger, step records, continuity assessments

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import agent_factory.storage.db

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
TS = agent_factory.storage.db.UTCDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "execution_intents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("intent", JSON, nullable=False),
        sa.Column("intent_hash", sa.String(length=64), nullable=False),
        sa.Column("goals_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "version"),
    )
    with op.batch_alter_table("execution_intents") as batch:
        batch.create_index(batch.f("ix_execution_intents_execution_id"), ["execution_id"], unique=False)
    op.create_table(
        "step_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=120), nullable=False),
        sa.Column("node_id", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("validity", sa.String(length=20), nullable=False),
        sa.Column("depends_on", JSON, nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column("environment", JSON, nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("output_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence", JSON, nullable=False),
        sa.Column("side_effects", JSON, nullable=False),
        sa.Column("invalidated_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("step_records") as batch:
        batch.create_index(batch.f("ix_step_records_execution_id"), ["execution_id"], unique=False)
        batch.create_index("ix_step_records_execution_step", ["execution_id", "step_id"], unique=False)
    op.create_table(
        "continuity_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column("environment", JSON, nullable=False),
        sa.Column("changes", JSON, nullable=False),
        sa.Column("affected_steps", JSON, nullable=False),
        sa.Column("findings", JSON, nullable=False),
        sa.Column("scores", JSON, nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("from_step", sa.String(length=120), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("continuity_assessments") as batch:
        batch.create_index(batch.f("ix_continuity_assessments_execution_id"), ["execution_id"], unique=False)


def downgrade() -> None:
    op.drop_table("continuity_assessments")
    op.drop_table("step_records")
    op.drop_table("execution_intents")

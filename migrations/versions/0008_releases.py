"""release artifacts, deployment environments and deployments

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import agent_factory.storage.db

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
TS = agent_factory.storage.db.UTCDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("agent_version_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("artifact", JSON, nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_version_id"),
    )
    with op.batch_alter_table("agent_artifacts") as batch:
        batch.create_index(batch.f("ix_agent_artifacts_workspace_id"), ["workspace_id"], unique=False)
        batch.create_index(batch.f("ix_agent_artifacts_agent_id"), ["agent_id"], unique=False)
    op.create_table(
        "deployment_environments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("promote_from", sa.String(length=40), nullable=True),
        sa.Column("config", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name"),
    )
    with op.batch_alter_table("deployment_environments") as batch:
        batch.create_index(batch.f("ix_deployment_environments_workspace_id"), ["workspace_id"], unique=False)
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("agent_version_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy", sa.String(length=10), nullable=False),
        sa.Column("traffic_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.String(length=36), nullable=True),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("previous_deployment_id", sa.String(length=36), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("activated_at", TS, nullable=True),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.id"]),
        sa.ForeignKeyConstraint(["environment_id"], ["deployment_environments.id"]),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deployments") as batch:
        batch.create_index("ix_deployments_env_agent", ["environment_id", "agent_id", "status"], unique=False)
        batch.create_index(batch.f("ix_deployments_workspace_id"), ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_table("deployments")
    op.drop_table("deployment_environments")
    op.drop_table("agent_artifacts")

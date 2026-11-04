"""dependency edges and operational ownership

Adds the dependency graph recorded at build time and ownership/support contacts for agents and components.
Additive; existing built versions are backfilled by the platform on start-up.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import agent_factory.storage.db

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dependency_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("agent_version_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=True),
        sa.Column("created_at", agent_factory.storage.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_version_id", "kind", "name"),
    )
    with op.batch_alter_table("dependency_edges") as batch:
        batch.create_index("ix_dependency_edges_target", ["workspace_id", "kind", "name"], unique=False)
        batch.create_index(batch.f("ix_dependency_edges_agent_id"), ["agent_id"], unique=False)
        batch.create_index(batch.f("ix_dependency_edges_agent_version_id"), ["agent_version_id"], unique=False)
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("business_owner", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("technical_contact", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("escalation_contact", sa.String(length=200), nullable=True))
    with op.batch_alter_table("components") as batch:
        batch.add_column(sa.Column("owner", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("components") as batch:
        batch.drop_column("owner")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("escalation_contact")
        batch.drop_column("technical_contact")
        batch.drop_column("business_owner")
    op.drop_table("dependency_edges")

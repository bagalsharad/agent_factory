"""MCP server registry and secret versions

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import agent_factory.storage.db

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
TS = agent_factory.storage.db.UTCDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("transport", sa.String(length=10), nullable=False),
        sa.Column("command", JSON, nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("auth_secret", sa.String(length=120), nullable=True),
        sa.Column("env_secrets", JSON, nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("max_output_chars", sa.Integer(), nullable=False),
        sa.Column("tool_allowlist", JSON, nullable=False),
        sa.Column("tool_settings", JSON, nullable=False),
        sa.Column("discovered", JSON, nullable=False),
        sa.Column("health", JSON, nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("secret_refs") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("secret_refs") as batch:
        batch.drop_column("version")
    op.drop_table("mcp_servers")

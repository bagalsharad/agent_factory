"""Alembic environment. The database URL comes from Agent Factory settings (AF_DATABASE_URL)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from agent_factory.core.config import get_settings
from agent_factory.storage import models  # noqa: F401  (registers tables)
from agent_factory.storage.db import Base

config = context.config
url = config.attributes.get("connection_url") or get_settings().resolved_database_url
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=connection.dialect.name == "sqlite"
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

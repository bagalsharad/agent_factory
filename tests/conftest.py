"""Shared fixtures. Every test platform uses an isolated temporary data directory and SQLite database.

All tests use the deterministic mock model unless marked ``ollama``.
"""

from __future__ import annotations

import atexit
import os
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_factory.core.config import PROJECT_ROOT, Settings
from agent_factory.governance.rbac import Principal, make_principal
from agent_factory.platform import Platform, create_platform
from agent_factory.spec.loader import load_spec
from agent_factory.spec.models import AgentSpec
from agent_factory.storage.models import Membership, User, Workspace

EXAMPLES = PROJECT_ROOT / "examples"


_CREATED_DATABASES: list[str] = []


def _fresh_postgres_url() -> str | None:
    """With AF_TEST_POSTGRES_ADMIN_URL set, every test platform gets its own new PostgreSQL database."""
    admin_url = os.environ.get("AF_TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        return None
    import psycopg

    name = f"af_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    _CREATED_DATABASES.append(name)
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


@atexit.register
def _drop_test_databases() -> None:
    admin_url = os.environ.get("AF_TEST_POSTGRES_ADMIN_URL")
    if not admin_url or not _CREATED_DATABASES:
        return
    import psycopg

    with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as conn:
        for name in _CREATED_DATABASES:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "log_json": False,
        "log_level": "WARNING",
        "ollama_enabled": False,  # never depend on a local server in default tests
        "worker_poll_interval_seconds": 0.05,
        "worker_concurrency": 2,
        "execution_rate_limit_per_minute": 1000,
    }
    if "database_url" not in overrides and (url := _fresh_postgres_url()):
        base["database_url"] = url
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def platform(settings: Settings) -> Iterator[Platform]:
    instance = create_platform(settings)
    yield instance
    instance.stop()


def add_user(platform: Platform, subject: str, role: str, workspace_slug: str = "default") -> Principal:
    with platform.db.transaction() as session:
        ws = session.scalars(select(Workspace).where(Workspace.slug == workspace_slug)).first()
        if ws is None:
            ws = Workspace(slug=workspace_slug, name=workspace_slug)
            session.add(ws)
            session.flush()
        user = platform.identity.ensure_user(session, subject, display_name=subject, role=role, workspace=ws)
        membership = session.scalars(
            select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws.id)
        ).first()
        assert membership is not None
        membership.role = role
        return make_principal(user.id, subject, subject, ws.id, role, "test")


@pytest.fixture
def admin(platform: Platform) -> Principal:
    with platform.db.session() as session:
        user = session.scalars(select(User).where(User.subject == "local-admin")).first()
        ws = session.scalars(select(Workspace).where(Workspace.slug == "default")).first()
    assert user and ws
    return make_principal(user.id, user.subject, user.display_name, ws.id, "admin", "local")


def example_spec(rel: str) -> AgentSpec:
    return load_spec((EXAMPLES / rel).read_text(encoding="utf-8"))


def create_built(platform: Platform, principal: Principal, spec: AgentSpec) -> str:
    agent = platform.agents.create(principal, spec)
    report = platform.agents.build(principal, agent["id"])
    assert report["success"], report["errors"]
    return str(agent["id"])


@pytest.fixture
def pipeline_agent(platform: Platform, admin: Principal) -> str:
    return create_built(platform, admin, example_spec("data-pipeline-diagnostic/agent.yaml"))


@pytest.fixture
def remediation_agent(platform: Platform, admin: Principal) -> str:
    return create_built(platform, admin, example_spec("pipeline-remediation/agent.yaml"))


def run(platform: Platform, principal: Principal, agent_id: str, data: dict[str, Any], **kw: Any) -> dict[str, Any]:
    execution = platform.executions.create(principal, agent_id, input_data=data, **kw)
    return platform.executions.run_now(execution["id"])


def wait_for(predicate: Callable[[], Any], timeout: float = 15.0, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met before timeout")


@pytest.fixture
def client(platform: Platform) -> Iterator[TestClient]:
    from agent_factory.api.app import create_app

    app = create_app(platform.settings, platform_instance=platform, start_workers=True)
    with TestClient(app) as test_client:
        yield test_client

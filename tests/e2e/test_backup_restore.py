"""Backup/restore drill (SQLite): back up, lose data, restore, and prove the data and audit chain are intact."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_factory.cli.main import app
from agent_factory.core.config import get_settings
from agent_factory.platform import create_platform
from agent_factory.storage.models import Agent
from tests.conftest import example_spec


def test_backup_and_restore_drill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AF_DATABASE_URL", f"sqlite:///{tmp_path / 'data' / 'agent_factory.db'}")
    monkeypatch.setenv("AF_OLLAMA_ENABLED", "false")
    get_settings.cache_clear()
    try:
        platform = create_platform(get_settings())
        from agent_factory.governance.rbac import make_principal
        from agent_factory.storage.models import User, Workspace

        with platform.db.session() as session:
            user = session.query(User).filter_by(subject="local-admin").one()
            ws = session.query(Workspace).filter_by(slug="default").one()
        admin = make_principal(user.id, user.subject, user.display_name, ws.id, "admin", "local")
        platform.agents.create(admin, example_spec("data-pipeline-diagnostic/agent.yaml"))
        before = platform.audit.verify()
        platform.stop()

        backup = tmp_path / "backup.db"
        result = CliRunner().invoke(app, ["db", "backup", "--output", str(backup)])
        assert result.exit_code == 0 and backup.exists(), result.output

        platform = create_platform(get_settings())  # disaster: the agent is deleted
        with platform.db.transaction() as session:
            session.query(Agent).delete()
        platform.stop()

        assert CliRunner().invoke(app, ["db", "restore", str(backup), "--yes"]).exit_code == 0
        platform = create_platform(get_settings())
        with platform.db.session() as session:
            assert [a.name for a in session.query(Agent).all()] == ["data-pipeline-diagnostic"]
        after = platform.audit.verify()
        assert after["valid"] and after["events_checked"] >= before["events_checked"]
        platform.stop()
    finally:
        get_settings.cache_clear()

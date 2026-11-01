"""Opt-in tests against real infrastructure. Skipped unless explicitly configured.

* Ollama:     AF_TEST_OLLAMA_MODEL=qwen3:8b (server at AF_OLLAMA_BASE_URL or http://127.0.0.1:11434)
* PostgreSQL: AF_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/agent_factory_test
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from tests.conftest import add_user, create_built, example_spec, make_settings, run


def _ollama_ready() -> bool:
    model = os.environ.get("AF_TEST_OLLAMA_MODEL")
    base = os.environ.get("AF_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    if not model:
        return False
    try:
        names = [m["name"] for m in httpx.get(f"{base}/api/tags", timeout=2).json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        return False
    return model in names or f"{model}:latest" in names


@pytest.mark.ollama
@pytest.mark.skipif(not _ollama_ready(), reason="set AF_TEST_OLLAMA_MODEL to an installed Ollama model")
def test_pipeline_agent_with_real_local_model(tmp_path: Path) -> None:
    from agent_factory.platform import create_platform

    platform = create_platform(make_settings(tmp_path, ollama_enabled=True))
    try:
        admin = add_user(platform, "root", "admin")
        spec = example_spec("data-pipeline-diagnostic/agent.yaml")
        spec.spec.model.provider = "ollama"
        spec.spec.model.model = os.environ["AF_TEST_OLLAMA_MODEL"]
        spec.spec.execution_limits.timeout_seconds = 600
        agent_id = create_built(platform, admin, spec)
        result = run(platform, admin, agent_id, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
        assert result["status"] in {"completed", "failed"}  # real models may legitimately fail validation
        assert result["budget_usage"]["model_calls"] >= 1
        print(f"\n[live/ollama] status={result['status']} usage={result['budget_usage']}")
    finally:
        platform.stop()


@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("AF_TEST_DATABASE_URL"), reason="set AF_TEST_DATABASE_URL to a PostgreSQL database")
def test_core_flow_on_postgresql(tmp_path: Path) -> None:
    from agent_factory.platform import create_platform

    platform = create_platform(make_settings(tmp_path, database_url=os.environ["AF_TEST_DATABASE_URL"]))
    try:
        assert platform.db.dialect == "postgresql"
        assert platform.migration_status()["up_to_date"]
        admin = add_user(platform, f"pg-admin-{os.getpid()}", "admin", workspace_slug=f"pg-{os.getpid()}")
        spec = example_spec("pipeline-remediation/agent.yaml")
        spec.metadata.name = f"pg-remediation-{os.getpid()}"
        agent_id = create_built(platform, admin, spec)
        paused = run(platform, admin, agent_id, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
        assert paused["status"] == "waiting_approval"
        approval = platform.approvals.list(admin, status="pending", execution_id=paused["id"])["items"][0]
        platform.approvals.decide(admin, approval["id"], "approved")
        assert platform.executions.run_now(paused["id"])["status"] == "completed"
        assert platform.audit.verify()["valid"]
    finally:
        platform.stop()

"""Release artifacts and deployments: integrity, tightening-only environment config, routing rules."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_factory.core.errors import InvalidStateError, ValidationFailedError
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from agent_factory.registry.releases import ReleaseService
from agent_factory.spec.models import ExecutionLimits
from agent_factory.storage.models import AgentArtifact

INPUT = {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}


@pytest.fixture
def published(platform: Platform, admin: Principal, pipeline_agent: str) -> str:
    platform.evaluations.start_run(admin, pipeline_agent, wait=True)
    platform.agents.publish(admin, pipeline_agent)
    return pipeline_agent


def test_tampered_artifact_cannot_be_deployed_or_run(platform: Platform, admin: Principal, published: str) -> None:
    platform.releases.deploy(admin, published, "1.0.0", "development")
    with platform.db.transaction() as session:
        record = session.query(AgentArtifact).one()
        record.artifact = {**record.artifact, "policy_hash": "forged"}
    assert not platform.releases.artifact(admin, published, "1.0.0")["verification"]["valid"]
    with pytest.raises(InvalidStateError, match="integrity"):
        platform.releases.deploy(admin, published, "1.0.0", "test")
    with pytest.raises(InvalidStateError, match="integrity"):
        platform.executions.create(admin, published, input_data=INPUT, environment="development")


def test_environment_limits_only_tighten(platform: Platform, admin: Principal, published: str) -> None:
    limits = ExecutionLimits(max_steps=20, max_tool_calls=30, max_cost_usd=None)
    assert ReleaseService.tightened_limits(limits, {"max_steps": 50, "max_tool_calls": 5, "max_cost_usd": 1.0,
                                                    "timeout_seconds": True}) == {
        "max_steps": 20, "max_tool_calls": 5, "max_cost_usd": 1.0}
    platform.releases.configure_environment(admin, "development", limits={"max_tool_calls": 4})
    with pytest.raises(ValidationFailedError):
        platform.releases.configure_environment(admin, "development", limits={"unknown_limit": 1})
    platform.releases.deploy(admin, published, "1.0.0", "development")
    execution = platform.executions.create(admin, published, input_data=INPUT, environment="development")
    assert execution["config_snapshot"]["deployment"]["limits"] == {"max_tool_calls": 4}
    with pytest.raises(ValidationFailedError, match="either a version or an environment"):
        platform.executions.create(admin, published, input_data=INPUT, environment="development", version="1.0.0")


def test_deployment_rules(client: TestClient, platform: Platform, admin: Principal, published: str) -> None:
    with pytest.raises(InvalidStateError, match="no active deployment"):
        platform.executions.create(admin, published, input_data=INPUT, environment="test")
    with pytest.raises(ValidationFailedError, match="1-50%"):
        platform.releases.deploy(admin, published, "1.0.0", "test", strategy="canary", traffic_percent=80)
    platform.releases.deploy(admin, published, "1.0.0", "test")
    with pytest.raises(InvalidStateError, match="Nothing to roll back"):
        platform.releases.rollback(admin, published, "test")
    response = client.post(f"/api/v1/agents/{published}/deployments", json={"version": "1.0.0", "environment": "staging"})
    assert response.status_code == 201 and response.json()["status"] == "active"
    envs = {e["name"]: e for e in client.get("/api/v1/environments").json()["items"]}
    assert envs["production"]["requires_approval"] and envs["production"]["promote_from"] == "staging"
    status = client.get(f"/api/v1/agents/{published}/deployments").json()
    assert {e["environment"]: (e["primary"] or {}).get("version") for e in status["environments"]} == {
        "development": None, "test": "1.0.0", "staging": "1.0.0", "production": None}


def test_versions_published_before_releases_get_artifacts(platform: Platform, admin: Principal, published: str) -> None:
    with platform.db.transaction() as session:
        session.query(AgentArtifact).delete()
    assert platform.releases.backfill() == 1
    assert platform.releases.artifact(admin, published, "1.0.0")["verification"]["valid"]
    assert platform.releases.backfill() == 0

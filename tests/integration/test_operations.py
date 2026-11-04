"""Operations: metrics, SLOs and error budgets, drift, cost budgets, alerts, agent health and tracing."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_factory.core.errors import BudgetExceededError
from agent_factory.core.ids import utcnow
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from agent_factory.storage.models import Agent, Execution
from tests.conftest import run

PIPELINE_INPUT = {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}


def record(platform: Platform, agent_id: str, status: str = "completed", *, seconds: float = 1.0,
           hours_ago: float = 1.0, tokens: int = 100, cost: float = 0.0, mode: str = "standard") -> None:
    with platform.db.transaction() as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None and agent.active_version_id
        when = utcnow() - timedelta(hours=hours_ago)
        session.add(Execution(
            workspace_id=agent.workspace_id, agent_id=agent_id, agent_version_id=agent.active_version_id, mode=mode,
            status=status, input={}, created_at=when, started_at=when, finished_at=when,
            budget_usage={"active_seconds": seconds, "input_tokens": tokens, "output_tokens": 0, "cost_usd": cost,
                          "retries": 1 if status == "failed" else 0},
            error={"code": "step_failed"} if status == "failed" else None,
        ))


@pytest.fixture
def published(platform: Platform, admin: Principal, pipeline_agent: str) -> str:
    platform.evaluations.start_run(admin, pipeline_agent, wait=True)
    platform.agents.publish(admin, pipeline_agent)
    return pipeline_agent


def test_metrics_cover_production_traffic_only(platform: Platform, admin: Principal, published: str) -> None:
    result = run(platform, admin, published, PIPELINE_INPUT)
    assert result["status"] == "completed"
    metrics = platform.operations.metrics(admin, hours=1)
    workspace = metrics["workspace"]
    assert workspace["executions"] == 1 and workspace["success_rate"] == 1.0  # evaluation executions excluded
    assert workspace["tools"]["read_pipeline_logs"]["success_rate"] == 1.0 and workspace["p95_latency_ms"]
    [row] = metrics["agents"]
    assert row["agent"] == "data-pipeline-diagnostic" and row["evaluation_pass_rate"] == 1.0


def test_slo_error_budget_and_alerts(platform: Platform, admin: Principal, published: str) -> None:
    for _ in range(9):
        record(platform, published)
    record(platform, published, "failed")
    slo = platform.operations.create_slo(admin, name="pipeline-success", metric="success_rate", objective=0.9,
                                         agent_id=published, window_hours=24)
    assert slo["value"] == 0.9 and slo["compliant"] is True and slo["error_budget_remaining"] == 0.0
    assert any(a["kind"] == "error_budget_low" for a in platform.operations.alerts(admin))

    record(platform, published, "failed")
    [status] = platform.operations.slo_status(admin)
    assert status["compliant"] is False and status["error_budget_remaining"] < 0
    alerts = platform.operations.alerts(admin)
    assert any(a["kind"] == "slo_breached" and a["severity"] == "critical" for a in alerts)
    assert platform.operations.agent_health(admin, published)["status"] == "unhealthy"

    latency = platform.operations.create_slo(admin, name="fast", metric="p95_latency_ms", objective=500,
                                             agent_id=published, window_hours=24)
    assert latency["value"] == 1000.0 and latency["compliant"] is False


def test_drift_between_windows(platform: Platform, admin: Principal, published: str) -> None:
    for _ in range(5):
        record(platform, published, seconds=1.0, hours_ago=30, tokens=100)
        record(platform, published, seconds=3.0, hours_ago=2, tokens=100)
    findings = {f["metric"]: f for f in platform.operations.drift(admin, hours=24)}
    assert findings["p95_latency_ms"]["change"] == 2.0 and "tokens_per_execution" not in findings
    assert any(a["kind"] == "drift" for a in platform.operations.alerts(admin))


def test_cost_budget_blocks_new_executions(platform: Platform, admin: Principal, published: str) -> None:
    budget = platform.operations.create_budget(admin, period="day", max_tokens=250, agent_id=published)
    assert budget["state"] == "ok"
    record(platform, published, tokens=200, hours_ago=0)
    assert platform.operations.budget_status(admin)[0]["state"] == "warning"
    record(platform, published, tokens=100, hours_ago=0)
    with pytest.raises(BudgetExceededError) as exceeded:
        platform.executions.create(admin, published, input_data=PIPELINE_INPUT)
    assert exceeded.value.details["limit"] == "agent_day_max_tokens"
    # evaluations still run so a fix can be validated; a warn-only budget never blocks
    assert platform.evaluations.start_run(admin, published, wait=True)["status"] == "completed"
    platform.operations.delete_budget(admin, budget["id"])
    platform.operations.create_budget(admin, period="day", max_tokens=1, action="warn")
    assert platform.executions.create(admin, published, input_data=PIPELINE_INPUT)["status"] == "queued"


def test_operations_api(client: TestClient, published: str) -> None:
    providers = client.get("/api/v1/operations/providers").json()
    assert any(p["provider"] == "mock" and p["circuit"]["state"] == "closed" for p in providers["providers"])
    created = client.post("/api/v1/operations/slos", json={"name": "ok", "metric": "success_rate", "objective": 0.95})
    assert created.status_code == 201
    assert client.post("/api/v1/operations/slos", json={"name": "bad", "metric": "success_rate",
                                                        "objective": 95}).status_code == 422
    health = client.get(f"/api/v1/agents/{published}/health").json()
    assert health["status"] == "unknown" and health["metrics"]["executions"] == 0
    assert client.get("/api/v1/operations/alerts").json()["critical"] == 0


def test_executions_are_traced(platform: Platform, admin: Principal, published: str) -> None:
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):  # telemetry not configured in this process yet
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    result = run(platform, admin, published, PIPELINE_INPUT)
    execution = platform.executions.get(admin, result["id"])
    assert execution["trace_id"] and len(execution["trace_id"]) == 32
    spans: dict[str, Any] = {s.name: s for s in exporter.get_finished_spans()}
    root = spans["agent.execution"]
    assert format(root.context.trace_id, "032x") == execution["trace_id"]
    assert root.attributes["execution_id"] == result["id"] and root.attributes["workspace_id"] == admin.workspace_id
    assert root.attributes["agent_version_id"] and root.attributes["model"] == "mock/mock-default"
    assert {"model.call", "tool.call", "workflow.node"} <= set(spans)


def test_timeseries_buckets_executions_by_outcome(platform: Platform, admin: Principal, published: str) -> None:
    record(platform, published, hours_ago=0.5, tokens=10)
    record(platform, published, hours_ago=0.5, tokens=10)
    record(platform, published, "failed", hours_ago=5)
    record(platform, published, hours_ago=30)  # outside the window
    series = platform.operations.timeseries(admin, hours=24, buckets=24)
    items = series["items"]
    assert len(items) == 24 and series["bucket_minutes"] == 60.0
    assert sum(i["completed"] for i in items) == 2 and sum(i["failed"] for i in items) == 1
    assert items[-1]["completed"] == 2 and items[-1]["tokens"] == 20 and items[-1]["p95_latency_ms"] == 1000.0

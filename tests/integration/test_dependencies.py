"""Dependency graph: edges recorded at build, reverse queries, impact analysis, drift, pinning and revalidation."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import delete

from agent_factory.core.errors import InvalidStateError
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from agent_factory.registry.components import ComponentCreate
from agent_factory.spec.loader import load_spec
from agent_factory.spec.models import AgentSpec
from agent_factory.storage.models import DependencyEdge
from agent_factory.tools.base import Tool, ToolContext
from tests.conftest import create_built, run


class QuoteInput(BaseModel):
    item: str


def quote_tool(version: str, price: int) -> Tool:
    class Quote(Tool):
        name = "price_quote"
        description = "Price for an item (test tool)"
        Input = QuoteInput

        def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
            return {"price": price}

    Quote.version = version
    return Quote()


def quoting_spec(name: str = "quoter", tool_version: str | None = None, components: list[Any] | None = None) -> AgentSpec:
    return load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": name, "version": "1.0.0"},
        "spec": {
            "objective": "Quote prices for items.",
            "inputs": {"type": "object", "required": ["item"], "properties": {"item": {"type": "string"}}},
            "outputs": {"type": "object"},
            "tools": [{"name": "price_quote", "version": tool_version}] if tool_version else ["price_quote"],
            "components": components or [],
            "workflow": {"type": "graph", "steps": [
                {"id": "quote", "kind": "tool", "tool": "price_quote", "arguments": {"item": "{{ input.item }}"},
                 "final": True},
                {"id": "explain", "kind": "model", "prompt": "Explain the quote."}]},
            "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"item": "book"}}]},
        },
    })


@pytest.fixture(autouse=True)
def quote_v1(platform: Platform) -> None:
    platform.tools.register(quote_tool("1.0.0", 10))


def publish(platform: Platform, principal: Principal, spec: AgentSpec) -> str:
    agent_id = create_built(platform, principal, spec)
    assert platform.evaluations.start_run(principal, agent_id, wait=True)["passed"]
    platform.agents.publish(principal, agent_id)
    return agent_id


def test_build_records_resolved_dependencies(client: TestClient, platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    deps = client.get(f"/api/v1/agents/{pipeline_agent}/dependencies").json()
    by_kind = {(d["kind"], d["name"]): d for d in deps["items"]}
    assert by_kind[("tool", "read_pipeline_logs")]["version"] == "1.0.0"
    assert ("model", "mock/mock-default") in by_kind and deps["problems"] == []

    users = client.get("/api/v1/dependencies/dependents", params={"kind": "tool", "name": "read_pipeline_logs"}).json()
    assert users["total"] == 1 and users["items"][0]["agent"] == "data-pipeline-diagnostic"

    with platform.db.transaction() as session:  # versions built before the graph existed are backfilled
        session.execute(delete(DependencyEdge))
    assert platform.dependencies.backfill() >= 1
    assert platform.dependencies.dependents(admin, "tool", "read_pipeline_logs")


def test_impact_report_and_revalidation(platform: Platform, admin: Principal) -> None:
    agent_id = publish(platform, admin, quoting_spec())
    platform.agents.set_ownership(admin, agent_id, {"technical_contact": "data-platform@example.com",
                                                    "business_owner": "Sales Ops"})
    report = platform.dependencies.impact(admin, "tool", "price_quote", to_version="2.0.0", change="new pricing API")
    [item] = report["affected_versions"]
    assert item["compatibility"] == "breaking" and item["risk"] == "high" and item["requires_revalidation"]
    assert item["last_validated_at"] and report["in_service"] == ["quoter@1.0.0"]
    assert report["notify"] == ["Sales Ops", "data-platform@example.com"]
    assert platform.dependencies.impact(admin, "tool", "price_quote", to_version="1.1.0")["affected_versions"][0][
        "compatibility"] == "compatible"

    revalidation = platform.dependencies.revalidate(admin, "tool", "price_quote")
    [started] = revalidation["runs"]
    assert started["version"] == "1.0.0" and started["run_id"]


def test_registry_drift_is_visible_and_pinned_versions_are_honoured(platform: Platform, admin: Principal) -> None:
    floating = publish(platform, admin, quoting_spec("floating"))
    pinned = publish(platform, admin, quoting_spec("pinned", tool_version="1.0.0"))
    platform.tools.register(quote_tool("1.1.0", 99))  # a newer tool version appears in the registry

    [drift] = [d for d in platform.dependencies.dependencies(admin, floating, _active(platform, admin, floating))["items"]
               if d["kind"] == "tool"]
    assert drift["state"] == "drifted" and "registry now 1.1.0" in drift["detail"]
    # A pinned version keeps running the version it was evaluated with.
    assert run(platform, admin, pinned, {"item": "book"})["output"] == {"price": 10}


def test_deprecated_component_blocks_publish(platform: Platform, admin: Principal) -> None:
    component = platform.components.create(admin.workspace_id, ComponentCreate(
        kind="system_instruction", name="tone", version="1.0.0", definition={"text": "Be concise."}, owner="docs-team"))
    spec = quoting_spec(components=[{"kind": "system_instruction", "name": "tone", "version": "1.0.0"}])
    agent_id = create_built(platform, admin, spec)
    assert platform.evaluations.start_run(admin, agent_id, wait=True)["passed"]
    platform.components.deprecate(admin.workspace_id, component["id"])

    gate = platform.evaluations.release_gate(admin, agent_id)
    assert not gate["publishable"] and "deprecated" in gate["blockers"][-1]
    with pytest.raises(InvalidStateError, match="Unsafe dependencies"):
        platform.agents.publish(admin, agent_id)
    users = platform.dependencies.dependents(admin, "component", "system_instruction/tone")
    assert users[0]["dependency_version"] == "1.0.0"


def _active(platform: Platform, principal: Principal, agent_id: str) -> str:
    return str(platform.agents.get(principal, agent_id)["active_version"]["id"])

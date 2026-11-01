"""Security tests mapped to the threat model (docs/security/threat-model.md).

Prompt-injection tests verify the *policy boundary*: even a model that fully follows injected instructions
(``mock-adversarial``) cannot execute unauthorized tools, escape tool sandboxes or fabricate approvals.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from sqlalchemy import select

from agent_factory.core.errors import PermissionDeniedError
from agent_factory.core.logging import JsonFormatter
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from agent_factory.storage.models import AuditEvent, ExecutionEvent, ToolInvocation
from tests.conftest import add_user, create_built, example_spec, make_settings, run


def invocations(platform: Platform, execution_id: str) -> list[ToolInvocation]:
    with platform.db.session() as session:
        return list(session.scalars(select(ToolInvocation).where(ToolInvocation.execution_id == execution_id)))


@pytest.fixture
def research_agent(platform: Platform, admin: Principal) -> str:
    from agent_factory.core.config import PROJECT_ROOT

    platform.knowledge.ingest_directory(admin.workspace_id, "research-docs",
                                        PROJECT_ROOT / "examples/document-research/documents")
    return create_built(platform, admin, example_spec("document-research/agent.yaml"))


def test_indirect_prompt_injection_cannot_trigger_unapproved_tools(
    platform: Platform, admin: Principal, research_agent: str
) -> None:
    result = run(platform, admin, research_agent,
                 {"question": "Summarize the vendor security review findings for Acme Analytics"},
                 options={"model_override": {"model": "mock-adversarial"}})
    assert result["status"] == "completed"
    denied = [i for i in invocations(platform, result["id"]) if i.status == "denied"]
    assert {i.tool_name for i in denied} == {"delete_dataset"}
    assert denied[0].decision["rules"] == ["agent.tool_allowlist"]
    with platform.db.session() as session:
        audit = session.scalars(select(AuditEvent).where(AuditEvent.action == "tool.denied")).all()
    assert audit and audit[0].outcome == "denied"


def test_compromised_model_cannot_escape_tool_sandbox(platform: Platform, admin: Principal) -> None:
    reviewer = create_built(platform, admin, example_spec("software-review/agents/architecture-reviewer.yaml"))
    result = run(platform, admin, reviewer, {"artifact": "payments-service"},
                 options={"model_override": {"model": "mock-adversarial"}})
    by_tool = {i.tool_name: i for i in invocations(platform, result["id"])}
    assert by_tool["delete_dataset"].status == "denied"
    traversal = by_tool["read_code_file"]
    assert traversal.status == "failed" and traversal.result["error"]["code"] == "path_traversal"


def test_model_cannot_fabricate_approvals(platform: Platform, admin: Principal, remediation_agent: str) -> None:
    paused = run(platform, admin, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"},
                 options={"model_override": {"model": "mock-adversarial"}})
    assert paused["status"] == "waiting_approval"  # text claiming approval changes nothing
    # a spurious resume (no decision recorded) re-reads the database and pauses again
    platform.executions.enqueue_resume(paused["id"])
    again = platform.executions.run_now(paused["id"])
    assert again["status"] == "waiting_approval"
    assert not any(i.tool_name == "restart_pipeline" and i.status == "completed"
                   for i in invocations(platform, paused["id"]))
    assert not (platform.settings.sandbox_dir / "simulated_actions.jsonl").exists()  # type: ignore[operator]


def test_separation_of_duties_and_approver_roles(tmp_path: Path) -> None:
    from agent_factory.platform import create_platform

    platform = create_platform(make_settings(tmp_path, allow_self_approval=False))
    try:
        operator = add_user(platform, "olga", "operator")
        developer = add_user(platform, "dev", "developer")
        other_operator = add_user(platform, "otto", "operator")
        agent_id = create_built(platform, add_user(platform, "root", "admin"),
                                example_spec("pipeline-remediation/agent.yaml"))
        paused = run(platform, operator, agent_id, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
        approval = platform.approvals.list(operator, status="pending")["items"][0]
        with pytest.raises(PermissionDeniedError, match="Separation of duties"):
            platform.approvals.decide(operator, approval["id"], "approved")
        with pytest.raises(PermissionDeniedError):
            platform.approvals.decide(developer, approval["id"], "approved")  # lacks approvals:decide
        assert platform.approvals.decide(other_operator, approval["id"], "approved")["status"] == "approved"
        assert platform.executions.run_now(paused["id"])["status"] == "completed"
    finally:
        platform.stop()


def test_malicious_tool_output_is_contained(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    result = run(platform, admin, pipeline_agent, {"pipeline_id": "events_stream", "execution_id": "run_2026_10_30"})
    assert result["status"] == "completed"
    assert {i.tool_name for i in invocations(platform, result["id"])} == {"read_pipeline_logs", "inspect_execution_metadata"}
    assert result["budget_usage"]["tool_calls"] == 2


def test_secrets_are_redacted_from_events_audit_and_logs(platform: Platform, admin: Principal) -> None:
    spec = platform.templates.instantiate(admin.workspace_id, "custom-agent", name="echo")
    agent_id = create_built(platform, admin, spec)
    leaked = "password=SuperSecret123 and Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
    result = run(platform, admin, agent_id, {"task": leaked})
    with platform.db.session() as session:
        payloads = json.dumps([e.payload for e in session.scalars(
            select(ExecutionEvent).where(ExecutionEvent.execution_id == result["id"]))])
    assert "SuperSecret123" not in payloads and "eyJhbGciOiJIUzI1NiJ9" not in payloads
    record = logging.LogRecord("x", logging.INFO, "f", 1, "token=abc123secret leaked", None, None)
    assert "abc123secret" not in JsonFormatter().format(record)


def test_specs_with_embedded_credentials_cannot_be_built(platform: Platform, admin: Principal) -> None:
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    spec.spec.instructions += "\nConnect with postgresql://admin:hunter22@prod-db:5432/main"
    agent = platform.agents.create(admin, spec)
    report = platform.agents.build(admin, agent["id"])
    assert not report["success"] and any(e.get("code") == "secret.embedded" for e in report["errors"])


def test_unauthorized_network_destinations_are_blocked(platform: Platform, admin: Principal) -> None:
    from agent_factory.spec.loader import load_spec

    spec = load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": "fetcher"},
        "spec": {"objective": "Fetch data from approved endpoints only.", "tools": ["http_get"],
                 "inputs": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}}},
                 "workflow": {"type": "graph", "steps": [
                     {"id": "fetch", "kind": "tool", "tool": "http_get", "arguments": {"url": "{{ input.url }}"}},
                     {"id": "answer", "kind": "model", "structured": True, "final": True}]},
                 "policies": {"external_network": True, "allowed_network_hosts": ["api.example.com"]}},
    })
    agent_id = create_built(platform, admin, spec)
    for url in ["http://169.254.169.254/latest/meta-data/", "https://evil.example.net/x", "https://api.example.com/x"]:
        result = run(platform, admin, agent_id, {"url": url})
        assert result["status"] == "failed"
        invocation = invocations(platform, result["id"])[0]
        # api.example.com is in the agent allowlist but not the platform allowlist (AF_NETWORK_ALLOWLIST)
        assert invocation.status == "denied" and invocation.decision["rules"] == ["network.allowlist"]


def test_cross_workspace_isolation_for_knowledge_and_approvals(
    platform: Platform, admin: Principal, remediation_agent: str
) -> None:
    outsider = add_user(platform, "eve", "admin", workspace_slug="evil-corp")
    run(platform, admin, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
    approval = platform.approvals.list(admin, status="pending")["items"][0]
    from agent_factory.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        platform.approvals.decide(outsider, approval["id"], "approved")
    assert platform.approvals.list(outsider)["total"] == 0


def test_execution_budget_prevents_unbounded_consumption(platform: Platform, admin: Principal) -> None:
    reviewer = create_built(platform, admin, example_spec("software-review/agents/security-reviewer.yaml"))
    result = run(platform, admin, reviewer, {"artifact": "payments-service"},
                 options={"limits_override": {"max_model_calls": 1}})
    assert result["status"] == "budget_exceeded" and result["error"]["details"]["limit"] == "max_model_calls"


def test_production_profile_refuses_insecure_configuration() -> None:
    from agent_factory.core.config import Settings

    problems = Settings(_env_file=None, profile="production", host="0.0.0.0").startup_problems()
    assert any("loopback" in p for p in problems)
    assert any("production profile requires AF_AUTH_MODE" in p for p in problems)
    assert any("SELF_APPROVAL" in p for p in problems)
    ok = Settings(_env_file=None, profile="production", auth_mode="api_key", allow_self_approval=False,
                  host="0.0.0.0").startup_problems()
    assert ok == []

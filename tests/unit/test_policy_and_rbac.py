"""Policy engine and RBAC: permissions are decided outside the model."""

from __future__ import annotations

import pytest

from agent_factory.core.config import Settings
from agent_factory.core.errors import PermissionDeniedError
from agent_factory.governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from agent_factory.governance.rbac import ROLE_PERMISSIONS, Role, make_principal
from agent_factory.spec.loader import load_spec
from agent_factory.tools.registry import build_default_registry
from tests.conftest import EXAMPLES

REGISTRY = build_default_registry()


def agent_body(rel: str = "pipeline-remediation/agent.yaml"):  # type: ignore[no-untyped-def]
    return load_spec((EXAMPLES / rel).read_text()).spec


def decide(tool: str, role: str = "admin", body=None, settings: Settings | None = None, **kw):  # type: ignore[no-untyped-def]
    engine = PolicyEngine(settings or Settings(_env_file=None))
    principal = make_principal("u1", "u1", "u1", "ws1", role, "test")
    definition = REGISTRY.get(tool).definition if REGISTRY.has(tool) else None
    return engine.evaluate_tool(
        ToolAuthorizationRequest(
            principal=principal,
            workspace_id=kw.pop("workspace_id", "ws1"),
            agent=body or agent_body(),
            tool_name=tool,
            tool=definition,
            tool_enabled=True,
            arguments=kw.pop("arguments", {}),
            **kw,
        )
    )


def test_read_tool_in_allowlist_is_allowed() -> None:
    assert decide("inspect_execution_metadata").effect == Effect.ALLOW


def test_tool_outside_allowlist_is_denied() -> None:
    decision = decide("delete_dataset")
    assert decision.effect == Effect.DENY
    assert decision.rules == ["agent.tool_allowlist"]


def test_unregistered_tool_is_denied() -> None:
    assert decide("format_disk").rules == ["tool.registered"]


def test_write_tool_requires_approval_then_allows_once_granted() -> None:
    first = decide("restart_pipeline")
    assert first.effect == Effect.REQUIRE_APPROVAL
    assert first.approval_action == "production_restart"
    assert {"approval.action_class", "approval.risk", "approval.writes"} <= set(first.rules)
    assert decide("restart_pipeline", approval_already_granted=True).effect == Effect.ALLOW


def test_rbac_blocks_roles_without_tool_permissions() -> None:
    decision = decide("restart_pipeline", role="viewer")
    assert decision.effect == Effect.DENY
    assert decision.rules == ["rbac.permission"]
    assert decide("restart_pipeline", role="evaluator").rules == ["rbac.permission"]


def test_workspace_mismatch_is_denied() -> None:
    assert decide("inspect_execution_metadata", workspace_id="other").rules == ["workspace.isolation"]


def test_write_denied_when_agent_policy_forbids_writes() -> None:
    body = agent_body()
    body.policies.allow_write_operations = False
    assert decide("restart_pipeline", body=body).rules == ["agent.write_operations"]


def test_network_destination_must_be_in_both_allowlists() -> None:
    body = agent_body()
    body.tools.append(body.tools[0].model_copy(update={"name": "http_get"}))
    assert decide("http_get", body=body, arguments={"url": "https://api.example.com/x"}).rules == [
        "agent.external_network"
    ]
    body.policies.external_network = True
    body.policies.allowed_network_hosts = ["api.example.com"]
    settings = Settings(_env_file=None, network_allowlist=["api.example.com"])
    ok = decide("http_get", body=body, settings=settings, arguments={"url": "https://api.example.com/v1"})
    assert ok.effect == Effect.ALLOW
    evil = decide("http_get", body=body, settings=settings, arguments={"url": "https://evil.example.net/"})
    assert evil.rules == ["network.allowlist"]
    not_platform = decide("http_get", body=body, arguments={"url": "https://api.example.com/v1"})
    assert not_platform.rules == ["network.allowlist"]


def test_sandboxed_tools_refused_without_sandbox() -> None:
    body = agent_body()
    body.tools.append(body.tools[0].model_copy(update={"name": "python_sandbox_exec"}))
    assert decide("python_sandbox_exec", body=body).rules == ["sandbox.available"]


def test_delegation_is_bounded() -> None:
    body = agent_body("software-review/agent.yaml")
    engine = PolicyEngine(Settings(_env_file=None))
    assert engine.evaluate_delegation(body, 0).effect == Effect.ALLOW
    assert engine.evaluate_delegation(body, 4).effect == Effect.DENY


def test_role_matrix() -> None:
    viewer = make_principal("u", "u", "u", "w", "viewer", "t")
    with pytest.raises(PermissionDeniedError):
        viewer.require("agents:write")
    assert viewer.can("agents:read")
    assert make_principal("u", "u", "u", "w", "admin", "t").can("anything:at_all")
    assert "approvals:decide" in ROLE_PERMISSIONS[Role.OPERATOR]
    assert "approvals:decide" not in ROLE_PERMISSIONS[Role.DEVELOPER]
    assert make_principal("u", "u", "u", "w", "nonsense", "t").permissions == frozenset()

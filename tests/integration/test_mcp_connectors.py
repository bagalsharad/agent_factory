"""MCP connectors: explicit trust, discovery, schema validation, default high risk, policy, credentials, limits."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_factory.connectors.mcp import HttpMCPClient, MCPError
from agent_factory.connectors.service import MCPServerCreate, MCPServerUpdate
from agent_factory.core.errors import ValidationFailedError
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform, create_platform
from agent_factory.spec.loader import load_spec
from agent_factory.storage.models import ExecutionEvent, ToolInvocation
from tests.conftest import add_user, create_built, make_settings, run

SERVER = str(Path(__file__).parents[1] / "fixtures" / "fake_mcp_server.py")


@pytest.fixture
def mcp_platform(tmp_path: Path) -> Iterator[Platform]:
    platform = create_platform(make_settings(tmp_path, mcp_allowed_commands=[sys.executable], secrets_backend="file"))
    yield platform
    platform.stop()


@pytest.fixture
def admin_p(mcp_platform: Platform) -> Principal:
    return add_user(mcp_platform, "mcp-admin", "admin")


@pytest.fixture
def shop(mcp_platform: Platform, admin_p: Principal, tmp_path: Path) -> dict[str, Any]:
    mcp_platform.secrets.set("shop_token", "s3cr3t-token-value")
    mcp_platform.secrets.set("refund_log", str(tmp_path / "refunds.log"))
    server = mcp_platform.connectors.register(admin_p, MCPServerCreate(
        name="shop", transport="stdio", command=[sys.executable, SERVER], max_output_chars=2000,
        env_secrets={"SHOP_TOKEN": "shop_token", "REFUND_LOG": "refund_log"}))
    return mcp_platform.connectors.discover(admin_p, server["id"])


def agent(platform: Platform, principal: Principal, name: str, tool: str, **policies: Any) -> str:
    return create_built(platform, principal, load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": name, "version": "1.0.0"},
        "spec": {
            "objective": "Help customers with their orders.",
            "inputs": {"type": "object", "required": ["order_id"], "properties": {"order_id": {"type": "string"}}},
            "outputs": {"type": "object"},
            "tools": [tool],
            "policies": policies,
            "workflow": {"type": "graph", "steps": [
                {"id": "call", "kind": "tool", "tool": tool, "arguments": {"order_id": "{{ input.order_id }}"},
                 "final": True},
                {"id": "reply", "kind": "model", "prompt": "Tell the customer."}]},
            "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"order_id": "42"}}]},
        },
    }))


def test_registration_rules(mcp_platform: Platform, admin_p: Principal) -> None:
    with pytest.raises(ValidationFailedError, match="AF_MCP_ALLOWED_COMMANDS"):
        mcp_platform.connectors.register(admin_p, MCPServerCreate(name="shell", transport="stdio", command=["/bin/sh"]))
    with pytest.raises(ValidationFailedError, match="metacharacters"):
        mcp_platform.connectors.register(admin_p, MCPServerCreate(
            name="sneaky", transport="stdio", command=[sys.executable, "server.py; rm -rf /"]))
    with pytest.raises(ValidationFailedError, match="https"):
        mcp_platform.connectors.register(admin_p, MCPServerCreate(name="plain", transport="http", url="http://example.com/mcp"))
    developer = add_user(mcp_platform, "dev", "developer")
    from agent_factory.core.errors import PermissionDeniedError

    with pytest.raises(PermissionDeniedError):
        mcp_platform.connectors.register(developer, MCPServerCreate(name="remote", transport="http", url="https://x.example/mcp"))


def test_discovery_trust_and_classification(mcp_platform: Platform, admin_p: Principal, shop: dict[str, Any]) -> None:
    tools = {t["name"]: t for t in shop["tools"]}
    assert shop["health"]["status"] == "ok" and set(tools) == {"lookup_order", "refund_order", "bad_tool", "huge", "whoami"}
    assert tools["bad_tool"]["schema_valid"] is False and "object" in tools["bad_tool"]["problems"][0]
    assert tools["lookup_order"]["annotations"] == {"readOnlyHint": True}  # recorded, not trusted
    assert tools["lookup_order"]["risk"] == "high" and tools["lookup_order"]["side_effects"] == "write"
    assert not any(t["exposed"] for t in tools.values())  # untrusted server: nothing is exposed
    assert "s3cr3t" not in json.dumps(shop)

    configured = mcp_platform.connectors.configure(admin_p, shop["id"], MCPServerUpdate(
        trusted=True, tool_allowlist=["lookup_order", "refund_order", "bad_tool"],
        tool_settings={"lookup_order": {"risk": "low", "side_effects": "read"}}))
    exposed = {t["name"] for t in configured["tools"] if t["exposed"]}
    assert exposed == {"lookup_order", "refund_order"}  # invalid schema never exposed
    lookup = mcp_platform.tools.get("mcp__shop__lookup_order").definition
    refund = mcp_platform.tools.get("mcp__shop__refund_order").definition
    assert (lookup.risk, lookup.side_effects, lookup.idempotent) == ("low", "read", True)
    assert (refund.risk, refund.side_effects, refund.idempotent) == ("high", "write", False)
    assert lookup.input_schema["required"] == ["order_id"] and lookup.source == "mcp:shop"
    with pytest.raises(ValidationFailedError):
        mcp_platform.connectors.configure(admin_p, shop["id"], MCPServerUpdate(tool_allowlist=["not_there"]))


def test_mcp_calls_are_governed(mcp_platform: Platform, admin_p: Principal, shop: dict[str, Any], tmp_path: Path) -> None:
    mcp_platform.connectors.configure(admin_p, shop["id"], MCPServerUpdate(
        trusted=True, tool_allowlist=["lookup_order", "refund_order", "whoami"],
        tool_settings={"lookup_order": {"risk": "low", "side_effects": "read"}}))
    reader = agent(mcp_platform, admin_p, "order-reader", "mcp__shop__lookup_order")
    result = run(mcp_platform, admin_p, reader, {"order_id": "42"})
    assert result["status"] == "completed"
    assert "Order 42 shipped" in result["output"]["content"] and result["output"]["structured"] == {"status": "shipped"}
    assert not (tmp_path / "refunds.log").exists()  # injected text in the tool output did not trigger a refund

    # Writes: an agent cannot even be built with an MCP write tool unless its policy allows writes ...
    created = mcp_platform.agents.create(admin_p, load_spec({**mcp_platform.agents.get_version(
        admin_p, reader, "1.0.0")["spec"], "metadata": {"name": "refunder-ro", "version": "1.0.0"}}))
    spec = load_spec(mcp_platform.agents.get_version(admin_p, created["id"], "1.0.0")["spec"])
    spec.spec.tools = [t.model_copy(update={"name": "mcp__shop__refund_order"}) for t in spec.spec.tools]
    spec.spec.workflow.steps[0].tool = "mcp__shop__refund_order"
    mcp_platform.agents.update(admin_p, created["id"], spec)
    report = mcp_platform.agents.build(admin_p, created["id"])
    assert not report["success"] and any(e.get("code") == "policy.write_conflict" for e in report["errors"])
    # ... and when it is allowed, the call waits for a human (MCP tools default to high risk).
    refunder = agent(mcp_platform, admin_p, "refunder", "mcp__shop__refund_order", allow_write_operations=True)
    waiting = run(mcp_platform, admin_p, refunder, {"order_id": "42"})
    assert waiting["status"] == "waiting_approval" and not (tmp_path / "refunds.log").exists()

    # Credentials reach the server process, never the execution record.
    mcp_platform.connectors.configure(admin_p, shop["id"], MCPServerUpdate(
        tool_settings={"whoami": {"risk": "low", "side_effects": "read"}}))
    whoami = agent(mcp_platform, admin_p, "whoami", "mcp__shop__whoami")
    out = run(mcp_platform, admin_p, whoami, {"order_id": "1"})
    assert out["output"]["content"] == "token length 18"
    with mcp_platform.db.session() as session:
        stored = json.dumps([e.payload for e in session.query(ExecutionEvent).all()]
                            + [i.result for i in session.query(ToolInvocation).all()], default=str)
    assert "s3cr3t-token-value" not in stored


def test_output_limit_and_health(mcp_platform: Platform, admin_p: Principal, shop: dict[str, Any]) -> None:
    mcp_platform.connectors.configure(admin_p, shop["id"], MCPServerUpdate(
        trusted=True, tool_allowlist=["huge"], tool_settings={"huge": {"risk": "low", "side_effects": "read"}}))
    tool = mcp_platform.tools.get("mcp__shop__huge")
    output = tool.run(tool.Input(), None)  # type: ignore[arg-type]
    assert output["truncated"] is True and len(output["content"]) == 2000
    assert mcp_platform.connectors.health(admin_p, shop["id"])["status"] == "ok"


def test_http_transport_json_and_sse() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        seen.append({"method": message.get("method"), "session": request.headers.get("mcp-session-id"),
                     "auth": request.headers.get("authorization")})
        if message["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}},
                                  headers={"Mcp-Session-Id": "sess-1"})
        if "id" not in message:
            return httpx.Response(202)
        body = {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [{"name": "t", "inputSchema": {"type": "object"}}]}}
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(body)}\n\n",
                              headers={"content-type": "text/event-stream"})

    client = HttpMCPClient("https://mcp.example.com/mcp", {"Authorization": "Bearer t"}, 5, 100_000,
                           transport=httpx.MockTransport(handler))
    assert client.list_tools()[0]["name"] == "t"
    assert seen[-1] == {"method": "tools/list", "session": "sess-1", "auth": "Bearer t"}

    failing = HttpMCPClient("https://mcp.example.com/mcp", {}, 5, 100_000,
                            transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(MCPError) as exc:
        failing.list_tools()
    assert exc.value.retryable

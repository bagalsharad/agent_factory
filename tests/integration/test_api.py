"""HTTP API: endpoints, error format, idempotency, pagination, limits, auth modes and isolation."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_factory.api.app import StartupError, create_app
from agent_factory.core.config import AuthMode, Settings
from agent_factory.platform import create_platform
from tests.conftest import EXAMPLES, make_settings, wait_for


def spec_yaml(rel: str) -> str:
    return (EXAMPLES / rel).read_text()


def create_and_build(client: TestClient, rel: str = "data-pipeline-diagnostic/agent.yaml", headers: dict | None = None) -> str:
    response = client.post("/api/v1/agents", json={"spec_yaml": spec_yaml(rel)}, headers=headers or {})
    assert response.status_code == 201, response.text
    agent_id = response.json()["id"]
    build = client.post(f"/api/v1/agents/{agent_id}/build", headers=headers or {})
    assert build.status_code == 200 and build.json()["success"], build.text
    return str(agent_id)


def test_probes_and_identity(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"database": "ok", "migrations": "ok", "worker": "ok"}
    me = client.get("/api/v1/me").json()
    assert me["role"] == "admin" and me["auth_method"] == "local" and me["workspace"]["slug"] == "default"
    status = client.get("/api/v1/system/status").json()
    assert status["database"]["up_to_date"] and status["audit"]["valid"]
    response = client.get("/api/v1/me")
    for header in ("x-request-id", "x-content-type-options", "x-frame-options", "content-security-policy"):
        assert header in response.headers
    assert client.get("/api/v1/me", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"


def test_catalog_endpoints(client: TestClient) -> None:
    tools = client.get("/api/v1/tools").json()
    assert {t["name"] for t in tools} >= {"read_pipeline_logs", "restart_pipeline", "http_get"}
    providers = {p["name"]: p for p in client.get("/api/v1/models/providers").json()}
    assert providers["mock"]["status"] == "up" and providers["anthropic"]["status"] == "disabled"
    models = client.get("/api/v1/models/available").json()["models"]
    assert any(m["name"] == "mock-default" for m in models)
    test = client.post("/api/v1/models/test", json={"provider": "mock", "model": "mock-default", "prompt": "hi"})
    assert test.status_code == 200 and test.json()["content"]
    assert len(client.get("/api/v1/templates").json()) == 10
    assert client.get("/api/v1/templates/rag-agent").json()["document"]["spec"]["workflow"]["type"] == "graph"
    assert client.get("/api/v1/components").json()
    schema = client.get("/api/v1/schemas/agent-spec").json()
    assert schema["title"] == "AgentSpec"
    assert "recommendations" in client.get("/api/v1/models/recommendations").json()


def test_agent_designer_flow(client: TestClient) -> None:
    draft = client.post("/api/v1/agents/draft", json={
        "objective": "Create an agent that investigates failed data ingestion pipelines by analyzing logs."}).json()
    assert draft["executable"] is False and draft["validation"]["valid"]
    preview = client.post("/api/v1/agents/preview", json={"spec": draft["spec"]}).json()
    assert preview["workflow"]["nodes"] and preview["validation"]["valid"]
    created = client.post("/api/v1/agents", json={"spec": draft["spec"]})
    assert created.status_code == 201
    agent_id = created.json()["id"]
    assert client.post(f"/api/v1/agents/{agent_id}/validate").json()["valid"]
    built = client.post(f"/api/v1/agents/{agent_id}/build").json()
    assert built["status"] == "testing"
    workflow = client.get(f"/api/v1/agents/{agent_id}/workflow").json()
    assert workflow["pattern"] == "single_agent"
    from_template = client.post("/api/v1/agents", json={"template": "custom-agent", "name": "tmpl-agent"})
    assert from_template.status_code == 201


def test_execution_lifecycle_over_http(client: TestClient) -> None:
    agent_id = create_and_build(client)
    response = client.post("/api/v1/executions", json={
        "agent_id": agent_id, "input": {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}})
    assert response.status_code == 202 and response.json()["status"] == "queued"
    execution_id = response.json()["id"]
    final = wait_for(lambda: (r := client.get(f"/api/v1/executions/{execution_id}").json())["status"] == "completed" and r)
    assert final["graph"]["nodes"] and len(final["tool_invocations"]) == 2
    events = client.get(f"/api/v1/executions/{execution_id}/events", params={"after": 3}).json()["items"]
    assert events and events[0]["seq"] == 4
    with client.stream("GET", f"/api/v1/executions/{execution_id}/events/stream") as stream:
        body = "".join(chunk for chunk in stream.iter_text())
    assert "event: execution.completed" in body and "event: end" in body
    listing = client.get("/api/v1/executions", params={"agent_id": agent_id, "status": "completed", "limit": 1}).json()
    assert listing["total"] == 1 and listing["limit"] == 1
    assert client.get("/api/v1/executions", params={"q": execution_id[:8]}).json()["total"] == 1
    assert client.delete(f"/api/v1/executions/{execution_id}").status_code == 204


def test_approvals_over_http(client: TestClient) -> None:
    agent_id = create_and_build(client, "pipeline-remediation/agent.yaml")
    execution_id = client.post("/api/v1/executions", json={
        "agent_id": agent_id, "input": {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"}}).json()["id"]
    wait_for(lambda: client.get(f"/api/v1/executions/{execution_id}").json()["status"] == "waiting_approval")
    approval = client.get("/api/v1/approvals", params={"status": "pending"}).json()["items"][0]
    assert approval["tool_name"] == "restart_pipeline"
    decided = client.post(f"/api/v1/approvals/{approval['id']}/approve", json={"comment": "go"})
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    final = wait_for(lambda: (r := client.get(f"/api/v1/executions/{execution_id}").json())["status"] == "completed" and r)
    assert "simulated" in str(final["output"])
    again = client.post(f"/api/v1/approvals/{approval['id']}/reject")
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"


def test_evaluation_and_publish_over_http(client: TestClient) -> None:
    agent_id = create_and_build(client)
    early = client.post(f"/api/v1/agents/{agent_id}/publish")
    assert early.status_code == 409
    run = client.post("/api/v1/evaluations", json={"agent_id": agent_id, "wait": True}).json()
    assert run["passed"] and run["status"] == "completed"
    assert client.get(f"/api/v1/evaluations/{run['id']}").json()["results"]
    assert client.post(f"/api/v1/agents/{agent_id}/publish").json()["status"] == "published"
    assert client.get("/api/v1/dashboard").json()["agents"]["published"] == 1


def test_error_format_and_validation(client: TestClient) -> None:
    missing = client.get("/api/v1/agents/does-not-exist")
    body = missing.json()
    assert missing.status_code == 404 and body["error"]["code"] == "not_found" and body["error"]["request_id"]
    invalid = client.post("/api/v1/agents", json={"spec": {"metadata": {"name": "X"}}})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation_failed"
    assert invalid.json()["error"]["details"]
    bad_request = client.post("/api/v1/executions", json={"input": {}})
    assert bad_request.status_code == 422 and bad_request.json()["error"]["code"] == "request_invalid"


def test_idempotency_keys(client: TestClient) -> None:
    payload = {"template": "custom-agent", "name": "idem-agent"}
    first = client.post("/api/v1/agents", json=payload, headers={"Idempotency-Key": "k-1"})
    second = client.post("/api/v1/agents", json=payload, headers={"Idempotency-Key": "k-1"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"] and second.json()["idempotent_replay"]
    conflict = client.post("/api/v1/agents", json={**payload, "name": "other"}, headers={"Idempotency-Key": "k-1"})
    assert conflict.status_code == 409
    agent_id = create_and_build(client)
    exec_payload = {"agent_id": agent_id, "input": {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_28"}}
    a = client.post("/api/v1/executions", json=exec_payload, headers={"Idempotency-Key": "run-1"})
    b = client.post("/api/v1/executions", json=exec_payload, headers={"Idempotency-Key": "run-1"})
    assert a.json()["id"] == b.json()["id"] and b.status_code == 200


def test_request_size_limit(client: TestClient) -> None:
    huge = {"spec_yaml": "x" * 1_200_000}
    response = client.post("/api/v1/agents", json=huge)
    assert response.status_code == 413 and response.json()["error"]["code"] == "payload_too_large"


def test_rate_limit(tmp_path: Path) -> None:
    platform = create_platform(make_settings(tmp_path, execution_rate_limit_per_minute=2))
    app = create_app(platform.settings, platform_instance=platform, start_workers=False)
    with TestClient(app) as client:
        agent_id = create_and_build(client)
        payload = {"agent_id": agent_id, "input": {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_28"}}
        codes = [client.post("/api/v1/executions", json=payload).status_code for _ in range(3)]
    assert codes == [202, 202, 429]


def test_local_auth_refuses_network_binding() -> None:
    with pytest.raises(StartupError, match="loopback"):
        create_app(Settings(_env_file=None, host="0.0.0.0"))
    with pytest.raises(StartupError, match="production profile"):
        create_app(Settings(_env_file=None, profile="production"))


@pytest.fixture
def api_key_env(tmp_path: Path) -> Any:
    settings = make_settings(tmp_path, auth_mode=AuthMode.API_KEY)
    platform = create_platform(settings)
    keys: dict[str, str] = {}
    for subject, role, ws in [("alice", "developer", "default"), ("victor", "viewer", "default"),
                              ("bob", "admin", "team-b")]:
        from tests.conftest import add_user

        principal = add_user(platform, subject, role, workspace_slug=ws)
        keys[subject], _ = platform.identity.create_api_key(principal.user_id, f"{subject}-key")
    app = create_app(settings, platform_instance=platform, start_workers=False)
    with TestClient(app) as client:
        yield client, keys


def test_api_key_authentication_and_rbac(api_key_env: Any) -> None:
    client, keys = api_key_env
    assert client.get("/api/v1/agents").status_code == 401
    assert client.get("/api/v1/agents", headers={"Authorization": "Bearer af_bad_key"}).status_code == 401
    alice = {"Authorization": f"Bearer {keys['alice']}"}
    victor = {"Authorization": f"Bearer {keys['victor']}"}
    bob = {"Authorization": f"Bearer {keys['bob']}"}
    agent_id = create_and_build(client, headers=alice)
    denied = client.post("/api/v1/agents", json={"template": "custom-agent", "name": "nope"}, headers=victor)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "permission_denied"
    assert client.get(f"/api/v1/agents/{agent_id}", headers=victor).status_code == 200
    # cross-workspace: bob is an admin, but of another workspace
    assert client.get(f"/api/v1/agents/{agent_id}", headers=bob).status_code == 404
    assert client.get("/api/v1/agents", headers=bob).json()["total"] == 0
    forbidden_ws = client.get("/api/v1/agents", headers={**bob, "X-Workspace-ID": "default"})
    assert forbidden_ws.status_code == 403
    assert client.get("/api/v1/audit", headers=alice).status_code == 403
    assert client.get("/api/v1/audit", headers=bob).status_code == 200
    created = client.post("/api/v1/api-keys", json={"name": "ci"}, headers=alice).json()
    assert created["key"].startswith("af_")
    assert client.delete(f"/api/v1/api-keys/{created['id']}", headers=alice).status_code == 204
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {created['key']}"}).status_code == 401


def test_oidc_authentication(tmp_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = make_settings(tmp_path, auth_mode=AuthMode.OIDC, oidc_issuer="https://idp.example.com",
                             oidc_jwks_url="https://idp.example.com/jwks", oidc_audience="agent-factory")
    platform = create_platform(settings)

    class FakeJwks:
        def get_signing_key_from_jwt(self, token: str) -> Any:
            return type("K", (), {"key": key.public_key()})()

    platform.identity._jwks = FakeJwks()  # type: ignore[assignment]
    now = int(time.time())

    def token(**claims: Any) -> str:
        base = {"iss": "https://idp.example.com", "aud": "agent-factory", "sub": "u-123", "iat": now,
                "exp": now + 300, "email": "dev@example.com", "af_roles": ["developer"]}
        return jwt.encode({**base, **claims}, key, algorithm="RS256")

    app = create_app(settings, platform_instance=platform, start_workers=False)
    with TestClient(app) as client:
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token()}"}).json()
        assert me["role"] == "developer" and me["auth_method"] == "oidc"
        expired = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token(exp=now - 10)}"})
        assert expired.status_code == 401
        wrong_aud = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token(aud='other')}"})
        assert wrong_aud.status_code == 401
        viewer = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token(sub='u-9', af_roles=[])}"}).json()
        assert viewer["role"] == "viewer"


def test_settings_and_secrets_never_leak_values(tmp_path: Path) -> None:
    from pydantic import SecretStr

    settings = make_settings(tmp_path, secrets_backend="file", anthropic_api_key=SecretStr("sk-ant-supersecretvalue123456"))
    platform = create_platform(settings)
    app = create_app(settings, platform_instance=platform, start_workers=False)
    with TestClient(app) as client:
        view = client.get("/api/v1/system/settings").json()
        assert view["configuration"]["anthropic_api_key"] == {"configured": True}
        assert "supersecret" not in str(view)
        assert client.post("/api/v1/secrets", json={"name": "crm_api_token", "value": "tok-123456789"}).status_code == 201
        listed = client.get("/api/v1/secrets").json()
        assert listed["items"][0]["name"] == "crm_api_token" and "tok-123456789" not in str(listed)
        assert platform.secrets.get("crm_api_token") == "tok-123456789"
        toggled = client.put("/api/v1/system/settings/tools", json={"disabled": ["http_get"]}).json()
        assert toggled["disabled"] == ["http_get"]
        assert not next(t for t in client.get("/api/v1/tools").json() if t["name"] == "http_get")["enabled"]

"""Enterprise controls: Vault secrets, secret access auditing and rotation, external audit sink and export,
offline chain verification, and policy simulation/history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_factory.core.errors import NotFoundError, ValidationFailedError
from agent_factory.governance.audit import AuditLog, verify_records
from agent_factory.governance.rbac import Principal
from agent_factory.governance.secrets import AuditedSecretStore, EnvSecretStore, VaultSecretStore
from agent_factory.platform import Platform, create_platform
from agent_factory.storage.models import AuditEvent
from tests.conftest import example_spec, make_settings


def test_vault_secret_store() -> None:
    store: dict[str, str] = {}
    seen: list[httpx.Request] = []

    def vault(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "POST":
            store[name] = json.loads(request.content)["data"]["value"]
            return httpx.Response(200, json={})
        if request.method == "DELETE":
            store.pop(name, None)
            return httpx.Response(204)
        if name not in store:
            return httpx.Response(404, json={"errors": []})
        return httpx.Response(200, json={"data": {"data": {"value": store[name]}}})

    vs = VaultSecretStore("https://vault.example:8200", "kv", "af", token="t0k", transport=httpx.MockTransport(vault))
    vs.set("db_password", "hunter2")
    assert vs.get("db_password") == "hunter2"
    assert seen[0].url.path == "/v1/kv/data/af/db_password" and seen[0].headers["x-vault-token"] == "t0k"
    vs.delete("db_password")
    with pytest.raises(NotFoundError):
        vs.get("db_password")
    with pytest.raises(ValidationFailedError):
        vs.get("../escape")
    with pytest.raises(ValidationFailedError, match="VAULT_TOKEN"):
        VaultSecretStore("https://vault.example:8200", token="")


def test_secret_reads_are_audited_by_name_only(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AF_SECRET_API_TOKEN", "value-that-must-not-leak")
    audited = AuditedSecretStore(EnvSecretStore(), platform.audit)
    assert audited.get("api_token") == "value-that-must-not-leak"
    audited.get("api_token")  # within the window: summarized, not re-recorded
    with platform.db.session() as session:
        events = [e for e in session.query(AuditEvent).all() if e.action == "secret.access"]
    assert len(events) == 1 and events[0].resource_id == "api_token"
    assert "value-that-must-not-leak" not in json.dumps([e.details for e in events])


def test_secret_rotation_is_versioned(tmp_path: Path) -> None:
    platform = create_platform(make_settings(tmp_path, secrets_backend="file"))
    try:
        from agent_factory.api.app import create_app

        with TestClient(create_app(platform.settings, platform_instance=platform)) as client:
            assert client.post("/api/v1/secrets", json={"name": "smtp_pw", "value": "a"}).json()["version"] == 1
            assert client.post("/api/v1/secrets", json={"name": "smtp_pw", "value": "b"}).json()["version"] == 2
            [item] = client.get("/api/v1/secrets").json()["items"]
            assert item["version"] == 2 and item["last_rotated_at"] and "value" not in item
        assert platform.secrets.get("smtp_pw") == "b"
        with platform.db.session() as session:
            assert [e.action for e in session.query(AuditEvent).all()].count("secret.rotate") == 1
    finally:
        platform.stop()


def test_external_audit_sink_and_offline_verification(tmp_path: Path) -> None:
    sink = tmp_path / "worm" / "audit.jsonl"
    platform = create_platform(make_settings(tmp_path, audit_sink_path=sink))
    try:
        log = AuditLog(platform.db)
        for i in range(3):
            log.record("test.event", "thing", str(i), details={"n": i})
        lines = [json.loads(line) for line in sink.read_text().splitlines()]
        assert len(lines) >= 3 and verify_records(lines)["valid"]
        exported = list(log.export())
        assert verify_records(exported) == {"valid": True, "events_checked": len(exported), "last_hash": exported[-1]["hash"]}

        tampered = [dict(r) for r in exported]
        tampered[1]["details"] = {"n": 999}
        assert verify_records(tampered) == {"valid": False, "broken_at_seq": tampered[1]["seq"],
                                            "reason": "hash mismatch", "events_checked": 2}
        assert not verify_records([exported[0], exported[2]])["valid"]  # a deleted event breaks the chain

        from agent_factory.cli.main import app

        export_file = tmp_path / "export.jsonl"
        export_file.write_text("\n".join(json.dumps(r) for r in exported) + "\n")
        result = CliRunner().invoke(app, ["audit", "verify-export", str(export_file)])
        assert result.exit_code == 0 and '"valid": true' in result.output
        export_file.write_text("\n".join(json.dumps(r) for r in tampered) + "\n")
        assert CliRunner().invoke(app, ["audit", "verify-export", str(export_file)]).exit_code == 1
    finally:
        platform.stop()


def test_audit_export_api_is_audited(client: TestClient) -> None:
    response = client.get("/api/v1/audit/export")
    records = [json.loads(line) for line in response.text.splitlines()]
    assert response.headers["content-type"].startswith("application/x-ndjson") and verify_records(records)["valid"]
    again = [json.loads(line) for line in client.get("/api/v1/audit/export").text.splitlines()]
    assert again[-1]["action"] == "audit.export"


def test_policy_simulation_and_history(client: TestClient, platform: Platform, admin: Principal,
                                       remediation_agent: str) -> None:
    def simulate(**body: Any) -> dict[str, Any]:
        return client.post("/api/v1/policies/simulate", json={"agent_id": remediation_agent, **body}).json()

    write = simulate(tool="restart_pipeline", arguments={"pipeline_id": "orders_daily", "reason": "stuck"})
    assert write["decision"]["effect"] == "require_approval" and write["executed"] is False
    assert simulate(tool="http_get", arguments={"url": "https://x"})["decision"]["rules"] == ["agent.tool_allowlist"]
    viewer = simulate(tool="restart_pipeline", arguments={}, as_role="viewer")
    assert viewer["decision"]["effect"] == "deny" and viewer["decision"]["rules"] == ["rbac.permission"]

    spec = example_spec("pipeline-remediation/agent.yaml")
    spec.spec.human_approval.expires_after_seconds = 600
    platform.agents.update(admin, remediation_agent, spec)
    history = client.get(f"/api/v1/agents/{remediation_agent}/policies").json()["items"]
    assert len(history) == 1  # mutable version edited in place
    assert history[0]["policy"]["human_approval"]["expires_after_seconds"] == 600 and history[0]["policy_hash"]

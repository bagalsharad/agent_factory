"""End-to-end journey over HTTP with the embedded worker (deterministic mock model).

Create (natural language) → validate → build → execute → inspect → evaluate → publish → reject an unauthorized
action → cancel an execution → recover from a simulated failure → compare and roll back versions.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from agent_factory.storage.models import Execution
from tests.conftest import EXAMPLES, wait_for


def poll(client: TestClient, execution_id: str, statuses: set[str]) -> dict[str, Any]:
    return wait_for(lambda: (r := client.get(f"/api/v1/executions/{execution_id}").json())["status"] in statuses and r)


def test_complete_agent_lifecycle_journey(client: TestClient) -> None:
    platform = client.app.state.platform  # type: ignore[attr-defined]

    # 1. Natural language → draft (not executable), then save as a draft version
    draft = client.post("/api/v1/agents/draft", json={
        "objective": "Create an agent that investigates failed data ingestion pipelines by analyzing logs, "
                     "identifying probable root causes, and recommending remediation steps.",
        "name": "ingestion-investigator"}).json()
    assert draft["executable"] is False and draft["template"].startswith("data-pipeline-diagnostic")
    agent = client.post("/api/v1/agents", json={"spec": draft["spec"]}).json()
    agent_id = agent["id"]
    assert agent["latest_version"]["status"] == "draft"
    blocked = client.post("/api/v1/executions", json={"agent_id": agent_id, "input": {
        "pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}})
    assert blocked.status_code == 409  # not executable before validation + build

    # 2. Inspect, edit (advanced YAML), validate and build
    detail = client.get(f"/api/v1/agents/{agent_id}").json()
    edited_yaml = detail["spec_yaml"].replace("Data Pipeline Diagnostic Agent", "Ingestion Investigator")
    assert client.put(f"/api/v1/agents/{agent_id}", json={"spec_yaml": edited_yaml}).status_code == 200
    assert client.post(f"/api/v1/agents/{agent_id}/validate").json()["valid"]
    assert client.post(f"/api/v1/agents/{agent_id}/build").json()["status"] == "testing"

    # 3. Execute and inspect the result and timeline
    execution = client.post("/api/v1/executions", json={"agent_id": agent_id, "input": {
        "pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}}).json()
    done = poll(client, execution["id"], {"completed", "failed"})
    assert done["status"] == "completed" and "discount_code" in str(done["output"])
    timeline = client.get(f"/api/v1/executions/{execution['id']}/events").json()["items"]
    assert [e["type"] for e in timeline][-1] == "execution.completed"

    # 4. Evaluate and publish
    run = client.post("/api/v1/evaluations", json={"agent_id": agent_id, "wait": True}).json()
    assert run["passed"], run["summary"]
    first = client.post(f"/api/v1/agents/{agent_id}/publish").json()
    assert first["status"] == "published"

    # 5. An unauthorized action is rejected (compromised model inside a research agent)
    platform.knowledge.ingest_directory(platform.identity.authenticate(None, None).workspace_id, "research-docs",
                                        EXAMPLES / "document-research/documents")
    research = client.post("/api/v1/agents", json={"spec_yaml": (EXAMPLES / "document-research/agent.yaml").read_text()}).json()
    client.post(f"/api/v1/agents/{research['id']}/build")
    attack = client.post("/api/v1/executions", json={
        "agent_id": research["id"], "input": {"question": "Summarize the vendor security review findings"},
        "options": {"model_override": {"model": "mock-adversarial"}}}).json()
    attacked = poll(client, attack["id"], {"completed", "failed"})
    assert any(t["tool_name"] == "delete_dataset" and t["status"] == "denied" for t in attacked["tool_invocations"])

    # 6. Cancel an execution that is waiting for approval
    remediation = client.post("/api/v1/agents", json={"spec_yaml": (EXAMPLES / "pipeline-remediation/agent.yaml").read_text()}).json()
    client.post(f"/api/v1/agents/{remediation['id']}/build")
    pending = client.post("/api/v1/executions", json={"agent_id": remediation["id"], "input": {
        "pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"}}).json()
    poll(client, pending["id"], {"waiting_approval"})
    assert client.post(f"/api/v1/executions/{pending['id']}/cancel").json()["status"] == "cancelled"

    # 7. Recover from a simulated failure
    failing = client.post("/api/v1/executions", json={"agent_id": agent_id, "input": {
        "pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"},
        "options": {"fault_injection": {"fail_tool_always": "inspect_execution_metadata"}}}).json()
    assert poll(client, failing["id"], {"failed", "completed"})["status"] == "failed"
    with platform.db.transaction() as session:
        session.execute(update(Execution).where(Execution.id == failing["id"]).values(options={}))
    assert client.post(f"/api/v1/executions/{failing['id']}/retry").json()["status"] == "queued"
    assert poll(client, failing["id"], {"completed", "failed"})["status"] == "completed"

    # 8. Version comparison and rollback
    improved = client.get(f"/api/v1/agents/{agent_id}").json()["spec_yaml"].replace(
        "Never claim", "Be concise. Never claim")
    assert client.put(f"/api/v1/agents/{agent_id}", json={"spec_yaml": improved}).json()["new_version_created"]
    client.post(f"/api/v1/agents/{agent_id}/build")
    client.post("/api/v1/evaluations", json={"agent_id": agent_id, "wait": True})
    second = client.post(f"/api/v1/agents/{agent_id}/publish").json()
    assert second["status"] == "published" and second["version"] != first["version"]
    diff = client.get(f"/api/v1/agents/{agent_id}/compare",
                      params={"base": first["version"], "other": second["version"]}).json()
    assert diff["changes"]
    rollback = client.post(f"/api/v1/agents/{agent_id}/rollback", json={"version": first["version"]}).json()
    assert rollback["active_version"]["version"] == first["version"]

    # Audit trail is intact
    assert client.get("/api/v1/audit/verify").json()["valid"]

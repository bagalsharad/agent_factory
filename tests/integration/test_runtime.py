"""Execution through the harness and LangGraph runtime (mock model, real database, real tools)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from agent_factory.core.errors import InvalidStateError, PermissionDeniedError, ValidationFailedError
from agent_factory.core.ids import utcnow
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from agent_factory.storage.models import Execution, ToolInvocation
from tests.conftest import add_user, create_built, example_spec, run


def events(platform: Platform, principal: Principal, execution_id: str) -> list[str]:
    return [e["type"] for e in platform.executions.events(principal, execution_id)["items"]]


def test_single_agent_execution_is_observable(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    result = run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    assert result["status"] == "completed"
    assert "discount_code" in str(result["output"])
    snapshot = result["config_snapshot"]
    assert snapshot["agent_version"] == "1.0.0" and snapshot["spec_hash"] and snapshot["policy_hash"]
    assert {t["name"] for t in snapshot["tools"]} == {"read_pipeline_logs", "inspect_execution_metadata"}
    assert snapshot["workflow_hash"] and len(snapshot["components"]) == 4
    types = events(platform, admin, result["id"])
    assert types[0] == "execution.queued" and types[-1] == "execution.completed"
    assert types.count("tool.call") == 2 and "model.call" in types and "validation.passed" in types
    usage = result["budget_usage"]
    assert usage["tool_calls"] == 2 and usage["model_calls"] == 1 and usage["steps"] == 4


def test_input_is_validated_before_execution(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    with pytest.raises(ValidationFailedError, match="input schema"):
        platform.executions.create(admin, pipeline_agent, input_data={"pipeline_id": "x"})


def test_draft_specs_are_never_executable(platform: Platform, admin: Principal) -> None:
    agent = platform.agents.create(admin, example_spec("data-pipeline-diagnostic/agent.yaml"))
    with pytest.raises(InvalidStateError, match="draft"):
        platform.executions.create(admin, agent["id"], input_data={"pipeline_id": "a", "execution_id": "b"})


def test_approval_pause_and_resume(platform: Platform, admin: Principal, remediation_agent: str) -> None:
    paused = run(platform, admin, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
    assert paused["status"] == "waiting_approval"
    pending = platform.approvals.list(admin, status="pending")["items"]
    assert len(pending) == 1 and pending[0]["action"] == "production_restart" and pending[0]["risk"] == "high"
    assert pending[0]["requested_by"] == admin.user_id and pending[0]["expires_at"]
    with platform.db.session() as session:
        statuses = {i.tool_name: i.status for i in session.scalars(select(ToolInvocation).where(
            ToolInvocation.execution_id == paused["id"], ToolInvocation.tool_call_id != "approval"))}
    assert statuses["restart_pipeline"] == "awaiting_approval"
    platform.approvals.decide(admin, pending[0]["id"], "approved", "transient")
    done = platform.executions.run_now(paused["id"])
    assert done["status"] == "completed" and "simulated" in str(done["output"])
    types = events(platform, admin, paused["id"])
    assert types.index("execution.waiting_approval") < types.index("execution.resumed") < types.index("approval.decided")
    log = (platform.settings.sandbox_dir / "simulated_actions.jsonl").read_text().strip().splitlines()  # type: ignore[union-attr]
    assert len(log) == 1  # resumed node re-executed, but the side effect happened exactly once


def test_rejected_approval_skips_action(platform: Platform, admin: Principal, remediation_agent: str) -> None:
    paused = run(platform, admin, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
    approval = platform.approvals.list(admin, status="pending")["items"][0]
    platform.approvals.decide(admin, approval["id"], "rejected", "not now")
    done = platform.executions.run_now(paused["id"])
    assert done["status"] == "completed" and "NOT performed" in str(done["output"])
    assert not (platform.settings.sandbox_dir / "simulated_actions.jsonl").exists()  # type: ignore[union-attr]


def test_cancel_while_waiting_for_approval(platform: Platform, admin: Principal, remediation_agent: str) -> None:
    paused = run(platform, admin, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
    cancelled = platform.executions.cancel(admin, paused["id"])
    assert cancelled["status"] == "cancelled"
    assert platform.approvals.list(admin, status="cancelled")["total"] == 1
    with pytest.raises(InvalidStateError):
        platform.executions.cancel(admin, paused["id"])


def test_cancel_queued_execution(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    created = platform.executions.create(admin, pipeline_agent,
                                         input_data={"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    assert platform.executions.cancel(admin, created["id"])["status"] == "cancelled"
    assert platform.executions.run_now(created["id"])["status"] == "cancelled"  # never claimed


def test_expired_approval_is_treated_as_rejection(platform: Platform, admin: Principal, remediation_agent: str) -> None:
    paused = run(platform, admin, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
    from agent_factory.storage.models import Approval

    with platform.db.transaction() as session:
        session.execute(update(Approval).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert platform.approvals.expire_due() == 1
    done = platform.executions.run_now(paused["id"])
    assert done["status"] == "completed" and "approval_expired" in str(done["output"])


def test_budget_exhaustion_terminates_safely(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    result = run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"},
                 options={"limits_override": {"max_tool_calls": 1}})
    assert result["status"] == "budget_exceeded"
    assert result["error"]["details"]["limit"] == "max_tool_calls"
    assert "budget.exceeded" in events(platform, admin, result["id"])


def test_recovery_from_checkpoint_does_not_repeat_completed_steps(
    platform: Platform, admin: Principal, pipeline_agent: str
) -> None:
    failed = run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"},
                 options={"fault_injection": {"fail_tool_always": "inspect_execution_metadata"}})
    assert failed["status"] == "failed" and failed["error"]["code"] == "step_failed"
    with platform.db.transaction() as session:  # the transient problem is fixed
        session.execute(update(Execution).where(Execution.id == failed["id"]).values(options={}))
    platform.executions.retry(admin, failed["id"])
    recovered = platform.executions.run_now(failed["id"])
    assert recovered["status"] == "completed"
    with platform.db.session() as session:
        names = [i.tool_name for i in session.scalars(select(ToolInvocation).where(ToolInvocation.execution_id == failed["id"]))]
    assert names.count("read_pipeline_logs") == 1  # not re-executed after recovery
    assert "execution.recovering" in events(platform, admin, failed["id"])


def test_stale_worker_executions_are_requeued(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    created = platform.executions.create(admin, pipeline_agent,
                                         input_data={"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    assert platform.executions.claim("crashed-worker", created["id"]) == created["id"]
    with platform.db.transaction() as session:
        session.execute(update(Execution).where(Execution.id == created["id"])
                        .values(heartbeat_at=utcnow() - timedelta(hours=1)))
    assert platform.executions.recover_stale() == 1
    assert platform.executions.run_now(created["id"])["status"] == "completed"


def test_revoked_requester_cannot_resume(platform: Platform, remediation_agent: str) -> None:
    operator = add_user(platform, "olivia", "operator")
    platform_admin = add_user(platform, "root", "admin")
    paused = run(platform, operator, remediation_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_30"})
    approval = platform.approvals.list(platform_admin, status="pending")["items"][0]
    from agent_factory.storage.models import Membership

    with platform.db.transaction() as session:
        session.execute(update(Membership).where(Membership.user_id == operator.user_id).values(role="viewer"))
    platform.approvals.decide(platform_admin, approval["id"], "approved")
    done = platform.executions.run_now(paused["id"])
    # the requester is now a viewer: the restart is denied by RBAC at execution time
    with platform.db.session() as session:
        restart = session.scalars(select(ToolInvocation).where(ToolInvocation.execution_id == paused["id"],
                                                               ToolInvocation.tool_name == "restart_pipeline",
                                                               ToolInvocation.tool_call_id != "approval")).one()
    assert restart.status == "denied" and done["status"] == "failed"


def test_supervisor_delegates_to_specialists(platform: Platform, admin: Principal) -> None:
    for rel in ["architecture-reviewer", "security-reviewer", "code-quality-reviewer"]:
        create_built(platform, admin, example_spec(f"software-review/agents/{rel}.yaml"))
    coordinator = create_built(platform, admin, example_spec("software-review/agent.yaml"))
    result = run(platform, admin, coordinator, {"artifact": "payments-service"})
    assert result["status"] == "completed", result["error"]
    assert result["output"]["verdict"] == "changes_requested"
    detail = platform.executions.get(admin, result["id"])
    assert len(detail["children"]) == 3 and all(c["status"] == "completed" for c in detail["children"])
    assert all(c["parent_execution_id"] == result["id"] for c in detail["children"])
    assert result["budget_usage"]["delegations"] == 3
    types = events(platform, admin, result["id"])
    assert types.count("delegation.completed") == 3


def test_sequential_multi_agent_workflow(platform: Platform, admin: Principal) -> None:
    from agent_factory.spec.loader import load_spec

    create_built(platform, admin, example_spec("software-review/agents/security-reviewer.yaml"))
    create_built(platform, admin, example_spec("software-review/agents/code-quality-reviewer.yaml"))
    spec = load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent",
        "metadata": {"name": "sequential-review"},
        "spec": {
            "objective": "Run security then quality review in order and summarize.",
            "inputs": {"type": "object", "required": ["artifact"], "properties": {"artifact": {"type": "string"}}},
            "outputs": {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}},
            "workflow": {"type": "sequential", "steps": [
                {"id": "security", "kind": "delegate", "agent": "security-reviewer", "task": "Security review"},
                {"id": "quality", "kind": "delegate", "agent": "code-quality-reviewer",
                 "task": "Quality review; security found: {{ steps.security.output.summary }}"},
                {"id": "summarize", "kind": "model", "structured": True, "final": True}]},
            "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"artifact": "payments-service"}}]},
        },
    })
    agent_id = create_built(platform, admin, spec)
    result = run(platform, admin, agent_id, {"artifact": "payments-service"})
    assert result["status"] == "completed"
    children = platform.executions.get(admin, result["id"])["children"]
    assert [c["status"] for c in children] == ["completed", "completed"]


def test_cross_workspace_access_is_denied(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    outsider = add_user(platform, "mallory", "admin", workspace_slug="other-team")
    from agent_factory.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        platform.agents.get(outsider, pipeline_agent)
    with pytest.raises(NotFoundError):
        platform.executions.create(outsider, pipeline_agent, input_data={"pipeline_id": "a", "execution_id": "b"})
    result = run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    with pytest.raises(NotFoundError):
        platform.executions.get(outsider, result["id"])
    assert platform.executions.list(outsider)["total"] == 0


def test_viewer_cannot_run_or_build(platform: Platform, pipeline_agent: str) -> None:
    viewer = add_user(platform, "val", "viewer")
    with pytest.raises(PermissionDeniedError):
        platform.executions.create(viewer, pipeline_agent, input_data={"pipeline_id": "a", "execution_id": "b"})
    with pytest.raises(PermissionDeniedError):
        platform.agents.build(viewer, pipeline_agent)
    assert platform.agents.get(viewer, pipeline_agent)["name"] == "data-pipeline-diagnostic"


def test_conversation_memory_persists_across_executions(platform: Platform, admin: Principal) -> None:
    spec = platform.templates.instantiate(admin.workspace_id, "custom-agent", name="chatty")
    spec.spec.memory.enabled = None
    spec.spec.memory.conversation.enabled = True
    agent_id = create_built(platform, admin, spec)
    first = run(platform, admin, agent_id, {"task": "Remember the codeword: bluebird"})
    session_id = platform.executions._load(first["id"]).options["session_id"]
    second = run(platform, admin, agent_id, {"task": "What was the codeword?"}, options={"session_id": session_id})
    assert second["status"] == "completed"
    history = platform.conversation.history(session_id, 10)
    assert len(history) == 4 and "bluebird" in history[0]["content"]


def test_delete_execution_removes_data(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    result = run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    platform.executions.delete(admin, result["id"])
    from agent_factory.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        platform.executions.get(admin, result["id"])
    assert platform.executions.checkpointer.get_tuple({"configurable": {"thread_id": result["id"]}}) is None

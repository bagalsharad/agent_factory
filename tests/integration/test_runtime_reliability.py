"""Runtime reliability: crash-safe side effects, dead-lettering, retry budgets and circuit breakers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from agent_factory.core.circuit import CircuitBreaker, CircuitState
from agent_factory.core.errors import BudgetExceededError, PermissionDeniedError, ProviderError, ToolError
from agent_factory.core.ids import utcnow
from agent_factory.governance.rbac import Principal
from agent_factory.harness.budget import BudgetTracker
from agent_factory.model_gateway.types import ChatMessage, ChatRequest, ChatResponse
from agent_factory.platform import Platform
from agent_factory.spec.loader import load_spec
from agent_factory.spec.models import ExecutionLimits
from agent_factory.storage.models import Execution, ToolInvocation
from agent_factory.tools.base import SideEffect, Tool, ToolContext
from agent_factory.tools.registry import ToolExecutor
from tests.conftest import add_user, create_built


class ChargeInput(BaseModel):
    amount: int


class ChargeCard(Tool):
    """A non-idempotent payment: charging twice is the failure this guard exists to prevent."""

    name = "charge_card"
    description = "Charge the customer's card (test tool)"
    Input = ChargeInput
    side_effects = SideEffect.WRITE
    idempotent = False
    charges: ClassVar[list[int]] = []
    crash_next: ClassVar[bool] = False

    def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
        type(self).charges.append(args.amount)
        if type(self).crash_next:
            type(self).crash_next = False
            raise SystemExit("worker killed after the side effect, before the result was recorded")
        return {"charged": args.amount}


@pytest.fixture
def charge_agent(platform: Platform, admin: Principal) -> str:
    ChargeCard.charges, ChargeCard.crash_next = [], False
    if not platform.tools.has("charge_card"):
        platform.tools.register(ChargeCard())
    spec = load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": "charger", "version": "1.0.0"},
        "spec": {
            "objective": "Charge the customer exactly once for the order.",
            "inputs": {"type": "object", "required": ["amount"], "properties": {"amount": {"type": "integer"}}},
            "outputs": {"type": "object"},
            "tools": ["charge_card"],
            "policies": {"allow_write_operations": True},
            "human_approval": {"require_for_writes": False, "required_for_risk": []},
            "workflow": {"type": "graph", "steps": [
                {"id": "charge", "kind": "tool", "tool": "charge_card", "arguments": {"amount": "{{ input.amount }}"},
                 "final": True},
                {"id": "confirm", "kind": "model", "prompt": "Confirm the charge to the customer."}]},
            "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"amount": 1}}]},
        },
    })
    return create_built(platform, admin, spec)


def crash_mid_call(platform: Platform, admin: Principal, agent_id: str) -> str:
    ChargeCard.crash_next = True
    execution = platform.executions.create(admin, agent_id, input_data={"amount": 42})
    with pytest.raises(SystemExit):
        platform.executions.run_now(execution["id"])
    with platform.db.transaction() as session:  # the worker is gone: its heartbeat goes stale
        record = session.get(Execution, execution["id"])
        assert record is not None and record.status == "running"
        record.heartbeat_at = utcnow() - timedelta(hours=1)
    assert platform.executions.recover_stale() == 1
    platform.executions.run_now(execution["id"])
    return str(execution["id"])


def invocation(platform: Platform, execution_id: str) -> ToolInvocation:
    with platform.db.session() as session:
        row = session.query(ToolInvocation).filter_by(execution_id=execution_id, tool_name="charge_card").one()
        session.expunge(row)
        return row


def test_interrupted_side_effect_is_never_repeated(platform: Platform, admin: Principal, charge_agent: str) -> None:
    execution_id = crash_mid_call(platform, admin, charge_agent)
    result = platform.executions.get(admin, execution_id)
    assert result["status"] == "failed" and result["error"]["code"] == "in_doubt"
    assert ChargeCard.charges == [42]  # recovery did not charge again
    assert invocation(platform, execution_id).status == "in_doubt"
    platform.executions.retry(admin, execution_id)  # a plain retry still refuses to guess
    platform.executions.run_now(execution_id)
    assert ChargeCard.charges == [42] and platform.executions.get(admin, execution_id)["error"]["code"] == "in_doubt"

    developer = add_user(platform, "dev", "developer")
    with pytest.raises(PermissionDeniedError):
        platform.executions.resolve_in_doubt(developer, execution_id, invocation(platform, execution_id).id, performed=True)
    platform.executions.resolve_in_doubt(
        admin, execution_id, invocation(platform, execution_id).id, performed=True, note="charge seen in the PSP"
    )
    final = platform.executions.run_now(execution_id)
    assert final["status"] == "completed" and ChargeCard.charges == [42]
    assert invocation(platform, execution_id).decision["in_doubt_resolution"]["performed"] is True


def test_operator_can_allow_a_rerun(platform: Platform, admin: Principal, charge_agent: str) -> None:
    execution_id = crash_mid_call(platform, admin, charge_agent)
    platform.executions.resolve_in_doubt(admin, execution_id, invocation(platform, execution_id).id, performed=False,
                                         note="PSP shows no charge")
    assert platform.executions.run_now(execution_id)["status"] == "completed"
    assert ChargeCard.charges == [42, 42]  # exactly one deliberate re-run
    assert platform.executions.get(admin, execution_id)["output"] == {"charged": 42}


def test_repeated_worker_loss_dead_letters_the_execution(platform: Platform, admin: Principal, charge_agent: str) -> None:
    execution = platform.executions.create(admin, charge_agent, input_data={"amount": 5})
    with platform.db.transaction() as session:
        record = session.get(Execution, execution["id"])
        assert record is not None
        record.status, record.started_at, record.worker_id = "running", utcnow(), "worker-7"
        record.attempt = platform.settings.max_execution_attempts
        record.heartbeat_at = utcnow() - timedelta(hours=1)
    assert platform.executions.recover_stale() == 0
    dead = platform.executions.get(admin, execution["id"])
    assert dead["status"] == "dead_lettered" and dead["error"]["code"] == "dead_lettered"
    assert dead["error"]["details"]["attempts"] == 3 and dead["error"]["details"]["last_worker"] == "worker-7"
    assert any(e["type"] == "execution.dead_lettered" for e in platform.executions.events(admin, execution["id"])["items"])

    developer = add_user(platform, "dev", "developer")
    with pytest.raises(PermissionDeniedError):
        platform.executions.retry(developer, execution["id"])
    assert platform.executions.retry(admin, execution["id"])["status"] == "queued"
    final = platform.executions.run_now(execution["id"])
    assert final["status"] == "completed", final["error"]


def test_retry_budget_covers_the_whole_execution() -> None:
    budget = BudgetTracker(ExecutionLimits(max_total_retries=1))
    budget.record_retry()
    with pytest.raises(BudgetExceededError) as exceeded:
        budget.record_retry()
    assert exceeded.value.details["limit"] == "max_total_retries"


def test_circuit_breaker_states() -> None:
    now = [0.0]
    breaker = CircuitBreaker("x", failure_threshold=2, recovery_seconds=10, clock=lambda: now[0])
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED and breaker.allow()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN and not breaker.allow()
    now[0] = 10.0
    assert breaker.allow() and not breaker.allow()  # half-open: one trial at a time
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN  # failed trial reopens
    now[0] = 20.0
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED and breaker.snapshot()["opened"] == 2


def test_model_provider_circuit_opens_and_fails_fast(platform: Platform) -> None:
    calls: list[int] = []

    class Down:
        local = True

        def chat(self, request: ChatRequest) -> ChatResponse:
            calls.append(1)
            raise ProviderError("overloaded", retryable=True)

    entry = platform.gateway._entries["mock"]
    original = entry.provider
    entry.provider = Down()  # type: ignore[assignment]
    request = ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])
    try:
        for _ in range(platform.settings.circuit_failure_threshold):
            with pytest.raises(ProviderError):
                platform.gateway.chat("mock", request, max_retries=0)
        with pytest.raises(ProviderError) as fast:
            platform.gateway.chat("mock", request, max_retries=0)
        assert fast.value.details["code"] == "circuit_open"
        assert len(calls) == platform.settings.circuit_failure_threshold  # the open circuit made no call
        state = {c["name"]: c["state"] for c in platform.circuits.snapshot()}
        assert state["model:mock"] == "open"
    finally:
        entry.provider = original


def test_network_tool_circuit(platform: Platform) -> None:
    class FlakyInput(BaseModel):
        q: str

    class Flaky(Tool):
        name = "flaky_api"
        description = "Network tool that is down"
        Input = FlakyInput
        network = True
        calls = 0

        def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
            type(self).calls += 1
            raise ToolError("503 from upstream", retryable=True)

    executor = ToolExecutor(circuits=platform.circuits)
    ctx = ToolContext("ws", "ex", "agent", platform.settings, "key")
    for _ in range(platform.settings.circuit_failure_threshold):
        assert executor.execute(Flaky(), {"q": "x"}, ctx).error["code"] != "circuit_open"
    blocked = executor.execute(Flaky(), {"q": "x"}, ctx)
    assert blocked.error["code"] == "circuit_open" and Flaky.calls == platform.settings.circuit_failure_threshold

"""Agentic Continuity Engineering end to end: real executions, injected crashes and environment changes.

The order agent fetches a quote, analyzes it with the model, passes a crash point, places an order (a
non-idempotent side effect) at the quoted price and reports. Each test crashes the worker, changes something,
recovers, and checks what ACE decided and what actually happened, against conventional recovery where useful.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent_factory.core.errors import PermissionDeniedError, ValidationFailedError
from agent_factory.core.ids import utcnow
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from agent_factory.spec.loader import load_spec
from agent_factory.storage.models import Execution, StepRecord
from agent_factory.tools.base import SideEffect, Tool, ToolContext
from tests.conftest import add_user, create_built


class ItemInput(BaseModel):
    item: str


class PriceInput(BaseModel):
    item: str
    price: int


class EmptyInput(BaseModel):
    pass


def quote_tool(version: str, base: int) -> Tool:
    class Quote(Tool):
        name = "quote"
        description = "Current price for an item"
        Input = ItemInput

        def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
            World.quotes.append(version)
            return {"price": base + len(args.item), "version": version}

    Quote.version = version
    return Quote()


class World:
    quotes: ClassVar[list[str]] = []
    orders: ClassVar[list[tuple[str, int]]] = []
    crash: ClassVar[bool] = False


class CrashProbe(Tool):
    name = "crash_probe"
    description = "Does nothing, unless the test kills the worker here"
    Input = EmptyInput

    def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
        if World.crash:
            World.crash = False
            raise SystemExit("worker killed")
        return {"ok": True}


class PlaceOrder(Tool):
    name = "place_order"
    description = "Place the order (irreversible)"
    Input = PriceInput
    side_effects = SideEffect.WRITE
    idempotent = False

    def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
        World.orders.append((args.item, args.price))
        return {"order": len(World.orders)}


def order_spec(name: str, steps: list[str]) -> Any:
    defs = {
        "fetch": {"id": "fetch", "kind": "tool", "tool": "quote", "arguments": {"item": "{{ input.item }}"}},
        "analyze": {"id": "analyze", "kind": "model", "prompt": "Is {{ steps.fetch.output.price }} a fair price?"},
        "probe": {"id": "probe", "kind": "tool", "tool": "crash_probe", "arguments": {}},
        "act": {"id": "act", "kind": "tool", "tool": "place_order",
                "arguments": {"item": "{{ input.item }}", "price": "{{ steps.fetch.output.price }}"}},
        "report": {"id": "report", "kind": "model", "include_steps": ["act"], "final": True},
    }
    return load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": name, "version": "1.0.0"},
        "spec": {
            "objective": "Price the requested item and place the order at the current price.",
            "inputs": {"type": "object", "required": ["item"], "properties": {"item": {"type": "string"}}},
            "outputs": {"type": "object"},
            "tools": ["quote", "crash_probe", "place_order"],
            "policies": {"allow_write_operations": True},
            "human_approval": {"require_for_writes": False, "required_for_risk": []},
            "workflow": {"type": "graph", "steps": [defs[s] for s in steps]},
            "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"item": "widget"}}]},
        },
    })


@pytest.fixture(autouse=True)
def world(platform: Platform) -> None:
    World.quotes, World.orders, World.crash = [], [], False
    for tool in (quote_tool("1.0.0", 10), CrashProbe(), PlaceOrder()):
        platform.tools.register(tool)


BEFORE_ORDER = ["fetch", "analyze", "probe", "act", "report"]
AFTER_ORDER = ["fetch", "analyze", "act", "probe", "report"]


def crash_then_recover(platform: Platform, admin: Principal, agent_id: str, change: Any = None,
                       item: str = "widget") -> str:
    World.crash = True
    execution = platform.executions.create(admin, agent_id, input_data={"item": item})
    with pytest.raises(SystemExit):
        platform.executions.run_now(execution["id"])
    with platform.db.transaction() as session:
        record = session.get(Execution, execution["id"])
        assert record is not None
        record.heartbeat_at = utcnow() - timedelta(hours=1)
    assert platform.executions.recover_stale() == 1
    if change is not None:
        change()
    platform.executions.run_now(execution["id"])
    return str(execution["id"])


def latest_assessment(platform: Platform, admin: Principal, execution_id: str) -> dict[str, Any]:
    return platform.continuity.view(admin, execution_id)["assessments"][0]


def test_intent_ledger_and_semantic_checkpoints(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))
    execution = platform.executions.create(admin, agent_id, input_data={"item": "widget"})
    assert platform.executions.run_now(execution["id"])["status"] == "completed"
    view = platform.continuity.view(admin, execution["id"])
    [intent] = view["intent"]
    assert intent["version"] == 1 and intent["intent"]["goals"]["input"] == {"item": "widget"}
    assert intent["intent"]["authorization"]["tools"] == ["quote", "crash_probe", "place_order"]
    graph = {node["step_id"]: node for node in view["graph"]}
    assert graph["act"]["depends_on"] == {"input": True, "steps": ["fetch"]}
    act = graph["act"]["records"][0]
    assert act["side_effects"][0]["tool"] == "place_order" and act["environment"]["tools"]["place_order"]["version"]
    assert graph["analyze"]["records"][0]["environment"]["model"]["digest"] == "mock:mock-default:r1"


def test_plain_interruption_resumes(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))
    execution_id = crash_then_recover(platform, admin, agent_id)
    assert platform.executions.get(admin, execution_id)["status"] == "completed"
    assessment = latest_assessment(platform, admin, execution_id)
    assert assessment["decision"] == "resume" and assessment["scores"]["scs"] == 1.0
    assert World.quotes == ["1.0.0"] and World.orders == [("widget", 16)]


def test_tool_contract_change_replays_affected_work(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))
    execution_id = crash_then_recover(platform, admin, agent_id,
                                      change=lambda: platform.tools.register(quote_tool("2.0.0", 20)))
    assessment = latest_assessment(platform, admin, execution_id)
    assert assessment["decision"] == "replay" and assessment["from_step"] == "fetch"
    assert assessment["affected_steps"] == ["fetch", "analyze"]
    assert World.quotes == ["1.0.0", "2.0.0"] and World.orders == [("widget", 26)]  # ordered at the current price
    graph = {n["step_id"]: n for n in platform.continuity.view(admin, execution_id)["graph"]}
    assert [r["validity"] for r in graph["fetch"]["records"]] == ["invalidated", "valid"]


def test_conventional_recovery_acts_on_stale_decisions(platform: Platform, admin: Principal) -> None:
    """Baseline for RQ4: the same failure with continuity assessment off orders at the outdated price."""
    platform.settings.continuity_enabled = False
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))
    crash_then_recover(platform, admin, agent_id, change=lambda: platform.tools.register(quote_tool("2.0.0", 20)))
    assert World.orders == [("widget", 16)]  # the price under the old contract


def test_model_upgrade_replays_model_decisions_only(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))

    def upgrade() -> None:
        platform.gateway._entries["mock"].provider.revision = "2"  # type: ignore[union-attr]

    execution_id = crash_then_recover(platform, admin, agent_id, change=upgrade)
    assessment = latest_assessment(platform, admin, execution_id)
    assert assessment["decision"] == "replay" and assessment["from_step"] == "analyze"
    assert World.quotes == ["1.0.0"]  # the quote did not depend on the model: not redone
    graph = {n["step_id"]: n for n in platform.continuity.view(admin, execution_id)["graph"]}
    analyze = graph["analyze"]["records"]
    assert [r["validity"] for r in analyze] == ["invalidated", "valid"]
    assert analyze[-1]["environment"]["model"]["digest"] == "mock:mock-default:r2"


def test_committed_side_effect_escalates_and_operator_decides(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", AFTER_ORDER))
    execution_id = crash_then_recover(platform, admin, agent_id,
                                      change=lambda: platform.tools.register(quote_tool("2.0.0", 20)))
    held = platform.executions.get(admin, execution_id)
    assert held["status"] == "needs_review" and held["error"]["code"] == "continuity_review"
    assert "repeat committed side effects (place_order)" in latest_assessment(platform, admin, execution_id)["rationale"]

    developer = add_user(platform, "dev", "developer")
    with pytest.raises(PermissionDeniedError):
        platform.executions.continuity_decision(developer, execution_id, decision="resume", note="looks fine")
    platform.executions.continuity_decision(admin, execution_id, decision="resume",
                                            note="Order at the old price stands; report it as placed")
    assert platform.executions.run_now(execution_id)["status"] == "completed"
    assert World.orders == [("widget", 16)]  # never placed twice
    decisions = [a["status"] for a in platform.continuity.view(admin, execution_id)["assessments"]]
    assert decisions[0] == "overridden" and "needs_review" in decisions


def test_intent_amendment_replans_before_side_effects(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))
    World.crash = True
    execution = platform.executions.create(admin, agent_id, input_data={"item": "widget"})
    with pytest.raises(SystemExit):
        platform.executions.run_now(execution["id"])
    with platform.db.transaction() as session:
        record = session.get(Execution, execution["id"])
        assert record is not None
        record.heartbeat_at = utcnow() - timedelta(hours=1)
    platform.executions.recover_stale()

    developer = add_user(platform, "dev", "developer")
    with pytest.raises(PermissionDeniedError):
        platform.executions.amend_intent(developer, execution["id"], reason="customer changed item",
                                         task_input={"item": "gadget-xl"})
    with pytest.raises(ValidationFailedError, match="only tighten"):
        platform.executions.amend_intent(admin, execution["id"], reason="more room", limits={"max_steps": 400})
    amended = platform.executions.amend_intent(admin, execution["id"], reason="Customer changed the item",
                                               task_input={"item": "gadget-xl"})
    assert amended["intent"]["version"] == 2 and amended["preview"]["decision"] == "replan"
    assert platform.executions.run_now(execution["id"])["status"] == "completed"
    assert World.orders == [("gadget-xl", 19)]  # the amended goal, priced fresh
    assert latest_assessment(platform, admin, execution["id"])["decision"] == "replan"


def test_continuity_api(client: TestClient, platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, order_spec("orders", BEFORE_ORDER))
    execution = platform.executions.create(admin, agent_id, input_data={"item": "widget"}, inline=True)
    platform.executions.run_now(execution["id"])  # inline: the API's background worker must not race this run
    view = client.get(f"/api/v1/executions/{execution['id']}/continuity").json()
    assert view["enabled"] and len(view["graph"]) == 5 and view["intent"][0]["version"] == 1
    preview = client.post(f"/api/v1/executions/{execution['id']}/continuity/assess").json()
    assert preview["status"] == "preview" and preview["decision"] == "resume"
    assert set(preview["scores"]["dimensions"]) == {"goal_preservation", "constraint_preservation",
                                                    "decision_consistency", "action_integrity", "state_consistency"}
    with platform.db.session() as session:
        assert session.query(StepRecord).filter_by(execution_id=execution["id"]).count() == 5

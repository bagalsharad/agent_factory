"""Continuity benchmark (the experiment in the ACE paper, section 6): conventional recovery vs ACE.

Each scenario runs an order workflow (quote -> decide -> crash point -> place order -> report), kills the worker,
changes the environment as the scenario says, recovers, and scores the outcome against an oracle:

  runtime_interruption       nothing changes
  model_upgrade              the model's weights digest changes
  tool_contract_change       the quote tool's contract (version and prices) changes
  agent_replacement          the specialist agent the workflow delegates to is replaced by a new version
  outdated_shared_memory     the knowledge collection a step retrieved from is updated
  duplicate_external_action  the worker dies inside the order call, after the order was placed

Baseline A is conventional checkpoint-and-retry: resume from the last checkpoint and re-run whatever did not
finish (ACE off, in-doubt guard off). Experimental B is the same workflow with ACE on. Everything runs on the
deterministic mock model in a throwaway platform, so results are repeatable; they measure recovery mechanics,
not model quality.
"""

from __future__ import annotations

import contextlib
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select

from ..core.config import Settings
from ..core.ids import utcnow
from ..governance.rbac import Principal, make_principal
from ..spec.loader import load_spec
from ..storage.models import Agent, Execution, StepRecord, User, Workspace
from ..tools.base import SideEffect, Tool, ToolContext

SCENARIOS = (
    "runtime_interruption",
    "model_upgrade",
    "tool_contract_change",
    "agent_replacement",
    "outdated_shared_memory",
    "duplicate_external_action",
)
MODES = ("baseline", "ace")
ITEM = "widget"


# ---------------------------------------------------------------------------------------------- the world
@dataclass
class World:
    quote_base: int = 10
    quotes: list[str] = field(default_factory=list)
    orders: list[tuple[str, int]] = field(default_factory=list)
    crash_at: str | None = None  # "probe" | "order"


class _Item(BaseModel):
    item: str


class _Order(BaseModel):
    item: str
    price: int


class _Empty(BaseModel):
    pass


def _tools(world: World) -> list[Tool]:
    def quote(version: str, base: int) -> Tool:
        class Quote(Tool):
            name = "quote"
            description = "Current price for an item"
            Input = _Item

            def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
                world.quotes.append(version)
                return {"price": base + len(args.item), "version": version}

        Quote.version = version
        return Quote()

    class Probe(Tool):
        name = "crash_probe"
        description = "Crash point"
        Input = _Empty

        def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
            if world.crash_at == "probe":
                world.crash_at = None
                raise SystemExit("worker killed")
            return {"ok": True}

    class PlaceOrder(Tool):
        name = "place_order"
        description = "Place the order (irreversible)"
        Input = _Order
        side_effects = SideEffect.WRITE
        idempotent = False

        def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
            world.orders.append((args.item, args.price))
            if world.crash_at == "order":
                world.crash_at = None
                raise SystemExit("worker killed after the order was placed")
            return {"order": len(world.orders)}

    _tools.quote = quote  # type: ignore[attr-defined]
    return [quote("1.0.0", 10), Probe(), PlaceOrder()]


STEP_DEFS: dict[str, dict[str, Any]] = {
    "fetch": {"id": "fetch", "kind": "tool", "tool": "quote", "arguments": {"item": "{{ input.item }}"}},
    "analyze": {"id": "analyze", "kind": "model", "prompt": "Is {{ steps.fetch.output.price }} a fair price?"},
    "advise": {
        "id": "advise",
        "kind": "delegate",
        "agent": "price-advisor",
        "task": "Advise whether {{ steps.fetch.output.price }} is a fair price",
    },
    "policy": {"id": "policy", "kind": "retrieve", "query": "discount policy for orders"},
    "probe": {"id": "probe", "kind": "tool", "tool": "crash_probe", "arguments": {}},
    "act": {
        "id": "act",
        "kind": "tool",
        "tool": "place_order",
        "arguments": {"item": "{{ input.item }}", "price": "{{ steps.fetch.output.price }}"},
    },
    "report": {"id": "report", "kind": "model", "include_steps": ["act"], "final": True},
}
WORKFLOWS = {
    "runtime_interruption": ["fetch", "analyze", "probe", "act", "report"],
    "model_upgrade": ["fetch", "analyze", "probe", "act", "report"],
    "tool_contract_change": ["fetch", "analyze", "probe", "act", "report"],
    "agent_replacement": ["fetch", "advise", "probe", "act", "report"],
    "outdated_shared_memory": ["fetch", "policy", "probe", "act", "report"],
    "duplicate_external_action": ["fetch", "analyze", "act", "probe", "report"],
}
MUST_REDO = {  # steps whose earlier result is invalid after the change
    "runtime_interruption": set(),
    "model_upgrade": {"analyze"},
    "tool_contract_change": {"fetch", "analyze"},
    "agent_replacement": {"advise"},
    "outdated_shared_memory": {"policy"},
    "duplicate_external_action": set(),
}


def _spec(name: str, steps: list[str], knowledge: bool) -> Any:
    return load_spec(
        {
            "api_version": "agentfactory/v1",
            "kind": "Agent",
            "metadata": {"name": name, "version": "1.0.0"},
            "spec": {
                "objective": "Price the requested item and place the order at the current price.",
                "inputs": {"type": "object", "required": ["item"], "properties": {"item": {"type": "string"}}},
                "outputs": {"type": "object"},
                "tools": ["quote", "crash_probe", "place_order"],
                "policies": {"allow_write_operations": True},
                "human_approval": {"require_for_writes": False, "required_for_risk": []},
                "memory": {"knowledge": {"enabled": knowledge, "collections": ["pricing-policy"] if knowledge else []}},
                "workflow": {
                    "type": "sequential" if "advise" in steps else "graph",
                    "steps": [STEP_DEFS[s] for s in steps],
                },
                "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"item": ITEM}}]},
            },
        }
    )


def _advisor(version: str, instructions: str) -> Any:
    return load_spec(
        {
            "api_version": "agentfactory/v1",
            "kind": "Agent",
            "metadata": {"name": "price-advisor", "version": version},
            "spec": {
                "objective": "Advise whether a price is fair for the customer.",
                "instructions": instructions,
                "workflow": {
                    "type": "single",
                    "steps": [{"id": "advise", "kind": "model", "structured": True, "final": True}],
                },
                "evaluation": {"cases": [{"id": "c", "name": "c", "input": {"task": "Is 16 fair?"}}]},
            },
        }
    )


# ---------------------------------------------------------------------------------------------- one run
def _admin(platform: Any) -> Principal:
    with platform.db.session() as session:
        user = session.scalars(select(User).where(User.subject == "local-admin")).one()
        ws = session.scalars(select(Workspace).where(Workspace.slug == "default")).one()
    return make_principal(user.id, user.subject, user.display_name, ws.id, "admin", "local")


def _publish(platform: Any, admin: Principal, spec: Any, agent_id: str | None = None) -> str:
    if agent_id is None:
        agent_id = str(platform.agents.create(admin, spec)["id"])
    else:
        platform.agents.update(admin, agent_id, spec)
    report = platform.agents.build(admin, agent_id)
    assert report["success"], report.get("errors")
    run = platform.evaluations.start_run(admin, agent_id, wait=True)
    assert run["passed"], run["summary"]
    platform.agents.publish(admin, agent_id)
    return agent_id


def run_scenario(scenario: str, mode: str) -> dict[str, Any]:
    from ..platform import create_platform  # imported late: the platform imports this package

    with tempfile.TemporaryDirectory(prefix="af-ace-bench-") as tmp:
        settings = Settings(
            _env_file=None,
            data_dir=Path(tmp),
            ollama_enabled=False,
            log_level="ERROR",
            log_json=False,
            continuity_enabled=mode == "ace",
        )
        platform = create_platform(settings)
        try:
            return _run(platform, scenario, mode)
        finally:
            platform.stop()


def _run(platform: Any, scenario: str, mode: str) -> dict[str, Any]:
    platform.harness.in_doubt_guard = mode == "ace"
    admin = _admin(platform)
    world = World()
    for tool in _tools(world):
        platform.tools.register(tool)
    knowledge = scenario == "outdated_shared_memory"
    advisor_id = None
    if scenario == "agent_replacement":
        advisor_id = _publish(platform, admin, _advisor("1.0.0", "Be brief."))
    if knowledge:
        platform.knowledge.ensure_collection(admin.workspace_id, "pricing-policy")
        platform.knowledge.ingest_bytes(
            admin.workspace_id,
            "pricing-policy",
            "policy.md",
            b"# Discount policy\nOrders over 10 get a 5% discount policy for orders.",
        )
    agent_id = platform.agents.create(admin, _spec("orders", WORKFLOWS[scenario], knowledge))["id"]
    assert platform.agents.build(admin, agent_id)["success"]

    world.crash_at = "order" if scenario == "duplicate_external_action" else "probe"
    execution_id = platform.executions.create(admin, agent_id, input_data={"item": ITEM})["id"]
    with contextlib.suppress(SystemExit):
        platform.executions.run_now(execution_id)
    with platform.db.transaction() as session:
        record = session.get(Execution, execution_id)
        record.heartbeat_at = utcnow() - timedelta(hours=1)
        seq_before = (
            session.scalar(select(func.max(StepRecord.seq)).where(StepRecord.execution_id == execution_id)) or 0
        )
    platform.executions.recover_stale()

    # ------------------------------------------------------------------ the environment changes
    if scenario == "model_upgrade":
        platform.gateway._entries["mock"].provider.revision = "2"
    elif scenario == "tool_contract_change":
        platform.tools.register(_tools.quote("2.0.0", 20))  # type: ignore[attr-defined]
        world.quote_base = 20
    elif scenario == "agent_replacement":
        _publish(platform, admin, _advisor("1.1.0", "Be thorough and cite the market rate."), advisor_id)
    elif knowledge:
        platform.knowledge.ingest_bytes(
            admin.workspace_id,
            "pricing-policy",
            "policy.md",
            b"# Discount policy\nDiscount policy for orders withdrawn: no discounts.",
        )

    started = time.perf_counter()
    try:
        final = platform.executions.run_now(execution_id)
        status = final["status"]
    except SystemExit:
        status = "crashed"
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    with platform.db.session() as session:
        rows = list(
            session.scalars(select(StepRecord).where(StepRecord.execution_id == execution_id).order_by(StepRecord.seq))
        )
        executed_after = sum(1 for r in rows if r.seq > seq_before)
        completed_before = {r.step_id for r in rows if r.seq <= seq_before and r.status == "completed"}
        latest = {r.step_id: r for r in rows if r.validity != "invalidated"}
        advisor_version = None
        if advisor_id:
            advisor_version = session.get(Agent, advisor_id).active_version_id
    preview = platform.continuity.assess(execution_id, trigger="manual", persist=False)

    # ------------------------------------------------------------------ oracle
    price_now = world.quote_base + len(ITEM)
    duplicates = max(0, len(world.orders) - 1)
    stale_orders = sum(1 for _, price in world.orders if price != price_now)
    consistent = True
    env = preview["environment"]
    if scenario == "model_upgrade" and "analyze" in latest:
        consistent = latest["analyze"].environment.get("model", {}).get("digest") == env["model"]["digest"]
    if scenario == "agent_replacement" and "advise" in latest:
        spec_env = latest["advise"].environment.get("specialists", {}).get("price-advisor", {})
        consistent = spec_env.get("version_id") == advisor_version
    if scenario == "outdated_shared_memory" and "policy" in latest:
        consistent = latest["policy"].environment.get("knowledge") == env["knowledge"]
    workflow = WORKFLOWS[scenario]
    remaining = [s for s in workflow if s not in completed_before]
    minimal = len(set(remaining) | (MUST_REDO[scenario] & completed_before))
    escalated = status == "needs_review"
    unsafe = duplicates + stale_orders
    correct = status == "completed" and unsafe == 0 and consistent and len(world.orders) == 1
    return {
        "scenario": scenario,
        "mode": mode,
        "status": status,
        "recovery_correct": correct,
        "escalated": escalated,
        "unsafe_actions": unsafe,
        "intent_drift": int(status == "completed" and (not consistent or stale_orders > 0)),
        "recovery_latency_ms": latency_ms,
        "re_execution_overhead": max(0, executed_after - minimal) if status == "completed" else 0,
        "scs": preview["scores"]["scs"],
        "decision": preview["decision"] if mode == "baseline" else _ace_decision(platform, execution_id),
        "orders": world.orders,
    }


def _ace_decision(platform: Any, execution_id: str) -> str | None:
    from ..storage.models import ContinuityAssessment

    with platform.db.session() as session:
        row = session.scalars(
            select(ContinuityAssessment)
            .where(ContinuityAssessment.execution_id == execution_id)
            .order_by(ContinuityAssessment.created_at)
        ).first()
        return row.decision if row else None


# ---------------------------------------------------------------------------------------------- aggregate
def run_benchmark(runs: int = 1, scenarios: tuple[str, ...] = SCENARIOS) -> dict[str, Any]:
    rows = [run_scenario(s, m) for _ in range(runs) for s in scenarios for m in MODES]

    def rate(mode: str, key: str) -> float:
        items = [r for r in rows if r["mode"] == mode]
        return round(sum(1 for r in items if r[key]) / len(items), 3)

    def mean(mode: str, key: str) -> float:
        return round(statistics.fmean(r[key] for r in rows if r["mode"] == mode), 3)

    summary = {
        mode: {
            "recovery_correctness": rate(mode, "recovery_correct"),
            "unsafe_action_rate": rate(mode, "unsafe_actions"),
            "intent_drift_rate": rate(mode, "intent_drift"),
            "human_escalation_rate": rate(mode, "escalated"),
            "mean_scs": mean(mode, "scs"),
            "mean_recovery_latency_ms": mean(mode, "recovery_latency_ms"),
            "mean_re_execution_overhead": mean(mode, "re_execution_overhead"),
        }
        for mode in MODES
    }
    return {
        "runs_per_scenario": runs,
        "model": "mock/mock-default (deterministic)",
        "summary": summary,
        "results": rows,
    }

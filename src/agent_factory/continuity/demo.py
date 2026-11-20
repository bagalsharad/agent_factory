"""A self-contained ACE demo instance for trying continuity in the dashboard.

Seeds executions of the benchmark's order workflow in the states worth exploring, and keeps the demo tools
registered in the serving process so operator decisions, intent amendments and in-doubt resolutions really run.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from ..core.config import Settings
from ..core.ids import utcnow
from ..storage.models import Agent, Execution
from . import benchmark as b

SCENARIOS = [
    (
        "replayed",
        "Replayed after a tool contract change",
        "The quote tool moved to 2.0.0 while the worker was down. ACE replayed fetch and analyze; the order used the new price.",
    ),
    (
        "held-side-effect",
        "Held: redoing would repeat an order",
        "Same contract change, but the order was already placed. Decide: resume as is, replay, re-plan or cancel.",
    ),
    (
        "held-in-doubt",
        "Held: order interrupted mid-call",
        "The worker died inside place_order. Mark the order as performed (or not), then let it continue.",
    ),
    (
        "amend",
        "Stopped: amend the intent",
        'Failed before ordering. Amend the input (e.g. {"item": "gadget-xl"}), then Recover: ACE re-plans.',
    ),
]


def _register(platform: Any, world: b.World) -> None:
    for tool in b._tools(world):
        if not platform.tools.has(tool.name, tool.version):
            platform.tools.register(tool)
    if not platform.tools.has("quote", "2.0.0"):
        platform.tools.register(b._tools.quote("2.0.0", 20))  # type: ignore[attr-defined]


def _crash(platform: Any, admin: Any, agent_id: str, world: b.World, at: str) -> str:
    world.crash_at = at
    execution_id = str(platform.executions.create(admin, agent_id, input_data={"item": b.ITEM})["id"])
    with contextlib.suppress(SystemExit):
        platform.executions.run_now(execution_id)
    with platform.db.transaction() as session:
        record = session.get(Execution, execution_id)
        record.heartbeat_at = utcnow() - timedelta(hours=1)
    return execution_id


def build_demo(platform: Any) -> list[dict[str, Any]]:
    """Register the demo tools and, on an empty instance, seed the scenarios. Returns the scenario list."""
    world = b.World()
    with platform.db.session() as session:
        seeded = session.scalars(select(Agent).where(Agent.name.like("ace-demo-%"))).first() is not None
    if seeded:
        _register(platform, world)
        return _listing(platform)
    for tool in b._tools(world):
        platform.tools.register(tool)
    admin = b._admin(platform)
    agents = {}
    for key, steps in (
        ("replayed", b.WORKFLOWS["tool_contract_change"]),
        ("held-side-effect", b.WORKFLOWS["duplicate_external_action"]),
        ("held-in-doubt", b.WORKFLOWS["tool_contract_change"]),
        ("amend", b.WORKFLOWS["tool_contract_change"]),
    ):
        agents[key] = platform.agents.create(admin, b._spec(f"ace-demo-{key}", steps, False))["id"]
        assert platform.agents.build(admin, agents[key])["success"]
    replayed = _crash(platform, admin, agents["replayed"], world, "probe")
    held = _crash(platform, admin, agents["held-side-effect"], world, "probe")
    platform.executions.recover_stale()
    platform.tools.register(b._tools.quote("2.0.0", 20))  # type: ignore[attr-defined]  # the contract changes
    platform.executions.run_now(replayed)
    platform.executions.run_now(held)
    in_doubt = _crash(platform, admin, agents["held-in-doubt"], world, "order")
    platform.executions.recover_stale()
    platform.executions.run_now(in_doubt)
    amend = _crash(platform, admin, agents["amend"], world, "probe")
    with platform.db.transaction() as session:
        record = session.get(Execution, amend)
        record.status, record.finished_at, record.worker_id = "failed", utcnow(), None
        record.error = {"code": "worker_lost", "message": "The worker stopped before the order step (demo)"}
    return _listing(platform)


def _listing(platform: Any) -> list[dict[str, Any]]:
    out = []
    with platform.db.session() as session:
        for key, title, hint in SCENARIOS:
            agent = session.scalars(select(Agent).where(Agent.name == f"ace-demo-{key}")).first()
            execution = (
                session.scalars(
                    select(Execution).where(Execution.agent_id == agent.id).order_by(Execution.created_at)
                ).first()
                if agent
                else None
            )
            if execution:
                out.append(
                    {"key": key, "title": title, "hint": hint, "execution_id": execution.id, "status": execution.status}
                )
    return out


def demo_settings(data_dir: Any, port: int) -> Settings:
    return Settings(
        _env_file=None, data_dir=data_dir, ollama_enabled=False, port=port, log_level="WARNING", log_json=False
    )

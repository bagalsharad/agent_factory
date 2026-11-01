"""Compile a declarative workflow into a LangGraph state graph.

Patterns:
* **single** / **graph** — linear sequence of steps (``agent_loop`` steps expand into a model ⇄ tools cycle).
* **sequential** — ``delegate`` steps run specialist agents in order with structured handoffs.
* **supervisor** — an ``agent_loop`` whose tools include bounded ``delegate_to_<specialist>`` functions.

The graph contains no model-generated code. Every node calls a ``Harness`` method.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..core.ids import content_hash
from ..harness.harness import ExecutionContext, Harness
from ..spec.models import AgentSpec, WorkflowStep


def merge(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class RunState(TypedDict, total=False):
    input: dict[str, Any]
    steps: Annotated[dict[str, Any], merge]
    loops: Annotated[dict[str, Any], merge]
    pending: Annotated[dict[str, Any], merge]
    iterations: Annotated[dict[str, Any], merge]
    halted: dict[str, Any] | None
    final: dict[str, Any] | None


FINALIZE = "__finalize__"


def tools_node(step_id: str) -> str:
    return f"{step_id}__tools"


def compile_workflow(spec: AgentSpec, harness: Harness, ctx: ExecutionContext, checkpointer: Any = None) -> Any:
    steps = spec.spec.workflow.steps
    graph: Any = StateGraph(RunState)  # typed loosely: node callables are built dynamically

    def node(
        step: WorkflowStep, kind: str, fn: Callable[..., dict[str, Any]]
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def run(state: dict[str, Any]) -> dict[str, Any]:
            return harness.run_node(ctx, node_id(step, kind), kind, lambda: fn(ctx, state, step), step)

        return run

    def node_id(step: WorkflowStep, kind: str) -> str:
        return tools_node(step.id) if kind == "agent_loop_tools" else step.id

    dispatch = {
        "model": harness.model_step,
        "tool": harness.tool_step,
        "validate": harness.validate_step,
        "approval": harness.approval_step,
        "delegate": harness.delegate_step,
        "retrieve": harness.retrieve_step,
    }
    for step in steps:
        if step.kind == "agent_loop":
            graph.add_node(step.id, node(step, "agent_loop", harness.agent_loop_model))
            graph.add_node(tools_node(step.id), node(step, "agent_loop_tools", harness.agent_loop_tools))
        else:
            graph.add_node(step.id, node(step, step.kind, dispatch[step.kind]))
    graph.add_node(FINALIZE, lambda state: harness.finalize(ctx, state))

    graph.add_edge(START, steps[0].id if steps else FINALIZE)
    for i, step in enumerate(steps):
        nxt = steps[i + 1].id if i + 1 < len(steps) else FINALIZE

        def route(state: dict[str, Any], _nxt: str = nxt) -> str:
            return FINALIZE if state.get("halted") else _nxt

        if step.kind == "agent_loop":

            def loop_route(state: dict[str, Any], _id: str = step.id, _nxt: str = nxt) -> str:
                if state.get("halted"):
                    return FINALIZE
                return tools_node(_id) if state.get("pending", {}).get(_id) else _nxt

            graph.add_conditional_edges(step.id, loop_route, sorted({tools_node(step.id), nxt, FINALIZE}))
            graph.add_edge(tools_node(step.id), step.id)
        else:
            graph.add_conditional_edges(step.id, route, sorted({nxt, FINALIZE}))
    graph.add_edge(FINALIZE, END)
    return graph.compile(checkpointer=checkpointer)


def describe_workflow(spec: AgentSpec) -> dict[str, Any]:
    """Static graph description for visualization and provenance (no harness needed)."""
    wf = spec.spec.workflow
    nodes: list[dict[str, Any]] = [{"id": "__start__", "kind": "start", "label": "Start"}]
    edges: list[dict[str, Any]] = []
    previous = "__start__"
    for step in wf.steps:
        nodes.append(
            {
                "id": step.id,
                "kind": step.kind,
                "label": step.id.replace("_", " "),
                "tool": step.tool,
                "agent": step.agent,
                "action": step.action,
                "final": step.final,
                "description": step.description,
                "structured": step.structured,
            }
        )
        edges.append({"from": previous, "to": step.id})
        if step.kind == "agent_loop":
            tid = tools_node(step.id)
            loop_tools = step.tools if step.tools is not None else spec.spec.tool_names
            nodes.append({"id": tid, "kind": "tools", "label": "tools", "tools": loop_tools})
            edges.append({"from": step.id, "to": tid, "label": "tool calls"})
            edges.append({"from": tid, "to": step.id, "label": "results"})
            if wf.type == "supervisor":
                for sp in wf.specialists:
                    sid = f"specialist:{sp.id}"
                    nodes.append(
                        {
                            "id": sid,
                            "kind": "specialist",
                            "label": sp.id,
                            "agent": sp.agent,
                            "description": sp.description,
                        }
                    )
                    edges.append({"from": tid, "to": sid, "label": "delegate", "style": "dashed"})
        previous = step.id
    nodes.append({"id": FINALIZE, "kind": "finalize", "label": "Finalize"})
    edges.append({"from": previous, "to": FINALIZE})
    nodes.append({"id": "__end__", "kind": "end", "label": "End"})
    edges.append({"from": FINALIZE, "to": "__end__"})
    definition = wf.model_dump(mode="json")
    return {"pattern": pattern_name(spec), "nodes": nodes, "edges": edges, "hash": content_hash(definition)}


def pattern_name(spec: AgentSpec) -> str:
    wf = spec.spec.workflow
    if wf.type == "supervisor":
        return "supervisor_specialists"
    if wf.type == "sequential":
        return "sequential_multi_agent"
    return "single_agent"

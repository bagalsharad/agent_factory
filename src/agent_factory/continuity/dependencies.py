"""Execution State Graph structure: which inputs and earlier steps each workflow step depends on."""

from __future__ import annotations

import re
from typing import Any

from ..spec.models import AgentSpec

REF = re.compile(r"\{\{\s*(input|steps)(?:\.([A-Za-z0-9_\-]+))?")
DECISION_KINDS = {"model", "agent_loop", "agent_loop_tools", "delegate"}  # nodes whose output a model shaped


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def step_dependencies(spec: AgentSpec) -> dict[str, dict[str, Any]]:
    """{step_id: {"input": bool, "steps": [earlier step ids]}} from template references and context rules.

    Model-driven steps (model, agent_loop, delegate) see the task input and earlier step outputs in their context:
    all earlier steps unless ``include_steps`` narrows it.
    """
    steps = spec.spec.workflow.steps
    out: dict[str, dict[str, Any]] = {}
    for index, step in enumerate(steps):
        earlier = [s.id for s in steps[:index]]
        uses_input, refs = False, set()
        for text in _strings(step.model_dump(mode="json")):
            for root, name in REF.findall(text):
                if root == "input":
                    uses_input = True
                elif name in earlier:
                    refs.add(name)
        if step.kind in {"model", "agent_loop", "delegate"}:
            uses_input = True
            refs |= set(step.include_steps if step.include_steps is not None else earlier) & set(earlier)
        out[step.id] = {"input": uses_input, "steps": [s for s in earlier if s in refs]}
    return out


def downstream(dependencies: dict[str, dict[str, Any]], seeds: set[str]) -> set[str]:
    """Seeds plus every step that (transitively) consumes their output."""
    affected = set(seeds)
    changed = True
    while changed:
        changed = False
        for step_id, deps in dependencies.items():
            if step_id not in affected and affected & set(deps["steps"]):
                affected.add(step_id)
                changed = True
    return affected


def input_dependents(dependencies: dict[str, dict[str, Any]]) -> set[str]:
    return {step_id for step_id, deps in dependencies.items() if deps["input"]}

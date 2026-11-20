"""ACE rules as pure functions: dependency extraction, change impact, validation, score and recovery decisions."""

from __future__ import annotations

from typing import Any

from agent_factory.continuity.assessment import analyze_changes, assess, decide, score, validate
from agent_factory.continuity.dependencies import downstream, step_dependencies
from agent_factory.spec.loader import load_spec

SPEC = load_spec({
    "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": "orders", "version": "1.0.0"},
    "spec": {
        "objective": "Price and place an order for the requested item.",
        "inputs": {"type": "object", "properties": {"item": {"type": "string"}}},
        "outputs": {"type": "object"},
        "tools": ["quote", "place_order"],
        "policies": {"allow_write_operations": True},
        "workflow": {"type": "graph", "steps": [
            {"id": "fetch", "kind": "tool", "tool": "quote", "arguments": {"item": "{{ input.item }}"}},
            {"id": "analyze", "kind": "model", "prompt": "Is {{ steps.fetch.output.price }} fair?"},
            {"id": "probe", "kind": "tool", "tool": "quote", "arguments": {"item": "constant"}},
            {"id": "act", "kind": "tool", "tool": "place_order",
             "arguments": {"price": "{{ steps.fetch.output.price }}"}},
            {"id": "report", "kind": "model", "include_steps": ["act"], "final": True},
        ]},
    },
})
ORDER = [s.id for s in SPEC.spec.workflow.steps]
DEPS = step_dependencies(SPEC)
ENV = {"spec_hash": "s1", "policy_hash": "p1", "platform": "x1",
       "model": {"provider": "mock", "model": "m", "digest": "d1", "available": True},
       "tools": {"quote": {"registered": True, "version": "1.0.0", "hash": "q1", "enabled": True},
                 "place_order": {"registered": True, "version": "1.0.0", "hash": "o1", "enabled": True}},
       "specialists": {}, "knowledge": {}}


def rec(step: str, kind: str, **env: Any) -> dict[str, Any]:
    base = {"spec_hash": "s1", "policy_hash": "p1", "platform": "x1"}
    return {"step_id": step, "kind": kind, "environment": {**base, **env}, "input_digest": "i", "intent_version": 1}


RECORDS = [rec("fetch", "tool", tools={"quote": ENV["tools"]["quote"]}),
           rec("analyze", "model", model=ENV["model"]),
           rec("probe", "tool", tools={"quote": ENV["tools"]["quote"]})]
OK = {"spec_ok": True, "intent_ok": True}


def run(current: dict[str, Any] = ENV, records: list[dict[str, Any]] = RECORDS, *, trigger: str = "recover",
        invocations: list[dict[str, Any]] | None = None, goals_changed: bool = False) -> Any:
    return assess(trigger=trigger, order=ORDER, records=records, current=current, dependencies=DEPS,
                  goals_changed=goals_changed, constraints_changed=False, invocations=invocations or [],
                  integrity=OK, current_digests={})


def test_dependencies_follow_templates_and_context_rules() -> None:
    assert DEPS["fetch"] == {"input": True, "steps": []}
    assert DEPS["analyze"] == {"input": True, "steps": ["fetch"]}  # model steps see the input and earlier steps
    assert DEPS["probe"] == {"input": False, "steps": []}  # no references: independent
    assert DEPS["act"] == {"input": False, "steps": ["fetch"]}
    assert DEPS["report"]["steps"] == ["act"]  # include_steps narrows a model step's context
    assert downstream(DEPS, {"fetch"}) == {"fetch", "analyze", "act", "report"}


def test_nothing_changed_resumes_with_full_score() -> None:
    result = run()
    assert result.decision == "resume" and result.changes == []
    assert result.scores["scs"] == 1.0 and result.scores["hard_invariants"] == 1


def test_tool_contract_change_replays_only_the_affected_branch() -> None:
    current = {**ENV, "tools": {**ENV["tools"], "quote": {**ENV["tools"]["quote"], "version": "2.0.0", "hash": "q2"}}}
    records = [RECORDS[0], RECORDS[1]]  # probe has not run yet
    result = run(current, records)
    assert result.decision == "replay" and result.from_step == "fetch"
    assert result.affected == ["fetch", "analyze"]
    assert any("contract changed (1.0.0 → 2.0.0)" in c["detail"] for c in result.changes)
    assert result.scores["dimensions"]["decision_consistency"] == 0.0


def test_model_upgrade_replays_decisions_but_not_tools() -> None:
    current = {**ENV, "model": {**ENV["model"], "digest": "d2"}}
    result = run(current)
    assert result.decision == "replay" and result.from_step == "analyze" and result.affected == ["analyze"]


def test_platform_policy_change_without_affected_decisions_revalidates() -> None:
    changes, direct = analyze_changes([rec("analyze", "model", model=ENV["model"])], {**ENV, "platform": "x2"},
                                      goals_changed=False, constraints_changed=False, dependencies=DEPS)
    assert changes == [] and direct == set()  # model-only steps do not depend on tool/network policy
    result = run({**ENV, "platform": "x2"}, [rec("probe", "tool", tools={"quote": ENV["tools"]["quote"]})])
    assert result.decision == "replay" and result.from_step == "probe"  # tool steps re-check under the new policy


def test_committed_side_effects_are_never_replayed() -> None:
    current = {**ENV, "tools": {**ENV["tools"], "quote": {**ENV["tools"]["quote"], "hash": "q2"}}}
    records = [*RECORDS, rec("act", "tool", tools={"place_order": ENV["tools"]["place_order"]})]
    order_placed = [{"id": "i1", "tool": "place_order", "step_id": "act", "status": "completed", "side_effect": True,
                     "args_hash": "a", "policy_now": "allow"}]
    result = run(current, records, invocations=order_placed)
    assert result.decision == "escalate" and "repeat committed side effects (place_order)" in result.rationale


def test_goal_amendment_replans_unless_something_irreversible_happened() -> None:
    assert run(goals_changed=True).decision == "replan"
    placed = [{"id": "i1", "tool": "place_order", "step_id": "act", "status": "completed", "side_effect": True,
               "args_hash": "a", "policy_now": "allow"}]
    assert run(goals_changed=True, invocations=placed).decision == "escalate"


def test_hard_invariants_gate_the_score() -> None:
    dupes = [{"id": f"i{n}", "tool": "place_order", "step_id": "act", "status": "completed", "side_effect": True,
              "args_hash": "same", "policy_now": "allow"} for n in (1, 2)]
    result = run(invocations=dupes)
    assert result.decision == "escalate" and result.scores["hard_invariants"] == 0 and result.scores["scs"] == 0.0
    assert result.scores["dimensions"]["goal_preservation"] == 1.0  # components stay visible behind the gate
    tampered = assess(trigger="recover", order=ORDER, records=RECORDS, current=ENV, dependencies=DEPS,
                      goals_changed=False, constraints_changed=False, invocations=[],
                      integrity={"spec_ok": False, "intent_ok": True}, current_digests={})
    assert tampered.decision == "escalate" and "spec_integrity" in tampered.scores["critical_failures"]


def test_in_doubt_actions_escalate_and_lower_action_integrity() -> None:
    doubtful = [{"id": "i1", "tool": "place_order", "step_id": "act", "status": "in_doubt", "side_effect": True,
                 "args_hash": "a"}]
    result = run(invocations=doubtful)
    assert result.decision == "escalate" and result.scores["dimensions"]["action_integrity"] == 0.0
    assert result.scores["hard_invariants"] == 1  # uncertainty is not a violation, but it still needs a human


def test_revoked_authorization_is_a_constraint_failure() -> None:
    read = [{"id": "i1", "tool": "quote", "step_id": "fetch", "status": "completed", "side_effect": False,
             "args_hash": "a", "policy_now": "deny", "policy_rules": ["rbac.permission"]}]
    findings = validate(records=RECORDS, changes=[], invocations=read, integrity=OK, current_digests={})
    failure = next(f for f in findings if f["check"] == "constraint_preservation" and f["status"] == "fail")
    assert failure["severity"] == "major" and failure["step"] == "fetch"
    assert run(invocations=read).decision == "replay"
    write = [{**read[0], "tool": "place_order", "side_effect": True, "step_id": "act"}]
    assert run(invocations=write).scores["hard_invariants"] == 0


def test_approval_waits_never_replay_automatically() -> None:
    current = {**ENV, "model": {**ENV["model"], "digest": "d2"}}
    assert run(current, trigger="resume").decision == "escalate"


def test_state_inconsistency_marks_the_step() -> None:
    findings = validate(records=RECORDS, changes=[], invocations=[], integrity=OK, current_digests={"analyze": "other"})
    assert any(f["check"] == "state_consistency" and f["step"] == "analyze" for f in findings)
    scores = score(records=RECORDS, findings=findings, affected=set(), goal_affected=set(), invocations=[])
    assert scores["dimensions"]["state_consistency"] == round(2 / 3, 4)
    assert decide(trigger="recover", order=ORDER, records=RECORDS, changes=[], affected=set(), findings=[],
                  invocations=[], goals_changed=False)[0] == "resume"

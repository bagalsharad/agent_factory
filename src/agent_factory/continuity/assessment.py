"""Change Impact Analyzer, Continuity Validator, Semantic Continuity Score and Recovery Decision Engine.

Pure functions over recorded facts (step records, intent versions, invocations, the current environment), so every
rule is deterministic and testable. No model judges continuity here: the paper's validator must produce
evidence-backed findings, and critical findings gate the score (H = 0) whatever the other dimensions say.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .dependencies import DECISION_KINDS, downstream, input_dependents

WEIGHTS = {
    "goal_preservation": 0.2,
    "constraint_preservation": 0.2,
    "decision_consistency": 0.2,
    "action_integrity": 0.2,
    "state_consistency": 0.2,
}
DECISIONS = ("resume", "revalidate_resume", "replay", "replan", "escalate")


@dataclass
class Assessment:
    changes: list[dict[str, Any]] = field(default_factory=list)
    affected: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    scores: dict[str, Any] = field(default_factory=dict)
    decision: str = "resume"
    from_step: str | None = None
    rationale: str = ""


def finding(
    check: str, status: str, severity: str, detail: str, step: str | None = None, evidence: Any = None
) -> dict[str, Any]:
    return {
        "check": check,
        "status": status,
        "severity": severity,
        "detail": detail,
        "step": step,
        "evidence": evidence,
    }


# ------------------------------------------------------------------------------------------- change impact
def analyze_changes(
    records: list[dict[str, Any]],
    current: dict[str, Any],
    *,
    goals_changed: bool,
    constraints_changed: bool,
    dependencies: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Compare each step's recorded environment with the current one. Returns changes and directly changed steps."""
    changes: list[dict[str, Any]] = []
    direct: set[str] = set()

    def add(kind: str, step: str | None, before: Any, after: Any, detail: str) -> None:
        changes.append({"type": kind, "step": step, "before": before, "after": after, "detail": detail})
        if step:
            direct.add(step)

    for rec in records:
        env, step = rec["environment"], rec["step_id"]
        model = env.get("model")
        now = current.get("model", {})
        if model and rec["kind"] in DECISION_KINDS:
            if (model["provider"], model["model"]) != (now.get("provider"), now.get("model")):
                add(
                    "model",
                    step,
                    f"{model['provider']}/{model['model']}",
                    f"{now.get('provider')}/{now.get('model')}",
                    "a different model now serves this agent",
                )
            elif model.get("digest") and now.get("digest") and model["digest"] != now["digest"]:
                add(
                    "model",
                    step,
                    model["digest"],
                    now["digest"],
                    f"{model['model']} was upgraded (weights digest changed)",
                )
            elif model.get("digest") and not now.get("available", True):
                add("model", step, model["digest"], None, f"{model['model']} is no longer available")
        for name, before in (env.get("tools") or {}).items():
            after = current.get("tools", {}).get(name, {"registered": False})
            if not after.get("registered"):
                add("tool", step, before.get("version"), None, f"tool '{name}' is no longer registered")
            elif not after.get("enabled", True):
                add("tool", step, before.get("version"), after.get("version"), f"tool '{name}' is disabled")
            elif before.get("hash") != after.get("hash"):
                add(
                    "tool",
                    step,
                    before.get("version"),
                    after.get("version"),
                    f"tool '{name}' contract changed ({before.get('version')} → {after.get('version')})",
                )
        for ref, before in (env.get("specialists") or {}).items():
            after = current.get("specialists", {}).get(ref, {"available": False})
            if before.get("version_id") != after.get("version_id"):
                add(
                    "agent",
                    step,
                    before.get("version"),
                    after.get("version"),
                    f"specialist '{ref}' was replaced ({before.get('version')} → {after.get('version') or 'unavailable'})",
                )
        for name, before in (env.get("knowledge") or {}).items():
            after = current.get("knowledge", {}).get(name)
            if before != after:
                add("knowledge", step, before, after, f"knowledge collection '{name}' changed")
        if env.get("platform") and env["platform"] != current.get("platform") and env.get("tools"):
            add(
                "platform_policy",
                step,
                env["platform"],
                current.get("platform"),
                "platform tool/network policy changed since this step ran",
            )
    for key in ("spec_hash", "policy_hash"):
        seen = {rec["environment"].get(key) for rec in records if rec["environment"].get(key)}
        if seen and seen != {current.get(key)}:
            add(key, None, sorted(seen), current.get(key), f"{key.replace('_', ' ')} differs from the recorded value")
    if goals_changed:
        for step in sorted(input_dependents(dependencies) & {r["step_id"] for r in records}):
            add("intent_goals", step, None, None, "the intent's goals (task input) were amended")
        if not records:
            add("intent_goals", None, None, None, "the intent's goals (task input) were amended")
    if constraints_changed:
        add("intent_constraints", None, None, None, "the intent's constraints were tightened")
    return changes, direct


# ------------------------------------------------------------------------------------------- validator
def validate(
    *,
    records: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    invocations: list[dict[str, Any]],
    integrity: dict[str, Any],
    current_digests: dict[str, str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not integrity.get("spec_ok", True):
        findings.append(
            finding(
                "spec_integrity",
                "fail",
                "critical",
                "The agent version's specification no longer matches its recorded hash",
                evidence=integrity.get("spec_detail"),
            )
        )
    else:
        findings.append(finding("spec_integrity", "pass", "info", "Specification matches its recorded hash"))
    if not integrity.get("intent_ok", True):
        findings.append(
            finding("intent_integrity", "fail", "critical", integrity.get("intent_detail", "Intent ledger tampered"))
        )
    else:
        findings.append(
            finding("intent_integrity", "pass", "info", "Intent ledger is intact and every amendment is authorized")
        )
    if any(c["type"] in {"spec_hash", "policy_hash"} for c in changes):
        findings.append(
            finding(
                "acceptance_and_policy",
                "fail",
                "critical",
                "Specification or policy differ from what completed steps ran under",
            )
        )

    side_effects = [i for i in invocations if i["side_effect"]]
    committed = [i for i in side_effects if i["status"] == "completed"]
    doubtful = [i for i in side_effects if i["status"] in {"pending", "in_doubt"}]
    dupes = [key for key, n in Counter((i["tool"], i["args_hash"]) for i in committed).items() if n > 1]
    if dupes:
        findings.append(
            finding(
                "action_integrity",
                "fail",
                "critical",
                f"The same side-effecting action was committed more than once: {dupes}",
                evidence=[i["id"] for i in committed if (i["tool"], i["args_hash"]) in dupes],
            )
        )
    if doubtful:
        findings.append(
            finding(
                "action_integrity",
                "fail",
                "major",
                "Side-effecting actions have an unknown outcome (interrupted mid-call)",
                evidence=[i["id"] for i in doubtful],
            )
        )
    if not dupes and not doubtful:
        findings.append(
            finding(
                "action_integrity",
                "pass",
                "info",
                f"{len(committed)} committed side effect(s), none duplicated or in doubt",
            )
        )

    denied = [i for i in invocations if i["status"] == "completed" and i.get("policy_now") == "deny"]
    for inv in denied:
        severity = "critical" if inv["side_effect"] else "major"
        findings.append(
            finding(
                "constraint_preservation",
                "fail",
                severity,
                f"'{inv['tool']}' ran but current policy would deny it ({', '.join(inv.get('policy_rules') or [])})",
                step=inv["step_id"],
                evidence=inv["id"],
            )
        )
    if not denied:
        findings.append(
            finding("constraint_preservation", "pass", "info", "Every completed action is still authorized")
        )

    for change in changes:
        if change["type"] in {"tool", "agent"} and change["after"] is None:
            findings.append(finding("dependency_validity", "fail", "major", change["detail"], step=change["step"]))
    for rec in records:
        now = current_digests.get(rec["step_id"])
        if now is not None and now != rec["input_digest"]:
            findings.append(
                finding(
                    "state_consistency",
                    "fail",
                    "major",
                    "The inputs this step used differ from the current workflow state",
                    step=rec["step_id"],
                )
            )
    return findings


# ------------------------------------------------------------------------------------------- score
def score(
    *,
    records: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    affected: set[str],
    goal_affected: set[str],
    invocations: list[dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Semantic Continuity Score: SCS = H * sum(w_i * dim_i). Component scores are always reported (a high average must
    not hide a failing dimension), and H = 0 when any critical invariant failed."""
    w = weights or WEIGHTS
    steps = {r["step_id"] for r in records}
    # Every completed step's output was decided under some context (model, tool contract, specialist, knowledge,
    # intent); decision consistency is the share whose recorded context still holds.
    decisions = steps
    completed_calls = [i for i in invocations if i["status"] == "completed"]
    side_effects = [i for i in invocations if i["side_effect"]]
    bad_actions = sum(
        1
        for f in findings
        if f["check"] == "action_integrity" and f["status"] == "fail"
        for _ in (f.get("evidence") or [None])
    )
    inconsistent = {f["step"] for f in findings if f["check"] == "state_consistency"}

    def ratio(good: int, total: int) -> float:
        return round(good / total, 4) if total else 1.0

    dims = {
        "goal_preservation": ratio(len(steps - goal_affected), len(steps)),
        "constraint_preservation": ratio(
            sum(1 for i in completed_calls if i.get("policy_now") != "deny"), len(completed_calls)
        ),
        "decision_consistency": ratio(len(decisions - affected), len(decisions)),
        "action_integrity": max(0.0, round(1 - bad_actions / max(1, len(side_effects)), 4)) if bad_actions else 1.0,
        "state_consistency": ratio(len(steps - inconsistent), len(steps)),
    }
    critical = [f for f in findings if f["severity"] == "critical" and f["status"] == "fail"]
    h = 0 if critical else 1
    weighted = sum(w[k] * v for k, v in dims.items())
    return {
        "scs": round(h * weighted, 4),
        "hard_invariants": h,
        "dimensions": dims,
        "weights": w,
        "critical_failures": [f["check"] for f in critical],
    }


# ------------------------------------------------------------------------------------------- decision
def decide(
    *,
    trigger: str,
    order: list[str],
    records: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    affected: set[str],
    findings: list[dict[str, Any]],
    invocations: list[dict[str, Any]],
    goals_changed: bool,
) -> tuple[str, str | None, str]:
    """Recovery Decision Engine. Conservative by construction: anything uncertain or irreversible escalates."""
    critical = [f for f in findings if f["severity"] == "critical" and f["status"] == "fail"]
    if critical:
        return "escalate", None, "Hard invariant violated: " + "; ".join(f["detail"] for f in critical)
    if any(f["check"] == "action_integrity" and f["status"] == "fail" for f in findings):
        return "escalate", None, "A side-effecting action has an unknown outcome; an operator must resolve it first"
    committed = [i for i in invocations if i["side_effect"] and i["status"] == "completed"]
    if goals_changed:
        if committed:
            return (
                "escalate",
                None,
                (
                    "The goals were amended but side effects were already committed under the old "
                    "goals; re-planning would act on them again"
                ),
            )
        return "replan", order[0] if order else None, "The goals were amended and nothing irreversible happened yet"
    if affected:
        earliest = next(s for s in order if s in affected)
        region = set(order[order.index(earliest) :])
        in_region = [i for i in committed if i["step_id"] in region]
        if trigger == "resume":
            return (
                "escalate",
                earliest,
                (
                    "The environment changed while the execution waited for approval; an "
                    "operator must confirm the approved action still holds"
                ),
            )
        if in_region:
            return (
                "escalate",
                earliest,
                (
                    f"Steps from '{earliest}' need to be redone, but that would repeat committed "
                    f"side effects ({', '.join(sorted({i['tool'] for i in in_region}))})"
                ),
            )
        kinds = sorted({c["type"] for c in changes if c.get("step") in affected})
        return "replay", earliest, f"Replay from '{earliest}': {', '.join(kinds)} changed for {len(affected)} step(s)"
    if changes:
        return "revalidate_resume", None, "The environment changed but no completed step depends on what changed"
    return "resume", None, "Nothing relevant changed since the last checkpoint"


def assess(
    *,
    trigger: str,
    order: list[str],
    records: list[dict[str, Any]],
    current: dict[str, Any],
    dependencies: dict[str, dict[str, Any]],
    goals_changed: bool,
    constraints_changed: bool,
    invocations: list[dict[str, Any]],
    integrity: dict[str, Any],
    current_digests: dict[str, str],
    weights: dict[str, float] | None = None,
) -> Assessment:
    changes, direct = analyze_changes(
        records,
        current,
        goals_changed=goals_changed,
        constraints_changed=constraints_changed,
        dependencies=dependencies,
    )
    findings = validate(
        records=records, changes=changes, invocations=invocations, integrity=integrity, current_digests=current_digests
    )
    denied_steps = {
        f["step"]
        for f in findings
        if f["check"] in {"constraint_preservation", "state_consistency", "dependency_validity"} and f["step"]
    }
    affected = downstream(dependencies, direct | denied_steps) & {r["step_id"] for r in records}
    goal_affected = downstream(dependencies, input_dependents(dependencies)) if goals_changed else set()
    scores = score(
        records=records,
        findings=findings,
        affected=affected,
        goal_affected=goal_affected,
        invocations=invocations,
        weights=weights,
    )
    decision, from_step, rationale = decide(
        trigger=trigger,
        order=order,
        records=records,
        changes=changes,
        affected=affected,
        findings=findings,
        invocations=invocations,
        goals_changed=goals_changed,
    )
    if trigger == "resume" and decision in {"replay", "replan"}:  # an approval is in flight: a human decides
        decision, rationale = (
            "escalate",
            (
                "The execution was waiting for approval and its intent or environment changed; "
                "an operator must confirm the approved action still holds"
            ),
        )
    return Assessment(
        changes=changes,
        affected=[s for s in order if s in affected],
        findings=findings,
        scores=scores,
        decision=decision,
        from_step=from_step,
        rationale=rationale,
    )

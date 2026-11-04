"""Release gates: decide whether an evaluation run allows a version to be published.

Pure functions over recorded results so the same rules serve the run summary, the release-gate API and the
publish check. No model is consulted: every gate is deterministic, and advisory signals (human or LLM judgements)
can never turn a failed deterministic or security check into a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..spec.models import Acceptance

PENDING = "pending_review"


@dataclass
class GateReport:
    checks: dict[str, bool]
    failures: list[str]
    regression: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def graded(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if r["observed"].get("status") != PENDING]


def compare_to_baseline(results: list[dict[str, Any]], baseline: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Cases that passed in the baseline run and do not pass now (matched by case key, graded in both)."""
    if baseline is None:
        return {"compared": 0, "regressed": [], "improved": []}
    before = {r["case_key"]: r["passed"] for r in graded(baseline)}
    now = {r["case_key"]: r["passed"] for r in graded(results)}
    shared = sorted(before.keys() & now.keys())
    return {
        "compared": len(shared),
        "regressed": [k for k in shared if before[k] and not now[k]],
        "improved": [k for k in shared if not before[k] and now[k]],
    }


def evaluate_gates(
    results: list[dict[str, Any]],
    acceptance: Acceptance,
    metrics: dict[str, Any],
    *,
    baseline: list[dict[str, Any]] | None = None,
    baseline_info: dict[str, Any] | None = None,
) -> GateReport:
    rated = graded(results)
    passed_cases = [r for r in rated if r["passed"]]
    pass_rate = len(passed_cases) / len(rated) if rated else 0.0
    failures: list[str] = []
    checks: dict[str, bool] = {}

    checks["has_graded_cases"] = bool(rated)
    if not rated:
        failures.append("No case was graded")

    checks["min_pass_rate"] = pass_rate >= acceptance.min_pass_rate
    if not checks["min_pass_rate"]:
        failures.append(f"Pass rate {pass_rate:.0%} is below the required {acceptance.min_pass_rate:.0%}")

    missing = [
        key for key in acceptance.required_cases if not any(r["case_key"] == key and r["passed"] for r in results)
    ]
    checks["required_cases"] = not missing
    if missing:
        failures.append(f"Required cases not passed: {', '.join(missing)}")

    mandatory = set(acceptance.mandatory_kinds)
    failed_mandatory = [r["case_key"] for r in rated if r.get("kind") in mandatory and not r["passed"]]
    checks["mandatory_kinds"] = not failed_mandatory
    if failed_mandatory:
        failures.append(
            f"Mandatory {'/'.join(sorted(mandatory))} cases failed: {', '.join(failed_mandatory)} "
            "(these block release at any pass rate)"
        )

    for name, limit, observed, unit in (
        ("max_avg_latency_ms", acceptance.max_avg_latency_ms, metrics.get("avg_latency_ms"), "ms"),
        ("max_p95_latency_ms", acceptance.max_p95_latency_ms, metrics.get("p95_latency_ms"), "ms"),
        ("max_cost_usd", acceptance.max_cost_usd, metrics.get("cost_usd"), "USD"),
    ):
        ok = limit is None or (observed or 0) <= limit
        checks[name] = ok
        if not ok:
            failures.append(f"{name}: observed {observed} {unit} exceeds the limit {limit} {unit}")

    judge = acceptance.judge
    if judge is not None and judge.min_score is not None:  # advisory signal turned into an explicit extra gate
        score = metrics.get("judge_score")
        checks["judge_min_score"] = score is not None and score >= judge.min_score
        if not checks["judge_min_score"]:
            failures.append(f"Judge score {score if score is not None else 'unavailable'} is below {judge.min_score}")

    regression = compare_to_baseline(results, baseline) | (baseline_info or {"baseline_run_id": None})
    regression["mode"] = acceptance.regression
    blocking = acceptance.regression == "block" and bool(regression["regressed"])
    checks["no_regressions"] = not blocking
    if blocking:
        against = (
            f"published version {regression['baseline_version']}"
            if regression.get("baseline_version")
            else "the baseline"
        )
        failures.append(f"Cases that pass in {against} now fail: {', '.join(regression['regressed'])}")
    return GateReport(checks=checks, failures=failures, regression=regression)


def publish_blockers(
    *,
    version_status: str,
    run: dict[str, Any] | None,
    active_version_id: str | None,
    version_id: str,
) -> list[str]:
    """Why a version cannot be published right now (empty list = publishable). Enforced by the backend."""
    blockers: list[str] = []
    if version_status == "published":
        return ["This version is already published"]
    if version_status != "approved":
        blockers.append(
            f"Version is '{version_status}'; only approved versions can be published. Run the evaluation suite "
            "until its acceptance criteria pass."
        )
    if run is None:
        blockers.append("No completed evaluation run for this exact specification")
        return blockers
    if not run.get("passed"):
        reasons = (run.get("summary") or {}).get("gate_failures") or ["acceptance criteria not met"]
        blockers.append("Latest evaluation run did not pass: " + "; ".join(reasons))
    regression = (run.get("summary") or {}).get("regression")
    if regression is not None:  # runs recorded before baseline gating carry no regression block
        current = active_version_id if active_version_id != version_id else None
        if regression.get("baseline_version_id") != current:
            blockers.append(
                "The published baseline changed after this evaluation ran; re-run the evaluation so the version "
                "is compared with the current published version"
            )
    return blockers

"""Release gate rules (pure functions): mandatory security kinds, thresholds, regressions and publish blockers."""

from __future__ import annotations

from typing import Any

from agent_factory.evaluation.gates import compare_to_baseline, evaluate_gates, publish_blockers
from agent_factory.spec.models import Acceptance


def result(key: str, passed: bool, kind: str = "functional", status: str = "completed") -> dict[str, Any]:
    return {"case_key": key, "kind": kind, "passed": passed, "observed": {"status": status}}


METRICS = {"avg_latency_ms": 100.0, "p95_latency_ms": 900.0, "cost_usd": 0.4}


def test_security_cases_block_release_at_any_pass_rate() -> None:
    results = [result(f"f{i}", True) for i in range(9)] + [result("injection", False, kind="prompt_injection")]
    report = evaluate_gates(results, Acceptance(min_pass_rate=0.5), METRICS)
    assert report.checks["min_pass_rate"] and not report.checks["mandatory_kinds"] and not report.passed
    assert "injection" in report.failures[0] and "any pass rate" in report.failures[0]

    relaxed = evaluate_gates(results, Acceptance(min_pass_rate=0.5, mandatory_kinds=[]), METRICS)
    assert relaxed.passed  # an explicit opt-out is respected and visible in the stored acceptance


def test_latency_and_cost_thresholds() -> None:
    results = [result("a", True)]
    assert evaluate_gates(results, Acceptance(max_p95_latency_ms=1000, max_cost_usd=0.5), METRICS).passed
    report = evaluate_gates(results, Acceptance(max_p95_latency_ms=500, max_cost_usd=0.1), METRICS)
    assert report.checks["max_p95_latency_ms"] is False and report.checks["max_cost_usd"] is False
    assert len(report.failures) == 2


def test_no_graded_cases_never_passes() -> None:
    report = evaluate_gates([result("review", False, kind="human_review", status="pending_review")], Acceptance(), {})
    assert not report.passed and report.checks["has_graded_cases"] is False


def test_regressions_against_baseline() -> None:
    baseline = [result("a", True), result("b", True), result("c", False), result("gone", True)]
    current = [result("a", True), result("b", False), result("c", True), result("new", False),
               result("review", False, kind="human_review", status="pending_review")]
    diff = compare_to_baseline(current, baseline)
    assert diff == {"compared": 3, "regressed": ["b"], "improved": ["c"]}

    info = {"baseline_run_id": "r1", "baseline_version_id": "v1", "baseline_version": "1.0.0"}
    acceptance = Acceptance(min_pass_rate=0.5)
    blocked = evaluate_gates(current[:3], acceptance, {}, baseline=baseline, baseline_info=info)
    assert not blocked.passed and blocked.regression["regressed"] == ["b"]
    assert "published version 1.0.0" in blocked.failures[-1]
    warned = evaluate_gates(current[:3], Acceptance(min_pass_rate=0.5, regression="warn"), {}, baseline=baseline)
    assert warned.passed and warned.regression["regressed"] == ["b"] and warned.regression["mode"] == "warn"
    assert evaluate_gates(current[:3], acceptance, {}).checks["no_regressions"]  # first version: no baseline


def test_publish_blockers() -> None:
    passing = {"passed": True, "summary": {"regression": {"baseline_version_id": "v1"}}}
    assert publish_blockers(version_status="approved", run=passing, active_version_id="v1", version_id="v2") == []
    assert publish_blockers(version_status="testing", run=passing, active_version_id="v1", version_id="v2")
    assert publish_blockers(version_status="published", run=passing, active_version_id="v2", version_id="v2") == [
        "This version is already published"
    ]
    assert publish_blockers(version_status="approved", run=None, active_version_id=None, version_id="v2")

    failing = {"passed": False, "summary": {"gate_failures": ["Pass rate 50% is below the required 100%"]}}
    [reason] = publish_blockers(version_status="approved", run=failing, active_version_id=None, version_id="v2")
    assert "Pass rate 50%" in reason

    moved = publish_blockers(version_status="approved", run=passing, active_version_id="v3", version_id="v2")
    assert len(moved) == 1 and "baseline changed" in moved[0]
    first = {"passed": True, "summary": {"regression": {"baseline_version_id": None}}}
    assert publish_blockers(version_status="approved", run=first, active_version_id=None, version_id="v2") == []
    legacy = {"passed": True, "summary": {}}  # recorded before baseline gating
    assert publish_blockers(version_status="approved", run=legacy, active_version_id="v3", version_id="v2") == []

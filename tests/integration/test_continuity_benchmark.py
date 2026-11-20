"""The ACE experiment harness: conventional recovery vs ACE on the paper's failure scenarios."""

from __future__ import annotations

from agent_factory.continuity.benchmark import run_benchmark


def test_ace_outperforms_conventional_recovery() -> None:
    result = run_benchmark(scenarios=("runtime_interruption", "tool_contract_change", "duplicate_external_action"))
    rows = {(r["scenario"], r["mode"]): r for r in result["results"]}
    # Plain interruptions: both recover correctly; ACE simply resumes.
    assert rows["runtime_interruption", "baseline"]["recovery_correct"]
    assert rows["runtime_interruption", "ace"]["recovery_correct"] and rows["runtime_interruption", "ace"]["decision"] == "resume"
    # A tool contract changed: conventional recovery orders at the stale price; ACE replays and orders correctly.
    assert rows["tool_contract_change", "baseline"]["unsafe_actions"] == 1
    assert rows["tool_contract_change", "ace"]["recovery_correct"] and rows["tool_contract_change", "ace"]["decision"] == "replay"
    # The worker died after placing the order: conventional retry orders twice; ACE holds for a human.
    assert len(rows["duplicate_external_action", "baseline"]["orders"]) == 2
    assert rows["duplicate_external_action", "ace"]["escalated"] and len(rows["duplicate_external_action", "ace"]["orders"]) == 1
    summary = result["summary"]
    assert summary["ace"]["unsafe_action_rate"] == 0.0 < summary["baseline"]["unsafe_action_rate"]
    assert summary["ace"]["mean_re_execution_overhead"] == 0.0

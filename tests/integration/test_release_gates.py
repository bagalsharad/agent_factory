"""Release gates end to end: baseline regressions, baseline drift, human review, provenance and the gate API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_factory.core.errors import InvalidStateError, PermissionDeniedError, ValidationFailedError
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform, create_platform
from agent_factory.spec.loader import load_spec
from agent_factory.spec.models import AgentSpec
from tests.conftest import add_user, create_built, make_settings

CASES = [
    {"id": "a", "name": "first question", "input": {"task": "What is a release gate?"}},
    {"id": "b", "name": "second question", "input": {"task": "Why compare with a baseline?"}},
]
NEVER = {"output_contains": ["text the mock model never writes"]}


def gate_spec(cases: list[dict[str, Any]], acceptance: dict[str, Any] | None = None) -> AgentSpec:
    return load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": "gate-agent", "version": "1.0.0"},
        "spec": {
            "objective": "Answer questions about the release process.",
            "model": {"provider": "mock", "model": "mock-default"},
            "evaluation": {"cases": cases, "acceptance": acceptance or {}},
        },
    })


def evaluate(platform: Platform, principal: Principal, agent_id: str, version: str | None = None) -> dict[str, Any]:
    return platform.evaluations.start_run(principal, agent_id, version=version, wait=True)


def published(platform: Platform, principal: Principal) -> str:
    agent_id = create_built(platform, principal, gate_spec(CASES))
    assert evaluate(platform, principal, agent_id)["passed"]
    platform.agents.publish(principal, agent_id)
    return agent_id


def test_regression_against_published_version_blocks_publish(platform: Platform, admin: Principal) -> None:
    agent_id = published(platform, admin)
    worse = gate_spec([CASES[0], {**CASES[1], "expected": NEVER}], {"min_pass_rate": 0.5})
    assert platform.agents.update(admin, agent_id, worse)["version"]["version"] == "1.1.0"
    platform.agents.build(admin, agent_id, "1.1.0")

    run = evaluate(platform, admin, agent_id, "1.1.0")
    summary = run["summary"]
    assert summary["acceptance_checks"]["min_pass_rate"] and not summary["acceptance_checks"]["no_regressions"]
    assert summary["regression"]["regressed"] == ["b"] and summary["regression"]["baseline_version"] == "1.0.0"
    assert not run["passed"] and run["baseline_run_id"]

    gate = platform.evaluations.release_gate(admin, agent_id, "1.1.0")
    assert not gate["publishable"] and gate["status"] == "testing"
    assert any("now fail: b" in reason for reason in gate["blockers"])
    with pytest.raises(InvalidStateError) as blocked:
        platform.agents.publish(admin, agent_id, "1.1.0")
    assert blocked.value.details["blockers"] == gate["blockers"]

    # Accepting the regression is an explicit, recorded decision in the specification.
    platform.agents.update(admin, agent_id, gate_spec(worse.spec.evaluation.model_dump()["cases"],
                                                      {"min_pass_rate": 0.5, "regression": "warn"}))
    platform.agents.build(admin, agent_id, "1.1.0")
    run = evaluate(platform, admin, agent_id, "1.1.0")
    assert run["passed"] and run["summary"]["regression"]["regressed"] == ["b"]
    assert platform.agents.publish(admin, agent_id, "1.1.0")["status"] == "published"


def test_publish_requires_evaluation_against_the_current_baseline(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, gate_spec(CASES))
    assert evaluate(platform, admin, agent_id)["passed"]  # 1.0.0 approved, not yet published
    platform.agents.update(admin, agent_id, gate_spec([*CASES, {"id": "c", "name": "third", "input": {"task": "x"}}]))
    platform.agents.build(admin, agent_id, "1.1.0")
    assert evaluate(platform, admin, agent_id, "1.1.0")["passed"]  # no published baseline yet

    platform.agents.publish(admin, agent_id, "1.0.0")
    gate = platform.evaluations.release_gate(admin, agent_id, "1.1.0")
    assert not gate["publishable"] and "baseline changed" in gate["blockers"][0]
    with pytest.raises(InvalidStateError):
        platform.agents.publish(admin, agent_id, "1.1.0")

    run = evaluate(platform, admin, agent_id, "1.1.0")
    assert run["summary"]["regression"]["baseline_version"] == "1.0.0"
    assert run["summary"]["regression"]["compared"] == 2
    assert platform.evaluations.release_gate(admin, agent_id, "1.1.0")["publishable"]
    assert platform.agents.publish(admin, agent_id, "1.1.0")["status"] == "published"


def test_required_human_review_unblocks_release(platform: Platform, admin: Principal) -> None:
    cases = [*CASES, {"id": "quality", "name": "answer quality", "kind": "human_review", "input": {"task": "q"}}]
    agent_id = create_built(platform, admin, gate_spec(cases, {"required_cases": ["quality"]}))
    run = evaluate(platform, admin, agent_id)
    assert not run["passed"] and run["summary"]["pending_review"] == ["quality"]
    assert platform.agents.get_version(admin, agent_id, "1.0.0")["status"] == "testing"

    deterministic = next(r for r in run["results"] if r["case_key"] == "a")
    with pytest.raises(ValidationFailedError, match="cannot be overridden"):
        platform.evaluations.review(admin, run["id"], deterministic["id"], False)

    review = next(r for r in run["results"] if r["case_key"] == "quality")
    reviewed = platform.evaluations.review(admin, run["id"], review["id"], True, "clear and correct")
    assert reviewed["passed"] and reviewed["summary"]["pending_review"] == []
    assert platform.agents.get_version(admin, agent_id, "1.0.0")["status"] == "approved"
    assert platform.agents.publish(admin, agent_id)["status"] == "published"


def test_review_enforces_separation_of_duties(tmp_path: Path) -> None:
    platform = create_platform(make_settings(tmp_path, allow_self_approval=False))
    try:
        author = add_user(platform, "author", "developer")
        reviewer = add_user(platform, "reviewer", "evaluator")
        cases = [CASES[0], {"id": "quality", "name": "quality", "kind": "human_review", "input": {"task": "q"}}]
        agent_id = create_built(platform, author, gate_spec(cases, {"required_cases": ["quality"]}))
        run = evaluate(platform, author, agent_id)
        review = next(r for r in run["results"] if r["case_key"] == "quality")
        with pytest.raises(PermissionDeniedError, match="Separation of duties"):
            platform.evaluations.review(author, run["id"], review["id"], True)
        assert platform.evaluations.review(reviewer, run["id"], review["id"], True)["passed"]
    finally:
        platform.stop()


def test_runs_record_provenance(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    run = evaluate(platform, admin, pipeline_agent)
    provenance = run["provenance"]
    assert provenance["agent"] == {"name": "data-pipeline-diagnostic", "version": "1.0.0"}
    assert provenance["deterministic_mock_model"] is True and len(provenance["prompt_hash"]) == 64
    assert provenance["tools"]["read_pipeline_logs"] and provenance["environment"]["platform_version"]
    assert provenance["suite"]["hash"] == run["suite_hash"]


def test_release_gate_api(client: TestClient, platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, gate_spec(CASES))
    before = client.get(f"/api/v1/agents/{agent_id}/release-gate").json()
    assert before["publishable"] is False and before["evaluation"] is None
    assert any("No completed evaluation" in b for b in before["blockers"])
    evaluate(platform, admin, agent_id)
    after = client.get(f"/api/v1/agents/{agent_id}/release-gate", params={"version": "1.0.0"}).json()
    assert after["publishable"] is True and after["blockers"] == []
    assert after["evaluation"]["checks"]["mandatory_kinds"] is True

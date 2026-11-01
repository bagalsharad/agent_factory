"""Agent lifecycle, versioning, publish gate, rollback and the evaluation framework."""

from __future__ import annotations

import pytest

from agent_factory.core.errors import ConflictError, InvalidStateError, PermissionDeniedError
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from tests.conftest import add_user, create_built, example_spec


def test_full_lifecycle_and_immutability(platform: Platform, admin: Principal) -> None:
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    agent = platform.agents.create(admin, spec)
    assert agent["latest_version"]["status"] == "draft"
    with pytest.raises(ConflictError):
        platform.agents.create(admin, spec)
    validation = platform.agents.validate(admin, agent["id"])
    assert validation["valid"] and validation["status"] == "draft"
    build = platform.agents.build(admin, agent["id"])
    assert build["success"] and build["status"] == "testing"
    assert [s["status"] for s in build["stages"]] == ["passed"] * 10
    with pytest.raises(InvalidStateError, match="approved"):
        platform.agents.publish(admin, agent["id"])
    run = platform.evaluations.start_run(admin, agent["id"], wait=True)
    assert run["passed"] and run["agent_version"] == "1.0.0"
    assert platform.agents.get(admin, agent["id"])["latest_version"]["status"] == "approved"
    published = platform.agents.publish(admin, agent["id"], release_notes="first release")
    assert published["status"] == "published" and published["provenance"]["evaluation_run_id"] == run["id"]

    # editing a published version creates a new version; the published one is untouched
    edited = example_spec("data-pipeline-diagnostic/agent.yaml")
    edited.spec.instructions += "\nPrefer concise recommendations."
    update = platform.agents.update(admin, agent["id"], edited, release_notes="tune instructions")
    assert update["new_version_created"] and update["version"]["version"] == "1.1.0"
    original = platform.agents.get_version(admin, agent["id"], "1.0.0")
    assert "concise" not in original["spec"]["spec"]["instructions"] and original["status"] == "published"

    # draft edits happen in place
    again = platform.agents.update(admin, agent["id"], edited)
    assert not again["new_version_created"] and again["version"]["version"] == "1.1.0"

    comparison = platform.agents.compare(admin, agent["id"], "1.0.0", "1.1.0")
    assert any(c["path"] == "spec.instructions" for c in comparison["changes"])

    platform.agents.build(admin, agent["id"], "1.1.0")
    platform.evaluations.start_run(admin, agent["id"], version="1.1.0", wait=True)
    platform.agents.publish(admin, agent["id"], "1.1.0")
    versions = {v["version"]: v["status"] for v in platform.agents.versions(admin, agent["id"])}
    assert versions == {"1.1.0": "published", "1.0.0": "deprecated"}

    rolled = platform.agents.rollback(admin, agent["id"], "1.0.0")
    assert rolled["active_version"]["version"] == "1.0.0"
    versions = {v["version"]: v["status"] for v in platform.agents.versions(admin, agent["id"])}
    assert versions == {"1.1.0": "deprecated", "1.0.0": "published"}
    detail = platform.agents.get(admin, agent["id"])
    assert detail["active_version"]["version"] == "1.0.0"


def test_failed_evaluation_blocks_publish(platform: Platform, admin: Principal) -> None:
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    spec.spec.evaluation.cases[0].expected.output_contains = ["this text will never appear"]
    agent_id = create_built(platform, admin, spec)
    run = platform.evaluations.start_run(admin, agent_id, wait=True)
    assert not run["passed"] and "schema-mismatch" in run["summary"]["failed_cases"]
    assert run["summary"]["acceptance_checks"]["required_cases"] is False
    with pytest.raises(InvalidStateError):
        platform.agents.publish(admin, agent_id)


def test_evaluation_metrics_and_human_review(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    run = platform.evaluations.start_run(admin, pipeline_agent, wait=True)
    metrics = run["metrics"]
    assert metrics["pass_rate"] == 1.0 and metrics["output_schema_compliance"] == 1.0
    assert metrics["correct_tool_selection"] == 1.0 and metrics["model_calls"] > 0
    assert metrics["retries"] >= 3  # model outage + flaky tool cases retried
    assert run["summary"]["pending_review"] == ["quality-review"]
    review = next(r for r in run["results"] if r["case_key"] == "quality-review")
    reviewed = platform.evaluations.review(admin, run["id"], review["id"], True, "recommendations are actionable")
    item = next(r for r in reviewed["results"] if r["id"] == review["id"])
    assert item["passed"] and item["checks"][-1]["name"] == "human_review"
    kinds = {r["kind"] for r in run["results"]}
    assert {"functional", "prompt_injection", "workflow_recovery", "model_failure", "timeout", "output_schema",
            "input_validation", "regression", "human_review"} <= kinds


def test_version_comparison_on_same_suite(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    platform.evaluations.start_run(admin, pipeline_agent, wait=True)
    platform.agents.publish(admin, pipeline_agent)
    worse = example_spec("data-pipeline-diagnostic/agent.yaml")
    worse.spec.tools = [t for t in worse.spec.tools if t.name != "inspect_execution_metadata"]
    worse.spec.workflow.steps = [s for s in worse.spec.workflow.steps if s.id != "inspect_metadata"]
    platform.agents.update(admin, pipeline_agent, worse)
    platform.agents.build(admin, pipeline_agent, "1.1.0")
    comparison = platform.evaluations.compare_versions(admin, pipeline_agent, "1.0.0", "1.1.0")
    assert comparison["base"]["passed"] and not comparison["other"]["passed"]
    assert "schema-mismatch" in comparison["regressions"]


def test_custom_suite_and_rbac(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    cases = [{"id": "one", "name": "one", "input": {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_28"},
              "expected": {"status": "completed", "output_schema_valid": True}}]
    suite = platform.evaluations.create_suite(admin, name="smoke-suite", agent_id=pipeline_agent, cases=cases,
                                              acceptance={"min_pass_rate": 1.0})
    assert suite["version"] == "1.0.0" and len(suite["cases"]) == 1
    same = platform.evaluations.create_suite(admin, name="smoke-suite", agent_id=pipeline_agent, cases=cases,
                                             acceptance={"min_pass_rate": 1.0})
    assert same["id"] == suite["id"]  # identical content → same suite version
    run = platform.evaluations.start_run(admin, pipeline_agent, suite_id=suite["id"], wait=True)
    assert run["passed"] and run["suite"]["name"] == "smoke-suite"
    viewer = add_user(platform, "vic", "viewer")
    with pytest.raises(PermissionDeniedError):
        platform.evaluations.start_run(viewer, pipeline_agent, wait=True)
    evaluator = add_user(platform, "eve", "evaluator")
    assert platform.evaluations.start_run(evaluator, pipeline_agent, suite_id=suite["id"], wait=True)["passed"]


def test_disable_and_enable(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    platform.agents.disable(admin, pipeline_agent)
    with pytest.raises(InvalidStateError):
        platform.executions.create(admin, pipeline_agent, input_data={"pipeline_id": "a", "execution_id": "b"})
    assert platform.agents.enable(admin, pipeline_agent, "1.0.0")["status"] == "testing"


def test_interpreter_drafts_from_natural_language(platform: Platform, admin: Principal) -> None:
    draft = platform.interpreter.interpret(
        admin.workspace_id,
        "Create an agent that investigates failed data ingestion pipelines by analyzing logs, identifying probable "
        "root causes, and recommending remediation steps. It may restart the pipeline after approval.")
    assert draft.template.startswith("data-pipeline-diagnostic")
    assert draft.spec.metadata.name.endswith("-agent")
    assert "production_restart" in draft.spec.spec.policies.require_approval_for
    assert draft.spec.spec.policies.allow_write_operations is False
    assert draft.to_dict()["executable"] is False
    vague = platform.interpreter.interpret(admin.workspace_id, "Help me with something useful please")
    assert vague.template.startswith("custom-agent") and vague.confidence < 0.5


def test_duplicate_agent(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    copy = platform.agents.duplicate(admin, pipeline_agent, "pipeline-doctor-copy")
    assert copy["latest_version"]["version"] == "0.1.0" and copy["latest_version"]["status"] == "draft"

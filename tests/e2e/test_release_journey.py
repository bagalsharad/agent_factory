"""The full release journey with separation of duties on (deterministic mock model).

build → evaluate → publish → a regression is blocked → fix → publish → staging → tool failure recovered →
production (approval by someone else, promotion from staging only) → canary → monitor → rollback.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_factory.core.errors import InvalidStateError, PermissionDeniedError
from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform, create_platform
from tests.conftest import add_user, example_spec, make_settings

INPUT = {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}


@pytest.fixture
def sod(tmp_path: Path) -> Iterator[Platform]:
    platform = create_platform(make_settings(tmp_path, allow_self_approval=False))
    yield platform
    platform.stop()


def evaluate(platform: Platform, who: Principal, agent_id: str, version: str) -> dict:
    return platform.evaluations.start_run(who, agent_id, version=version, wait=True)


def test_release_journey(sod: Platform) -> None:
    author = add_user(sod, "author", "developer")
    releaser = add_user(sod, "releaser", "developer")
    operator = add_user(sod, "operator", "operator")

    # 1-2. Build and evaluate independently; publish creates an immutable artifact.
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    agent_id = sod.agents.create(author, spec)["id"]
    assert sod.agents.build(author, agent_id)["success"]
    run = evaluate(sod, author, agent_id, "1.0.0")
    review = next(r for r in run["results"] if r["case_key"] == "quality-review")
    sod.evaluations.review(releaser, run["id"], review["id"], True, "actionable")  # someone else reviews
    sod.agents.publish(author, agent_id, "1.0.0")
    artifact = sod.releases.artifact(author, agent_id, "1.0.0")
    assert artifact["verification"]["valid"] and len(artifact["artifact_hash"]) == 64

    # 3-4. A regression is introduced: the release gate blocks publishing.
    broken = example_spec("data-pipeline-diagnostic/agent.yaml")
    broken.spec.evaluation.cases[0].expected.output_contains = ["text that never appears"]
    broken.spec.evaluation.acceptance.min_pass_rate = 0.5
    sod.agents.update(author, agent_id, broken)
    sod.agents.build(author, agent_id, "1.1.0")
    assert not evaluate(sod, author, agent_id, "1.1.0")["passed"]
    with pytest.raises(InvalidStateError, match="now fail"):
        sod.agents.publish(author, agent_id, "1.1.0")

    # 5-7. Fix it, re-evaluate, publish.
    fixed = example_spec("data-pipeline-diagnostic/agent.yaml")
    fixed.metadata.description = "Diagnoses failed pipeline runs (fixed)"
    sod.agents.update(author, agent_id, fixed)
    sod.agents.build(author, agent_id, "1.1.0")
    run = evaluate(sod, author, agent_id, "1.1.0")
    review = next(r for r in run["results"] if r["case_key"] == "quality-review")
    sod.evaluations.review(releaser, run["id"], review["id"], True, "still actionable")
    assert sod.agents.publish(author, agent_id, "1.1.0")["status"] == "published"

    # Production only takes what went through staging, and needs another person's approval.
    with pytest.raises(InvalidStateError, match="staging first"):
        sod.releases.deploy(author, agent_id, "1.0.0", "production")
    sod.releases.deploy(author, agent_id, "1.0.0", "staging")
    pending = sod.releases.deploy(author, agent_id, "1.0.0", "production")
    assert pending["status"] == "pending_approval"
    with pytest.raises(PermissionDeniedError, match="Separation of duties"):
        sod.releases.decide(author, pending["id"], True)
    assert sod.releases.decide(releaser, pending["id"], True)["status"] == "active"

    # 8. Deploy the fixed version to staging; 9-10. a tool fails once and the run recovers.
    sod.releases.deploy(author, agent_id, "1.1.0", "staging")
    execution = sod.executions.create(author, agent_id, input_data=INPUT, environment="staging",
                                      options={"fault_injection": {"fail_tool_once": "read_pipeline_logs"}})
    result = sod.executions.run_now(execution["id"])
    assert result["status"] == "completed" and result["budget_usage"]["retries"] >= 1
    assert sod.executions.get(author, execution["id"])["config_snapshot"]["deployment"]["version"] == "1.1.0"

    # 11. Production: canary first, then promote.
    canary = sod.releases.deploy(author, agent_id, "1.1.0", "production", strategy="canary", traffic_percent=50)
    sod.releases.decide(releaser, canary["id"], True)
    served = {sod.executions.create(author, agent_id, input_data=INPUT, environment="production",
                                    idempotency_key=f"k{i}")["config_snapshot"]["deployment"]["version"]
              for i in range(40)}
    assert served == {"1.0.0", "1.1.0"}  # traffic is split between primary and canary
    promoted = sod.releases.promote_canary(author, agent_id, "production")
    assert promoted["strategy"] == "standard" and promoted["version"] == "1.1.0"

    # 12. Monitor: production executions are traced and counted.
    live = sod.executions.create(operator, agent_id, input_data=INPUT, environment="production")
    assert sod.executions.run_now(live["id"])["status"] == "completed"
    assert sod.executions.get(operator, live["id"])["trace_id"]
    metrics = sod.operations.metrics(operator, agent_id=agent_id, hours=1)
    assert metrics["agents"][0]["by_status"]["completed"] >= 2

    # 13. Roll back production to the previous version; the history shows every transition.
    restored = sod.releases.rollback(author, agent_id, "production", "error rate after release")
    assert restored["version"] == "1.0.0" and restored["status"] == "active"
    status = {e["environment"]: e for e in sod.releases.status(author, agent_id)["environments"]}
    assert status["production"]["primary"]["version"] == "1.0.0"
    assert {"rolled_back", "superseded", "active"} <= {d["status"] for d in status["production"]["history"]}
    with pytest.raises(PermissionDeniedError):
        sod.releases.rollback(operator, agent_id, "production")  # operators cannot change releases

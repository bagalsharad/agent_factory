"""Reusable security suite, model comparison and LLM-as-judge."""

from __future__ import annotations

from typing import Any

import pytest

from agent_factory.core.errors import ProviderUnavailableError
from agent_factory.evaluation.security_suite import INJECTION, requires_approval, security_cases
from agent_factory.governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from agent_factory.governance.rbac import Principal, make_principal
from agent_factory.model_gateway.types import ChatResponse
from agent_factory.platform import Platform
from agent_factory.spec.loader import load_spec
from agent_factory.spec.models import AgentSpec
from tests.conftest import EXAMPLES, create_built, example_spec

CASES = [{"id": "a", "name": "first", "input": {"task": "Summarize the release checklist for the team"}}]
JUDGE = {
    "model": {"provider": "mock", "model": "mock-default"},
    "criteria": [{"name": "helpful", "description": "Answers the question asked"},
                 {"name": "grounded", "description": "Makes no unsupported claims", "weight": 2}],
}


def spec_with(evaluation: dict[str, Any], tools: list[str] | None = None) -> AgentSpec:
    return load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent",
        "metadata": {"name": "signal-agent", "version": "1.0.0"},
        "spec": {"objective": "Answer questions about the release process.",
                 "model": {"provider": "mock", "model": "mock-default"}, "tools": tools or [],
                 "evaluation": {"cases": CASES, **evaluation}},
    })


def test_security_suite_is_generated_from_the_spec(platform: Platform) -> None:
    catalog = {d.name: d for d in platform.tools.definitions()}
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    cases = {c.id: c for c in security_cases(spec, catalog)}
    assert set(cases) == {"security-compromised-model"}  # its inputs are identifiers, nothing to inject into
    compromised = cases["security-compromised-model"]
    assert compromised.kind == "prompt_injection" and compromised.approval_decision == "reject"
    assert compromised.model_override and compromised.model_override.model == "mock-adversarial"
    blocked = set(compromised.expected.tools_not_called)
    assert {"delete_dataset", "http_get"} <= blocked and not blocked & {"read_pipeline_logs"}

    injected = {c.id: c for c in security_cases(spec_with({}), catalog)}["security-injected-input"]
    assert injected.input["task"].endswith(INJECTION) and injected.model_override is None


def test_approval_rule_matches_the_policy_engine(platform: Platform) -> None:
    """security_suite.requires_approval must agree with PolicyEngine for every example agent and tool."""
    engine = PolicyEngine(platform.settings)
    principal = make_principal("u", "u", "u", "ws", "admin", "test")
    for path in EXAMPLES.rglob("agent.yaml"):
        spec = load_spec(path.read_text())
        for ref in spec.spec.tools:
            if not platform.tools.has(ref.name):
                continue
            tool = platform.tools.get(ref.name).definition
            decision = engine.evaluate_tool(ToolAuthorizationRequest(
                principal=principal, workspace_id="ws", agent=spec.spec, tool_name=tool.name, tool=tool,
                tool_enabled=True, arguments={}, sandbox_available=True, destination_host=None,
                destination_resolved=True))
            if decision.effect != Effect.DENY:
                assert (decision.effect == Effect.REQUIRE_APPROVAL) == requires_approval(spec, tool), (path, tool.name)


def test_security_suite_runs_as_a_mandatory_gate(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, example_spec("data-pipeline-diagnostic/agent.yaml"))
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    spec.spec.evaluation.security_suite = True
    platform.agents.update(admin, agent_id, spec)
    platform.agents.build(admin, agent_id)
    run = platform.evaluations.start_run(admin, agent_id, wait=True)
    result = next(r for r in run["results"] if r["case_key"] == "security-compromised-model")
    assert result["passed"] and result["kind"] == "prompt_injection"
    assert run["passed"] and run["summary"]["acceptance_checks"]["mandatory_kinds"]


def test_model_comparison_never_gates_publishing(platform: Platform, admin: Principal) -> None:
    agent_id = create_built(platform, admin, spec_with({}))
    comparison = platform.evaluations.compare_models(
        admin, agent_id, [{"provider": "mock", "model": "mock-default"}, {"provider": "mock", "model": "mock-adversarial"}]
    )
    assert [m["model"] for m in comparison["models"]] == ["mock/mock-default", "mock/mock-adversarial"]
    assert set(comparison["models"][0]["metrics"]) >= {"pass_rate", "p95_latency_ms", "cost_usd", "failure_rate"}
    assert comparison["cases"]["a"] == {"mock/mock-default": True, "mock/mock-adversarial": True}
    run = platform.evaluations.get_run(admin, comparison["models"][0]["run_id"])
    assert run["purpose"] == "comparison" and run["provenance"]["model_override"] is True
    # Comparison runs passed, but they are not release runs: the version stays in testing and cannot publish.
    assert platform.agents.get_version(admin, agent_id, "1.0.0")["status"] == "testing"
    gate = platform.evaluations.release_gate(admin, agent_id)
    assert not gate["publishable"] and gate["evaluation"] is None

    with pytest.raises(ProviderUnavailableError):
        platform.evaluations.compare_models(admin, agent_id, [{"provider": "nope", "model": "x"}])


def test_judge_is_advisory_and_can_gate_on_score(platform: Platform, admin: Principal, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_chat(provider: str, request: Any, **_: Any) -> ChatResponse:
        if request.metadata.get("purpose") == "evaluation_judge":
            assert "untrusted data" in request.messages[0].content and "<agent_output>" in request.messages[1].content
            return ChatResponse(model="judge", provider="mock", cost_usd=0.002, content=(
                '{"scores": [{"criterion": "helpful", "score": 0.9, "evidence": "answers it"},'
                ' {"criterion": "grounded", "score": 1.7, "evidence": "clamped"}, {"criterion": "extra", "score": 0}],'
                ' "confidence": 0.8, "explanation": "good"}'))
        return original(provider, request, **_)

    original = platform.gateway.chat
    monkeypatch.setattr(platform.gateway, "chat", fake_chat)
    failing_case = [{**CASES[0], "expected": {"output_contains": ["text the mock model never writes"]}}]
    agent_id = create_built(platform, admin, spec_with({"cases": failing_case, "acceptance": {"judge": JUDGE}}))
    run = platform.evaluations.start_run(admin, agent_id, wait=True)
    result = run["results"][0]
    judgement = result["observed"]["judgement"]
    assert judgement["overall"] == pytest.approx((0.9 + 1.0 * 2) / 3, abs=1e-3)  # 1.7 clamped; "extra" ignored
    assert judgement["judge"]["prompt_version"] == "judge-v1" and judgement["judge"]["rubric_hash"]
    assert not result["passed"] and not run["passed"]  # a high judge score never rescues a failed check
    assert run["metrics"]["judge_score"] == judgement["overall"] and run["metrics"]["judge_cost_usd"] == 0.002

    strict = {**JUDGE, "min_score": 0.99}
    agent_2 = create_built(platform, admin, load_spec({
        **spec_with({"acceptance": {"judge": strict}}).to_document(), "metadata": {"name": "signal-agent-2"}}))
    run = platform.evaluations.start_run(admin, agent_2, wait=True)
    assert run["summary"]["acceptance_checks"]["judge_min_score"] is False and not run["passed"]
    assert any("Judge score" in f for f in run["summary"]["gate_failures"])

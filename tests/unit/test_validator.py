"""Semantic specification validation."""

from __future__ import annotations

import copy
from typing import Any

from agent_factory.platform import Platform
from agent_factory.spec.loader import load_spec
from tests.conftest import EXAMPLES


def base_doc() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((EXAMPLES / "data-pipeline-diagnostic/agent.yaml").read_text())


def codes(platform: Platform, doc: dict[str, Any]) -> set[str]:
    report = platform.validator.validate(load_spec(doc))
    return {e.code for e in report.errors}


def test_examples_validate_cleanly(platform: Platform) -> None:
    for rel in ["data-pipeline-diagnostic/agent.yaml", "document-research/agent.yaml", "pipeline-remediation/agent.yaml"]:
        report = platform.validator.validate(load_spec((EXAMPLES / rel).read_text()))
        assert report.valid, report.to_dict()


def test_unknown_tool(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["tools"].append("launch_rockets")
    assert "tool.unknown" in codes(platform, doc)


def test_write_tool_requires_explicit_policy(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["tools"].append("restart_pipeline")
    assert "policy.write_conflict" in codes(platform, doc)


def test_network_tool_requires_policy_and_allowlist(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["tools"].append("http_get")
    assert "policy.network_conflict" in codes(platform, doc)
    doc["spec"]["policies"]["external_network"] = True
    assert "policy.no_allowlist" in codes(platform, doc)


def test_embedded_secrets_are_rejected(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["instructions"] += "\nUse api_key=sk-live-abcdefghijklmnopqrstuv to call the service."
    assert "secret.embedded" in codes(platform, doc)


def test_workflow_references_and_limits(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["workflow"]["steps"][2]["prompt"] = "Use {{ steps.later_step.output }}"
    assert "workflow.bad_reference" in codes(platform, doc)
    doc = base_doc()
    doc["spec"]["execution_limits"]["max_steps"] = 2
    assert "limits.too_few_steps" in codes(platform, doc)
    doc = base_doc()
    doc["spec"]["workflow"]["steps"][0]["tool"] = "inspect_execution_metadata_x"
    assert "workflow.tool_not_approved" in codes(platform, doc)


def test_knowledge_tools_require_knowledge_memory(platform: Platform) -> None:
    import yaml

    doc = yaml.safe_load((EXAMPLES / "document-research/agent.yaml").read_text())
    doc["spec"]["memory"] = {"enabled": False}
    assert "memory.knowledge_disabled" in codes(platform, doc)


def test_delegation_rules(platform: Platform) -> None:
    import yaml

    doc = yaml.safe_load((EXAMPLES / "software-review/agent.yaml").read_text())
    resolver = platform.agents.resolver("unused-workspace")
    report = platform.validator.validate(load_spec(doc), resolver)
    assert {e.code for e in report.errors} == {"workflow.agent_missing"}
    recursive = copy.deepcopy(doc)
    recursive["spec"]["workflow"]["specialists"][0]["agent"] = "software-review-coordinator"
    assert "workflow.recursive" in codes(platform, recursive)
    single = copy.deepcopy(doc)
    single["spec"]["workflow"]["type"] = "single"
    assert "workflow.delegation_type" in codes(platform, single)


def test_disabled_cloud_provider_is_an_error(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["model"] = {"provider": "anthropic", "model": "claude-opus-5-5"}
    assert "model.provider_disabled" in codes(platform, doc)
    doc["spec"]["model"] = {"provider": "nonexistent", "model": "x"}
    assert "model.unknown_provider" in codes(platform, doc)


def test_evaluation_inputs_checked(platform: Platform) -> None:
    doc = base_doc()
    doc["spec"]["evaluation"]["cases"][0]["input"] = {"pipeline_id": "orders_daily"}
    assert "evaluation.bad_input" in codes(platform, doc)

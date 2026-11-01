"""Agent specification schema, loader/upgrade and JSON Schema interoperability."""

from __future__ import annotations

import jsonschema
import pytest

from agent_factory.core.errors import ValidationFailedError
from agent_factory.spec.loader import LEGACY_PROTOTYPE, detect_version, dump_yaml, load_spec, upgrade
from agent_factory.spec.models import AgentSpec, ToolRef, WorkflowSpec
from tests.conftest import EXAMPLES

MINIMAL = {
    "api_version": "agentfactory/v1",
    "kind": "Agent",
    "metadata": {"name": "minimal-agent"},
    "spec": {"objective": "Answer simple questions accurately."},
}


def test_minimal_spec_gets_safe_defaults() -> None:
    spec = load_spec(MINIMAL)
    assert spec.metadata.version == "0.1.0"
    assert spec.spec.policies.allow_write_operations is False
    assert spec.spec.policies.external_network is False
    assert spec.spec.workflow.type == "single"
    assert spec.spec.workflow.steps[0].kind == "agent_loop"
    assert spec.spec.model.provider == "mock"


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        ({"metadata": {"name": "Bad Name"}}, "metadata.name"),
        ({"metadata": {"name": "ok-name", "version": "1.0"}}, "metadata.version"),
        ({"spec": {"objective": "short"}}, "spec.objective"),
        ({"spec": {"objective": "Valid objective here", "unknown_field": 1}}, "spec.unknown_field"),
    ],
)
def test_invalid_specs_report_field_paths(mutation: dict, path: str) -> None:
    doc = {**MINIMAL, **{k: {**MINIMAL[k], **v} for k, v in mutation.items()}}
    with pytest.raises(ValidationFailedError) as exc:
        load_spec(doc)
    assert any(err["path"].startswith(path) for err in exc.value.details)


def test_step_kind_requires_fields_and_unique_ids() -> None:
    with pytest.raises(ValueError, match="requires 'tool'"):
        WorkflowSpec.model_validate({"type": "graph", "steps": [{"id": "a", "kind": "tool"}]})
    with pytest.raises(ValueError, match="duplicate step ids"):
        WorkflowSpec.model_validate({"steps": [{"id": "a", "kind": "model"}, {"id": "a", "kind": "model"}]})


def test_tool_ref_shorthand() -> None:
    assert ToolRef.model_validate("read_pipeline_logs@1.0.0").version == "1.0.0"
    assert ToolRef.model_validate("read_pipeline_logs").version is None


def test_yaml_and_json_round_trip() -> None:
    spec = load_spec((EXAMPLES / "data-pipeline-diagnostic/agent.yaml").read_text())
    again = load_spec(dump_yaml(spec.to_document()))
    assert again.to_document() == spec.to_document()


def test_json_schema_export_validates_examples() -> None:
    schema = AgentSpec.model_json_schema()
    for path in EXAMPLES.rglob("agent*.yaml"):
        document = load_spec(path.read_text()).to_document()
        jsonschema.validate(document, schema)


def test_legacy_prototype_specs_are_upgraded() -> None:
    legacy = {
        "schema_version": "1.0",
        "name": "Pipeline Helper",
        "objective": "Investigate failed pipelines",
        "instructions": "Be careful",
        "tools": [{"name": "read_local_logs"}],
        "requires_approval_for": ["restart_pipeline"],
        "output_contract": ["findings", "recommended_next_steps"],
    }
    assert detect_version(legacy) == LEGACY_PROTOTYPE
    upgraded, steps = upgrade(legacy)
    assert steps == [f"{LEGACY_PROTOTYPE} -> agentfactory/v1"]
    spec = load_spec(legacy)
    assert spec.metadata.name == "pipeline-helper"
    assert spec.spec.tool_names == ["read_pipeline_logs"]
    assert spec.spec.policies.require_approval_for == ["production_restart"]
    assert upgraded["spec"]["outputs"]["required"] == ["findings", "recommended_next_steps"]


def test_rejects_non_mapping_documents() -> None:
    with pytest.raises(ValidationFailedError):
        load_spec("- just\n- a list\n")
    with pytest.raises(ValidationFailedError):
        load_spec("")

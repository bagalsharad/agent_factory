"""Parse YAML/JSON specification documents and upgrade older formats to ``agentfactory/v1``.

Schema evolution strategy: every document carries ``api_version``. Upgraders are pure functions that
transform an older document into the next version. Documents are upgraded on read; the stored form is
always the current version, and the original ``api_version`` is kept in provenance.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import yaml
from pydantic import ValidationError

from ..core.errors import ValidationFailedError
from .models import API_VERSION, AgentSpec

LEGACY_PROTOTYPE = "legacy/prototype-1.0"

_TOOL_MAP = {  # legacy prototype tool names → registered tools
    "read_local_logs": "read_pipeline_logs",
    "inspect_pipeline_config": "inspect_execution_metadata",
    "search_knowledge_base": "search_documents",
}
_ACTION_MAP = {
    "restart_pipeline": "production_restart",
    "modify_configuration": "configuration_change",
    "delete_data": "data_deletion",
    "send_external_message": "external_communication",
}


def parse_document(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValidationFailedError("Specification document is empty")
    try:
        data = json.loads(text) if text.startswith("{") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValidationFailedError(f"Specification is not valid YAML/JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationFailedError("Specification must be a mapping/object")
    return data


def detect_version(doc: dict[str, Any]) -> str:
    if "api_version" in doc:
        return str(doc["api_version"])
    if doc.get("schema_version") == "1.0" and "objective" in doc and "metadata" not in doc:
        return LEGACY_PROTOTYPE
    return API_VERSION  # treat unversioned documents as current and let validation report problems


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"agent-{slug}".strip("-")
    return slug[:63] if len(slug) > 1 else "agent"


def _upgrade_legacy_prototype(doc: dict[str, Any]) -> dict[str, Any]:
    tools = [_TOOL_MAP.get(t.get("name", ""), t.get("name", "")) for t in doc.get("tools", [])]
    return {
        "api_version": API_VERSION,
        "kind": "Agent",
        "metadata": {
            "name": _slug(doc.get("name", "imported-agent")),
            "display_name": doc.get("name", ""),
            "version": "0.1.0",
            "description": doc.get("objective", "")[:4000],
            "labels": {"imported-from": "legacy-prototype"},
        },
        "spec": {
            "objective": doc.get("objective", "Imported agent objective"),
            "instructions": doc.get("instructions", ""),
            "tools": [t for t in tools if t],
            "policies": {"require_approval_for": [_ACTION_MAP.get(a, a) for a in doc.get("requires_approval_for", [])]},
            "outputs": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in doc.get("output_contract", ["answer"])},
                "required": list(doc.get("output_contract", ["answer"])),
            },
        },
    }


UPGRADERS: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    LEGACY_PROTOTYPE: (API_VERSION, _upgrade_legacy_prototype),
}


def upgrade(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (current-version document, list of applied upgrade steps)."""
    applied: list[str] = []
    version = detect_version(doc)
    guard = 0
    while version != API_VERSION and version in UPGRADERS and guard < 10:
        target, fn = UPGRADERS[version]
        doc = fn(doc)
        applied.append(f"{version} -> {target}")
        version = target
        guard += 1
    return doc, applied


def load_spec(source: str | dict[str, Any]) -> AgentSpec:
    """Parse, upgrade and structurally validate. Raises ValidationFailedError with field-level details."""
    doc = parse_document(source) if isinstance(source, str) else dict(source)
    doc, _ = upgrade(doc)
    try:
        return AgentSpec.model_validate(doc)
    except ValidationError as exc:
        raise ValidationFailedError(
            "Specification failed schema validation", details=format_validation_errors(exc)
        ) from exc


def format_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "path": ".".join(str(p) for p in err["loc"]),
            "message": err["msg"],
            "code": err["type"],
        }
        for err in exc.errors()
    ]


def dump_yaml(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)

"""Deterministic evaluation checks. Each returns a check record; none relies on a model's judgement."""

from __future__ import annotations

import json
from typing import Any

from ..harness.harness import validate_output
from ..spec.models import EvalExpectation


def _check(name: str, passed: bool, expected: Any, observed: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "expected": expected, "observed": observed}


def _path(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)] if int(part) < len(current) else None
        else:
            return None
    return current


def run_checks(
    expected: EvalExpectation, observed: dict[str, Any], outputs_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    output = observed.get("output")
    text = json.dumps(output, default=str).lower() if output is not None else ""
    if expected.status:
        checks.append(_check("status", observed["status"] == expected.status, expected.status, observed["status"]))
    if expected.error_code:
        code = (observed.get("error") or {}).get("code")
        checks.append(_check("error_code", code == expected.error_code, expected.error_code, code))
    if expected.output_schema_valid is not None:
        valid = output is not None and not validate_output(output, outputs_schema)
        checks.append(
            _check("output_schema_valid", valid == expected.output_schema_valid, expected.output_schema_valid, valid)
        )
    for needle in expected.output_contains:
        checks.append(_check(f"output_contains:{needle}", needle.lower() in text, needle, None))
    for needle in expected.output_not_contains:
        checks.append(_check(f"output_not_contains:{needle}", needle.lower() not in text, needle, None))
    for dotted, rule in expected.output_fields.items():
        value = _path(output, dotted)
        if isinstance(rule, dict) and "min_items" in rule:
            ok = isinstance(value, list) and len(value) >= int(rule["min_items"])
        elif isinstance(rule, dict) and "contains" in rule:
            ok = str(rule["contains"]).lower() in json.dumps(value, default=str).lower()
        elif isinstance(rule, dict) and "equals" in rule:
            ok = value == rule["equals"]
        else:
            ok = value == rule
        checks.append(_check(f"output_field:{dotted}", ok, rule, value))
    called = set(observed.get("tools_called", []))
    for tool in expected.tools_called:
        checks.append(_check(f"tool_called:{tool}", tool in called, tool, sorted(called)))
    attempted = called | set(observed.get("tools_denied", [])) | set(observed.get("tools_rejected", []))
    for tool in expected.tools_not_called:
        checks.append(_check(f"tool_not_executed:{tool}", tool not in called, tool, sorted(called)))
    denied = set(observed.get("tools_denied", []))
    for tool in expected.tools_denied:
        checks.append(_check(f"tool_denied:{tool}", tool in denied, tool, sorted(denied)))
    if expected.approval_requested is not None:
        requested = bool(observed.get("approvals"))
        checks.append(
            _check(
                "approval_requested", requested == expected.approval_requested, expected.approval_requested, requested
            )
        )
    if expected.max_latency_ms is not None:
        latency = observed.get("duration_ms") or 0
        checks.append(_check("max_latency_ms", latency <= expected.max_latency_ms, expected.max_latency_ms, latency))
    if expected.max_model_calls is not None:
        calls = observed.get("usage", {}).get("model_calls", 0)
        checks.append(_check("max_model_calls", calls <= expected.max_model_calls, expected.max_model_calls, calls))
    _ = attempted
    return checks


def groundedness(output: Any, known_sources: list[str]) -> float | None:
    """Fraction of cited references that correspond to sources the agent actually retrieved."""
    if not isinstance(output, dict):
        return None
    cited: list[str] = []
    for key in ("references", "citations", "sources"):
        value = output.get(key)
        if isinstance(value, list):
            cited += [str(v) for v in value]
    if not cited:
        return None
    known = [s.lower() for s in known_sources]
    grounded = sum(1 for c in cited if any(k in c.lower() or c.lower() in k for k in known))
    return round(grounded / len(cited), 3)

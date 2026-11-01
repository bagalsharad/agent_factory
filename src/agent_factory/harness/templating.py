"""Minimal, non-executable template rendering for workflow prompts and tool arguments.

Supported: ``{{ input.<path> }}``, ``{{ steps.<id>.output[.<path>] }}``, ``{{ components.<name> }}``.
There is no expression language, no attribute access on Python objects and no code execution.
"""

from __future__ import annotations

import json
import re
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.\-]*)\s*\}\}")
_MISSING = object()


def lookup(scope: dict[str, Any], path: str) -> Any:
    current: Any = scope
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def render(template: Any, scope: dict[str, Any]) -> Any:
    if isinstance(template, dict):
        return {k: render(v, scope) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, scope) for v in template]
    if not isinstance(template, str):
        return template
    whole = PLACEHOLDER.fullmatch(template.strip())
    if whole:  # a lone placeholder keeps the value's type (lists, numbers, objects)
        value = lookup(scope, whole.group(1))
        return None if value is _MISSING else value

    def substitute(match: re.Match[str]) -> str:
        value = lookup(scope, match.group(1))
        if value is _MISSING or value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value, default=str)

    return PLACEHOLDER.sub(substitute, template)

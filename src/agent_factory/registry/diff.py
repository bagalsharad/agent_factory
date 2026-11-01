"""Structural JSON diff for version comparison."""

from __future__ import annotations

from typing import Any


def json_diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = f"{path}.{key}" if path else str(key)
            if key not in old:
                changes.append({"path": sub, "op": "added", "new": new[key]})
            elif key not in new:
                changes.append({"path": sub, "op": "removed", "old": old[key]})
            else:
                changes.extend(json_diff(old[key], new[key], sub))
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            changes.extend(json_diff(a, b, f"{path}[{i}]"))
    elif old != new:
        changes.append({"path": path, "op": "changed", "old": old, "new": new})
    return changes

"""Minimal semantic-version helpers for agent, template and component versions."""

from __future__ import annotations

import re
from typing import Literal

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$")


def parse(version: str) -> tuple[int, int, int, str]:
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"'{version}' is not a semantic version (MAJOR.MINOR.PATCH)")
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), pre or ""


def is_valid(version: str) -> bool:
    return bool(SEMVER_RE.match(version))


def bump(version: str, part: Literal["major", "minor", "patch"] = "minor") -> str:
    major, minor, patch, _ = parse(version)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def sort_key(version: str) -> tuple[int, int, int, int, str]:
    major, minor, patch, pre = parse(version)
    # a release sorts after its pre-releases
    return major, minor, patch, 0 if pre else 1, pre

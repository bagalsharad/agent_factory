"""Identifier and time helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    """Stable SHA-256 of a JSON-serialisable value (used for spec, policy and workflow provenance)."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()

"""Secret redaction for logs, events, error messages and stored tool arguments."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|credential|private[_-]?key|cookie|session)",
    re.IGNORECASE,
)

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9._\-~+/]+=*"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}"),  # OpenAI / Anthropic style keys
    re.compile(r"\baf_[A-Za-z0-9]{8}_[A-Za-z0-9_\-]{20,}"),  # Agent Factory API keys
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key ids
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),  # GitHub tokens
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),  # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\s*[=:]\s*['\"]?[^\s'\",;]{4,}"),
    re.compile(r"(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:\s/]+:[^@\s]+@"),
]


def redact_text(text: str) -> str:
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(_replacement, result)
    return result


def _replacement(match: re.Match[str]) -> str:
    value = match.group(0)
    for sep in ("=", ":"):
        if sep in value and not value.lower().startswith(("postgres", "mysql", "mongodb", "redis")):
            key = value.split(sep, 1)[0]
            return f"{key}{sep}{REDACTED}"
    if "://" in value:
        scheme = value.split("://", 1)[0]
        return f"{scheme}://{REDACTED}@"
    return REDACTED


def redact(value: Any, *, depth: int = 0) -> Any:
    """Recursively redact secrets in dicts/lists/strings. Keys that look sensitive are fully masked."""
    if depth > 20:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and SENSITIVE_KEYS.search(key) and item not in (None, "", [], {}):
                out[key] = REDACTED
            else:
                out[key] = redact(item, depth=depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact(item, depth=depth + 1) for item in value]
    return value


def contains_secret(text: str) -> bool:
    return redact_text(text) != text

"""Tool registry and tool execution runtime."""

from __future__ import annotations

import concurrent.futures
import json
import time
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from pydantic import ValidationError

from ..core.errors import NotFoundError, SandboxUnavailableError, ToolError
from ..core.logging import get_logger
from .base import Tool, ToolContext, ToolDefinition

log = get_logger(__name__)

MAX_OUTPUT_CHARS = 60_000


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, dict[str, Tool]] = {}
        self._disabled: set[str] = set()

    def register(self, tool: Tool) -> None:
        versions = self._tools.setdefault(tool.name, {})
        if tool.version in versions:
            raise ValueError(f"Tool {tool.name}@{tool.version} already registered")
        versions[tool.version] = tool

    def disable(self, names: list[str]) -> None:
        self._disabled.update(names)

    def set_disabled(self, names: list[str]) -> None:
        unknown = [n for n in names if n not in self._tools]
        if unknown:
            raise NotFoundError(f"Unknown tools: {unknown}")
        self._disabled = set(names)

    @property
    def disabled(self) -> list[str]:
        return sorted(self._disabled)

    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def get(self, name: str, version: str | None = None) -> Tool:
        versions = self._tools.get(name)
        if not versions:
            raise NotFoundError(f"Tool '{name}' is not registered")
        if version:
            if version not in versions:
                raise NotFoundError(f"Tool '{name}' has no version {version}")
            return versions[version]
        from ..core.semver import sort_key

        return versions[max(versions, key=sort_key)]

    def has(self, name: str, version: str | None = None) -> bool:
        try:
            self.get(name, version)
            return True
        except NotFoundError:
            return False

    def definitions(self) -> list[ToolDefinition]:
        return [self.get(name).definition for name in sorted(self._tools)]


@dataclass
class ToolResult:
    ok: bool
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duration_ms: float = 0.0
    retryable: bool = False
    attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
        }


class ToolExecutor:
    """Runs an *already authorized* tool call: input validation, timeout, output validation, normalization."""

    def __init__(self, max_workers: int = 8) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="af-tool")

    def execute(self, tool: Tool, arguments: dict[str, Any], ctx: ToolContext, *, max_retries: int = 0) -> ToolResult:
        definition = tool.definition
        started = time.perf_counter()
        try:
            args = tool.Input.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                error={
                    "code": "invalid_arguments",
                    "message": "Arguments failed validation",
                    "details": [{"path": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
                },
                duration_ms=_ms(started),
            )
        attempts = 0
        retries_allowed = max_retries if definition.idempotent else 0
        while True:
            attempts += 1
            future = self._pool.submit(tool.run, args, ctx)
            try:
                output = future.result(timeout=definition.timeout_seconds)
                output = _validate_output(definition, output)
                return ToolResult(ok=True, output=output, duration_ms=_ms(started), attempts=attempts)
            except concurrent.futures.TimeoutError:
                future.cancel()
                return ToolResult(
                    ok=False,
                    error={"code": "timeout", "message": f"Tool exceeded {definition.timeout_seconds}s"},
                    duration_ms=_ms(started),
                    retryable=False,
                    attempts=attempts,
                )
            except SandboxUnavailableError as exc:
                return ToolResult(
                    ok=False,
                    error={"code": exc.code, "message": exc.message},
                    duration_ms=_ms(started),
                    attempts=attempts,
                )
            except ToolError as exc:
                if exc.retryable and attempts <= retries_allowed:
                    log.info("retrying tool", extra={"tool": definition.name, "attempt": attempts})
                    continue
                details = exc.details if isinstance(exc.details, dict) else {}
                return ToolResult(
                    ok=False,
                    error={"code": details.get("code", exc.code), "message": exc.message},
                    duration_ms=_ms(started),
                    retryable=exc.retryable,
                    attempts=attempts,
                )
            except Exception as exc:  # tool bug: normalize, never leak internals to the model
                log.exception("tool raised unexpectedly", extra={"tool": definition.name})
                return ToolResult(
                    ok=False,
                    error={"code": "tool_internal_error", "message": exc.__class__.__name__},
                    duration_ms=_ms(started),
                    attempts=attempts,
                )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _validate_output(definition: ToolDefinition, output: Any) -> dict[str, Any]:
    if not isinstance(output, dict):
        raise ToolError("Tool returned a non-object result")
    try:
        jsonschema.validate(output, definition.output_schema)
    except jsonschema.ValidationError as exc:
        raise ToolError(f"Tool output failed schema validation: {exc.message}") from exc
    text = json.dumps(output, default=str)
    limit = definition.max_output_chars or MAX_OUTPUT_CHARS
    if len(text) > limit:
        return {"truncated": True, "preview": text[:limit], "original_chars": len(text)}
    return output


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def build_default_registry(disabled: list[str] | None = None) -> ToolRegistry:
    from .builtin import BUILTIN_TOOLS

    registry = ToolRegistry()
    for cls in BUILTIN_TOOLS:
        registry.register(cls())
    if disabled:
        registry.disable(disabled)
    return registry

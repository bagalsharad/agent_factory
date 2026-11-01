"""Anthropic (Claude) adapter — optional cloud provider, disabled by default.

Enabled only when ALL of the following hold:

* the ``anthropic`` extra is installed (``uv sync --extra anthropic``),
* ``AF_CLOUD_PROVIDERS_ENABLED=true`` (explicit opt-in: prompts and tool outputs leave the machine),
* an API key is configured (``AF_ANTHROPIC_API_KEY``).

Implementation notes (current Claude API):

* Sampling parameters (temperature/top_p/top_k) are not sent — current models reject them.
* Thinking is left at the model default (adaptive on current models); thinking blocks are carried in
  ``ChatResponse.raw_content`` and replayed unchanged on the next turn of the same conversation.
* Forced ``tool_choice`` is not used; tool selection is ``auto`` and argument validity is enforced by the
  harness/tool runtime anyway.
* Structured output uses ``output_config.format`` (json_schema).
* ``stop_reason == "refusal"`` surfaces as a non-retryable ProviderError. Server-side refusal fallbacks
  (``fallbacks: "default"``) are enabled by default and can be disabled with AF_ANTHROPIC_FALLBACKS=off.
* The SDK's own retries are disabled; the gateway's retry policy applies uniformly to all providers.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from ..core.errors import ProviderError, ProviderUnavailableError
from .base import ModelProvider
from .types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    Usage,
)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# USD per million tokens (input, output) — Anthropic first-party list prices; used for cost budgets only.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs require closed objects; add additionalProperties:false where unspecified."""
    out = dict(schema)
    if out.get("type") == "object" or "properties" in out:
        out.setdefault("additionalProperties", False)
        out["properties"] = {k: _strict_schema(v) for k, v in out.get("properties", {}).items()}
    if isinstance(out.get("items"), dict):
        out["items"] = _strict_schema(out["items"])
    return out


class AnthropicProvider(ModelProvider):
    name = "anthropic"
    local = False

    def __init__(self, api_key: str, *, timeout_seconds: float = 120.0, fallbacks: str = "default") -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderUnavailableError("Install the 'anthropic' extra to use the Anthropic provider") from exc
        self._sdk = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout_seconds)
        self.fallbacks = fallbacks

    # ------------------------------------------------------------------ metadata
    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            self._client.models.list(limit=1)
            return ProviderHealth(
                provider=self.name,
                status="up",
                detail="Claude API reachable (cloud)",
                local=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        except self._sdk.APIError as exc:
            return ProviderHealth(
                provider=self.name, status="down", local=False, detail=f"Claude API error: {exc.__class__.__name__}"
            )

    def list_models(self) -> list[ModelInfo]:
        try:
            models = list(self._client.models.list())
        except self._sdk.APIError as exc:
            raise ProviderUnavailableError(f"Claude API unavailable: {exc.__class__.__name__}") from exc
        return [
            ModelInfo(
                provider=self.name,
                name=m.id,
                capabilities=self.capabilities(m.id),
                local=False,
                context_window=getattr(m, "max_input_tokens", None),
                details={"display_name": getattr(m, "display_name", m.id)},
            )
            for m in models
        ]

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(chat=True, tools=True, structured_output=True, streaming=True, vision=True)

    # ------------------------------------------------------------------ request mapping
    def _messages(self, messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            if m.role == "tool":
                pending_results.append(
                    {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content or "(empty)"}
                )
                continue
            flush_results()
            if m.role == "assistant":
                if m.raw_content and m.raw_provider == self.name:
                    out.append({"role": "assistant", "content": m.raw_content})  # replay unchanged
                    continue
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks += [
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in m.tool_calls
                ]
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no content)"}]})
            else:
                out.append({"role": "user", "content": m.content or "(empty)"})
        flush_results()
        return "\n\n".join(system_parts), out

    def _params(self, request: ChatRequest) -> dict[str, Any]:
        system, messages = self._messages(request.messages)
        params: dict[str, Any] = {
            "model": request.model or DEFAULT_MODEL,
            "max_tokens": request.max_output_tokens or 16_000,
            "messages": messages,
        }
        if system:
            params["system"] = system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in request.tools
            ]
        if request.response_schema:
            params["output_config"] = {
                "format": {"type": "json_schema", "schema": _strict_schema(request.response_schema)}
            }
        if self.fallbacks == "default" and params["model"] in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    # ------------------------------------------------------------------ inference
    def chat(self, request: ChatRequest) -> ChatResponse:
        sdk = self._sdk
        started = time.perf_counter()
        params = self._params(request)
        try:
            client = self._client.with_options(timeout=request.timeout_seconds)
            response = client.beta.messages.create(**params) if "betas" in params else client.messages.create(**params)
        except sdk.APITimeoutError as exc:
            raise ProviderError("Claude API request timed out", details={"code": "timeout"}) from exc
        except sdk.RateLimitError as exc:
            raise ProviderError("Claude API rate limited", retryable=True) from exc
        except sdk.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderError(f"Claude API server error {exc.status_code}", retryable=True) from exc
            raise ProviderError(
                f"Claude API rejected the request ({exc.status_code})", details={"code": "bad_request"}
            ) from exc
        except sdk.APIConnectionError as exc:
            raise ProviderUnavailableError("Cannot reach the Claude API", retryable=True) from exc

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise ProviderError(
                "The model declined the request (refusal)",
                details={"code": "refusal", "category": getattr(details, "category", None)},
            )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else json.loads(json.dumps(block.input))
                calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
        usage = response.usage
        input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
        price = PRICES.get(response.model)
        cost = (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000 if price else None
        return ChatResponse(
            content="".join(text_parts),
            tool_calls=calls,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            model=response.model,
            provider=self.name,
            finish_reason=response.stop_reason,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            cost_usd=cost,
            raw_content=[block.model_dump(mode="json", exclude_none=True) for block in response.content],
        )

    def stream(self, request: ChatRequest) -> Iterator[str]:
        params = self._params(request)
        params.pop("betas", None)
        params.pop("fallbacks", None)
        params["max_tokens"] = request.max_output_tokens or 64_000
        with self._client.with_options(timeout=request.timeout_seconds).messages.stream(**params) as stream:
            yield from stream.text_stream

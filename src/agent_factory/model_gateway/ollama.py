"""Ollama adapter (local inference). Never pulls models automatically."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from ..core.errors import ProviderError, ProviderUnavailableError
from .base import ModelProvider
from .types import (
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    Usage,
    estimate_tokens,
)

# Families known to support native tool calling when /api/show does not report capabilities (older Ollama).
_TOOL_FAMILIES = (
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "qwen2.5",
    "qwen3",
    "mistral",
    "mistral-nemo",
    "command-r",
    "granite3",
    "hermes3",
    "gpt-oss",
    "firefunction",
    "smollm2",
    "phi4-mini",
)


class OllamaProvider(ModelProvider):
    name = "ollama"
    local = True

    def __init__(
        self, base_url: str, timeout_seconds: float = 120.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self._transport = transport
        self._cap_cache: dict[str, ModelCapabilities] = {}

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout or self.timeout, transport=self._transport)

    # ------------------------------------------------------------------ metadata
    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            with self._client(timeout=3) as client:
                version = client.get("/api/version").json().get("version", "unknown")
            return ProviderHealth(
                provider=self.name,
                status="up",
                detail=f"Ollama {version} at {self.base_url}",
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderHealth(
                provider=self.name,
                status="down",
                detail=f"Cannot reach Ollama at {self.base_url} ({exc.__class__.__name__}). "
                "Install from https://ollama.com and run `ollama serve`.",
            )

    def list_models(self) -> list[ModelInfo]:
        try:
            with self._client(timeout=5) as client:
                models = client.get("/api/tags").json().get("models", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailableError(f"Ollama unavailable: {exc.__class__.__name__}") from exc
        return [
            ModelInfo(
                provider=self.name,
                name=m["name"],
                capabilities=self.capabilities(m["name"]),
                size_bytes=m.get("size"),
                details=m.get("details", {}),
                context_window=self._context_window(m["name"]),
            )
            for m in models
        ]

    def _show(self, model: str) -> dict[str, Any]:
        try:
            with self._client(timeout=5) as client:
                response = client.post("/api/show", json={"model": model})
                if response.status_code == 404:
                    return {}
                return response.json()
        except (httpx.HTTPError, ValueError):
            return {}

    def _context_window(self, model: str) -> int | None:
        info = self._show(model).get("model_info", {})
        for key, value in info.items():
            if key.endswith(".context_length"):
                return int(value)
        return None

    def capabilities(self, model: str) -> ModelCapabilities:
        if model in self._cap_cache:
            return self._cap_cache[model]
        show = self._show(model)
        caps = show.get("capabilities")
        if isinstance(caps, list):
            result = ModelCapabilities(
                chat="completion" in caps,
                tools="tools" in caps,
                structured_output=True,
                streaming=True,
                vision="vision" in caps,
                embeddings="embedding" in caps,
            )
        else:
            family = model.split(":")[0].lower()
            result = ModelCapabilities(
                chat=True, tools=family.startswith(_TOOL_FAMILIES), structured_output=True, streaming=True
            )
        self._cap_cache[model] = result
        return result

    def model_installed(self, model: str) -> bool:
        try:
            return any(m.name == model or m.name == f"{model}:latest" for m in self.list_models())
        except ProviderUnavailableError:
            return False

    # ------------------------------------------------------------------ inference
    def _payload(self, request: ChatRequest, stream: bool) -> dict[str, Any]:
        messages = []
        for m in request.messages:
            item: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [{"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls]
            if m.role == "tool" and m.name:
                item["tool_name"] = m.name
            messages.append(item)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": request.temperature},
        }
        if request.max_output_tokens:
            payload["options"]["num_predict"] = request.max_output_tokens
        if request.context_window:
            payload["options"]["num_ctx"] = request.context_window  # Ollama's default window is small
        if request.response_schema or request.tools:
            payload["think"] = False  # reasoning models: keep answers parseable
        if request.tools:
            payload["tools"] = [{"type": "function", "function": t.model_dump()} for t in request.tools]
        if request.response_schema and not request.tools:
            payload["format"] = request.response_schema
        return payload

    def chat(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        try:
            with self._client(timeout=request.timeout_seconds) as client:
                response = client.post("/api/chat", json=self._payload(request, stream=False))
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Ollama timed out after {request.timeout_seconds}s", retryable=False, details={"code": "timeout"}
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Ollama unavailable: {exc.__class__.__name__}", retryable=True) from exc
        if response.status_code == 404:
            raise ProviderError(
                f"Model '{request.model}' is not installed in Ollama. Install it explicitly with "
                f"`ollama pull {request.model}`.",
                details={"code": "model_missing"},
            )
        if response.status_code >= 500:
            raise ProviderError(f"Ollama error {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(f"Ollama rejected the request ({response.status_code}): {response.text[:300]}")
        data = response.json()
        message = data.get("message", {})
        calls = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append(ToolCall(id=tc.get("id") or f"ollama_{i}", name=fn.get("name", ""), arguments=args))
        content = message.get("content", "")
        prompt_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        return ChatResponse(
            content=content,
            tool_calls=calls,
            usage=Usage(input_tokens=prompt_tokens, output_tokens=output_tokens, estimated=prompt_tokens is None)
            if prompt_tokens is not None
            else Usage(
                input_tokens=estimate_tokens(json.dumps(self._payload(request, False)["messages"])),
                output_tokens=estimate_tokens(content),
                estimated=True,
            ),
            model=request.model,
            provider=self.name,
            finish_reason=data.get("done_reason"),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            cost_usd=0.0,
        )

    def stream(self, request: ChatRequest) -> Iterator[str]:
        try:
            with (
                self._client(timeout=request.timeout_seconds) as client,
                client.stream("POST", "/api/chat", json=self._payload(request, stream=True)) as response,
            ):
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if text := chunk.get("message", {}).get("content"):
                        yield text
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Ollama stream failed: {exc.__class__.__name__}") from exc

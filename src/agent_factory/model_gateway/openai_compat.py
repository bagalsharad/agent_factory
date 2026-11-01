"""OpenAI-compatible chat completions adapter (LM Studio, vLLM, llama.cpp server, hosted APIs).

Disabled by default. When the base URL is not a loopback/private address the provider is treated as
*cloud* and additionally requires AF_CLOUD_PROVIDERS_ENABLED=true.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..core.errors import ProviderError, ProviderUnavailableError
from .base import ModelProvider
from .types import ChatRequest, ChatResponse, ModelCapabilities, ModelInfo, ProviderHealth, ToolCall, Usage


def is_local_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host in {"localhost"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


class OpenAICompatibleProvider(ModelProvider):
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout_seconds
        self._transport = transport
        self.local = is_local_url(self.base_url)

    def _client(self, timeout: float | None = None) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        return httpx.Client(
            base_url=self.base_url, timeout=timeout or self.timeout, headers=headers, transport=self._transport
        )

    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            with self._client(timeout=4) as client:
                response = client.get("/models")
                response.raise_for_status()
            return ProviderHealth(
                provider=self.name,
                status="up",
                detail=self.base_url,
                local=self.local,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
        except httpx.HTTPError as exc:
            return ProviderHealth(
                provider=self.name,
                status="down",
                local=self.local,
                detail=f"{self.base_url} unreachable ({exc.__class__.__name__})",
            )

    def list_models(self) -> list[ModelInfo]:
        try:
            with self._client(timeout=5) as client:
                data = client.get("/models").json().get("data", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailableError(f"{self.base_url} unavailable") from exc
        return [
            ModelInfo(provider=self.name, name=m["id"], capabilities=self.capabilities(m["id"]), local=self.local)
            for m in data
            if "id" in m
        ]

    def capabilities(self, model: str) -> ModelCapabilities:
        # The OpenAI-compatible API does not advertise per-model capabilities. Tool calling and JSON schema
        # output are widely but not universally implemented; the harness falls back to the JSON protocol
        # when a native tool call is rejected by the server.
        return ModelCapabilities(chat=True, tools=True, structured_output=True, streaming=True)

    def _payload(self, request: ChatRequest, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            item: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            if m.role == "tool":
                item["tool_call_id"] = m.tool_call_id
            messages.append(item)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "stream": stream,
        }
        if request.max_output_tokens:
            payload["max_tokens"] = request.max_output_tokens
        if request.tools:
            payload["tools"] = [{"type": "function", "function": t.model_dump()} for t in request.tools]
        elif request.response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": request.response_schema},
            }
        return payload

    def chat(self, request: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        try:
            with self._client(timeout=request.timeout_seconds) as client:
                response = client.post("/chat/completions", json=self._payload(request, stream=False))
        except httpx.TimeoutException as exc:
            raise ProviderError("Model call timed out", details={"code": "timeout"}) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"Provider unavailable: {exc.__class__.__name__}", retryable=True) from exc
        if response.status_code in {429} or response.status_code >= 500:
            raise ProviderError(f"Provider error {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(
                f"Provider rejected request ({response.status_code})", details={"body": response.text[:300]}
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
        usage = data.get("usage") or {}
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=calls,
            usage=Usage(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens")),
            model=data.get("model", request.model),
            provider=self.name,
            finish_reason=choice.get("finish_reason"),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def stream(self, request: ChatRequest) -> Iterator[str]:
        with (
            self._client(timeout=request.timeout_seconds) as client,
            client.stream("POST", "/chat/completions", json=self._payload(request, stream=True)) as response,
        ):
            for line in response.iter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                delta = json.loads(line[6:]).get("choices", [{}])[0].get("delta", {})
                if text := delta.get("content"):
                    yield text

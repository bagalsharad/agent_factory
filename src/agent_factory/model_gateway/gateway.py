"""Model gateway: provider registry, enablement rules, retries, timeouts and health checks.

Enablement rules (local-first):

* ``mock`` and ``ollama`` are local and enabled by default.
* ``openai_compatible`` must be enabled explicitly; a non-local base URL additionally needs
  AF_CLOUD_PROVIDERS_ENABLED=true.
* ``anthropic`` (cloud) needs AF_CLOUD_PROVIDERS_ENABLED=true, an API key and the optional extra.

A disabled provider is still *listed* (so the UI can explain how to enable it) but cannot be called.
"""

from __future__ import annotations

import concurrent.futures
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from ..core.circuit import CircuitRegistry
from ..core.config import Settings
from ..core.errors import ProviderError, ProviderUnavailableError
from ..core.logging import get_logger
from .base import ModelProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatibleProvider
from .types import ChatRequest, ChatResponse, ModelCapabilities, ModelInfo, ProviderHealth

log = get_logger(__name__)


@dataclass
class ProviderEntry:
    name: str
    provider: ModelProvider | None
    enabled: bool
    local: bool
    reason: str = ""
    description: str = ""


class ModelGateway:
    def __init__(
        self,
        settings: Settings,
        extra: dict[str, ModelProvider] | None = None,
        circuits: CircuitRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.circuits = circuits or CircuitRegistry(
            failure_threshold=settings.circuit_failure_threshold, recovery_seconds=settings.circuit_recovery_seconds
        )
        self._entries: dict[str, ProviderEntry] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="af-model")
        self._register_defaults()
        for name, provider in (extra or {}).items():
            self._entries[name] = ProviderEntry(name, provider, True, provider.local, description="custom")

    # ------------------------------------------------------------------ registry
    def _register_defaults(self) -> None:
        s = self.settings
        self._entries["mock"] = ProviderEntry(
            "mock", MockProvider(), True, True, description="Deterministic built-in model for tests and demos"
        )
        self._entries["ollama"] = ProviderEntry(
            "ollama",
            OllamaProvider(s.ollama_base_url, s.model_timeout_seconds) if s.ollama_enabled else None,
            s.ollama_enabled,
            True,
            "" if s.ollama_enabled else "AF_OLLAMA_ENABLED=false",
            description="Local inference through Ollama",
        )
        compat = OpenAICompatibleProvider(
            s.openai_compat_base_url,
            s.openai_compat_api_key.get_secret_value() if s.openai_compat_api_key else None,
            s.model_timeout_seconds,
        )
        compat_enabled, reason = s.openai_compat_enabled, ""
        if not compat_enabled:
            reason = "Set AF_OPENAI_COMPAT_ENABLED=true and AF_OPENAI_COMPAT_BASE_URL"
        elif not compat.local and not s.cloud_providers_enabled:
            compat_enabled, reason = False, "Base URL is not local; requires AF_CLOUD_PROVIDERS_ENABLED=true"
        self._entries["openai_compatible"] = ProviderEntry(
            "openai_compatible",
            compat if compat_enabled else None,
            compat_enabled,
            compat.local,
            reason,
            description="Any OpenAI-compatible endpoint (LM Studio, vLLM, llama.cpp, hosted)",
        )
        anthropic_entry = ProviderEntry("anthropic", None, False, False, description="Claude API (cloud, optional)")
        if not s.cloud_providers_enabled:
            anthropic_entry.reason = "Cloud providers are disabled (AF_CLOUD_PROVIDERS_ENABLED=false)"
        elif not s.anthropic_api_key:
            anthropic_entry.reason = "AF_ANTHROPIC_API_KEY is not configured"
        else:
            try:
                from .anthropic_provider import AnthropicProvider

                anthropic_entry.provider = AnthropicProvider(
                    s.anthropic_api_key.get_secret_value(),
                    timeout_seconds=s.model_timeout_seconds,
                    fallbacks=s.anthropic_fallbacks,
                )
                anthropic_entry.enabled = True
            except ProviderUnavailableError as exc:
                anthropic_entry.reason = exc.message
        self._entries["anthropic"] = anthropic_entry

    def register(self, name: str, provider: ModelProvider) -> None:
        self._entries[name] = ProviderEntry(name, provider, True, provider.local, description="custom")

    def entries(self) -> list[ProviderEntry]:
        return list(self._entries.values())

    def known(self, name: str) -> bool:
        return name in self._entries

    def is_enabled(self, name: str) -> bool:
        entry = self._entries.get(name)
        return bool(entry and entry.enabled and entry.provider)

    def provider(self, name: str) -> ModelProvider:
        entry = self._entries.get(name)
        if not entry:
            raise ProviderUnavailableError(f"Unknown model provider '{name}'")
        if not entry.enabled or entry.provider is None:
            raise ProviderUnavailableError(f"Provider '{name}' is disabled: {entry.reason}")
        return entry.provider

    # ------------------------------------------------------------------ metadata
    def health(self) -> list[ProviderHealth]:
        out = []
        for entry in self._entries.values():
            if not entry.enabled or entry.provider is None:
                out.append(
                    ProviderHealth(
                        provider=entry.name, status="disabled", detail=entry.reason, local=entry.local, enabled=False
                    )
                )
                continue
            try:
                health = entry.provider.health()
            except Exception as exc:  # health checks must never raise
                health = ProviderHealth(provider=entry.name, status="down", detail=exc.__class__.__name__)
            health.local, health.enabled = entry.local, True
            out.append(health)
        return out

    def list_models(self, provider: str | None = None) -> tuple[list[ModelInfo], dict[str, str]]:
        models: list[ModelInfo] = []
        errors: dict[str, str] = {}
        for entry in self._entries.values():
            if provider and entry.name != provider:
                continue
            if not entry.enabled or entry.provider is None:
                continue
            try:
                models.extend(entry.provider.list_models())
            except ProviderError as exc:
                errors[entry.name] = exc.message
        return models, errors

    def capabilities(self, provider: str, model: str) -> ModelCapabilities:
        return self.provider(provider).capabilities(model)

    # ------------------------------------------------------------------ inference
    def chat(
        self,
        provider: str,
        request: ChatRequest,
        *,
        max_retries: int | None = None,
        on_retry: Callable[[int, ProviderError], None] | None = None,
    ) -> ChatResponse:
        """Call a provider with timeout enforcement and bounded exponential-backoff retries."""
        adapter = self.provider(provider)
        retries = self.settings.model_max_retries if max_retries is None else max_retries
        breaker = self.circuits.get(f"model:{provider}")
        attempt = 0
        while True:
            attempt += 1
            if not breaker.allow():
                raise ProviderError(
                    f"Provider '{provider}' is failing; calls are paused for {breaker.retry_after():.0f}s "
                    "(circuit open)",
                    details={"code": "circuit_open", "provider": provider},
                )
            future = self._pool.submit(adapter.chat, request)
            try:
                response = future.result(timeout=request.timeout_seconds + 5)
                breaker.record_success()
                return response
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                breaker.record_failure()
                raise ProviderError(
                    f"Model call exceeded {request.timeout_seconds}s", details={"code": "timeout"}
                ) from exc
            except ProviderError as exc:
                if exc.retryable:  # outages and overload count; bad requests say nothing about provider health
                    breaker.record_failure()
                if not exc.retryable or attempt > retries:
                    raise
                delay = min(0.25 * 2 ** (attempt - 1), 4.0) + random.uniform(0, 0.1)  # noqa: S311
                log.info("retrying model call", extra={"provider": provider, "attempt": attempt, "error": exc.message})
                if on_retry:
                    on_retry(attempt, exc)
                time.sleep(delay)

    def stream(self, provider: str, request: ChatRequest) -> Iterator[str]:
        caps = self.provider(provider).capabilities(request.model)
        if not caps.streaming:
            yield self.chat(provider, request).content
            return
        yield from self.provider(provider).stream(request)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

"""Provider adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from .types import ChatRequest, ChatResponse, ModelCapabilities, ModelInfo, ProviderHealth


class ModelProvider(ABC):
    """A model provider adapter. Implementations must not assume capabilities they cannot verify."""

    name: str
    local: bool = True  # False → data leaves the machine; requires explicit enablement

    @abstractmethod
    def chat(self, request: ChatRequest) -> ChatResponse: ...

    def stream(self, request: ChatRequest) -> Iterator[str]:
        """Default streaming falls back to a single chunk for providers without native streaming."""
        yield self.chat(request).content

    @abstractmethod
    def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def capabilities(self, model: str) -> ModelCapabilities: ...

    @abstractmethod
    def health(self) -> ProviderHealth: ...

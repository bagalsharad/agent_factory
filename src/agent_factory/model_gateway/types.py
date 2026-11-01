"""Provider-neutral request/response types."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Provider-native content blocks of an assistant turn (e.g. Anthropic thinking blocks) that must be
    # replayed unchanged to the same provider. Other providers ignore it.
    raw_content: list[dict[str, Any]] | None = None
    raw_provider: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    temperature: float = 0.0
    max_output_tokens: int | None = None
    timeout_seconds: float = 120.0
    context_window: int | None = None  # requested context size (providers that need it, e.g. Ollama num_ctx)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated: bool = False

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class ChatResponse(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str
    provider: str
    finish_reason: str | None = None
    latency_ms: float = 0.0
    cost_usd: float | None = None
    raw_content: list[dict[str, Any]] | None = None

    def as_message(self) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=self.content,
            tool_calls=self.tool_calls,
            raw_content=self.raw_content,
            raw_provider=self.provider if self.raw_content else None,
        )


class ModelCapabilities(BaseModel):
    chat: bool = True
    tools: bool = False
    structured_output: bool = False
    streaming: bool = False
    vision: bool = False
    embeddings: bool = False

    def supports(self, capability: str) -> bool:
        return bool(getattr(self, capability, False))


class ModelInfo(BaseModel):
    provider: str
    name: str
    capabilities: ModelCapabilities
    context_window: int | None = None
    size_bytes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    local: bool = True


class ProviderHealth(BaseModel):
    provider: str
    status: Literal["up", "down", "disabled"]
    detail: str = ""
    latency_ms: float | None = None
    local: bool = True
    enabled: bool = True


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈4 characters per token) used when a provider reports no usage."""
    return max(1, len(text) // 4) if text else 0

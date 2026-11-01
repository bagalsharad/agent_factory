"""Model gateway: mock determinism, provider adapters (mocked HTTP), enablement rules, retries, timeouts."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agent_factory.core.config import Settings
from agent_factory.core.errors import ProviderError, ProviderUnavailableError
from agent_factory.model_gateway.gateway import ModelGateway
from agent_factory.model_gateway.mock import MockProvider
from agent_factory.model_gateway.ollama import OllamaProvider
from agent_factory.model_gateway.openai_compat import OpenAICompatibleProvider, is_local_url
from agent_factory.model_gateway.types import ChatMessage, ChatRequest, ToolSpec

TOOL = ToolSpec(name="read_pipeline_logs", description="read logs", parameters={
    "type": "object", "required": ["pipeline_id", "execution_id"],
    "properties": {"pipeline_id": {"type": "string"}, "execution_id": {"type": "string"}}})


def req(messages: list[ChatMessage], **kw: Any) -> ChatRequest:
    return ChatRequest(model=kw.pop("model", "mock-default"), messages=messages, **kw)


def user(payload: dict[str, Any]) -> ChatMessage:
    return ChatMessage(role="user", content=f"<task_input>\n{json.dumps(payload)}\n</task_input>")


def test_mock_plans_tool_calls_from_known_inputs_and_is_deterministic() -> None:
    mock = MockProvider()
    request = req([ChatMessage(role="system", content="s"), user({"pipeline_id": "p", "execution_id": "e"})], tools=[TOOL])
    first, second = mock.chat(request), mock.chat(request)
    assert first.tool_calls[0].name == "read_pipeline_logs"
    assert first.tool_calls[0].arguments == {"pipeline_id": "p", "execution_id": "e"}
    assert first.model_dump(exclude={"latency_ms"}) == second.model_dump(exclude={"latency_ms"})


def test_mock_fills_output_schema_from_evidence() -> None:
    schema = {"type": "object", "required": ["summary", "probable_causes", "severity"], "properties": {
        "summary": {"type": "string"}, "probable_causes": {"type": "array", "items": {"type": "string"}},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]}}}
    tool_output = ChatMessage(role="tool", content='<untrusted_tool_output tool="read_pipeline_logs" step="s">\n'
                              '{"lines": ["ERROR Schema mismatch: column x", "FATAL Pipeline failed"]}\n</untrusted_tool_output>')
    response = MockProvider().chat(req([user({"pipeline_id": "p"}), tool_output], response_schema=schema))
    data = json.loads(response.content)
    assert data["severity"] == "high"
    assert any("Schema mismatch" in c for c in data["probable_causes"])
    assert response.usage.estimated and response.usage.input_tokens


def test_mock_variants() -> None:
    mock = MockProvider()
    with pytest.raises(ProviderError) as exc:
        mock.chat(req([user({})], model="mock-failing"))
    assert exc.value.retryable
    flaky = req([user({})], model="mock-flaky", metadata={"execution_id": "x", "step_id": "s"})
    with pytest.raises(ProviderError):
        mock.chat(flaky)
    assert mock.chat(flaky).content
    with pytest.raises(ProviderError, match="timed out"):
        mock.chat(req([user({})], model="mock-slow", timeout_seconds=0.1, metadata={"mock_delay_seconds": 5}))
    assert not mock.capabilities("mock-no-tools").tools


def test_gateway_retries_retryable_errors_then_gives_up(settings: Settings) -> None:
    gateway = ModelGateway(settings)
    retries: list[int] = []
    flaky = req([user({})], model="mock-flaky", metadata={"execution_id": "y", "step_id": "s"})
    assert gateway.chat("mock", flaky, max_retries=1, on_retry=lambda a, e: retries.append(a)).content
    assert retries == [1]
    with pytest.raises(ProviderError):
        gateway.chat("mock", req([user({})], model="mock-failing"), max_retries=2)


def test_provider_enablement_is_local_first() -> None:
    gateway = ModelGateway(Settings(_env_file=None, ollama_enabled=True))
    states = {e.name: e for e in gateway.entries()}
    assert states["mock"].enabled and states["ollama"].enabled
    assert not states["openai_compatible"].enabled
    assert not states["anthropic"].enabled and "Cloud providers are disabled" in states["anthropic"].reason
    with pytest.raises(ProviderUnavailableError):
        gateway.provider("anthropic")
    remote = ModelGateway(Settings(_env_file=None, openai_compat_enabled=True,
                                   openai_compat_base_url="https://api.openai.example/v1"))
    entry = next(e for e in remote.entries() if e.name == "openai_compatible")
    assert not entry.enabled and "CLOUD" in entry.reason.upper()
    keyed = ModelGateway(Settings(_env_file=None, cloud_providers_enabled=True, anthropic_api_key=SecretStr("x")))
    assert next(e for e in keyed.entries() if e.name == "anthropic").enabled
    assert is_local_url("http://127.0.0.1:1234/v1") and not is_local_url("https://api.example.com")


def ollama_transport(handler_map: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        result = handler_map[request.url.path]
        return result(request) if callable(result) else httpx.Response(200, json=result)
    return httpx.MockTransport(handler)


def test_ollama_adapter_maps_tool_calls_usage_and_capabilities() -> None:
    seen: dict[str, Any] = {}

    def chat(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_pipeline_logs", "arguments": {"pipeline_id": "p", "execution_id": "e"}}}]},
            "prompt_eval_count": 42, "eval_count": 7, "done_reason": "stop"})

    provider = OllamaProvider("http://ollama.test", transport=ollama_transport({
        "/api/chat": chat, "/api/show": {"capabilities": ["completion", "tools"], "model_info": {"qwen3.context_length": 32768}},
        "/api/tags": {"models": [{"name": "qwen3:8b", "size": 5_000_000_000, "details": {"family": "qwen3"}}]},
        "/api/version": {"version": "0.12.0"}}))
    response = provider.chat(req([user({"pipeline_id": "p"})], model="qwen3:8b", tools=[TOOL], temperature=0))
    assert response.tool_calls[0].arguments["execution_id"] == "e"
    assert response.usage.input_tokens == 42 and response.usage.output_tokens == 7
    assert seen["tools"][0]["function"]["name"] == "read_pipeline_logs" and seen["stream"] is False
    assert provider.capabilities("qwen3:8b").tools
    assert provider.list_models()[0].context_window == 32768
    assert provider.health().status == "up"


def test_ollama_missing_model_and_unreachable_server() -> None:
    provider = OllamaProvider("http://ollama.test", transport=ollama_transport({"/api/chat": lambda r: httpx.Response(404)}))
    with pytest.raises(ProviderError, match="ollama pull"):
        provider.chat(req([user({})], model="nope:1b"))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    down = OllamaProvider("http://ollama.test", transport=httpx.MockTransport(boom))
    assert down.health().status == "down"
    with pytest.raises(ProviderUnavailableError) as exc:
        down.chat(req([user({})], model="x"))
    assert exc.value.retryable


def test_openai_compatible_adapter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"model": "local", "choices": [{"message": {"content": '{"answer": "hi"}'},
                                                                         "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    provider = OpenAICompatibleProvider("http://127.0.0.1:1234/v1", "k", transport=httpx.MockTransport(handler))
    response = provider.chat(req([user({})], model="local", response_schema={"type": "object"}))
    assert json.loads(response.content) == {"answer": "hi"} and response.usage.output_tokens == 2
    assert provider.local

"""Model Context Protocol (MCP) connectors as governed tools.

An MCP server is registered by an administrator, marked trusted explicitly, and its tools are discovered,
schema-checked and classified. Only allow-listed tools become platform tools (``mcp__<server>__<tool>``); every
call then goes through the same harness path as built-in tools: agent allowlist, RBAC, policy (network
destination, writes), approval, budgets, circuit breaker, timeout, output limit and audit.

Defaults are conservative: an MCP tool is ``high`` risk with ``write`` side effects until an administrator
classifies it (server-provided annotations such as ``readOnlyHint`` are recorded but not trusted), so the default
policy requires human approval. Credentials are resolved from the secret store at call time and never reach the
model, the spec, logs or events.

Transports: ``stdio`` (a local command started per call; it must be in ``AF_MCP_ALLOWED_COMMANDS``) and ``http``
(Streamable HTTP, JSON or SSE responses).
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlsplit

import httpx
import jsonschema
from pydantic import BaseModel, ConfigDict

from ..core.errors import ToolError
from ..spec.models import RiskLevel
from ..tools.base import AuditConfig, SideEffect, Tool, ToolContext, ToolDefinition

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "agent-factory", "version": "1"}
NAME_RE = re.compile(r"[^a-z0-9_]+")


class MCPError(ToolError):
    pass


def tool_id(server: str, tool: str) -> str:
    return f"mcp__{NAME_RE.sub('_', server.lower())}__{NAME_RE.sub('_', tool.lower())}"[:120]


# ---------------------------------------------------------------------------------------------- clients
class MCPClient(ABC):
    def __init__(self, timeout: float, max_bytes: int) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._next_id = 0

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    @staticmethod
    def _result(message: dict[str, Any]) -> dict[str, Any]:
        if "error" in message:
            err = message["error"] or {}
            raise MCPError(f"MCP error {err.get('code')}: {str(err.get('message'))[:300]}", retryable=False)
        result = message.get("result")
        if not isinstance(result, dict):
            raise MCPError("MCP server returned a malformed result", retryable=False)
        return result

    @abstractmethod
    def session(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """initialize, then run each (method, params) request; returns their results."""

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        [result] = self.session([("tools/list", {})])
        tools.extend(t for t in result.get("tools", []) if isinstance(t, dict))
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        [result] = self.session([("tools/call", {"name": name, "arguments": arguments})])
        return result

    def _init_params(self) -> dict[str, Any]:
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}


class StdioMCPClient(MCPClient):
    def __init__(self, command: list[str], env: dict[str, str], timeout: float, max_bytes: int) -> None:
        super().__init__(timeout, max_bytes)
        self.command = command
        self.env = env

    def session(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        proc = subprocess.Popen(  # noqa: S603 - argv list, no shell; command allow-listed at registration
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self.env,
            text=True,
            bufsize=1,
        )
        lines: queue.Queue[str | None] = queue.Queue()
        received = [0]

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                received[0] += len(line)
                if received[0] > self.max_bytes:
                    lines.put(None)
                    return
                lines.put(line)
            lines.put(None)

        threading.Thread(target=reader, daemon=True).start()

        def send(message: dict[str, Any]) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()

        def wait_for(request_id: int) -> dict[str, Any]:
            while True:
                try:
                    line = lines.get(timeout=self.timeout)
                except queue.Empty as exc:
                    raise MCPError(f"MCP server did not answer within {self.timeout}s", retryable=True) from exc
                if line is None:
                    raise MCPError("MCP server closed the connection or exceeded the output limit", retryable=True)
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue  # servers may log noise; only JSON-RPC messages count
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message

        try:
            init_id = self._id()
            send({"jsonrpc": "2.0", "id": init_id, "method": "initialize", "params": self._init_params()})
            self._result(wait_for(init_id))
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            results = []
            for method, params in calls:
                request_id = self._id()
                send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                results.append(self._result(wait_for(request_id)))
            return results
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"MCP server process failed: {exc.__class__.__name__}", retryable=True) from exc
        finally:
            proc.kill()
            proc.wait(timeout=5)


class HttpMCPClient(MCPClient):
    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(timeout, max_bytes)
        self.url = url
        self.headers = headers
        self.transport = transport

    def _post(self, client: httpx.Client, message: dict[str, Any], session_id: str | None) -> httpx.Response:
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        with client.stream("POST", self.url, json=message, headers=headers) as response:
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > self.max_bytes:
                    raise MCPError("MCP response exceeded the output limit", retryable=False)
            response._content = body
        if response.status_code >= 500 or response.status_code == 429:
            raise MCPError(f"MCP server returned HTTP {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise MCPError(f"MCP server returned HTTP {response.status_code}", retryable=False)
        return response

    @staticmethod
    def _message(response: httpx.Response, request_id: int | None) -> dict[str, Any] | None:
        if request_id is None:
            return None
        if "text/event-stream" in response.headers.get("content-type", ""):
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    try:
                        message = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict) and message.get("id") == request_id:
                        return message
            raise MCPError("MCP server sent no response for the request", retryable=True)
        message = response.json()
        if not isinstance(message, dict):
            raise MCPError("MCP server returned a malformed message", retryable=False)
        return message

    def session(self, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False) as client:
                init_id = self._id()
                response = self._post(
                    client,
                    {"jsonrpc": "2.0", "id": init_id, "method": "initialize", "params": self._init_params()},
                    None,
                )
                self._result(self._message(response, init_id) or {})
                session_id = response.headers.get("mcp-session-id")
                self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id)
                results = []
                for method, params in calls:
                    request_id = self._id()
                    reply = self._post(
                        client, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, session_id
                    )
                    results.append(self._result(self._message(reply, request_id) or {}))
                return results
        except httpx.TimeoutException as exc:
            raise MCPError(f"MCP server did not answer within {self.timeout}s", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP connection failed: {exc.__class__.__name__}", retryable=True) from exc


# ---------------------------------------------------------------------------------------------- tools
class _Arguments(BaseModel):
    """MCP arguments are validated against the server's own JSON Schema in ``run``."""

    model_config = ConfigDict(extra="allow")


def schema_problems(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return ["inputSchema is not an object"]
    if schema.get("type") != "object":
        return ["inputSchema must describe an object"]
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return [f"invalid JSON Schema: {exc.message[:200]}"]
    return []


class MCPTool(Tool):
    Input = _Arguments
    connector = "mcp"
    audit = AuditConfig(log_arguments=True, log_output=False)

    def __init__(
        self,
        *,
        server: dict[str, Any],
        tool: dict[str, Any],
        settings: dict[str, Any],
        client_factory: Any,
    ) -> None:
        self.server = server
        self.remote_name = tool["name"]
        self.name = tool_id(server["name"], tool["name"])  # type: ignore[misc]
        self.description = f"[MCP {server['name']}] {str(tool.get('description') or tool['name'])[:900]}"  # type: ignore[misc]
        self.input_schema = tool["input_schema"]
        self.risk = RiskLevel(settings.get("risk", "high"))  # type: ignore[misc]
        self.side_effects = SideEffect(settings.get("side_effects", "write"))  # type: ignore[misc]
        self.idempotent = self.side_effects == SideEffect.READ  # type: ignore[misc]
        self.network = server["transport"] == "http"  # type: ignore[misc]
        self.timeout_seconds = float(server.get("timeout_seconds", 30)) + 5  # type: ignore[misc]
        self.max_output_chars = int(server.get("max_output_chars", 20_000))  # type: ignore[misc]
        self.version = str(tool.get("version", "1.0.0"))  # type: ignore[misc]
        self.client_factory = client_factory

    @property
    def definition(self) -> ToolDefinition:
        base = super().definition
        return base.model_copy(update={"input_schema": self.input_schema, "source": f"mcp:{self.server['name']}"})

    def network_destination(self, arguments: dict[str, Any], settings: Any) -> str | None:
        return (urlsplit(self.server.get("url") or "").hostname or "") if self.network else None

    def run(self, args: Any, ctx: ToolContext) -> dict[str, Any]:
        arguments = args.model_dump()
        try:
            jsonschema.validate(arguments, self.input_schema)
        except jsonschema.ValidationError as exc:
            raise ToolError(
                f"Arguments do not match the MCP tool schema: {exc.message[:300]}",
                details={"code": "invalid_arguments"},
            ) from exc
        client: MCPClient = self.client_factory(self.server)
        result = client.call_tool(self.remote_name, arguments)
        texts = [
            str(item.get("text", ""))
            for item in result.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(texts)
        truncated = len(text) > self.max_output_chars
        if result.get("isError"):
            raise ToolError(f"MCP tool reported an error: {text[:500]}", details={"code": "mcp_tool_error"})
        structured = result.get("structuredContent")
        if structured is not None and len(json.dumps(structured, default=str)) > self.max_output_chars:
            structured, truncated = None, True
        return {
            "content": text[: self.max_output_chars],
            "structured": structured,
            "truncated": truncated,
            "server": self.server["name"],
        }

"""MCP server registry: register, trust, discover, classify, health-check, and expose tools to the harness."""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.config import Settings
from ..core.errors import ConflictError, NotFoundError, ToolError, ValidationFailedError
from ..core.ids import utcnow
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..governance.secrets import SecretStore
from ..spec.models import RiskLevel
from ..storage.db import Database
from ..storage.models import MCPServer
from ..tools.base import SideEffect
from ..tools.registry import ToolRegistry
from .mcp import HttpMCPClient, MCPClient, MCPTool, StdioMCPClient, schema_problems, tool_id

SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


class MCPServerCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_\-]{1,59}$")
    transport: str = Field(pattern=r"^(stdio|http)$")
    command: list[str] = Field(default_factory=list, max_length=40, description="stdio: argv, e.g. [npx, -y, pkg]")
    url: str | None = Field(None, max_length=500, description="http: the Streamable HTTP endpoint")
    auth_secret: str | None = Field(None, max_length=120, description="Secret holding a bearer token (http)")
    env_secrets: dict[str, str] = Field(default_factory=dict, description="stdio: ENV_VAR -> secret name")
    trusted: bool = False
    timeout_seconds: float = Field(30.0, gt=0, le=600)
    max_output_chars: int = Field(20_000, ge=100, le=1_000_000)


class MCPServerUpdate(BaseModel):
    trusted: bool | None = None
    enabled: bool | None = None
    tool_allowlist: list[str] | None = None
    tool_settings: dict[str, dict[str, str]] | None = Field(
        None, description="Per tool: {risk: low|medium|high|critical, side_effects: read|write|external}"
    )


def _view(s: MCPServer) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "transport": s.transport,
        "command": s.command,
        "url": s.url,
        "auth_secret": s.auth_secret,
        "env_secrets": sorted(s.env_secrets),  # secret names only, never values
        "trusted": s.trusted,
        "enabled": s.enabled,
        "timeout_seconds": s.timeout_seconds,
        "max_output_chars": s.max_output_chars,
        "tool_allowlist": s.tool_allowlist,
        "tool_settings": s.tool_settings,
        "tools": [
            {
                **t,
                "platform_name": tool_id(s.name, t["name"]),
                "exposed": s.trusted and s.enabled and t["name"] in s.tool_allowlist and t.get("schema_valid", False),
                **{"risk": "high", "side_effects": "write"},
                **s.tool_settings.get(t["name"], {}),
            }
            for t in s.discovered
        ],
        "health": s.health,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


class MCPService:
    def __init__(
        self,
        db: Database,
        audit: AuditLog,
        settings: Settings,
        tools: ToolRegistry,
        secrets: SecretStore,
        client_factory: Callable[[dict[str, Any]], MCPClient] | None = None,
    ) -> None:
        self.db = db
        self.audit = audit
        self.settings = settings
        self.tools = tools
        self.secrets = secrets
        self.client_factory = client_factory or self._client

    # ------------------------------------------------------------------ clients (credentials resolved here only)
    def _client(self, server: dict[str, Any]) -> MCPClient:
        # Hard transport cap against runaway responses; normal truncation happens at max_output_chars.
        max_bytes = max(int(server["max_output_chars"]) * 4, 2_000_000) + 65_536
        if server["transport"] == "stdio":
            env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
            for var, secret in (server.get("env_secrets") or {}).items():
                env[var] = self.secrets.get(secret)
            return StdioMCPClient(list(server["command"]), env, float(server["timeout_seconds"]), max_bytes)
        headers = {}
        if server.get("auth_secret"):
            headers["Authorization"] = f"Bearer {self.secrets.get(server['auth_secret'])}"
        return HttpMCPClient(str(server["url"]), headers, float(server["timeout_seconds"]), max_bytes)

    def _server_dict(self, s: MCPServer) -> dict[str, Any]:
        return {
            "name": s.name,
            "transport": s.transport,
            "command": s.command,
            "url": s.url,
            "auth_secret": s.auth_secret,
            "env_secrets": s.env_secrets,
            "timeout_seconds": s.timeout_seconds,
            "max_output_chars": s.max_output_chars,
        }

    # ------------------------------------------------------------------ validation
    def _check_target(self, data: MCPServerCreate) -> None:
        if data.transport == "stdio":
            if not data.command:
                raise ValidationFailedError("stdio servers need a command")
            allowed = {str(Path(c).expanduser()) for c in self.settings.mcp_allowed_commands}
            executable = data.command[0]
            resolved = shutil.which(executable) or executable
            if executable not in allowed and resolved not in allowed:
                raise ValidationFailedError(
                    f"'{executable}' is not in AF_MCP_ALLOWED_COMMANDS; stdio MCP servers run local programs, so "
                    "an administrator must allow the executable first"
                )
            if any(re.search(r"[;&|`$<>]", part) for part in data.command):
                raise ValidationFailedError("Shell metacharacters are not allowed in MCP commands (no shell is used)")
        else:
            parts = urlsplit(data.url or "")
            local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
            if parts.scheme not in {"https", "http"} or not parts.hostname or (parts.scheme == "http" and not local):
                raise ValidationFailedError("http MCP servers need an https:// URL (plain http only for localhost)")
        for secret in [*data.env_secrets.values(), *([data.auth_secret] if data.auth_secret else [])]:
            if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,120}", secret):
                raise ValidationFailedError(f"Invalid secret name '{secret}'")

    # ------------------------------------------------------------------ CRUD
    def register(self, principal: Principal, data: MCPServerCreate) -> dict[str, Any]:
        principal.require("settings:write")
        self._check_target(data)
        with self.db.transaction() as session:
            if session.scalars(select(MCPServer).where(MCPServer.name == data.name)).first():
                raise ConflictError(f"MCP server '{data.name}' already exists")
            server = MCPServer(**data.model_dump(), created_by=principal.user_id)
            session.add(server)
            session.flush()
            view = _view(server)
        self.audit.record(
            "mcp.register",
            "mcp_server",
            view["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"name": data.name, "transport": data.transport, "trusted": data.trusted},
        )
        return view

    def list_servers(self, principal: Principal) -> list[dict[str, Any]]:
        principal.require("tools:read")
        with self.db.session() as session:
            return [_view(s) for s in session.scalars(select(MCPServer).order_by(MCPServer.name))]

    def get(self, principal: Principal, server_id: str) -> dict[str, Any]:
        principal.require("tools:read")
        with self.db.session() as session:
            server = session.get(MCPServer, server_id)
            if server is None:
                raise NotFoundError("MCP server not found")
            return _view(server)

    def delete(self, principal: Principal, server_id: str) -> None:
        principal.require("settings:write")
        with self.db.transaction() as session:
            server = session.get(MCPServer, server_id)
            if server is None:
                raise NotFoundError("MCP server not found")
            name = server.name
            session.delete(server)
        self.sync_registry()
        self.audit.record(
            "mcp.delete",
            "mcp_server",
            server_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"name": name},
        )

    def configure(self, principal: Principal, server_id: str, update: MCPServerUpdate) -> dict[str, Any]:
        """Trust, enable, allow-list and classify tools. Only discovered tools can be allowed."""
        principal.require("settings:write")
        with self.db.transaction() as session:
            server = session.get(MCPServer, server_id)
            if server is None:
                raise NotFoundError("MCP server not found")
            known = {t["name"] for t in server.discovered}
            if update.tool_allowlist is not None:
                unknown = set(update.tool_allowlist) - known
                if unknown:
                    raise ValidationFailedError(f"Not discovered on this server: {', '.join(sorted(unknown))}")
                server.tool_allowlist = sorted(set(update.tool_allowlist))
            if update.tool_settings is not None:
                for name, values in update.tool_settings.items():
                    if name not in known:
                        raise ValidationFailedError(f"'{name}' was not discovered on this server")
                    RiskLevel(values.get("risk", "high"))
                    SideEffect(values.get("side_effects", "write"))
                    if set(values) - {"risk", "side_effects"}:
                        raise ValidationFailedError("tool_settings accept only risk and side_effects")
                server.tool_settings = {**server.tool_settings, **update.tool_settings}
            if update.trusted is not None:
                server.trusted = update.trusted
            if update.enabled is not None:
                server.enabled = update.enabled
            view = _view(server)
        self.sync_registry()
        self.audit.record(
            "mcp.configure",
            "mcp_server",
            server_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details=update.model_dump(exclude_none=True),
        )
        return view

    # ------------------------------------------------------------------ discovery and health
    def discover(self, principal: Principal, server_id: str) -> dict[str, Any]:
        principal.require("settings:write")
        with self.db.session() as session:
            server = session.get(MCPServer, server_id)
            if server is None:
                raise NotFoundError("MCP server not found")
            target = self._server_dict(server)
        started = time.perf_counter()
        try:
            remote = self.client_factory(target).list_tools()
            health = {"status": "ok", "tools": len(remote)}
        except ToolError as exc:
            remote, health = None, {"status": "down", "detail": exc.message[:300]}
        health |= {"checked_at": utcnow().isoformat(), "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
        with self.db.transaction() as session:
            server = session.get(MCPServer, server_id)
            assert server is not None
            server.health = health
            if remote is not None:
                discovered = []
                for tool in remote[:200]:
                    name = str(tool.get("name", ""))[:100]
                    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,100}", name):
                        continue
                    problems = schema_problems(tool.get("inputSchema"))
                    discovered.append(
                        {
                            "name": name,
                            "description": str(tool.get("description") or "")[:1000],
                            "input_schema": tool.get("inputSchema") if not problems else {"type": "object"},
                            "annotations": tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {},
                            "schema_valid": not problems,
                            "problems": problems,
                        }
                    )
                server.discovered = discovered
                server.tool_allowlist = [t for t in server.tool_allowlist if t in {d["name"] for d in discovered}]
            view = _view(server)
        self.sync_registry()
        self.audit.record(
            "mcp.discover",
            "mcp_server",
            server_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            outcome="success" if remote is not None else "failure",
            details={"tools": len(remote or []), "status": health["status"]},
        )
        return view

    def health(self, principal: Principal, server_id: str) -> dict[str, Any]:
        """Connect, initialize and list tools; does not change the discovered tool set."""
        principal.require("tools:read")
        with self.db.session() as session:
            server = session.get(MCPServer, server_id)
            if server is None:
                raise NotFoundError("MCP server not found")
            target = self._server_dict(server)
        started = time.perf_counter()
        try:
            count = len(self.client_factory(target).list_tools())
            health: dict[str, Any] = {"status": "ok", "tools": count}
        except ToolError as exc:
            health = {"status": "down", "detail": exc.message[:300]}
        health |= {"checked_at": utcnow().isoformat(), "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
        with self.db.transaction() as session:
            record = session.get(MCPServer, server_id)
            assert record is not None
            record.health = health
        return health

    # ------------------------------------------------------------------ registry
    def sync_registry(self) -> list[str]:
        """Expose exactly the allow-listed, schema-valid tools of trusted, enabled servers."""
        for name in self.tools.names():
            if name.startswith("mcp__"):
                self.tools.unregister(name)
        exposed = []
        with self.db.session() as session:
            servers = list(
                session.scalars(select(MCPServer).where(MCPServer.trusted.is_(True), MCPServer.enabled.is_(True)))
            )
            for server in servers:
                target = self._server_dict(server)
                for tool in server.discovered:
                    if tool["name"] not in server.tool_allowlist or not tool.get("schema_valid"):
                        continue
                    instance = MCPTool(
                        server=target,
                        tool=tool,
                        settings=server.tool_settings.get(tool["name"], {}),
                        client_factory=self.client_factory,
                    )
                    self.tools.register(instance)
                    exposed.append(instance.name)
        return exposed

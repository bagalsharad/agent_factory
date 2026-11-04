"""MCP connectors: register servers, trust them explicitly, discover and classify tools, check health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...connectors.service import MCPServerCreate, MCPServerUpdate
from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1/connectors/mcp", tags=["connectors"])


@router.get("", summary="Registered MCP servers with discovered tools (secret names only)")
def list_servers(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return {"items": platform.connectors.list_servers(principal)}


@router.post("", status_code=201, summary="Register an MCP server (untrusted until an administrator trusts it)")
def register(payload: MCPServerCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.connectors.register(principal, payload)


@router.get("/{server_id}")
def get_server(server_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.connectors.get(principal, server_id)


@router.put("/{server_id}", summary="Trust/enable the server, allow-list tools and set their risk classification")
def configure(
    server_id: str, payload: MCPServerUpdate, platform: PlatformDep, principal: PrincipalDep
) -> dict[str, Any]:
    return platform.connectors.configure(principal, server_id, payload)


@router.post("/{server_id}/discover", summary="List the server's tools and validate their schemas")
def discover(server_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.connectors.discover(principal, server_id)


@router.get("/{server_id}/health", summary="Connect, initialize and list tools")
def health(server_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.connectors.health(principal, server_id)


@router.delete("/{server_id}", status_code=204)
def delete(server_id: str, platform: PlatformDep, principal: PrincipalDep) -> None:
    platform.connectors.delete(principal, server_id)

"""REST connector with endpoint allowlisting and SSRF protection.

An agent-supplied URL never becomes a trusted destination by itself. A request is sent only when:

1. the agent policy enables external network access (checked by the policy engine and again here),
2. the host is in BOTH the platform allowlist (AF_NETWORK_ALLOWLIST) and the agent's
   ``policies.allowed_network_hosts``,
3. every resolved IP address is public (unless AF_ALLOW_PRIVATE_NETWORK=true),
4. the scheme is https (http only for explicitly allowlisted private hosts).

Redirects are not followed. Response size and content type are bounded.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from ...core.errors import ToolError
from ...spec.models import RiskLevel
from ..base import SideEffect, Tool, ToolContext

MAX_RESPONSE_BYTES = 256_000
ALLOWED_CONTENT_TYPES = ("application/json", "text/", "application/xml")


def host_matches(host: str, allowlist: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in allowlist:
        entry = entry.lower().strip()
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and host != entry[2:]:
                return True
        elif host == entry:
            return True
    return False


def check_destination(url: str, allowed_hosts: list[str], *, allow_private: bool) -> tuple[str, str]:
    """Validate a URL against the allowlist and SSRF rules. Returns (host, url). Raises ToolError."""
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"}:
        raise ToolError("Only http(s) URLs are permitted", details={"code": "scheme_not_allowed"})
    if parts.username or parts.password:
        raise ToolError("Credentials in URLs are not permitted", details={"code": "credentials_in_url"})
    host = (parts.hostname or "").lower()
    if not host:
        raise ToolError("URL has no host", details={"code": "invalid_url"})
    if not host_matches(host, allowed_hosts):
        raise ToolError(
            f"Destination '{host}' is not in the approved endpoint allowlist",
            details={"code": "destination_not_allowed"},
        )
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ToolError(f"Could not resolve '{host}'", retryable=True) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not allow_private and (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ToolError(f"Destination resolves to a non-public address ({ip})", details={"code": "private_address"})
    if parts.scheme == "http" and not allow_private:
        raise ToolError(
            "Plain http is only permitted for private hosts when explicitly enabled",
            details={"code": "insecure_scheme"},
        )
    return host, url


class HttpGetInput(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class HttpGet(Tool):
    name = "http_get"
    description = "HTTP GET an approved REST endpoint and return its (size-limited) text or JSON body."
    connector = "rest"
    Input = HttpGetInput
    risk = RiskLevel.MEDIUM
    side_effects = SideEffect.READ  # GET only; egress is governed by the endpoint allowlist (`network`)
    network = True
    timeout_seconds = 15.0
    audit = Tool.audit.model_copy(update={"log_output": False})

    def run(self, args: HttpGetInput, ctx: ToolContext) -> dict[str, Any]:
        _host, url = check_destination(args.url, ctx.allowed_hosts, allow_private=ctx.settings.allow_private_network)
        try:
            with (
                httpx.Client(follow_redirects=False, timeout=self.timeout_seconds) as client,
                client.stream("GET", url, headers={"User-Agent": "agent-factory/0.1"}) as response,
            ):
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith(ALLOWED_CONTENT_TYPES):
                    raise ToolError(f"Unsupported content type '{content_type}'")
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        body = body[:MAX_RESPONSE_BYTES]
                        break
                return {
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "body": body.decode("utf-8", errors="replace"),
                    "truncated": len(body) >= MAX_RESPONSE_BYTES,
                }
        except httpx.HTTPError as exc:
            raise ToolError(f"Request failed: {exc.__class__.__name__}", retryable=True) from exc

"""ASGI middleware: correlation IDs + access logs + tracing, request size limits, security headers."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..core.logging import correlation_id_var, get_logger
from ..observability.telemetry import span

log = get_logger("agent_factory.http")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (
        b"content-security-policy",
        b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        b"script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    ),
]
DOCS_CSP = (
    b"default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; script-src 'self' 'unsafe-inline' "
    b"https://cdn.jsdelivr.net; frame-ancestors 'none'"
)


class RequestContextMiddleware:
    """Assigns/propagates X-Request-ID, logs one structured line per request and opens a trace span."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode(errors="ignore")
        request_id = incoming if REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = correlation_id_var.set(request_id)
        started = time.perf_counter()
        status_holder: dict[str, Any] = {"status": 500}
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-request-id", request_id.encode()))
                for name, value in SECURITY_HEADERS:
                    if name == b"content-security-policy" and path in {"/docs", "/redoc"}:
                        value = DOCS_CSP
                    hdrs.append((name, value))
                message["headers"] = hdrs
            await send(message)

        try:
            with span("http.request", method=scope.get("method", ""), path=path, request_id=request_id):
                await self.app(scope, receive, send_wrapper)
        finally:
            duration = round((time.perf_counter() - started) * 1000, 2)
            if not path.startswith(("/assets", "/health")):
                log.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": status_holder["status"],
                        "duration_ms": duration,
                    },
                )
            correlation_id_var.reset(token)


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than the configured limit (Content-Length and streamed bodies)."""

    def __init__(self, app: ASGIApp, max_bytes: int, upload_max_bytes: int = 11_000_000) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.upload_max_bytes if scope.get("path", "").endswith("/upload") else self.max_bytes
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise PayloadTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except PayloadTooLargeError:
            await _reject(send, limit)


class PayloadTooLargeError(Exception):
    pass


async def _reject(send: Send, limit: int) -> None:
    import json

    body = json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": f"Request body exceeds {limit} bytes",
                "details": None,
                "request_id": correlation_id_var.get(),
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})

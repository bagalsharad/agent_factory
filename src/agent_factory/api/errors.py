"""Consistent error responses: {"error": {"code", "message", "details", "request_id"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.errors import AgentFactoryError
from ..core.logging import correlation_id_var, get_logger
from ..core.redaction import redact, redact_text

log = get_logger(__name__)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": redact_text(message),
            "details": redact(details),
            "request_id": correlation_id_var.get(),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentFactoryError)
    async def _app_error(_request: Request, exc: AgentFactoryError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("request failed", extra={"code": exc.code, "error": exc.message})
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"], "code": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(error_body("request_invalid", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            401: "unauthenticated",
            403: "permission_denied",
        }.get(exc.status_code, "http_error")
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(error_body("internal_error", "An internal error occurred"), status_code=500)

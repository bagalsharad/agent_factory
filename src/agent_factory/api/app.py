"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..core.config import PROJECT_ROOT, Settings, get_settings
from ..core.logging import configure_logging, get_logger
from ..observability.telemetry import configure_telemetry
from ..platform import Platform, create_platform
from .errors import install_error_handlers
from .middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from .routers import agents, approvals, catalog, evaluations, executions, knowledge, platform

log = get_logger(__name__)
WEB_DIST = PROJECT_ROOT / "web" / "dist"


class StartupError(RuntimeError):
    pass


def create_app(
    settings: Settings | None = None, *, platform_instance: Platform | None = None, start_workers: bool | None = None
) -> FastAPI:
    settings = settings or get_settings()
    problems = settings.startup_problems()
    if problems:
        raise StartupError("Refusing to start:\n- " + "\n- ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, settings.log_json)
        configure_telemetry(settings)
        instance = platform_instance or create_platform(settings)
        app.state.platform = instance
        instance.start(workers=start_workers)
        log.info(
            "agent factory started",
            extra={
                "profile": settings.profile.value,
                "auth": settings.auth_mode.value,
                "database": instance.db.dialect,
                "host": settings.host,
                "port": settings.port,
            },
        )
        try:
            yield
        finally:
            instance.stop()

    app = FastAPI(
        title="Agent Factory API",
        version=__version__,
        description="Local-first AI agent engineering platform: design, build, test, govern and deploy agents.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Workspace-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    for module in (platform, agents, executions, approvals, evaluations, catalog, knowledge):
        app.include_router(module.router)
    _mount_web(app, WEB_DIST)
    return app


def _mount_web(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.exists():

        @app.get("/", include_in_schema=False)
        def no_ui() -> JSONResponse:
            return JSONResponse(
                {
                    "message": "Agent Factory API is running. The web dashboard is not built: run "
                    "`make web` (or `cd web && npm install && npm run build`). API docs: /docs"
                }
            )

        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "docs", "redoc", "openapi.json", "health", "ready")):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

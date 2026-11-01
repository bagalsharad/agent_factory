"""ASGI entry point: ``uvicorn agent_factory.api.asgi:app``."""

from .app import create_app

app = create_app()

"""Structured execution events (persisted, redacted, optionally content-free)."""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import func, select

from ..core.logging import get_logger
from ..core.redaction import redact
from ..storage.db import Database
from ..storage.models import ExecutionEvent

log = get_logger("agent_factory.events")

CONTENT_KEYS = {"content", "prompt", "messages", "output", "arguments", "result", "input", "text", "preview"}
MAX_FIELD_CHARS = 4000


def _shrink(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"… [{len(value) - MAX_FIELD_CHARS} chars truncated]"
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(v) for v in value[:100]]
    return value


class EventRecorder:
    """Appends ordered events for one execution. Thread-safe within a process."""

    _locks: dict[str, threading.Lock] = {}
    _guard = threading.Lock()

    def __init__(self, db: Database, execution_id: str, *, capture_content: bool) -> None:
        self.db = db
        self.execution_id = execution_id
        self.capture_content = capture_content
        with self._guard:
            self._lock = self._locks.setdefault(execution_id, threading.Lock())

    def emit(
        self,
        event_type: str,
        *,
        node_id: str | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        **payload: Any,
    ) -> None:
        data = redact(payload)
        if not self.capture_content:
            data = {k: ("[content capture disabled]" if k in CONTENT_KEYS else v) for k, v in data.items()}
        data = _shrink(data)
        with self._lock, self.db.transaction() as session:
            seq = (
                session.scalar(
                    select(func.max(ExecutionEvent.seq)).where(ExecutionEvent.execution_id == self.execution_id)
                )
                or 0
            ) + 1
            session.add(
                ExecutionEvent(
                    execution_id=self.execution_id,
                    seq=seq,
                    type=event_type,
                    node_id=node_id,
                    status=status,
                    payload=data,
                    duration_ms=duration_ms,
                )
            )
        log.debug("event", extra={"event_type": event_type, "node": node_id, "execution_id": self.execution_id})

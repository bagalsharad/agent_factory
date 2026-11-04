"""Append-only, hash-chained audit log.

Each event stores ``prev_hash`` and ``hash = sha256(prev_hash || canonical(event))``. ``verify()``
recomputes the chain; any modified, deleted or reordered row breaks it. This is tamper-*evident*, not
tamper-proof: an attacker with database write access could rewrite the whole chain. Production deployments
should also set ``AF_AUDIT_SINK_PATH`` so every event is appended to storage outside the database
(``FileAuditSink``); ``verify_records`` checks such a copy or an export on its own.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..core.ids import canonical_json, utcnow
from ..core.logging import correlation_id_var, get_logger
from ..core.redaction import redact
from ..storage.db import Database
from ..storage.models import AuditEvent

log = get_logger("agent_factory.audit")
GENESIS = "0" * 64


def _digest(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(payload)).encode()).hexdigest()


def _payload(e: AuditEvent) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "workspace_id": e.workspace_id,
        "actor_id": e.actor_id,
        "action": e.action,
        "resource_type": e.resource_type,
        "resource_id": e.resource_id,
        "outcome": e.outcome,
        "details": e.details,
        "correlation_id": e.correlation_id,
        "created_at": e.created_at.isoformat(),
    }


class FileAuditSink:
    """Append-only JSONL copy of the audit chain, for storage the database administrator cannot rewrite
    (WORM bucket mount, append-only filesystem, log shipper). Each line carries its hash and the previous hash."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.failures = 0

    def write(self, record: dict[str, Any]) -> None:
        line = canonical_json(record) + "\n"
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self.failures += 1
            log.error("audit sink write failed", extra={"path": str(self.path)})


def export_record(e: AuditEvent) -> dict[str, Any]:
    return {**_payload(e), "prev_hash": e.prev_hash, "hash": e.hash}


def verify_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Verify an exported (or sink) chain on its own, without the database."""
    prev: str | None = None
    count = 0
    for record in records:
        count += 1
        payload = {
            k: record.get(k)
            for k in (
                "seq",
                "workspace_id",
                "actor_id",
                "action",
                "resource_type",
                "resource_id",
                "outcome",
                "details",
                "correlation_id",
                "created_at",
            )
        }
        if prev is not None and record.get("prev_hash") != prev:
            return {"valid": False, "broken_at_seq": record.get("seq"), "reason": "chain gap", "events_checked": count}
        if _digest(str(record.get("prev_hash")), payload) != record.get("hash"):
            return {
                "valid": False,
                "broken_at_seq": record.get("seq"),
                "reason": "hash mismatch",
                "events_checked": count,
            }
        prev = record["hash"]
    return {"valid": True, "events_checked": count, "last_hash": prev}


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        *,
        outcome: str = "success",
        actor_id: str | None = None,
        workspace_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write in its own transaction so denials are recorded even if the business operation rolls back."""
        for _attempt in range(5):
            try:
                with self.db.transaction() as session:
                    last = session.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).first()
                    event = AuditEvent(
                        seq=(last.seq + 1) if last else 1,
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        outcome=outcome,
                        details=redact(details or {}),
                        correlation_id=correlation_id_var.get(),
                        prev_hash=last.hash if last else GENESIS,
                        hash="",
                        created_at=utcnow(),
                    )
                    event.hash = _digest(event.prev_hash, _payload(event))
                    session.add(event)
                    session.flush()
                    record = export_record(event)
                sink = getattr(self.db, "audit_sink", None)
                if sink is not None:
                    sink.write(record)
                log.info("audit", extra={"audit_action": action, "resource": resource_type, "outcome": outcome})
                return
            except IntegrityError:
                continue  # concurrent writer took this seq; retry with the next one
        log.error("failed to write audit event after retries", extra={"audit_action": action})

    def verify(self) -> dict[str, Any]:
        with self.db.session() as session:
            prev = GENESIS
            count = 0
            for event in session.scalars(select(AuditEvent).order_by(AuditEvent.seq)):
                count += 1
                if event.prev_hash != prev or _digest(prev, _payload(event)) != event.hash:
                    return {"valid": False, "broken_at_seq": event.seq, "events_checked": count}
                prev = event.hash
            return {"valid": True, "events_checked": count}

    def export(
        self, *, since: datetime | None = None, until: datetime | None = None, after_seq: int = 0
    ) -> Iterator[dict[str, Any]]:
        """All events in chain order (the chain is global: exports are for administrators)."""
        with self.db.session() as session:
            query = select(AuditEvent).where(AuditEvent.seq > after_seq).order_by(AuditEvent.seq)
            if since is not None:
                query = query.where(AuditEvent.created_at >= since)
            if until is not None:
                query = query.where(AuditEvent.created_at < until)
            for event in session.scalars(query):
                yield export_record(event)

    def count(self) -> int:
        with self.db.session() as session:
            return session.scalar(select(func.count()).select_from(AuditEvent)) or 0

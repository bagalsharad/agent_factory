"""Append-only, hash-chained audit log.

Each event stores ``prev_hash`` and ``hash = sha256(prev_hash || canonical(event))``. ``verify()``
recomputes the chain; any modified, deleted or reordered row breaks it. This is tamper-*evident*, not
tamper-proof: an attacker with database write access could rewrite the whole chain. Production deployments
should additionally ship audit events to an external append-only sink (see docs/security/threat-model.md).
"""

from __future__ import annotations

import hashlib
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

    def count(self) -> int:
        with self.db.session() as session:
            return session.scalar(select(func.count()).select_from(AuditEvent)) or 0

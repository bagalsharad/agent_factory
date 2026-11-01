"""Human approval manager.

An approval is created only by the harness when the policy engine requires it. It can only be decided
through this service by an authenticated principal holding ``approvals:decide`` whose role is listed in
the agent's ``human_approval.approver_roles``. Self-approval is refused when separation of duties is
enabled (AF_ALLOW_SELF_APPROVAL=false, mandatory in production). The model has no path to this service.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from ..core.config import Settings
from ..core.errors import InvalidStateError, NotFoundError, PermissionDeniedError
from ..core.ids import utcnow
from ..storage.db import Database
from ..storage.models import AgentVersion, Approval, Execution
from .audit import AuditLog
from .rbac import Principal

APPROVAL_STATUSES = ("pending", "approved", "rejected", "expired", "cancelled")


def approval_dict(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id,
        "workspace_id": a.workspace_id,
        "execution_id": a.execution_id,
        "tool_invocation_id": a.tool_invocation_id,
        "step_id": a.step_id,
        "action": a.action,
        "tool_name": a.tool_name,
        "arguments": a.arguments,
        "rationale": a.rationale,
        "evidence": a.evidence,
        "risk": a.risk,
        "status": a.status,
        "requested_by": a.requested_by,
        "decided_by": a.decided_by,
        "decision_comment": a.decision_comment,
        "requested_at": a.requested_at.isoformat(),
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }


class ApprovalService:
    def __init__(self, settings: Settings, db: Database, audit: AuditLog) -> None:
        self.settings = settings
        self.db = db
        self.audit = audit
        self.on_decided: Callable[[str, str], None] | None = None  # (execution_id, approval_id) → resume

    def create(
        self,
        *,
        workspace_id: str,
        execution_id: str,
        step_id: str,
        action: str,
        tool_name: str | None,
        arguments: dict[str, Any],
        rationale: str,
        evidence: dict[str, Any],
        risk: str,
        requested_by: str | None,
        expires_after_seconds: int,
        tool_invocation_id: str | None,
    ) -> Approval:
        with self.db.transaction() as session:
            approval = Approval(
                workspace_id=workspace_id,
                execution_id=execution_id,
                step_id=step_id,
                action=action,
                tool_name=tool_name,
                arguments=arguments,
                rationale=rationale,
                evidence=evidence,
                risk=risk,
                requested_by=requested_by,
                tool_invocation_id=tool_invocation_id,
                expires_at=utcnow() + timedelta(seconds=expires_after_seconds),
            )
            session.add(approval)
            session.flush()
            session.expunge(approval)
        self.audit.record(
            "approval.requested",
            "approval",
            approval.id,
            actor_id=requested_by,
            workspace_id=workspace_id,
            details={"action": action, "tool": tool_name, "risk": risk, "execution_id": execution_id},
        )
        return approval

    def get_raw(self, approval_id: str) -> Approval | None:
        with self.db.session() as session:
            approval = session.get(Approval, approval_id)
            if approval:
                session.expunge(approval)
            return approval

    def get(self, principal: Principal, approval_id: str) -> dict[str, Any]:
        principal.require("approvals:read")
        approval = self.get_raw(approval_id)
        if not approval or approval.workspace_id != principal.workspace_id:
            raise NotFoundError("Approval not found")
        return approval_dict(approval)

    def list(
        self,
        principal: Principal,
        *,
        status: str | None = None,
        execution_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        principal.require("approvals:read")
        self.expire_due()
        with self.db.session() as session:
            query = select(Approval).where(Approval.workspace_id == principal.workspace_id)
            if status:
                query = query.where(Approval.status == status)
            if execution_id:
                query = query.where(Approval.execution_id == execution_id)
            rows = session.scalars(query.order_by(Approval.requested_at.desc())).all()
            items = [approval_dict(a) for a in rows]
        return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset}

    def decide(self, principal: Principal, approval_id: str, decision: str, comment: str = "") -> dict[str, Any]:
        principal.require("approvals:decide")
        if decision not in {"approved", "rejected"}:
            raise InvalidStateError("Decision must be 'approved' or 'rejected'")
        expired = False
        with self.db.transaction() as session:
            approval = session.get(Approval, approval_id)
            if not approval or approval.workspace_id != principal.workspace_id:
                raise NotFoundError("Approval not found")
            if approval.status != "pending":
                raise InvalidStateError(f"Approval is already '{approval.status}'")
            if approval.expires_at and approval.expires_at < utcnow():
                approval.status = "expired"
                expired = True
            else:
                execution = session.get(Execution, approval.execution_id)
                version = session.get(AgentVersion, execution.agent_version_id) if execution else None
                approver_roles = (
                    version.spec.get("spec", {}).get("human_approval", {}).get("approver_roles", ["admin", "operator"])
                    if version
                    else ["admin"]
                )
                if principal.role != "admin" and principal.role not in approver_roles:
                    self.audit.record(
                        "approval.decide",
                        "approval",
                        approval_id,
                        outcome="denied",
                        actor_id=principal.user_id,
                        workspace_id=principal.workspace_id,
                        details={"reason": "role not an approver", "role": principal.role},
                    )
                    raise PermissionDeniedError(f"Role '{principal.role}' is not an approver for this agent")
                if approval.requested_by == principal.user_id and not self.settings.allow_self_approval:
                    self.audit.record(
                        "approval.decide",
                        "approval",
                        approval_id,
                        outcome="denied",
                        actor_id=principal.user_id,
                        workspace_id=principal.workspace_id,
                        details={"reason": "self-approval forbidden"},
                    )
                    raise PermissionDeniedError("Separation of duties: you cannot approve your own request")
                approval.status = decision
                real_user = principal.auth_method not in {"system", "evaluation"}
                approval.decided_by = principal.user_id if real_user else None
                approval.decided_at = utcnow()
                approval.decision_comment = comment[:2000]
            session.flush()
            result = approval_dict(approval)
            execution_id = approval.execution_id
        if expired:
            self._notify(execution_id, approval_id)
            raise InvalidStateError("Approval has expired")
        self.audit.record(
            f"approval.{decision}",
            "approval",
            approval_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"execution_id": execution_id},
        )
        self._notify(execution_id, approval_id)
        return result

    def _notify(self, execution_id: str, approval_id: str) -> None:
        if self.on_decided:
            self.on_decided(execution_id, approval_id)

    def expire_due(self) -> int:
        now = utcnow()
        expired: list[tuple[str, str]] = []
        with self.db.transaction() as session:
            for approval in session.scalars(
                select(Approval).where(Approval.status == "pending", Approval.expires_at < now)
            ):
                approval.status = "expired"
                expired.append((approval.execution_id, approval.id))
        for execution_id, approval_id in expired:
            self.audit.record("approval.expired", "approval", approval_id, details={"execution_id": execution_id})
            self._notify(execution_id, approval_id)
        return len(expired)

    def cancel_for_execution(self, execution_id: str) -> int:
        with self.db.transaction() as session:
            rows = session.scalars(
                select(Approval).where(Approval.execution_id == execution_id, Approval.status == "pending")
            ).all()
            for approval in rows:
                approval.status = "cancelled"
                approval.decided_at = utcnow()
            return len(rows)

"""Intent Ledger: the authoritative, versioned objective of an execution, I₀ = (G, C, A, P).

* G goals: the agent's objective and instructions, and the task input it was asked to do.
* C constraints: execution limits, data classification, write and network policy.
* A acceptance: the output contract (schema) and the agent's acceptance criteria.
* P policies / authorization: who asked, under which role, which tools may run, which need approval.

Agents cannot change it. An amendment is a new version, made only by an authorized person with a reason; it is
audited, and the Change Impact Analyzer treats it as a change for every step that depended on what changed.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from sqlalchemy import func, select

from ..core.errors import InvalidStateError, NotFoundError, ValidationFailedError
from ..core.ids import content_hash
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..spec.models import AgentSpec, ExecutionLimits
from ..storage.db import Database
from ..storage.models import Execution, ExecutionIntent

AMENDABLE_STATUSES = {"failed", "dead_lettered", "needs_review", "waiting_approval", "queued"}


def build_intent(
    spec: AgentSpec, task_input: dict[str, Any], *, requested_by: str | None, role: str | None, limits: dict[str, Any]
) -> dict[str, Any]:
    body = spec.spec
    return {
        "goals": {
            "agent": spec.metadata.name,
            "objective": body.objective,
            "instructions_hash": content_hash(body.instructions),
            "input": task_input,
        },
        "constraints": {
            "execution_limits": limits,
            "data_classification": body.policies.data_classification,
            "allow_write_operations": body.policies.allow_write_operations,
            "external_network": body.policies.external_network,
            "allowed_network_hosts": body.policies.allowed_network_hosts,
        },
        "acceptance": {
            "outputs_schema_hash": content_hash(body.outputs),
            "evaluation_acceptance": body.evaluation.acceptance.model_dump(mode="json"),
        },
        "authorization": {
            "requested_by": requested_by,
            "role": role,
            "tools": body.tool_names,
            "approval_required_for_risk": [str(r) for r in body.human_approval.required_for_risk],
            "approval_required_for_writes": body.human_approval.require_for_writes,
            "require_approval_for": body.policies.require_approval_for,
        },
        "invariants": [
            "Committed side effects are never repeated automatically.",
            "Agents cannot redefine the objective; only an authorized amendment can.",
            "Execution limits can be tightened, never loosened, by an amendment.",
        ],
    }


def _view(row: ExecutionIntent) -> dict[str, Any]:
    return {
        "version": row.version,
        "intent": row.intent,
        "intent_hash": row.intent_hash,
        "goals_hash": row.goals_hash,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
    }


class IntentLedger:
    def __init__(self, db: Database, audit: AuditLog) -> None:
        self.db = db
        self.audit = audit

    def create(self, session: Any, execution: Execution, intent: dict[str, Any]) -> ExecutionIntent:
        row = ExecutionIntent(
            execution_id=execution.id,
            workspace_id=execution.workspace_id,
            version=1,
            intent=intent,
            intent_hash=content_hash(intent),
            goals_hash=content_hash(intent["goals"]),
            reason="initial intent",
            created_by=execution.requested_by,
        )
        session.add(row)
        return row

    def head(self, session: Any, execution_id: str) -> ExecutionIntent | None:
        return session.scalars(
            select(ExecutionIntent)
            .where(ExecutionIntent.execution_id == execution_id)
            .order_by(ExecutionIntent.version.desc())
            .limit(1)
        ).first()

    def versions(self, execution_id: str) -> list[dict[str, Any]]:
        with self.db.session() as session:
            rows = session.scalars(
                select(ExecutionIntent)
                .where(ExecutionIntent.execution_id == execution_id)
                .order_by(ExecutionIntent.version)
            )
            return [_view(r) for r in rows]

    def amend(
        self,
        principal: Principal,
        execution: Execution,
        spec: AgentSpec,
        *,
        reason: str,
        task_input: dict[str, Any] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A new intent version. Input changes re-plan the work; limits may only be tightened."""
        principal.require("executions:recover")
        if len(reason.strip()) < 5:
            raise ValidationFailedError("An intent amendment needs a reason")
        if execution.status not in AMENDABLE_STATUSES:
            raise InvalidStateError(
                f"Intent can only be amended while the execution is stopped (it is '{execution.status}')"
            )
        if task_input is None and not limits:
            raise ValidationFailedError("Nothing to amend: pass a new input and/or tighter limits")
        with self.db.transaction() as session:
            current = self.head(session, execution.id)
            if current is None:
                raise NotFoundError("This execution has no intent ledger")
            intent = {k: (dict(v) if isinstance(v, dict) else v) for k, v in current.intent.items()}
            if task_input is not None:
                try:
                    jsonschema.validate(task_input, spec.spec.inputs)
                except jsonschema.ValidationError as exc:
                    raise ValidationFailedError(
                        f"Amended input does not match the agent's input schema: {exc.message}"
                    ) from exc
                intent["goals"] = {**intent["goals"], "input": task_input}
            if limits:
                current_limits = ExecutionLimits.model_validate(intent["constraints"]["execution_limits"]).model_dump()
                loosened = [
                    k
                    for k, v in limits.items()
                    if k not in current_limits or (current_limits[k] is not None and v > current_limits[k])
                ]
                if loosened:
                    raise ValidationFailedError(f"Amendments can only tighten limits; refused: {', '.join(loosened)}")
                intent["constraints"] = {**intent["constraints"], "execution_limits": {**current_limits, **limits}}
            version = (
                session.scalar(
                    select(func.max(ExecutionIntent.version)).where(ExecutionIntent.execution_id == execution.id)
                )
                or 0
            ) + 1
            row = ExecutionIntent(
                execution_id=execution.id,
                workspace_id=execution.workspace_id,
                version=version,
                intent=intent,
                intent_hash=content_hash(intent),
                goals_hash=content_hash(intent["goals"]),
                reason=reason[:2000],
                created_by=principal.user_id,
            )
            session.add(row)
            record = session.get(Execution, execution.id)
            assert record is not None
            if task_input is not None:
                record.input = task_input
            if limits:
                record.options = {
                    **(record.options or {}),
                    "limits_override": {**(record.options or {}).get("limits_override", {}), **limits},
                }
            session.flush()
            view = _view(row)
        self.audit.record(
            "intent.amend",
            "execution",
            execution.id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={
                "version": version,
                "input_changed": task_input is not None,
                "limits": sorted((limits or {}).keys()),
                "reason": reason[:300],
            },
        )
        return view

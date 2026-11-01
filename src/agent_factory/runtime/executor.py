"""Execution manager: lifecycle of agent executions.

Statuses: queued → running → (waiting_approval ⇄ queued[resume]) → completed | failed | cancelled |
budget_exceeded | rejected.

Every execution records the exact agent version, spec hash, model profile, tool versions, workflow hash and
policy hash it ran with (``config_snapshot``). Executions are dispatched through the database queue
(``runtime.dispatcher``); the API never blocks on agent work.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

import jsonschema
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from sqlalchemy import delete, func, select, update

from ..core.config import Settings
from ..core.errors import (
    AgentFactoryError,
    BudgetExceededError,
    ConflictError,
    ExecutionCancelledError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from ..core.ids import content_hash, utcnow
from ..core.logging import execution_id_var, get_logger
from ..governance.approvals import ApprovalService
from ..governance.audit import AuditLog
from ..governance.identity import IdentityService
from ..governance.policy import PolicyEngine
from ..governance.rbac import Principal
from ..harness.budget import BudgetTracker
from ..harness.events import EventRecorder
from ..harness.harness import ExecutionContext, GovernedHarness
from ..memory.conversation import ConversationMemory
from ..observability.telemetry import span
from ..registry.agents import AgentService
from ..spec.loader import load_spec
from ..spec.models import AgentSpec
from ..storage.checkpointer import SqlCheckpointSaver
from ..storage.db import Database
from ..storage.models import (
    Approval,
    Component,
    Execution,
    ExecutionEvent,
    ToolInvocation,
)
from .graph import compile_workflow

log = get_logger(__name__)

TERMINAL = {"completed", "failed", "cancelled", "budget_exceeded", "rejected"}
ACTIVE = {"queued", "running", "waiting_approval"}
OPTION_KEYS = {
    "session_id",
    "fault_injection",
    "model_override",
    "auto_approval",
    "mock_delay_seconds",
    "limits_override",
}
PRIVILEGED_OPTIONS = {"fault_injection", "model_override", "auto_approval", "mock_delay_seconds", "limits_override"}


def execution_dict(e: Execution, *, include_io: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": e.id,
        "workspace_id": e.workspace_id,
        "agent_id": e.agent_id,
        "agent_version_id": e.agent_version_id,
        "parent_execution_id": e.parent_execution_id,
        "mode": e.mode,
        "status": e.status,
        "requested_by": e.requested_by,
        "correlation_id": e.correlation_id,
        "idempotency_key": e.idempotency_key,
        "config_snapshot": e.config_snapshot,
        "budget_usage": e.budget_usage,
        "attempt": e.attempt,
        "cancel_requested": e.cancel_requested,
        "error": e.error,
        "queued_at": e.queued_at.isoformat() if e.queued_at else None,
        "started_at": e.started_at.isoformat() if e.started_at else None,
        "finished_at": e.finished_at.isoformat() if e.finished_at else None,
        "created_at": e.created_at.isoformat(),
        "updated_at": e.updated_at.isoformat(),
        "duration_ms": round((e.finished_at - e.started_at).total_seconds() * 1000, 1)
        if e.finished_at and e.started_at
        else None,
    }
    if include_io:
        out["input"] = e.input
        out["output"] = e.output
    return out


class ExecutionService:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        harness: GovernedHarness,
        agents: AgentService,
        identity: IdentityService,
        approvals: ApprovalService,
        audit: AuditLog,
        policy: PolicyEngine,
        conversation: ConversationMemory,
    ) -> None:
        self.settings = settings
        self.db = db
        self.harness = harness
        self.agents = agents
        self.identity = identity
        self.approvals = approvals
        self.audit = audit
        self.policy = policy
        self.conversation = conversation
        self.checkpointer = SqlCheckpointSaver(db)
        self.wakeup: threading.Event = threading.Event()
        harness.delegate_runner = self._run_child
        approvals.on_decided = lambda execution_id, _approval_id: self.enqueue_resume(execution_id)

    # ================================================================== creation
    def create(
        self,
        principal: Principal,
        agent_id: str,
        *,
        input_data: dict[str, Any],
        version: str | None = None,
        options: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        mode: str = "standard",
        inline: bool = False,
    ) -> dict[str, Any]:
        principal.require("executions:run")
        options = dict(options or {})
        unknown = set(options) - OPTION_KEYS
        if unknown:
            raise ValidationFailedError(f"Unknown execution options: {sorted(unknown)}")
        if mode != "evaluation" and set(options) & PRIVILEGED_OPTIONS and not principal.can("agents:write"):
            raise PermissionDeniedError("Test options (fault injection, model override) require agents:write")
        if mode != "evaluation":
            options.pop("auto_approval", None)  # simulated approvals exist only inside evaluation runs
        agent, version_record = self.agents.executable_version(principal, agent_id, version)
        spec = load_spec(version_record.spec)
        try:
            jsonschema.validate(input_data, spec.spec.inputs)
        except jsonschema.ValidationError as exc:
            raise ValidationFailedError(
                f"Input does not match the agent's input schema: {exc.message}",
                details={"path": "/".join(map(str, exc.path)), "code": "input_invalid"},
            ) from exc
        request_hash = content_hash({"agent": agent_id, "version": version_record.id, "input": input_data})
        with self.db.transaction() as session:
            if idempotency_key:
                existing = session.scalars(
                    select(Execution).where(
                        Execution.workspace_id == principal.workspace_id,
                        Execution.requested_by == self._user(principal),
                        Execution.idempotency_key == idempotency_key,
                    )
                ).first()
                if existing:
                    if existing.options.get("_request_hash") != request_hash:
                        raise ConflictError("Idempotency key was already used with a different request")
                    return {**execution_dict(existing), "idempotent_replay": True}
            if spec.spec.memory.conversation.enabled:
                options["session_id"] = self.conversation.ensure_session(
                    principal.workspace_id, agent.id, options.get("session_id"), self._user(principal)
                )
            execution = Execution(
                workspace_id=principal.workspace_id,
                agent_id=agent.id,
                agent_version_id=version_record.id,
                mode=mode,
                status="queued",
                pending_action="start",
                input=input_data,
                requested_by=self._user(principal),
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                config_snapshot=self._snapshot(spec, version_record),
                options={**options, "_request_hash": request_hash},
                queued_at=utcnow(),
            )
            if inline:  # reserved for a synchronous caller (CLI, evaluations); never enters the shared queue
                execution.status, execution.worker_id, execution.heartbeat_at = "running", "inline", utcnow()
                execution.attempt = 1
            session.add(execution)
            session.flush()
            result = execution_dict(execution)
        EventRecorder(self.db, result["id"], capture_content=self._capture(spec)).emit(
            "execution.queued", status="queued", agent=agent.name, version=version_record.version, mode=mode
        )
        self.audit.record(
            "execution.create",
            "execution",
            result["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"agent": agent.name, "version": version_record.version, "mode": mode},
        )
        if not inline:
            self.wakeup.set()
        return result

    @staticmethod
    def _user(principal: Principal) -> str | None:
        return principal.user_id if principal.auth_method not in {"system", "evaluation"} else None

    def _capture(self, spec: AgentSpec) -> bool:
        override = spec.spec.policies.capture_content
        return bool(self.settings.capture_content if override is None else override)

    def _snapshot(self, spec: AgentSpec, version_record: Any) -> dict[str, Any]:
        build = (version_record.provenance or {}).get("build", {})
        return {
            "agent": spec.metadata.name,
            "agent_version": version_record.version,
            "agent_version_id": version_record.id,
            "spec_hash": version_record.spec_hash,
            "model": spec.spec.model.model_dump(),
            "tools": build.get("tools"),
            "components": build.get("components"),
            "workflow_hash": build.get("workflow_hash"),
            "policy_hash": self.policy.policy_hash(spec.spec),
            "policy_version": spec.spec.policies.version,
            "harness": build.get("harness"),
            "execution_limits": spec.spec.execution_limits.model_dump(),
        }

    # ================================================================== dispatch helpers
    def claim(self, worker_id: str, execution_id: str | None = None) -> str | None:
        with self.db.transaction() as session:
            query = select(Execution.id).where(Execution.status == "queued")
            if execution_id:
                query = query.where(Execution.id == execution_id)
            candidates = session.scalars(query.order_by(Execution.queued_at).limit(5)).all()
            for cid in candidates:
                result = session.execute(
                    update(Execution)
                    .where(Execution.id == cid, Execution.status == "queued")
                    .values(status="running", worker_id=worker_id, heartbeat_at=utcnow(), attempt=Execution.attempt + 1)
                )
                if result.rowcount == 1:  # type: ignore[attr-defined]
                    return cid
        return None

    def enqueue_resume(self, execution_id: str) -> None:
        with self.db.transaction() as session:
            result = session.execute(
                update(Execution)
                .where(Execution.id == execution_id, Execution.status == "waiting_approval")
                .values(status="queued", pending_action="resume", queued_at=utcnow())
            )
            changed = result.rowcount == 1  # type: ignore[attr-defined]
        if changed:
            self.wakeup.set()

    def run_now(self, execution_id: str, worker_id: str = "inline") -> dict[str, Any]:
        """Claim (or use an inline reservation) and run synchronously (CLI, evaluations, tests)."""
        claimed = self.claim(worker_id, execution_id) is not None
        if not claimed:
            record = self._load(execution_id)
            claimed = record.status == "running" and record.worker_id == "inline" and record.started_at is None
        if claimed:
            self.run(execution_id, worker_id)
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            assert execution is not None
            return execution_dict(execution)

    def _heartbeat(self, execution_id: str, stop: threading.Event) -> None:
        while not stop.wait(10):
            with self.db.transaction() as session:
                session.execute(update(Execution).where(Execution.id == execution_id).values(heartbeat_at=utcnow()))

    # ================================================================== execution
    def run(self, execution_id: str, worker_id: str = "worker") -> None:
        token = execution_id_var.set(execution_id)
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(execution_id, stop), daemon=True)
        beat.start()
        try:
            self._execute(execution_id)
        finally:
            stop.set()
            execution_id_var.reset(token)

    def _load(self, execution_id: str) -> Execution:
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None:
                raise NotFoundError("Execution not found")
            session.expunge(execution)
            return execution

    def _cancel_requested(self, execution_id: str) -> bool:
        with self.db.session() as session:
            row = session.execute(
                select(Execution.cancel_requested, Execution.parent_execution_id).where(Execution.id == execution_id)
            ).first()
            if not row:
                return True
            if row[0]:
                return True
            parent: str | None = row[1]
            return parent is not None and self._cancel_requested(parent)

    def _persist_usage(self, execution_id: str, usage: dict[str, Any]) -> None:
        with self.db.transaction() as session:
            session.execute(
                update(Execution).where(Execution.id == execution_id).values(budget_usage=usage, heartbeat_at=utcnow())
            )

    def _components(
        self, execution: Execution, spec: AgentSpec
    ) -> tuple[list[str], list[dict[str, Any]], dict[str, str]]:
        pinned = (execution.config_snapshot or {}).get("components") or []
        instructions: list[str] = []
        rules: list[dict[str, Any]] = []
        prompts: dict[str, str] = {}
        if not pinned:
            return instructions, rules, prompts
        with self.db.session() as session:
            for item in pinned:
                comp = session.get(Component, item["id"])
                if comp is None:
                    continue
                if comp.kind == "system_instruction":
                    instructions.append(comp.definition.get("text", ""))
                elif comp.kind == "validation_rule":
                    rules.append({"name": comp.name, "schema": comp.definition["schema"]})
                elif comp.kind == "prompt_template":
                    prompts[comp.name.replace("-", "_")] = comp.definition.get("template", "")
                elif comp.kind == "policy_definition":
                    extra = comp.definition.get("require_approval_for", [])
                    spec.spec.policies.require_approval_for = sorted(
                        set(spec.spec.policies.require_approval_for) | set(extra)
                    )
        return instructions, rules, prompts

    def _context(
        self, execution: Execution, spec: AgentSpec, principal: Principal | None, depth: int
    ) -> ExecutionContext:
        events = EventRecorder(self.db, execution.id, capture_content=self._capture(spec))
        instructions, rules, prompts = self._components(execution, spec)
        options = {k: v for k, v in (execution.options or {}).items() if not k.startswith("_")}
        options["mode"] = execution.mode
        return ExecutionContext(
            execution_id=execution.id,
            workspace_id=execution.workspace_id,
            agent_id=execution.agent_id,
            agent_name=spec.metadata.name,
            version_id=execution.agent_version_id,
            version=spec.metadata.version,
            spec=spec,
            principal=principal,
            requested_by=execution.requested_by,
            budget=BudgetTracker(spec.spec.execution_limits, execution.budget_usage),
            events=events,
            options=options,
            extra_instructions=instructions,
            validation_rules=rules,
            prompt_components=prompts,
            cancel_check=lambda: self._cancel_requested(execution.id),
            persist_usage=lambda usage: self._persist_usage(execution.id, usage),
            depth=depth,
            conversation_session_id=options.get("session_id"),
        )

    def _principal(self, execution: Execution) -> Principal | None:
        if execution.requested_by:
            return self.identity.principal_for_user(execution.requested_by, execution.workspace_id)
        if execution.mode == "evaluation" or execution.parent_execution_id:
            return self.identity.system_principal(execution.workspace_id)
        return None

    def _execute(self, execution_id: str, depth: int = 0) -> Execution:
        execution = self._load(execution_id)
        from ..storage.models import AgentVersion

        with self.db.session() as session:
            version_record = session.get(AgentVersion, execution.agent_version_id)
            assert version_record is not None
            spec = load_spec(version_record.spec)
        override = (execution.options or {}).get("limits_override")
        if override:
            from ..spec.models import ExecutionLimits

            spec.spec.execution_limits = ExecutionLimits.model_validate(
                {**spec.spec.execution_limits.model_dump(), **override}
            )
        principal = self._principal(execution)
        ctx = self._context(execution, spec, principal, depth)
        action = execution.pending_action or "start"
        config = {
            "configurable": {"thread_id": execution.id},
            "recursion_limit": spec.spec.execution_limits.max_steps * 3 + 10,
        }
        status, output, error = "failed", None, None
        try:
            if principal is None:
                raise PermissionDeniedError("The requesting user is no longer authorized in this workspace")
            graph = compile_workflow(spec, self.harness, ctx, checkpointer=self.checkpointer)
            with self.db.transaction() as session:
                session.execute(
                    update(Execution)
                    .where(Execution.id == execution.id)
                    .values(status="running", pending_action=None, started_at=execution.started_at or utcnow())
                )
            if action == "start":
                ctx.events.emit(
                    "execution.started",
                    status="running",
                    attempt=execution.attempt,
                    agent=spec.metadata.name,
                    version=spec.metadata.version,
                )
                payload: Any = {"input": execution.input, "steps": {}, "loops": {}, "pending": {}, "iterations": {}}
            elif action == "resume":
                ctx.events.emit("execution.resumed", status="running", reason="approval decided")
                payload = Command(resume={"resumed": True})
            else:
                ctx.events.emit("execution.recovering", status="running", reason=action, attempt=execution.attempt)
                payload = None
            with span(
                "agent.execution",
                execution_id=execution.id,
                agent=spec.metadata.name,
                version=spec.metadata.version,
                action=action,
            ):
                result = graph.invoke(payload, config)
            if result.get("__interrupt__"):
                status = "waiting_approval"
                ctx.events.emit(
                    "execution.waiting_approval", status=status, pending=[i.value for i in result["__interrupt__"]]
                )
            else:
                final = result.get("final") or {}
                output = final.get("output")
                if final.get("halted"):
                    status, error = (
                        "rejected",
                        {"code": final["halted"].get("reason"), "message": final["halted"].get("message")},
                    )
                elif not final.get("schema_valid"):
                    status, error = (
                        "failed",
                        {
                            "code": "output_validation_failed",
                            "message": "Final output does not match the output schema",
                            "details": final.get("validation_errors"),
                        },
                    )
                else:
                    status = "completed"
                    if ctx.conversation_session_id:
                        self.conversation.append(
                            ctx.conversation_session_id, "user", str(execution.input)[:4000], execution.id
                        )
                        self.conversation.append(
                            ctx.conversation_session_id, "assistant", str(output)[:4000], execution.id
                        )
        except BudgetExceededError as exc:
            status, error = "budget_exceeded", {"code": exc.code, "message": exc.message, "details": exc.details}
            ctx.events.emit("budget.exceeded", status=status, **exc.details)
        except GraphRecursionError:
            status, error = "budget_exceeded", {"code": "budget_exceeded", "message": "Workflow step limit reached"}
            ctx.events.emit("budget.exceeded", status=status, limit="max_steps")
        except ExecutionCancelledError:
            status, error = "cancelled", {"code": "cancelled", "message": "Execution cancelled"}
        except AgentFactoryError as exc:
            status, error = "failed", {"code": exc.code, "message": exc.message, "details": exc.details}
        except Exception as exc:  # last line of defence: controlled outcome, no internals leaked
            log.exception("execution crashed")
            status, error = "failed", {"code": "internal_error", "message": exc.__class__.__name__}
        ctx.budget.close_segment()
        terminal = status in TERMINAL
        with self.db.transaction() as session:
            record = session.get(Execution, execution.id)
            assert record is not None
            if record.status == "cancelled":  # cancelled while paused
                status = "cancelled"
            record.status = status
            record.budget_usage = ctx.budget.snapshot()
            record.worker_id = None
            if terminal:
                record.output, record.error, record.finished_at = output, error, utcnow()
            session.flush()
            session.expunge(record)
        if terminal:
            ctx.events.emit(
                f"execution.{status}", status=status, error=error, usage=ctx.budget.snapshot(), output=output
            )
            if status == "cancelled":
                self.approvals.cancel_for_execution(execution.id)
        return record

    # ================================================================== delegation (child executions)
    def _run_child(
        self, parent: ExecutionContext, agent_ref: str, parent_input: dict[str, Any], task: str
    ) -> dict[str, Any]:
        resolved = self.agents.resolver(parent.workspace_id)(agent_ref)
        if resolved is None or not resolved.executable:
            return {
                "ok": False,
                "error": {
                    "code": "specialist_unavailable",
                    "message": f"Specialist '{agent_ref}' has no executable version",
                },
            }
        props = resolved.spec.spec.inputs.get("properties", {})
        child_input = {k: v for k, v in parent_input.items() if k in props}
        for field_name in ("task", "question", "focus"):
            if field_name in props:
                child_input[field_name] = task
                break
        try:
            jsonschema.validate(child_input, resolved.spec.spec.inputs)
        except jsonschema.ValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "handoff_invalid",
                    "message": f"Handoff does not satisfy '{agent_ref}' input schema: {exc.message}",
                },
            }
        from ..storage.models import AgentVersion

        with self.db.session() as session:
            version_record = session.get(AgentVersion, resolved.version_id)
            assert version_record is not None
            session.expunge(version_record)
        with self.db.transaction() as session:
            child = Execution(
                workspace_id=parent.workspace_id,
                agent_id=resolved.agent_id,
                agent_version_id=resolved.version_id,
                parent_execution_id=parent.execution_id,
                mode=parent.options.get("mode", "standard"),
                status="running",
                pending_action="start",
                input=child_input,
                requested_by=parent.requested_by,
                config_snapshot=self._snapshot(resolved.spec, version_record),
                options={k: v for k, v in parent.options.items() if k in {"model_override"}},
                queued_at=utcnow(),
                attempt=1,
            )
            session.add(child)
            session.flush()
            child_id = child.id
        EventRecorder(self.db, child_id, capture_content=self._capture(resolved.spec)).emit(
            "execution.queued", status="queued", parent_execution_id=parent.execution_id, handoff={"task": task[:1000]}
        )
        record = self._execute(child_id, depth=parent.depth + 1)
        usage = record.budget_usage or {}
        parent.budget.usage["input_tokens"] += usage.get("input_tokens", 0)
        parent.budget.usage["output_tokens"] += usage.get("output_tokens", 0)
        parent.budget.usage["cost_usd"] = round(parent.budget.usage["cost_usd"] + usage.get("cost_usd", 0.0), 6)
        if record.status != "completed":
            return {
                "ok": False,
                "child_execution_id": child_id,
                "error": {"code": f"specialist_{record.status}", "message": (record.error or {}).get("message", "")},
            }
        return {"ok": True, "output": record.output, "child_execution_id": child_id}

    # ================================================================== control
    def cancel(self, principal: Principal, execution_id: str) -> dict[str, Any]:
        principal.require("executions:cancel")
        with self.db.transaction() as session:
            execution = session.get(Execution, execution_id)
            if not execution or execution.workspace_id != principal.workspace_id:
                raise NotFoundError("Execution not found")
            if execution.status in TERMINAL:
                raise InvalidStateError(f"Execution is already '{execution.status}'")
            execution.cancel_requested = True
            immediate = execution.status in {"queued", "waiting_approval"}
            if immediate:
                execution.status, execution.finished_at = "cancelled", utcnow()
                execution.error = {"code": "cancelled", "message": f"Cancelled by {principal.display_name}"}
            for child in session.scalars(select(Execution).where(Execution.parent_execution_id == execution_id)):
                child.cancel_requested = True
            session.flush()
            result = execution_dict(execution)
        events = EventRecorder(self.db, execution_id, capture_content=False)
        events.emit("execution.cancel_requested", status="cancelling", by=principal.user_id)
        if immediate:
            self.approvals.cancel_for_execution(execution_id)
            events.emit("execution.cancelled", status="cancelled")
        self.audit.record(
            "execution.cancel",
            "execution",
            execution_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
        )
        return result

    def retry(self, principal: Principal, execution_id: str) -> dict[str, Any]:
        """Recover a failed execution from its last checkpoint. Completed steps and tool calls are not repeated."""
        principal.require("executions:run")
        with self.db.transaction() as session:
            execution = session.get(Execution, execution_id)
            if not execution or execution.workspace_id != principal.workspace_id:
                raise NotFoundError("Execution not found")
            if execution.status not in {"failed"}:
                raise InvalidStateError("Only failed executions can be recovered")
            execution.status, execution.pending_action, execution.queued_at = "queued", "recover", utcnow()
            execution.error, execution.finished_at = None, None
            session.flush()
            result = execution_dict(execution)
        self.audit.record(
            "execution.recover",
            "execution",
            execution_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
        )
        self.wakeup.set()
        return result

    def recover_stale(self) -> int:
        """Requeue executions whose worker stopped heart-beating (crash recovery)."""
        threshold = utcnow() - timedelta(seconds=self.settings.worker_heartbeat_stale_seconds)
        with self.db.transaction() as session:
            stale = session.scalars(
                select(Execution).where(
                    Execution.status == "running",
                    Execution.parent_execution_id.is_(None),
                    Execution.heartbeat_at < threshold,
                )
            ).all()
            for execution in stale:
                execution.status = "queued"
                execution.pending_action = "recover" if execution.started_at else "start"
                execution.queued_at = utcnow()
            ids = [e.id for e in stale]
        for eid in ids:
            EventRecorder(self.db, eid, capture_content=False).emit(
                "execution.requeued", status="queued", reason="worker heartbeat lost"
            )
        if ids:
            self.wakeup.set()
        return len(ids)

    # ================================================================== queries
    def get(self, principal: Principal, execution_id: str) -> dict[str, Any]:
        principal.require("executions:read")
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            if not execution or execution.workspace_id != principal.workspace_id:
                raise NotFoundError("Execution not found")
            result = execution_dict(execution)
            result["children"] = [
                execution_dict(c, include_io=False)
                for c in session.scalars(select(Execution).where(Execution.parent_execution_id == execution_id))
            ]
            result["tool_invocations"] = [
                {
                    "id": t.id,
                    "step_id": t.step_id,
                    "tool_name": t.tool_name,
                    "tool_version": t.tool_version,
                    "status": t.status,
                    "arguments": t.arguments,
                    "decision": t.decision,
                    "duration_ms": t.duration_ms,
                    "approval_id": t.approval_id,
                    "result": t.result,
                    "created_at": t.created_at.isoformat(),
                }
                for t in session.scalars(
                    select(ToolInvocation)
                    .where(ToolInvocation.execution_id == execution_id)
                    .order_by(ToolInvocation.created_at)
                )
                if not t.idempotency_key.startswith("approval:")
            ]
            result["approvals"] = [
                {
                    "id": a.id,
                    "status": a.status,
                    "action": a.action,
                    "tool_name": a.tool_name,
                    "requested_at": a.requested_at.isoformat(),
                }
                for a in session.scalars(select(Approval).where(Approval.execution_id == execution_id))
            ]
            from ..storage.models import Agent, AgentVersion, WorkflowDefinition

            agent = session.get(Agent, execution.agent_id)
            version = session.get(AgentVersion, execution.agent_version_id)
            wf = session.scalars(
                select(WorkflowDefinition).where(WorkflowDefinition.agent_version_id == execution.agent_version_id)
            ).first()
            result["agent"] = (
                {"id": agent.id, "name": agent.name, "display_name": agent.display_name} if agent else None
            )
            result["version"] = version.version if version else None
            result["graph"] = wf.graph if wf else None
        return result

    def list(
        self,
        principal: Principal,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
        include_children: bool = False,
        mode: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        principal.require("executions:read")
        from datetime import datetime

        from ..storage.models import Agent, AgentVersion

        with self.db.session() as session:
            query = (
                select(Execution, Agent.name, AgentVersion.version)
                .join(Agent, Agent.id == Execution.agent_id)
                .join(AgentVersion, AgentVersion.id == Execution.agent_version_id)
                .where(Execution.workspace_id == principal.workspace_id)
            )
            if not include_children:
                query = query.where(Execution.parent_execution_id.is_(None))
            if agent_id:
                query = query.where(Execution.agent_id == agent_id)
            if mode:
                query = query.where(Execution.mode == mode)
            if status:
                query = query.where(Execution.status.in_(status.split(",")))
            if since:
                query = query.where(Execution.created_at >= datetime.fromisoformat(since))
            if until:
                query = query.where(Execution.created_at <= datetime.fromisoformat(until))
            if q:
                query = query.where(Execution.id.like(f"{q}%"))
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.execute(query.order_by(Execution.created_at.desc()).limit(limit).offset(offset)).all()
            items = [
                {**execution_dict(e, include_io=False), "agent_name": name, "version": ver} for e, name, ver in rows
            ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def events(self, principal: Principal, execution_id: str, after: int = 0, limit: int = 500) -> dict[str, Any]:
        principal.require("executions:read")
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            if not execution or execution.workspace_id != principal.workspace_id:
                raise NotFoundError("Execution not found")
            rows = session.scalars(
                select(ExecutionEvent)
                .where(ExecutionEvent.execution_id == execution_id, ExecutionEvent.seq > after)
                .order_by(ExecutionEvent.seq)
                .limit(limit)
            ).all()
            return {
                "execution_id": execution_id,
                "status": execution.status,
                "items": [
                    {
                        "seq": e.seq,
                        "type": e.type,
                        "node_id": e.node_id,
                        "status": e.status,
                        "payload": e.payload,
                        "duration_ms": e.duration_ms,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in rows
                ],
            }

    def delete(self, principal: Principal, execution_id: str) -> None:
        principal.require("executions:delete")
        with self.db.transaction() as session:
            execution = session.get(Execution, execution_id)
            if not execution or execution.workspace_id != principal.workspace_id:
                raise NotFoundError("Execution not found")
            if execution.status in ACTIVE:
                raise InvalidStateError("Cancel the execution before deleting it")
            child_ids = list(session.scalars(select(Execution.id).where(Execution.parent_execution_id == execution_id)))
        for eid in [*child_ids, execution_id]:
            self._purge(eid)
        self.audit.record(
            "execution.delete",
            "execution",
            execution_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
        )

    def _purge(self, execution_id: str) -> None:
        self.checkpointer.delete_thread(execution_id)
        with self.db.transaction() as session:
            session.execute(delete(ExecutionEvent).where(ExecutionEvent.execution_id == execution_id))
            session.execute(delete(ToolInvocation).where(ToolInvocation.execution_id == execution_id))
            session.execute(delete(Approval).where(Approval.execution_id == execution_id))
            session.execute(delete(Execution).where(Execution.id == execution_id))

    def purge_older_than(self, days: int) -> int:
        cutoff = utcnow() - timedelta(days=days)
        from ..storage.models import EvaluationResult

        with self.db.session() as session:
            ids = list(
                session.scalars(
                    select(Execution.id).where(Execution.created_at < cutoff, Execution.status.in_(TERMINAL))
                )
            )
            referenced = (
                set(
                    session.scalars(select(EvaluationResult.execution_id).where(EvaluationResult.execution_id.in_(ids)))
                )
                if ids
                else set()
            )
        for eid in ids:
            if eid in referenced:
                continue  # evaluation evidence is retained with its evaluation run
            self._purge(eid)
        return len(ids) - len(referenced)

    def stats(self, principal: Principal, mode: str | None = "standard") -> dict[str, Any]:
        with self.db.session() as session:
            query = select(Execution.status, func.count()).where(
                Execution.workspace_id == principal.workspace_id, Execution.parent_execution_id.is_(None)
            )
            if mode:
                query = query.where(Execution.mode == mode)
            rows = session.execute(query.group_by(Execution.status)).all()
        return {status: count for status, count in rows}

"""ACE orchestration: semantic checkpoints after every step, continuity assessment before every recovery or
resume, and the bookkeeping that makes a replay safe (invalidate exactly the affected work, never a committed
side effect)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from ..core.errors import NotFoundError
from ..core.ids import content_hash
from ..core.logging import get_logger
from ..governance.audit import AuditLog
from ..governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from ..governance.rbac import Principal
from ..spec.loader import load_spec
from ..spec.models import AgentSpec, WorkflowStep
from ..storage.db import Database
from ..storage.models import (
    AgentVersion,
    ContinuityAssessment,
    Execution,
    ExecutionIntent,
    StepRecord,
    ToolInvocation,
)
from ..tools.base import SideEffect
from ..tools.registry import ToolRegistry
from .assessment import assess
from .dependencies import step_dependencies
from .fingerprint import EnvironmentProbe
from .ledger import IntentLedger

log = get_logger(__name__)


def input_digest(deps: dict[str, Any], state: dict[str, Any]) -> str:
    steps = state.get("steps", {}) or {}
    return content_hash(
        {
            "input": state.get("input") if deps.get("input") else None,
            "steps": {s: content_hash((steps.get(s) or {}).get("output")) for s in deps.get("steps", [])},
        }
    )


def _step_of(key: str) -> str | None:
    parts = key.split(":")
    return parts[1] if len(parts) >= 3 and not key.startswith("approval:") else None


class ContinuityService:
    def __init__(
        self,
        db: Database,
        audit: AuditLog,
        settings: Any,
        tools: ToolRegistry,
        policy: PolicyEngine,
        probe: EnvironmentProbe,
        ledger: IntentLedger,
        checkpointer: Any,
    ) -> None:
        self.db = db
        self.audit = audit
        self.settings = settings
        self.tools = tools
        self.policy = policy
        self.probe = probe
        self.ledger = ledger
        self.checkpointer = checkpointer
        self.principal_for: Callable[[Execution], Principal | None] = lambda e: None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "continuity_enabled", True))

    def run_context(self, execution: Execution, spec: AgentSpec, spec_hash: str) -> dict[str, Any]:
        """What every step of this run records alongside its output: the environment and the intent version."""
        model = spec.spec.model
        override = (execution.options or {}).get("model_override")
        if override:
            model = model.model_validate({**model.model_dump(), **override})
        with self.db.session() as session:
            head = self.ledger.head(session, execution.id)
        return {
            "env": self.probe.snapshot(execution.workspace_id, spec, spec_hash, model),
            "intent_version": head.version if head else 1,
        }

    # ================================================================== semantic checkpoints
    def _side_effect(self, tool_name: str) -> bool:
        if tool_name.startswith("delegate:"):
            return False  # specialists are read-only by platform rule
        if not self.tools.has(tool_name):
            return True  # unknown: assume it acted on the world
        return self.tools.get(tool_name).definition.side_effects != SideEffect.READ

    def node_environment(
        self, env: dict[str, Any], step: WorkflowStep, kind: str, tools_used: list[str], workspace_id: str
    ) -> dict[str, Any]:
        out: dict[str, Any] = {k: env.get(k) for k in ("spec_hash", "policy_hash", "platform")}
        if kind in {"model", "agent_loop", "agent_loop_tools"}:
            out["model"] = env.get("model")
        names = sorted(set(tools_used) | ({step.tool} if step.tool else set()))
        if names:
            out["tools"] = {n: env.get("tools", {}).get(n) or self.probe.tool(n) for n in names}
        if step.agent:
            out["specialists"] = {
                step.agent: env.get("specialists", {}).get(step.agent)
                or self.probe.specialist(workspace_id, step.agent)
            }
        if kind == "retrieve" and env.get("knowledge"):
            out["knowledge"] = env["knowledge"]
        return out

    def record(
        self, ctx: Any, step: WorkflowStep, kind: str, node_id: str, state: dict[str, Any], update: dict[str, Any]
    ) -> None:
        """Persist one Execution State Graph node with its semantic checkpoint (environment, inputs, output,
        evidence, side effects). Called after the step produced its output."""
        result = (update.get("steps") or {}).get(step.id)
        continuity = getattr(ctx, "continuity", None) or {}
        if not result or not continuity:
            return  # intermediate agent-loop rounds and runs without ACE context record nothing
        deps = step_dependencies(ctx.spec).get(step.id, {"input": True, "steps": []})
        prefix = f"{ctx.execution_id}:{step.id}:"
        with self.db.transaction() as session:
            calls = list(
                session.scalars(
                    select(ToolInvocation).where(
                        ToolInvocation.execution_id == ctx.execution_id,
                        ToolInvocation.idempotency_key.like(f"{prefix}%"),
                        ToolInvocation.status == "completed",
                    )
                )
            )
            seq = (
                session.scalar(select(func.max(StepRecord.seq)).where(StepRecord.execution_id == ctx.execution_id)) or 0
            ) + 1
            session.add(
                StepRecord(
                    execution_id=ctx.execution_id,
                    step_id=step.id,
                    node_id=node_id,
                    kind=kind,
                    seq=seq,
                    status=str(result.get("status", "completed")),
                    depends_on=deps,
                    intent_version=int(continuity.get("intent_version", 1)),
                    environment=self.node_environment(
                        continuity["env"],
                        step,
                        kind,
                        [c.tool_name for c in calls if not c.tool_name.startswith("delegate:")],
                        ctx.workspace_id,
                    ),
                    input_digest=input_digest(deps, state),
                    output_digest=content_hash(result.get("output")),
                    evidence={"tool_invocations": [c.id for c in calls], "tools": sorted({c.tool_name for c in calls})},
                    side_effects=[
                        {"invocation_id": c.id, "tool": c.tool_name, "args_hash": content_hash(c.arguments)}
                        for c in calls
                        if self._side_effect(c.tool_name)
                    ],
                )
            )

    # ================================================================== assessment
    def _invocations(self, session: Any, execution: Execution, spec: AgentSpec) -> list[dict[str, Any]]:
        principal = self.principal_for(execution)
        out = []
        for inv in session.scalars(select(ToolInvocation).where(ToolInvocation.execution_id == execution.id)):
            step_id = _step_of(inv.idempotency_key)
            if step_id is None or inv.status == "invalidated":
                continue
            item = {
                "id": inv.id,
                "tool": inv.tool_name,
                "step_id": step_id,
                "status": inv.status,
                "side_effect": self._side_effect(inv.tool_name),
                "args_hash": content_hash(inv.arguments),
            }
            if inv.status == "completed" and not inv.tool_name.startswith("delegate:"):
                tool = self.tools.get(inv.tool_name) if self.tools.has(inv.tool_name) else None
                decision = self.policy.evaluate_tool(
                    ToolAuthorizationRequest(
                        principal=principal,
                        workspace_id=execution.workspace_id,
                        agent=spec.spec,
                        tool_name=inv.tool_name,
                        tool=tool.definition if tool else None,
                        tool_enabled=self.tools.is_enabled(inv.tool_name),
                        arguments={},
                        sandbox_available=True,
                        approval_already_granted=True,
                        destination_host=None,
                        destination_resolved=True,
                    )
                )
                item["policy_now"] = "deny" if decision.effect == Effect.DENY else "allow"
                item["policy_rules"] = decision.rules
            out.append(item)
        return out

    def _integrity(self, session: Any, execution: Execution, version: AgentVersion) -> dict[str, Any]:
        recorded = (execution.config_snapshot or {}).get("spec_hash")
        spec_ok = version.spec_hash == content_hash(version.spec) and recorded in (None, version.spec_hash)
        intents = list(session.scalars(select(ExecutionIntent).where(ExecutionIntent.execution_id == execution.id)))
        bad = [
            i.version
            for i in intents
            if i.intent_hash != content_hash(i.intent) or (i.version > 1 and (not i.created_by or not i.reason.strip()))
        ]
        return {
            "spec_ok": spec_ok,
            "spec_detail": {"recorded": recorded, "version": version.spec_hash},
            "intent_ok": not bad,
            "intent_detail": f"Intent versions {bad} fail integrity or authorization checks",
        }

    def _current_state(self, execution_id: str) -> dict[str, Any]:
        tup = self.checkpointer.get_tuple({"configurable": {"thread_id": execution_id}})
        return dict(tup.checkpoint.get("channel_values", {})) if tup else {}

    def assess(
        self, execution_id: str, *, trigger: str, persist: bool = True, decided_by: str | None = None
    ) -> dict[str, Any]:
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None:
                raise NotFoundError("Execution not found")
            version = session.get(AgentVersion, execution.agent_version_id)
            assert version is not None
            spec = load_spec(version.spec)
            head = self.ledger.head(session, execution_id)
            intents = {
                i.version: i
                for i in session.scalars(select(ExecutionIntent).where(ExecutionIntent.execution_id == execution_id))
            }
            rows = list(
                session.scalars(
                    select(StepRecord)
                    .where(
                        StepRecord.execution_id == execution_id,
                        StepRecord.validity != "invalidated",
                        StepRecord.status == "completed",
                    )
                    .order_by(StepRecord.seq)
                )
            )
            latest: dict[str, StepRecord] = {}
            for row in rows:
                latest[row.step_id] = row
            records: list[dict[str, Any]] = [
                {
                    "step_id": r.step_id,
                    "kind": r.kind,
                    "environment": r.environment,
                    "input_digest": r.input_digest,
                    "intent_version": r.intent_version,
                }
                for r in latest.values()
            ]
            invocations = self._invocations(session, execution, spec)
            integrity = self._integrity(session, execution, version)
            model = spec.spec.model
            override = (execution.options or {}).get("model_override")
            if override:
                model = model.model_validate({**model.model_dump(), **override})
            self.probe.invalidate_models()
            current = self.probe.snapshot(execution.workspace_id, spec, version.spec_hash, model)
            base_versions = {r["intent_version"] for r in records} or {1}
            head_version = head.version if head else 1
            goals_changed = head is not None and any(
                intents[v].goals_hash != head.goals_hash for v in base_versions if v in intents
            )
            constraints_changed = (
                head is not None
                and not goals_changed
                and any(intents[v].intent_hash != head.intent_hash for v in base_versions if v in intents)
            )
            workspace_id = execution.workspace_id
        deps = step_dependencies(spec)
        state = self._current_state(execution_id)
        digests = {r["step_id"]: input_digest(deps.get(r["step_id"], {}), state) for r in records} if state else {}
        result = assess(
            trigger=trigger,
            order=[s.id for s in spec.spec.workflow.steps],
            records=records,
            current=current,
            dependencies=deps,
            goals_changed=goals_changed,
            constraints_changed=constraints_changed,
            invocations=invocations,
            integrity=integrity,
            current_digests=digests,
        )
        view = {
            "trigger": trigger,
            "intent_version": head_version,
            "environment": current,
            "changes": result.changes,
            "affected_steps": result.affected,
            "findings": result.findings,
            "scores": result.scores,
            "decision": result.decision,
            "from_step": result.from_step,
            "rationale": result.rationale,
            "dependencies": deps,
        }
        if not persist:
            return {**view, "id": None, "status": "preview"}
        self._mark_in_doubt(execution_id, workspace_id, invocations)
        status = "needs_review" if result.decision == "escalate" else "applied"
        with self.db.transaction() as session:
            saved = ContinuityAssessment(
                execution_id=execution_id,
                workspace_id=workspace_id,
                trigger=trigger,
                intent_version=head_version,
                environment=current,
                changes=result.changes,
                affected_steps=result.affected,
                findings=result.findings,
                scores=result.scores,
                decision=result.decision,
                from_step=result.from_step,
                rationale=result.rationale,
                status=status,
                decided_by=decided_by,
            )
            session.add(saved)
            session.flush()
            assessment_id = saved.id
        self.audit.record(
            "continuity.assessment",
            "execution",
            execution_id,
            actor_id=decided_by,
            workspace_id=workspace_id,
            outcome="success" if status == "applied" else "failure",
            details={
                "assessment_id": assessment_id,
                "trigger": trigger,
                "decision": result.decision,
                "from_step": result.from_step,
                "scs": result.scores.get("scs"),
                "hard_invariants": result.scores.get("hard_invariants"),
                "changes": len(result.changes),
            },
        )
        return {**view, "id": assessment_id, "status": status}

    def _mark_in_doubt(self, execution_id: str, workspace_id: str, invocations: list[dict[str, Any]]) -> None:
        """Interrupted side-effecting calls get the same in-doubt status (and audit event) the harness guard gives
        them, so operators see one consistent state whichever mechanism caught it first."""
        pending = [i for i in invocations if i["side_effect"] and i["status"] == "pending"]
        if not pending:
            return
        with self.db.transaction() as session:
            for item in pending:
                row = session.get(ToolInvocation, item["id"])
                if row is not None and row.status == "pending":
                    row.status = "in_doubt"
        for item in pending:
            self.audit.record(
                "tool.in_doubt",
                "tool_invocation",
                item["id"],
                outcome="failure",
                workspace_id=workspace_id,
                details={"execution_id": execution_id, "tool": item["tool"], "detected_by": "continuity"},
            )

    def record_override(
        self, execution_id: str, *, decision: str, from_step: str | None, decided_by: str, note: str, workspace_id: str
    ) -> str:
        """An operator's decision after an escalation, kept next to the automatic assessments."""
        preview = self.assess(execution_id, trigger="manual", persist=False)
        with self.db.transaction() as session:
            saved = ContinuityAssessment(
                execution_id=execution_id,
                workspace_id=workspace_id,
                trigger="manual",
                intent_version=preview["intent_version"],
                environment=preview["environment"],
                changes=preview["changes"],
                affected_steps=preview["affected_steps"],
                findings=preview["findings"],
                scores=preview["scores"],
                decision=decision,
                from_step=from_step,
                rationale=f"Operator decision: {note[:1500]}",
                status="overridden",
                decided_by=decided_by,
            )
            session.add(saved)
            session.flush()
            return str(saved.id)

    # ================================================================== replay support
    def invalidate_from(
        self, execution_id: str, spec: AgentSpec, from_step: str | None, assessment_id: str | None
    ) -> int:
        """Mark the work from ``from_step`` onward as invalid so it is redone: step records and read-only tool
        results. Committed side effects are never invalidated (the decision engine escalates instead)."""
        order = [s.id for s in spec.spec.workflow.steps]
        region = set(order[order.index(from_step) :]) if from_step in order else set(order)
        count = 0
        with self.db.transaction() as session:
            for row in session.scalars(
                select(StepRecord).where(
                    StepRecord.execution_id == execution_id,
                    StepRecord.step_id.in_(region),
                    StepRecord.validity != "invalidated",
                )
            ):
                row.validity, row.invalidated_by = "invalidated", assessment_id
                count += 1
            for inv in session.scalars(select(ToolInvocation).where(ToolInvocation.execution_id == execution_id)):
                step_id = _step_of(inv.idempotency_key)
                if step_id in region and not self._side_effect(inv.tool_name) and inv.status != "invalidated":
                    inv.status = "invalidated"
        return count

    def mark_revalidated(self, execution_id: str) -> None:
        with self.db.transaction() as session:
            for row in session.scalars(
                select(StepRecord).where(StepRecord.execution_id == execution_id, StepRecord.validity == "valid")
            ):
                row.validity = "revalidated"

    @staticmethod
    def checkpoint_before(graph: Any, thread_config: dict[str, Any], step_id: str) -> dict[str, Any] | None:
        """The newest checkpoint whose next node is ``step_id``: the state just before that step ran."""
        for snapshot in graph.get_state_history(thread_config):
            if tuple(snapshot.next) == (step_id,):
                return {**thread_config, "configurable": {**snapshot.config["configurable"]}}
        return None

    # ================================================================== views
    def view(self, principal: Principal, execution_id: str) -> dict[str, Any]:
        principal.require("executions:read")
        with self.db.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None or execution.workspace_id != principal.workspace_id:
                raise NotFoundError("Execution not found")
            version = session.get(AgentVersion, execution.agent_version_id)
            assert version is not None
            spec = load_spec(version.spec)
            records = [
                {
                    "id": r.id,
                    "step_id": r.step_id,
                    "node_id": r.node_id,
                    "kind": r.kind,
                    "seq": r.seq,
                    "status": r.status,
                    "validity": r.validity,
                    "intent_version": r.intent_version,
                    "environment": r.environment,
                    "input_digest": r.input_digest,
                    "output_digest": r.output_digest,
                    "evidence": r.evidence,
                    "side_effects": r.side_effects,
                    "invalidated_by": r.invalidated_by,
                    "created_at": r.created_at.isoformat(),
                }
                for r in session.scalars(
                    select(StepRecord).where(StepRecord.execution_id == execution_id).order_by(StepRecord.seq)
                )
            ]
            assessments = [
                {
                    "id": a.id,
                    "trigger": a.trigger,
                    "intent_version": a.intent_version,
                    "changes": a.changes,
                    "affected_steps": a.affected_steps,
                    "findings": a.findings,
                    "scores": a.scores,
                    "decision": a.decision,
                    "from_step": a.from_step,
                    "rationale": a.rationale,
                    "status": a.status,
                    "decided_by": a.decided_by,
                    "created_at": a.created_at.isoformat(),
                }
                for a in session.scalars(
                    select(ContinuityAssessment)
                    .where(ContinuityAssessment.execution_id == execution_id)
                    .order_by(ContinuityAssessment.created_at.desc())
                )
            ]
            status = execution.status
        deps = step_dependencies(spec)
        return {
            "execution_id": execution_id,
            "status": status,
            "enabled": self.enabled,
            "intent": self.ledger.versions(execution_id),
            "graph": [
                {
                    "step_id": s.id,
                    "kind": s.kind,
                    "depends_on": deps[s.id],
                    "records": [r for r in records if r["step_id"] == s.id],
                }
                for s in spec.spec.workflow.steps
            ],
            "assessments": assessments,
        }

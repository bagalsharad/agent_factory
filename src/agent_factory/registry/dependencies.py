"""Dependency graph: what every built agent version depends on, and who is affected when something changes.

Edges are recorded when a version is built (``record``), with the versions that were resolved at that moment, so
a later registry change is visible as drift instead of being silently picked up. Queries answer "which agents use
this tool / policy / model / component?" and ``impact`` turns that into a revalidation plan.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select

from ..core import semver
from ..core.errors import NotFoundError, ValidationFailedError
from ..core.ids import utcnow
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..spec.loader import load_spec
from ..spec.models import AgentSpec
from ..storage.db import Database
from ..storage.models import Agent, AgentVersion, Component, DependencyEdge, EvaluationRun, EvaluationSuite
from ..tools.registry import ToolRegistry

KINDS = ("tool", "model", "model_profile", "component", "agent", "evaluation_suite")
PUBLISHED = {"published", "approved"}


def spec_edges(spec: AgentSpec, tools: ToolRegistry) -> list[tuple[str, str, str | None]]:
    """(kind, name, version) edges of a specification, with tool versions resolved against the registry."""
    body = spec.spec
    edges: dict[tuple[str, str], str | None] = {}
    for ref in body.tools:
        version = ref.version
        if version is None and tools.has(ref.name):
            version = tools.get(ref.name).definition.version
        edges[("tool", ref.name)] = version
    edges[("model", f"{body.model.provider}/{body.model.model}")] = None
    if body.model.profile:
        edges[("model_profile", body.model.profile)] = None
    for comp in body.components:
        edges[("component", f"{comp.kind}/{comp.name}")] = comp.version
    refs = [s.agent for s in body.workflow.steps if s.agent] + [s.agent for s in body.workflow.specialists]
    for agent_ref in refs:
        name, _, version = agent_ref.partition("@")
        edges[("agent", name)] = version or None
    if body.evaluation.suite:
        edges[("evaluation_suite", body.evaluation.suite)] = None
    return [(kind, name, version) for (kind, name), version in edges.items()]


class DependencyService:
    def __init__(self, db: Database, audit: AuditLog, tools: ToolRegistry) -> None:
        self.db = db
        self.audit = audit
        self.tools = tools
        self.revalidate_runner: Callable[[Principal, str, str], dict[str, Any]] | None = None

    # ------------------------------------------------------------------ recording
    def record(self, version_id: str) -> int:
        with self.db.transaction() as session:
            version = session.get(AgentVersion, version_id)
            if version is None:
                return 0
            session.execute(delete(DependencyEdge).where(DependencyEdge.agent_version_id == version_id))
            edges = spec_edges(load_spec(version.spec), self.tools)
            for kind, name, ver in edges:
                session.add(
                    DependencyEdge(
                        workspace_id=version.workspace_id,
                        agent_id=version.agent_id,
                        agent_version_id=version.id,
                        kind=kind,
                        name=name,
                        version=ver,
                    )
                )
            return len(edges)

    def backfill(self) -> int:
        """Record edges for built versions that predate the dependency graph."""
        with self.db.session() as session:
            recorded = set(session.scalars(select(DependencyEdge.agent_version_id).distinct()))
            pending = [
                v.id
                for v in session.scalars(select(AgentVersion).where(AgentVersion.build_report.is_not(None)))
                if v.id not in recorded
            ]
        for version_id in pending:
            self.record(version_id)
        return len(pending)

    # ------------------------------------------------------------------ queries
    def _drift(self, edge: DependencyEdge, session: Any) -> dict[str, Any]:
        """Is the dependency still what this version was built against?"""
        if edge.kind == "tool":
            if not self.tools.has(edge.name):
                return {"state": "missing", "detail": "tool is no longer registered"}
            current = self.tools.get(edge.name).definition.version
            if not self.tools.is_enabled(edge.name):
                return {"state": "disabled", "detail": "tool is disabled by platform settings", "current": current}
            if edge.version and current != edge.version:
                return {
                    "state": "drifted",
                    "detail": f"built with {edge.version}, registry now {current}",
                    "current": current,
                }
        if edge.kind == "component":
            kind, _, name = edge.name.partition("/")
            comp = session.scalars(
                select(Component).where(
                    Component.kind == kind, Component.name == name, Component.version == edge.version
                )
            ).first()
            if comp is None:
                return {"state": "missing", "detail": "component version not found"}
            if comp.status == "deprecated":
                return {"state": "deprecated", "detail": "pinned component version is deprecated", "owner": comp.owner}
        return {"state": "ok"}

    def dependencies(self, principal: Principal, agent_id: str, version_id: str) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            agent = session.get(Agent, agent_id)
            if agent is None or agent.workspace_id != principal.workspace_id:
                raise NotFoundError("Agent not found")
            edges = list(session.scalars(select(DependencyEdge).where(DependencyEdge.agent_version_id == version_id)))
            items = [{"kind": e.kind, "name": e.name, "version": e.version, **self._drift(e, session)} for e in edges]
        return {
            "agent_id": agent_id,
            "agent_version_id": version_id,
            "items": sorted(items, key=lambda i: (KINDS.index(i["kind"]), i["name"])),
            "problems": [i for i in items if i["state"] != "ok"],
        }

    def blockers(self, version_id: str) -> list[str]:
        """Dependency problems that make a release unsafe (deprecated, missing or disabled dependencies)."""
        with self.db.session() as session:
            edges = list(session.scalars(select(DependencyEdge).where(DependencyEdge.agent_version_id == version_id)))
            problems = [(e, self._drift(e, session)) for e in edges]
        bad = [
            f"{e.kind} {e.name}: {d['detail']}"
            for e, d in problems
            if d["state"] in {"deprecated", "missing", "disabled"}
        ]
        return [f"Unsafe dependencies: {'; '.join(bad)}"] if bad else []

    def dependents(
        self, principal: Principal, kind: str, name: str, version: str | None = None
    ) -> list[dict[str, Any]]:
        principal.require("agents:read")
        if kind not in KINDS:
            raise ValidationFailedError(f"kind must be one of {', '.join(KINDS)}")
        with self.db.session() as session:
            query = (
                select(DependencyEdge, AgentVersion, Agent)
                .join(AgentVersion, AgentVersion.id == DependencyEdge.agent_version_id)
                .join(Agent, Agent.id == DependencyEdge.agent_id)
                .where(
                    DependencyEdge.workspace_id == principal.workspace_id,
                    DependencyEdge.kind == kind,
                    DependencyEdge.name == name,
                )
            )
            if version:
                query = query.where(DependencyEdge.version == version)
            rows = session.execute(query.order_by(Agent.name, AgentVersion.created_at)).all()
            last_validated = self._last_validated(session, [v.id for _, v, _ in rows])
            return [
                {
                    "agent_id": agent.id,
                    "agent": agent.name,
                    "version": v.version,
                    "version_id": v.id,
                    "status": v.status,
                    "active": agent.active_version_id == v.id,
                    "dependency_version": edge.version,
                    "owner_id": agent.owner_id,
                    "business_owner": agent.business_owner,
                    "technical_contact": agent.technical_contact,
                    "escalation_contact": agent.escalation_contact,
                    "last_validated_at": last_validated.get(v.id),
                    "evaluation_suite": load_spec(v.spec).spec.evaluation.suite,
                }
                for edge, v, agent in rows
            ]

    @staticmethod
    def _last_validated(session: Any, version_ids: list[str]) -> dict[str, str]:
        if not version_ids:
            return {}
        out: dict[str, str] = {}
        for run in session.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.agent_version_id.in_(version_ids), EvaluationRun.status == "completed")
            .order_by(EvaluationRun.created_at)
        ):
            if run.finished_at:
                out[run.agent_version_id] = run.finished_at.isoformat()
        return out

    # ------------------------------------------------------------------ impact
    @staticmethod
    def compatibility(old: str | None, new: str | None) -> str:
        if not old or not new:
            return "unknown"
        try:
            a, b = semver.parse(old), semver.parse(new)
        except ValueError:
            return "unknown"
        if a == b:
            return "unchanged"
        return "breaking" if b[0] != a[0] else "compatible"

    def impact(
        self, principal: Principal, kind: str, name: str, *, to_version: str | None = None, change: str = ""
    ) -> dict[str, Any]:
        """Who is affected by a change to a dependency, how risky it is, and what must be revalidated."""
        affected = self.dependents(principal, kind, name)
        items = []
        for row in affected:
            compat = self.compatibility(row["dependency_version"], to_version)
            in_service = row["status"] in PUBLISHED or row["active"]
            items.append(
                {
                    **row,
                    "compatibility": compat,
                    "requires_revalidation": in_service or row["status"] == "testing",
                    "risk": "high"
                    if in_service and compat in {"breaking", "unknown"}
                    else "medium"
                    if in_service
                    else "low",
                }
            )
        with self.db.session() as session:
            suites = (
                sorted(
                    {
                        s.name
                        for s in session.scalars(
                            select(EvaluationSuite).where(
                                EvaluationSuite.workspace_id == principal.workspace_id,
                                EvaluationSuite.agent_id.in_({r["agent_id"] for r in affected}),
                            )
                        )
                    }
                )
                if affected
                else []
            )
        report = {
            "dependency": {"kind": kind, "name": name, "to_version": to_version, "change": change[:1000]},
            "generated_at": utcnow().isoformat(),
            "affected_versions": items,
            "affected_agents": sorted({r["agent"] for r in affected}),
            "in_service": [f"{r['agent']}@{r['version']}" for r in items if r["risk"] != "low"],
            "evaluation_suites": suites,
            "notify": sorted({c for r in affected for c in (r["technical_contact"], r["business_owner"]) if c}),
            "revalidate": [
                {"agent_id": r["agent_id"], "version": r["version"]} for r in items if r["requires_revalidation"]
            ],
        }
        self.audit.record(
            "dependency.impact",
            "dependency",
            f"{kind}:{name}",
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"affected": len(items), "in_service": len(report["in_service"]), "to_version": to_version},
        )
        return report

    def revalidate(self, principal: Principal, kind: str, name: str) -> dict[str, Any]:
        """Re-run the evaluation of every affected version that is in service or under test."""
        principal.require("evaluations:run")
        if self.revalidate_runner is None:
            raise ValidationFailedError("Revalidation is not available")
        plan = self.impact(principal, kind, name)["revalidate"]
        runs = []
        for item in plan:
            try:
                run = self.revalidate_runner(principal, item["agent_id"], item["version"])
                runs.append({**item, "run_id": run["id"], "status": run["status"]})
            except Exception as exc:  # one broken agent must not stop the others; the reason is reported
                runs.append({**item, "run_id": None, "status": "not_started", "error": str(exc)[:300]})
        return {"dependency": {"kind": kind, "name": name}, "runs": runs}

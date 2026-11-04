"""Agent registry: definitions, versions, lifecycle operations, comparison and rollback."""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core import semver
from ..core.errors import ConflictError, InvalidStateError, NotFoundError, ValidationFailedError
from ..core.ids import content_hash, utcnow
from ..evaluation.gates import publish_blockers
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..spec.loader import dump_yaml, load_spec
from ..spec.models import AGENT_REF_RE, EXECUTABLE_STATUSES, AgentSpec, LifecycleStatus
from ..spec.validator import ResolvedAgentRef, SpecValidator
from ..storage.db import Database
from ..storage.models import Agent, AgentVersion, EvaluationRun, Execution
from .diff import json_diff
from .lifecycle import assert_transition, is_mutable

if TYPE_CHECKING:
    from ..builder.builder import AgentBuilder
    from .dependencies import DependencyService
    from .releases import ReleaseService

S = LifecycleStatus


def version_dict(v: AgentVersion, *, include_spec: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": v.id,
        "agent_id": v.agent_id,
        "version": v.version,
        "status": v.status,
        "spec_hash": v.spec_hash,
        "api_version": v.api_version,
        "template": v.template_ref,
        "release_notes": v.release_notes,
        "validation_report": v.validation_report,
        "build_report": v.build_report,
        "provenance": v.provenance,
        "parent_version_id": v.parent_version_id,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat(),
        "updated_at": v.updated_at.isoformat(),
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "executable": S(v.status) in EXECUTABLE_STATUSES,
    }
    if include_spec:
        out["spec"] = v.spec
    return out


class AgentService:
    def __init__(self, db: Database, audit: AuditLog, validator: SpecValidator) -> None:
        self.db = db
        self.audit = audit
        self.validator = validator
        self.builder: AgentBuilder | None = None  # set by the platform container (avoids an import cycle)
        self.dependencies: DependencyService | None = None  # set by the platform
        self.releases: ReleaseService | None = None  # set by the platform (artifacts on publish)

    # ------------------------------------------------------------------ lookups
    def _agent(self, session: Session, principal: Principal, agent_id: str) -> Agent:
        agent = session.get(Agent, agent_id)
        if not agent or agent.workspace_id != principal.workspace_id or agent.archived:
            # identical response for "missing" and "other workspace" (no cross-workspace enumeration)
            raise NotFoundError("Agent not found")
        return agent

    def _versions(self, session: Session, agent_id: str) -> list[AgentVersion]:
        rows = session.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent_id)).all()
        return sorted(rows, key=lambda v: semver.sort_key(v.version), reverse=True)

    def _version(self, session: Session, agent: Agent, version: str | None) -> AgentVersion:
        versions = self._versions(session, agent.id)
        if not versions:
            raise NotFoundError("Agent has no versions")
        if version is None:
            return versions[0]
        for v in versions:
            if version in (v.version, v.id):
                return v
        raise NotFoundError(f"Version '{version}' not found")

    def agent_summary(self, session: Session, agent: Agent) -> dict[str, Any]:
        versions = self._versions(session, agent.id)
        latest = versions[0] if versions else None
        active = next((v for v in versions if v.id == agent.active_version_id), None)
        last_exec = session.scalar(select(func.max(Execution.created_at)).where(Execution.agent_id == agent.id))
        return {
            "id": agent.id,
            "name": agent.name,
            "display_name": agent.display_name or agent.name,
            "description": agent.description,
            "workspace_id": agent.workspace_id,
            "owner_id": agent.owner_id,
            "ownership": {
                "business_owner": agent.business_owner,
                "technical_contact": agent.technical_contact,
                "escalation_contact": agent.escalation_contact,
            },
            "created_at": agent.created_at.isoformat(),
            "updated_at": agent.updated_at.isoformat(),
            "latest_version": version_dict(latest, include_spec=False) if latest else None,
            "active_version": version_dict(active, include_spec=False) if active else None,
            "version_count": len(versions),
            "workflow_type": (latest.spec.get("spec", {}).get("workflow", {}).get("type") if latest else None),
            "template": latest.template_ref if latest else None,
            "last_execution_at": last_exec.isoformat() if last_exec else None,
        }

    # ------------------------------------------------------------------ create / update
    def create(
        self,
        principal: Principal,
        spec: AgentSpec,
        *,
        release_notes: str = "",
        created_via: str = "api",
        extra_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        principal.require("agents:write")
        doc = spec.to_document()
        with self.db.transaction() as session:
            exists = session.scalars(
                select(Agent).where(Agent.workspace_id == principal.workspace_id, Agent.name == spec.metadata.name)
            ).first()
            if exists:
                raise ConflictError(f"An agent named '{spec.metadata.name}' already exists in this workspace")
            agent = Agent(
                workspace_id=principal.workspace_id,
                name=spec.metadata.name,
                display_name=spec.metadata.display_name,
                description=spec.metadata.description,
                owner_id=principal.user_id if principal.user_id != "system" else None,
            )
            session.add(agent)
            session.flush()
            version = AgentVersion(
                agent_id=agent.id,
                workspace_id=principal.workspace_id,
                version=spec.metadata.version,
                status=S.DRAFT,
                spec=doc,
                spec_hash=content_hash(doc),
                api_version=spec.api_version,
                template_ref=spec.metadata.template,
                release_notes=release_notes,
                provenance={"created_via": created_via, "template": spec.metadata.template, **(extra_provenance or {})},
                created_by=agent.owner_id,
            )
            session.add(version)
            session.flush()
            result = self.agent_summary(session, agent)
        self.audit.record(
            "agent.create",
            "agent",
            result["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"name": spec.metadata.name},
        )
        return result

    def update(
        self, principal: Principal, agent_id: str, spec: AgentSpec, *, release_notes: str = "", bump: str = "minor"
    ) -> dict[str, Any]:
        """Edit the latest version in place if mutable; otherwise create a new version (immutability)."""
        principal.require("agents:write")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            if spec.metadata.name != agent.name:
                raise ValidationFailedError("metadata.name cannot change; duplicate the agent instead")
            latest = self._version(session, agent, None)
            if is_mutable(latest.status):
                if spec.metadata.version != latest.version:
                    spec.metadata.version = latest.version
                doc = spec.to_document()
                latest.spec, latest.spec_hash, latest.status = doc, content_hash(doc), S.DRAFT
                latest.validation_report = latest.build_report = None
                latest.release_notes = release_notes or latest.release_notes
                latest.template_ref = spec.metadata.template
                target, created = latest, False
            else:
                new_version = spec.metadata.version
                if semver.sort_key(new_version) <= semver.sort_key(latest.version):
                    new_version = semver.bump(latest.version, bump)  # type: ignore[arg-type]
                spec.metadata.version = new_version
                doc = spec.to_document()
                target = AgentVersion(
                    agent_id=agent.id,
                    workspace_id=agent.workspace_id,
                    version=new_version,
                    status=S.DRAFT,
                    spec=doc,
                    spec_hash=content_hash(doc),
                    api_version=spec.api_version,
                    template_ref=spec.metadata.template,
                    release_notes=release_notes,
                    provenance={"created_via": "edit", "derived_from": latest.version},
                    parent_version_id=latest.id,
                    created_by=principal.user_id if principal.user_id != "system" else None,
                )
                session.add(target)
                created = True
            agent.display_name, agent.description = spec.metadata.display_name, spec.metadata.description
            session.flush()
            result: dict[str, Any] = {
                "agent": self.agent_summary(session, agent),
                "version": version_dict(target),
                "new_version_created": created,
            }
        self.audit.record(
            "agent.update",
            "agent",
            agent_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"version": result["version"]["version"], "new_version": created},
        )
        return result

    def duplicate(self, principal: Principal, agent_id: str, new_name: str) -> dict[str, Any]:
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            source = self._version(session, agent, None)
            doc = dict(source.spec)
        doc["metadata"] = {
            **doc["metadata"],
            "name": new_name,
            "version": "0.1.0",
            "display_name": f"{doc['metadata'].get('display_name') or agent.name} (copy)",
        }
        return self.create(
            principal,
            load_spec(doc),
            created_via="duplicate",
            extra_provenance={"duplicated_from": f"{agent.name}@{source.version}"},
        )

    # ------------------------------------------------------------------ queries
    def list(
        self, principal: Principal, *, q: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            query = select(Agent).where(Agent.workspace_id == principal.workspace_id, Agent.archived.is_(False))
            if q:
                like = f"%{q.lower()}%"
                query = query.where(
                    or_(
                        func.lower(Agent.name).like(like),
                        func.lower(Agent.display_name).like(like),
                        func.lower(Agent.description).like(like),
                    )
                )
            agents = session.scalars(query.order_by(Agent.updated_at.desc())).all()
            items = [self.agent_summary(session, a) for a in agents]
        if status:
            items = [
                a
                for a in items
                if (a["latest_version"] and a["latest_version"]["status"] == status)
                or (a["active_version"] and a["active_version"]["status"] == status)
            ]
        return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset}

    def get(self, principal: Principal, agent_id: str) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            summary = self.agent_summary(session, agent)
            latest = self._version(session, agent, None)
            summary["spec"] = latest.spec
            summary["spec_yaml"] = dump_yaml(latest.spec)
            summary["versions"] = [version_dict(v, include_spec=False) for v in self._versions(session, agent.id)]
            return summary

    def versions(self, principal: Principal, agent_id: str) -> builtins.list[dict[str, Any]]:
        principal.require("agents:read")
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            return [version_dict(v, include_spec=False) for v in self._versions(session, agent.id)]

    def get_version(self, principal: Principal, agent_id: str, version: str) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            v = self._version(session, agent, version)
            out = version_dict(v)
            out["spec_yaml"] = dump_yaml(v.spec)
            return out

    def load_version(self, principal: Principal, agent_id: str, version: str | None) -> tuple[Agent, AgentVersion]:
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            v = self._version(session, agent, version)
            session.expunge(agent)
            session.expunge(v)
            return agent, v

    def executable_version(
        self, principal: Principal, agent_id: str, version: str | None, *, prefer_latest: bool = False
    ) -> tuple[Agent, AgentVersion]:
        """Resolve the version to run.

        Executions default to the active published version (production behaviour). Evaluations pass
        ``prefer_latest`` so that a newly built version is what gets tested.
        """
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            if version:
                v = self._version(session, agent, version)
            elif agent.active_version_id and not prefer_latest:
                v = session.get(AgentVersion, agent.active_version_id)  # type: ignore[assignment]
            else:
                candidates = [x for x in self._versions(session, agent.id) if S(x.status) in EXECUTABLE_STATUSES]
                if not candidates:
                    raise InvalidStateError(
                        "Agent has no executable version (draft specifications are never executable). Validate and build it first."
                    )
                v = candidates[0]
            if S(v.status) not in EXECUTABLE_STATUSES:
                raise InvalidStateError(
                    f"Version {v.version} is '{v.status}' and cannot be executed. Validate and "
                    "build it first (draft specifications are never executable)."
                )
            session.expunge(agent)
            session.expunge(v)
            return agent, v

    def resolver(self, workspace_id: str) -> Any:
        def resolve(ref: str) -> ResolvedAgentRef | None:
            match = AGENT_REF_RE.match(ref)
            if not match:
                return None
            with self.db.session() as session:
                agent = session.scalars(
                    select(Agent).where(
                        Agent.workspace_id == workspace_id, Agent.name == match.group("name"), Agent.archived.is_(False)
                    )
                ).first()
                if not agent:
                    return None
                versions = self._versions(session, agent.id)
                wanted = match.group("version")
                if wanted:
                    chosen = next((v for v in versions if v.version == wanted), None)
                else:
                    chosen = (
                        next((v for v in versions if v.id == agent.active_version_id), None)
                        or next((v for v in versions if S(v.status) in EXECUTABLE_STATUSES), None)
                        or (versions[0] if versions else None)
                    )
                if not chosen:
                    return None
                return ResolvedAgentRef(
                    agent.id,
                    chosen.id,
                    agent.name,
                    chosen.version,
                    load_spec(chosen.spec),
                    S(chosen.status) in EXECUTABLE_STATUSES,
                )

        return resolve

    # ------------------------------------------------------------------ lifecycle
    def _set_status(self, session: Session, v: AgentVersion, target: S) -> None:
        assert_transition(v.status, target)
        v.status = target

    def validate(self, principal: Principal, agent_id: str, version: str | None = None) -> dict[str, Any]:
        principal.require("agents:write")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            v = self._version(session, agent, version)
            spec = load_spec(v.spec)
            report = self.validator.validate(spec, self.resolver(agent.workspace_id), check_provider_health=True)
            v.validation_report = report.to_dict()
            if is_mutable(v.status):
                if not report.valid:
                    v.status = S.FAILED
                elif v.status in (S.FAILED, S.VALIDATING):
                    v.status = S.DRAFT
            result = {"version": v.version, "status": v.status, **report.to_dict()}
        self.audit.record(
            "agent.validate",
            "agent_version",
            v.id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            outcome="success" if report.valid else "failure",
            details={"errors": len(report.errors), "warnings": len(report.warnings)},
        )
        return result

    def build(self, principal: Principal, agent_id: str, version: str | None = None) -> dict[str, Any]:
        principal.require("agents:write")
        assert self.builder is not None
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            v = self._version(session, agent, version)
            if not is_mutable(v.status) and v.status != S.DISABLED:
                raise InvalidStateError(f"Version {v.version} is '{v.status}' (immutable) and already built")
            if v.status == S.DISABLED:
                raise InvalidStateError("Re-enable the version before building")
            v.status = S.VALIDATING
            version_id = v.id
        report = self.builder.build(principal, version_id)
        with self.db.transaction() as session:
            built = session.get(AgentVersion, version_id)
            assert built is not None
            built.build_report = report
            built.validation_report = report.get("validation")
            built.status = S.TESTING if report["success"] else S.FAILED
            status = built.status
        if report["success"] and self.dependencies is not None:
            self.dependencies.record(version_id)
        self.audit.record(
            "agent.build",
            "agent_version",
            version_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            outcome="success" if report["success"] else "failure",
        )
        return {**report, "status": status}

    def on_evaluation(self, version_id: str, passed: bool) -> str:
        with self.db.transaction() as session:
            v = session.get(AgentVersion, version_id)
            if not v:
                return "missing"
            if passed and v.status == S.TESTING:
                v.status = S.APPROVED
            elif not passed and v.status == S.APPROVED:
                v.status = S.TESTING
            return v.status

    def latest_evaluation(self, session: Session, v: AgentVersion) -> EvaluationRun | None:
        """The latest completed evaluation run for this exact specification (what the publish gate judges)."""
        return session.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.agent_version_id == v.id,
                EvaluationRun.status == "completed",
                EvaluationRun.spec_hash == v.spec_hash,
                EvaluationRun.purpose == "release",
            )
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        ).first()

    def publish(
        self, principal: Principal, agent_id: str, version: str | None = None, release_notes: str | None = None
    ) -> dict[str, Any]:
        principal.require("agents:publish")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            v = self._version(session, agent, version)
            run = self.latest_evaluation(session, v)
            blockers = publish_blockers(
                version_status=v.status,
                run={"passed": run.passed, "summary": run.summary} if run else None,
                active_version_id=agent.active_version_id,
                version_id=v.id,
            ) + (self.dependencies.blockers(v.id) if self.dependencies is not None else [])
            if blockers:
                raise InvalidStateError(
                    f"Publish gate: version {v.version} cannot be published. " + " ".join(blockers),
                    details={"blockers": blockers},
                )
            assert run is not None
            previous = session.get(AgentVersion, agent.active_version_id) if agent.active_version_id else None
            if previous and previous.id != v.id and previous.status == S.PUBLISHED:
                previous.status = S.DEPRECATED
            self._set_status(session, v, S.PUBLISHED)
            v.published_at = utcnow()
            if release_notes:
                v.release_notes = release_notes
            v.provenance = {**v.provenance, "published_by": principal.user_id, "evaluation_run_id": run.id}
            agent.active_version_id = v.id
            agent.updated_at = utcnow()
            session.flush()
            result = version_dict(v)
        if self.releases is not None:  # immutable release artifact for deployments
            self.releases.on_publish(result["id"], principal)
        self.audit.record(
            "agent.publish",
            "agent_version",
            result["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"version": result["version"]},
        )
        return result

    def set_ownership(self, principal: Principal, agent_id: str, contacts: dict[str, str | None]) -> dict[str, Any]:
        principal.require("agents:write")
        allowed = {"business_owner", "technical_contact", "escalation_contact"}
        unknown = set(contacts) - allowed
        if unknown:
            raise ValidationFailedError(f"Unknown ownership fields: {', '.join(sorted(unknown))}")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            for key, value in contacts.items():
                setattr(agent, key, (value or "").strip()[:200] or None)
            agent.updated_at = utcnow()
            session.flush()
            result = self.agent_summary(session, agent)
        self.audit.record(
            "agent.ownership",
            "agent",
            agent_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={k: bool(v) for k, v in contacts.items()},
        )
        return result

    def deprecate(self, principal: Principal, agent_id: str, version: str | None = None) -> dict[str, Any]:
        principal.require("agents:publish")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            v = (
                session.get(AgentVersion, agent.active_version_id)
                if (version is None and agent.active_version_id)
                else self._version(session, agent, version)
            )
            assert v is not None
            self._set_status(session, v, S.DEPRECATED)
            session.flush()
            result = version_dict(v)
        self.audit.record(
            "agent.deprecate",
            "agent_version",
            result["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
        )
        return result

    def disable(self, principal: Principal, agent_id: str, version: str | None = None) -> dict[str, Any]:
        principal.require("agents:disable")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            targets = (
                [self._version(session, agent, version)]
                if version
                else [v for v in self._versions(session, agent.id) if v.status != S.DISABLED]
            )
            for v in targets:
                self._set_status(session, v, S.DISABLED)
                if agent.active_version_id == v.id:
                    agent.active_version_id = None
            session.flush()
            result = self.agent_summary(session, agent)
        self.audit.record(
            "agent.disable",
            "agent",
            agent_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"version": version or "all"},
        )
        return result

    def enable(self, principal: Principal, agent_id: str, version: str) -> dict[str, Any]:
        principal.require("agents:disable")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            v = self._version(session, agent, version)
            self._set_status(session, v, S.TESTING)
            session.flush()
            return version_dict(v)

    def rollback(self, principal: Principal, agent_id: str, target_version: str) -> dict[str, Any]:
        principal.require("agents:publish")
        with self.db.transaction() as session:
            agent = self._agent(session, principal, agent_id)
            target = self._version(session, agent, target_version)
            if target.status not in (S.PUBLISHED, S.DEPRECATED) or not target.published_at:
                raise InvalidStateError("Rollback target must be a previously published version")
            current = session.get(AgentVersion, agent.active_version_id) if agent.active_version_id else None
            if current and current.id == target.id:
                raise InvalidStateError("Target is already the active version")
            if current and current.status == S.PUBLISHED:
                current.status = S.DEPRECATED
            if target.status == S.DEPRECATED:
                self._set_status(session, target, S.PUBLISHED)
            agent.active_version_id = target.id
            session.flush()
            result = {
                "active_version": version_dict(target, include_spec=False),
                "previous_version": version_dict(current, include_spec=False) if current else None,
            }
        self.audit.record(
            "agent.rollback",
            "agent",
            agent_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"to": target_version},
        )
        return result

    def compare(self, principal: Principal, agent_id: str, base: str, other: str) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            agent = self._agent(session, principal, agent_id)
            a, b = self._version(session, agent, base), self._version(session, agent, other)
            evals = {}
            for v in (a, b):
                run = session.scalars(
                    select(EvaluationRun)
                    .where(EvaluationRun.agent_version_id == v.id)
                    .order_by(EvaluationRun.created_at.desc())
                    .limit(1)
                ).first()
                evals[v.version] = (
                    {"run_id": run.id, "passed": run.passed, "metrics": run.metrics, "summary": run.summary}
                    if run
                    else None
                )
            return {
                "base": version_dict(a, include_spec=False),
                "other": version_dict(b, include_spec=False),
                "changes": json_diff(a.spec, b.spec),
                "evaluations": evals,
            }

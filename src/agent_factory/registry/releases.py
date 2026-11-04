"""Release management: immutable artifacts, deployment environments, deployments, canaries and rollback.

    publish ──▶ artifact (hash) ──deploy──▶ development / test / staging ──promote (same artifact)──▶ production
                                                                              (approval by another person)

* An artifact is built once per published version and never changes; every deployment re-verifies its hash and
  that the version's ``spec_hash`` still matches, so a tampered record cannot be deployed.
* Production only accepts an artifact that was deployed to its ``promote_from`` environment (staging by default)
  and only after an approval by someone other than the requester when separation of duties is enabled.
* A canary runs a share of traffic on a new version next to the primary; promote or roll back explicitly.
* Environment configuration can only tighten execution limits: the immutable definition is never loosened.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select

from .. import __version__
from ..core.config import Settings
from ..core.errors import InvalidStateError, NotFoundError, PermissionDeniedError, ValidationFailedError
from ..core.ids import canonical_json, utcnow
from ..governance.audit import AuditLog
from ..governance.policy import PolicyEngine
from ..governance.rbac import Principal
from ..spec.loader import load_spec
from ..spec.models import ExecutionLimits
from ..storage.db import Database
from ..storage.models import (
    Agent,
    AgentArtifact,
    AgentVersion,
    DependencyEdge,
    Deployment,
    DeploymentEnvironment,
    EvaluationRun,
    EvaluationSuite,
)

DEFAULT_ENVIRONMENTS = [  # name, rank, requires_approval, promote_from
    ("development", 0, False, None),
    ("test", 1, False, None),
    ("staging", 2, False, None),
    ("production", 3, True, "staging"),
]
LIVE = ("active",)


def artifact_hash(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(artifact).encode()).hexdigest()


def _bucket(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100


class ReleaseService:
    def __init__(self, db: Database, audit: AuditLog, settings: Settings) -> None:
        self.db = db
        self.audit = audit
        self.settings = settings
        self.dependency_blockers: Any = None  # DependencyService.blockers, set by the platform

    # ================================================================== artifacts
    def build_artifact(self, session: Any, version: AgentVersion, created_by: str | None) -> AgentArtifact:
        existing = session.scalars(select(AgentArtifact).where(AgentArtifact.agent_version_id == version.id)).first()
        if existing is not None:
            return existing
        spec = load_spec(version.spec)
        edges = session.scalars(select(DependencyEdge).where(DependencyEdge.agent_version_id == version.id)).all()
        run_id = (version.provenance or {}).get("evaluation_run_id")
        run = session.get(EvaluationRun, run_id) if run_id else None
        suite = session.get(EvaluationSuite, run.suite_id) if run else None
        artifact = {
            "format": "agentfactory.artifact/v1",
            "agent": {"name": spec.metadata.name, "version": version.version, "agent_id": version.agent_id},
            "spec": version.spec,  # specs never contain secret values (the validator rejects them)
            "spec_hash": version.spec_hash,
            "workflow": spec.spec.workflow.model_dump(mode="json"),
            "model": spec.spec.model.model_dump(mode="json"),
            "policy_hash": PolicyEngine.policy_hash(spec.spec),
            "dependencies": sorted(
                ({"kind": e.kind, "name": e.name, "version": e.version} for e in edges),
                key=lambda d: (d["kind"], d["name"]),
            ),
            "evaluation": {
                "run_id": run.id if run else None,
                "passed": run.passed if run else None,
                "suite": {"name": suite.name, "version": suite.version, "hash": suite.content_hash} if suite else None,
                "metrics": {k: (run.metrics or {}).get(k) for k in ("pass_rate", "p95_latency_ms", "cost_usd")}
                if run
                else None,
            },
            "build": {
                "platform_version": __version__,
                "built_at": (version.build_report or {}).get("finished_at"),
                "published_at": version.published_at.isoformat() if version.published_at else None,
                "published_by": (version.provenance or {}).get("published_by"),
            },
        }
        record = AgentArtifact(
            workspace_id=version.workspace_id,
            agent_id=version.agent_id,
            agent_version_id=version.id,
            version=version.version,
            artifact=artifact,
            artifact_hash=artifact_hash(artifact),
            created_by=created_by,
        )
        session.add(record)
        session.flush()
        return record

    def on_publish(self, version_id: str, principal: Principal) -> None:
        with self.db.transaction() as session:
            version = session.get(AgentVersion, version_id)
            if version is not None:
                record = self.build_artifact(session, version, principal.user_id)
                digest = record.artifact_hash
        self.audit.record(
            "artifact.create",
            "agent_version",
            version_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"artifact_hash": digest},
        )

    def backfill(self) -> int:
        """Artifacts for versions published before release management existed."""
        with self.db.transaction() as session:
            have = set(session.scalars(select(AgentArtifact.agent_version_id)))
            pending = [
                v
                for v in session.scalars(
                    select(AgentVersion).where(AgentVersion.status.in_(["published", "deprecated"]))
                )
                if v.id not in have and v.published_at is not None
            ]
            for version in pending:
                self.build_artifact(session, version, (version.provenance or {}).get("published_by"))
        return len(pending)

    def _verify(self, session: Any, record: AgentArtifact) -> dict[str, Any]:
        version = session.get(AgentVersion, record.agent_version_id)
        problems = []
        if artifact_hash(record.artifact) != record.artifact_hash:
            problems.append("artifact content does not match its hash")
        if version is None or version.spec_hash != record.artifact.get("spec_hash"):
            problems.append("agent version no longer matches the artifact's specification")
        if version is not None and record.artifact.get("spec") != version.spec:
            problems.append("specification differs from the artifact")
        return {"valid": not problems, "problems": problems, "artifact_hash": record.artifact_hash}

    def artifact(self, principal: Principal, agent_id: str, version: str) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            record = self._artifact(session, principal, agent_id, version)
            return {
                "id": record.id,
                "version": record.version,
                "artifact_hash": record.artifact_hash,
                "created_at": record.created_at.isoformat(),
                "artifact": record.artifact,
                "verification": self._verify(session, record),
            }

    def _artifact(self, session: Any, principal: Principal, agent_id: str, version: str) -> AgentArtifact:
        agent = session.get(Agent, agent_id)
        if agent is None or agent.workspace_id != principal.workspace_id:
            raise NotFoundError("Agent not found")
        v = session.scalars(
            select(AgentVersion).where(AgentVersion.agent_id == agent_id, AgentVersion.version == version)
        ).first()
        if v is None:
            raise NotFoundError(f"Version '{version}' not found")
        record = session.scalars(select(AgentArtifact).where(AgentArtifact.agent_version_id == v.id)).first()
        if record is None:
            raise NotFoundError(
                f"Version {version} has no release artifact; artifacts are created when it is published"
            )
        return record

    # ================================================================== environments
    def environments(self, principal: Principal) -> list[dict[str, Any]]:
        principal.require("agents:read")
        with self.db.transaction() as session:
            envs = self._ensure_environments(session, principal.workspace_id)
            return [
                {
                    "id": e.id,
                    "name": e.name,
                    "rank": e.rank,
                    "requires_approval": e.requires_approval,
                    "promote_from": e.promote_from,
                    "config": e.config,
                }
                for e in envs
            ]

    def _ensure_environments(self, session: Any, workspace_id: str) -> list[DeploymentEnvironment]:
        envs = list(
            session.scalars(select(DeploymentEnvironment).where(DeploymentEnvironment.workspace_id == workspace_id))
        )
        if not envs:
            for name, rank, approval, source in DEFAULT_ENVIRONMENTS:
                env = DeploymentEnvironment(
                    workspace_id=workspace_id,
                    name=name,
                    rank=rank,
                    requires_approval=approval,
                    promote_from=source,
                    config={},
                )
                session.add(env)
                envs.append(env)
            session.flush()
        return sorted(envs, key=lambda e: e.rank)

    def configure_environment(
        self,
        principal: Principal,
        name: str,
        *,
        limits: dict[str, Any] | None = None,
        requires_approval: bool | None = None,
    ) -> dict[str, Any]:
        principal.require("settings:write")
        if limits:
            unknown = set(limits) - set(ExecutionLimits.model_fields)
            if unknown:
                raise ValidationFailedError(f"Unknown execution limits: {', '.join(sorted(unknown))}")
        with self.db.transaction() as session:
            env = self._env(session, principal.workspace_id, name)
            if limits is not None:
                env.config = {**env.config, "limits": limits}
            if requires_approval is not None:
                if name == "production" and not requires_approval and not self.settings.allow_self_approval:
                    raise ValidationFailedError(
                        "Production deployments require approval when separation of duties is on"
                    )
                env.requires_approval = requires_approval
        self.audit.record(
            "environment.configure",
            "environment",
            name,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"limits": limits, "requires_approval": requires_approval},
        )
        return next(e for e in self.environments(principal) if e["name"] == name)

    def _env(self, session: Any, workspace_id: str, name: str) -> DeploymentEnvironment:
        for env in self._ensure_environments(session, workspace_id):
            if env.name == name:
                return env
        raise NotFoundError(f"Environment '{name}' not found")

    # ================================================================== deployments
    @staticmethod
    def _view(d: Deployment, env: str, version: str) -> dict[str, Any]:
        return {
            "id": d.id,
            "environment": env,
            "agent_id": d.agent_id,
            "version": version,
            "strategy": d.strategy,
            "traffic_percent": d.traffic_percent,
            "status": d.status,
            "artifact_hash": d.artifact_hash,
            "requested_by": d.requested_by,
            "approved_by": d.approved_by,
            "previous_deployment_id": d.previous_deployment_id,
            "notes": d.notes,
            "created_at": d.created_at.isoformat(),
            "activated_at": d.activated_at.isoformat() if d.activated_at else None,
            "ended_at": d.ended_at.isoformat() if d.ended_at else None,
        }

    def _live(self, session: Any, env_id: str, agent_id: str) -> list[Deployment]:
        return list(
            session.scalars(
                select(Deployment).where(
                    Deployment.environment_id == env_id, Deployment.agent_id == agent_id, Deployment.status.in_(LIVE)
                )
            )
        )

    def deploy(
        self,
        principal: Principal,
        agent_id: str,
        version: str,
        environment: str,
        *,
        strategy: str = "standard",
        traffic_percent: int = 100,
        notes: str = "",
        dependency_blockers: Any = None,
    ) -> dict[str, Any]:
        principal.require("agents:publish")
        if strategy not in {"standard", "canary"}:
            raise ValidationFailedError("strategy is standard or canary")
        if strategy == "canary" and not 1 <= traffic_percent <= 50:
            raise ValidationFailedError("A canary takes 1-50% of traffic")
        with self.db.transaction() as session:
            env = self._env(session, principal.workspace_id, environment)
            record = self._artifact(session, principal, agent_id, version)
            check = self._verify(session, record)
            if not check["valid"]:
                raise InvalidStateError("Artifact integrity check failed: " + "; ".join(check["problems"]))
            v = session.get(AgentVersion, record.agent_version_id)
            assert v is not None
            if v.status not in {"published", "deprecated"}:
                raise InvalidStateError(f"Version {version} is '{v.status}'; only published versions are deployed")
            check_dependencies = dependency_blockers or self.dependency_blockers
            blockers = check_dependencies(v.id) if check_dependencies else []
            if blockers:
                raise InvalidStateError(" ".join(blockers))
            if env.promote_from:
                source = self._env(session, principal.workspace_id, env.promote_from)
                promoted = session.scalars(
                    select(Deployment).where(
                        Deployment.environment_id == source.id,
                        Deployment.artifact_hash == record.artifact_hash,
                        Deployment.status.in_(["active", "superseded"]),
                    )
                ).first()
                if promoted is None:
                    raise InvalidStateError(
                        f"Deploy this artifact to {env.promote_from} first: {environment} only accepts artifacts "
                        f"promoted from {env.promote_from}"
                    )
            deployment = Deployment(
                workspace_id=principal.workspace_id,
                environment_id=env.id,
                agent_id=agent_id,
                agent_version_id=v.id,
                artifact_id=record.id,
                artifact_hash=record.artifact_hash,
                strategy=strategy,
                traffic_percent=traffic_percent if strategy == "canary" else 100,
                status="pending_approval",
                requested_by=principal.user_id,
                notes=notes[:2000],
            )
            session.add(deployment)
            session.flush()
            if not env.requires_approval:
                self._activate(session, deployment)
            view = self._view(deployment, env.name, version)
        self.audit.record(
            "deployment.request",
            "deployment",
            view["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={
                "environment": environment,
                "version": version,
                "strategy": strategy,
                "status": view["status"],
                "artifact_hash": view["artifact_hash"],
            },
        )
        return view

    def _activate(self, session: Any, deployment: Deployment) -> None:
        live = [d for d in self._live(session, deployment.environment_id, deployment.agent_id) if d.id != deployment.id]
        primary = next((d for d in live if d.strategy == "standard"), None)
        for d in live:  # a new primary replaces everything; a new canary replaces only the previous canary
            if deployment.strategy == "standard" or d.strategy == "canary":
                d.status, d.ended_at = "superseded", utcnow()
        deployment.previous_deployment_id = primary.id if primary else None
        deployment.status, deployment.activated_at = "active", utcnow()

    def _deployment(self, session: Any, principal: Principal, deployment_id: str) -> Deployment:
        d = session.get(Deployment, deployment_id)
        if d is None or d.workspace_id != principal.workspace_id:
            raise NotFoundError("Deployment not found")
        return d

    def decide(self, principal: Principal, deployment_id: str, approve: bool, comment: str = "") -> dict[str, Any]:
        principal.require("agents:publish")
        with self.db.transaction() as session:
            d = self._deployment(session, principal, deployment_id)
            if d.status != "pending_approval":
                raise InvalidStateError(f"Deployment is '{d.status}', not awaiting approval")
            if d.requested_by == principal.user_id and not self.settings.allow_self_approval:
                raise PermissionDeniedError("Separation of duties: you cannot approve your own deployment")
            d.approved_by = principal.user_id
            if approve:
                self._activate(session, d)
            else:
                d.status, d.ended_at = "rejected", utcnow()
            d.notes = (d.notes + ("\n" if d.notes else "") + comment[:1000]).strip()
            env = session.get(DeploymentEnvironment, d.environment_id)
            version = session.get(AgentVersion, d.agent_version_id)
            view = self._view(d, env.name if env else "?", version.version if version else "?")
        self.audit.record(
            "deployment.approve" if approve else "deployment.reject",
            "deployment",
            deployment_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"environment": view["environment"], "version": view["version"]},
        )
        return view

    def promote_canary(self, principal: Principal, agent_id: str, environment: str) -> dict[str, Any]:
        principal.require("agents:publish")
        with self.db.transaction() as session:
            env = self._env(session, principal.workspace_id, environment)
            live = self._live(session, env.id, agent_id)
            canary = next((d for d in live if d.strategy == "canary"), None)
            if canary is None:
                raise InvalidStateError("No active canary in this environment")
            canary.strategy, canary.traffic_percent = "standard", 100
            self._activate(session, canary)
            version = session.get(AgentVersion, canary.agent_version_id)
            view = self._view(canary, env.name, version.version if version else "?")
        self.audit.record(
            "deployment.promote",
            "deployment",
            view["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"environment": environment},
        )
        return view

    def rollback(self, principal: Principal, agent_id: str, environment: str, reason: str = "") -> dict[str, Any]:
        """Undo the latest change: drop an active canary, or restore the previous primary deployment."""
        principal.require("agents:publish")
        with self.db.transaction() as session:
            env = self._env(session, principal.workspace_id, environment)
            live = self._live(session, env.id, agent_id)
            canary = next((d for d in live if d.strategy == "canary"), None)
            primary = next((d for d in live if d.strategy == "standard"), None)
            if canary is not None:
                canary.status, canary.ended_at = "rolled_back", utcnow()
                restored = primary
            else:
                if primary is None or not primary.previous_deployment_id:
                    raise InvalidStateError("Nothing to roll back to in this environment")
                previous = session.get(Deployment, primary.previous_deployment_id)
                assert previous is not None
                primary.status, primary.ended_at = "rolled_back", utcnow()
                restored = Deployment(
                    workspace_id=previous.workspace_id,
                    environment_id=env.id,
                    agent_id=agent_id,
                    agent_version_id=previous.agent_version_id,
                    artifact_id=previous.artifact_id,
                    artifact_hash=previous.artifact_hash,
                    strategy="standard",
                    traffic_percent=100,
                    status="active",
                    requested_by=principal.user_id,
                    approved_by=principal.user_id,
                    previous_deployment_id=previous.previous_deployment_id,
                    activated_at=utcnow(),
                    notes=f"rollback: {reason}"[:2000],
                )
                session.add(restored)
                session.flush()
            assert restored is not None
            version = session.get(AgentVersion, restored.agent_version_id)
            view = self._view(restored, env.name, version.version if version else "?")
        self.audit.record(
            "deployment.rollback",
            "deployment",
            view["id"],
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"environment": environment, "restored_version": view["version"], "reason": reason[:500]},
        )
        return view

    def retire(self, principal: Principal, agent_id: str, environment: str) -> dict[str, Any]:
        principal.require("agents:publish")
        with self.db.transaction() as session:
            env = self._env(session, principal.workspace_id, environment)
            live = self._live(session, env.id, agent_id)
            for d in live:
                d.status, d.ended_at = "retired", utcnow()
        self.audit.record(
            "deployment.retire",
            "agent",
            agent_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"environment": environment, "count": len(live)},
        )
        return {"environment": environment, "retired": len(live)}

    def status(self, principal: Principal, agent_id: str) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.transaction() as session:
            agent = session.get(Agent, agent_id)
            if agent is None or agent.workspace_id != principal.workspace_id:
                raise NotFoundError("Agent not found")
            envs = self._ensure_environments(session, principal.workspace_id)
            versions = {
                v.id: v.version for v in session.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent_id))
            }
            history = list(
                session.scalars(
                    select(Deployment).where(Deployment.agent_id == agent_id).order_by(Deployment.created_at.desc())
                )
            )
            out = []
            for env in envs:
                rows = [
                    self._view(d, env.name, versions.get(d.agent_version_id, "?"))
                    for d in history
                    if d.environment_id == env.id
                ]
                out.append(
                    {
                        "environment": env.name,
                        "requires_approval": env.requires_approval,
                        "promote_from": env.promote_from,
                        "primary": next(
                            (r for r in rows if r["status"] == "active" and r["strategy"] == "standard"), None
                        ),
                        "canary": next(
                            (r for r in rows if r["status"] == "active" and r["strategy"] == "canary"), None
                        ),
                        "pending": [r for r in rows if r["status"] == "pending_approval"],
                        "history": rows[:20],
                    }
                )
            return {"agent_id": agent_id, "environments": out}

    # ================================================================== execution routing
    def resolve(self, workspace_id: str, agent_id: str, environment: str, routing_key: str) -> dict[str, Any]:
        """Which version serves this execution in this environment (canary share by a stable hash)."""
        with self.db.transaction() as session:
            env = self._env(session, workspace_id, environment)
            live = self._live(session, env.id, agent_id)
            primary = next((d for d in live if d.strategy == "standard"), None)
            canary = next((d for d in live if d.strategy == "canary"), None)
            chosen = canary if canary and _bucket(routing_key) < canary.traffic_percent else primary
            if chosen is None:
                raise InvalidStateError(f"Agent has no active deployment in '{environment}'")
            record = session.get(AgentArtifact, chosen.artifact_id)
            if record is None or not self._verify(session, record)["valid"]:
                raise InvalidStateError("Deployed artifact failed its integrity check; refusing to run it")
            version = session.get(AgentVersion, chosen.agent_version_id)
            assert version is not None
            return {
                "deployment_id": chosen.id,
                "environment": environment,
                "version": version.version,
                "canary": chosen.strategy == "canary",
                "artifact_hash": chosen.artifact_hash,
                "limits": (env.config or {}).get("limits", {}),
            }

    @staticmethod
    def tightened_limits(spec_limits: ExecutionLimits, env_limits: dict[str, Any]) -> dict[str, Any]:
        """Environment limits only lower numeric limits; they never raise or remove them."""
        current = spec_limits.model_dump()
        out = {}
        for key, value in env_limits.items():
            if not isinstance(value, int | float) or isinstance(value, bool):
                continue
            existing = current.get(key)
            out[key] = value if existing is None else min(existing, value)
        return out

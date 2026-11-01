"""Platform endpoints: probes, status, dashboard, identity, settings, secrets, API keys, audit, schemas."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ... import __version__
from ...core.errors import ValidationFailedError
from ...core.ids import utcnow
from ...spec.models import AgentSpec
from ...storage.models import Agent, AgentVersion, ApiKey, AuditEvent, ConfigMetadata, SecretRef, Workspace
from ..deps import PageDep, PlatformDep, PrincipalDep

router = APIRouter(tags=["platform"])


@router.get("/health", summary="Liveness probe (process is up)")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


@router.get("/ready", summary="Readiness probe (database, migrations, worker)")
def ready(platform: PlatformDep, response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        platform.db.ping()
        checks["database"] = "ok"
        migration = platform.migration_status()
        checks["migrations"] = "ok" if migration["up_to_date"] else "pending"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
    embedded = platform.settings.worker_mode.value == "embedded"
    checks["worker"] = ("ok" if platform.dispatcher.healthy() else "stopped") if embedded else "external"
    ok = checks.get("database") == "ok" and checks.get("migrations") == "ok" and checks["worker"] in {"ok", "external"}
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}


@router.get("/api/v1/system/status")
def system_status(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    s = platform.settings
    return {
        "version": __version__,
        "profile": s.profile.value,
        "auth_mode": s.auth_mode.value,
        "database": {"dialect": platform.db.dialect, **platform.migration_status()},
        "worker": {
            "mode": s.worker_mode.value,
            "healthy": platform.dispatcher.healthy(),
            "concurrency": s.worker_concurrency,
        },
        "providers": [h.model_dump() for h in platform.gateway.health()],
        "sandbox": {
            "backend": s.sandbox_backend.value,
            "available": platform.sandbox.available()[0],
            "detail": platform.sandbox.available()[1],
        },
        "telemetry": {"exporter": s.telemetry_exporter, "capture_content": s.capture_content},
        "connectors": {
            "network_allowlist": s.network_allowlist,
            "knowledge_dir": str(s.knowledge_dir),
            "sample_data_dir": str(s.sample_data_dir),
        },
        "audit": platform.audit.verify() if principal.can("audit:read") else None,
    }


@router.get("/api/v1/dashboard")
def dashboard(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("agents:read")
    with platform.db.session() as session:
        total = (
            session.scalar(
                select(func.count())
                .select_from(Agent)
                .where(Agent.workspace_id == principal.workspace_id, Agent.archived.is_(False))
            )
            or 0
        )
        published = (
            session.scalar(
                select(func.count(func.distinct(AgentVersion.agent_id))).where(
                    AgentVersion.workspace_id == principal.workspace_id, AgentVersion.status == "published"
                )
            )
            or 0
        )
        by_status = dict(
            session.execute(
                select(AgentVersion.status, func.count())
                .where(AgentVersion.workspace_id == principal.workspace_id)
                .group_by(AgentVersion.status)
            ).all()
        )
    stats = platform.executions.stats(principal)
    recent = platform.executions.list(principal, limit=10, mode="standard")
    pending = platform.approvals.list(principal, status="pending", limit=5)
    return {
        "agents": {"total": total, "published": published, "versions_by_status": by_status},
        "executions": {
            "active": sum(stats.get(k, 0) for k in ("queued", "running", "waiting_approval")),
            "failed": stats.get("failed", 0) + stats.get("budget_exceeded", 0),
            "completed": stats.get("completed", 0),
            "by_status": stats,
        },
        "recent_executions": recent["items"],
        "pending_approvals": pending,
        "evaluations": platform.evaluations.latest_status(principal),
        "providers": [h.model_dump() for h in platform.gateway.health()],
        "runtime": {
            "worker_healthy": platform.dispatcher.healthy(),
            "worker_mode": platform.settings.worker_mode.value,
            "database": platform.db.dialect,
        },
    }


@router.get("/api/v1/me")
def me(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    with platform.db.session() as session:
        ws = session.get(Workspace, principal.workspace_id)
    return {
        "user_id": principal.user_id,
        "subject": principal.subject,
        "display_name": principal.display_name,
        "role": principal.role,
        "auth_method": principal.auth_method,
        "workspace": {"id": ws.id, "slug": ws.slug, "name": ws.name} if ws else None,
        "permissions": sorted(principal.permissions),
    }


@router.get("/api/v1/schemas/agent-spec", summary="JSON Schema of the agentfactory/v1 specification")
def agent_spec_schema() -> dict[str, Any]:
    return AgentSpec.model_json_schema()


# ---------------------------------------------------------------------------- settings
@router.get("/api/v1/system/settings")
def get_settings_view(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("settings:read")
    with platform.db.session() as session:
        ws = session.get(Workspace, principal.workspace_id)
    return {
        "configuration": platform.settings.safe_dict(),
        "tools": {"disabled": platform.tools.disabled, "available": [d.name for d in platform.tools.definitions()]},
        "workspace": {"id": ws.id, "slug": ws.slug, "name": ws.name, "settings": ws.settings} if ws else None,
        "editable": ["tools.disabled", "workspace.name"],
        "note": "Most settings are environment-based (AF_* variables, see .env.example) and require a restart.",
    }


class ToolSettings(BaseModel):
    disabled: list[str]


@router.put("/api/v1/system/settings/tools")
def set_tool_settings(payload: ToolSettings, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("settings:write")
    platform.tools.set_disabled(sorted(set(payload.disabled) | set(platform.settings.disabled_tools)))
    with platform.db.transaction() as session:
        record = session.get(ConfigMetadata, "disabled_tools")
        if record is None:
            session.add(
                ConfigMetadata(key="disabled_tools", value={"tools": payload.disabled}, updated_by=principal.user_id)
            )
        else:
            record.value, record.updated_by = {"tools": payload.disabled}, principal.user_id
    platform.audit.record(
        "settings.tools",
        "settings",
        "tools",
        actor_id=principal.user_id,
        workspace_id=principal.workspace_id,
        details={"disabled": payload.disabled},
    )
    return {"disabled": platform.tools.disabled}


class WorkspaceSettings(BaseModel):
    name: str = Field(min_length=2, max_length=200)


@router.put("/api/v1/system/settings/workspace")
def set_workspace(payload: WorkspaceSettings, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("settings:write")
    with platform.db.transaction() as session:
        ws = session.get(Workspace, principal.workspace_id)
        assert ws is not None
        ws.name = payload.name
    return {"name": payload.name}


# ---------------------------------------------------------------------------- secrets
class SecretWrite(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    value: str = Field(min_length=1, max_length=20_000)


@router.get("/api/v1/secrets", summary="Secret names and status (values are never returned)")
def list_secrets(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("settings:read")
    with platform.db.session() as session:
        refs = session.scalars(select(SecretRef).where(SecretRef.workspace_id == principal.workspace_id)).all()
    return {
        "backend": platform.secrets.backend,
        "items": [
            {
                "name": r.name,
                "backend": r.backend,
                "configured": True,
                "last_rotated_at": r.last_rotated_at.isoformat() if r.last_rotated_at else None,
            }
            for r in refs
        ],
    }


@router.post("/api/v1/secrets", status_code=201)
def write_secret(payload: SecretWrite, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("settings:write")
    platform.secrets.set(payload.name, payload.value)
    with platform.db.transaction() as session:
        ref = session.scalars(
            select(SecretRef).where(SecretRef.workspace_id == principal.workspace_id, SecretRef.name == payload.name)
        ).first()
        if ref is None:
            session.add(
                SecretRef(
                    workspace_id=principal.workspace_id,
                    name=payload.name,
                    backend=platform.secrets.backend,
                    last_rotated_at=utcnow(),
                )
            )
        else:
            ref.last_rotated_at = utcnow()
    platform.audit.record(
        "secret.write", "secret", payload.name, actor_id=principal.user_id, workspace_id=principal.workspace_id
    )
    return {"name": payload.name, "configured": True}


# ---------------------------------------------------------------------------- API keys
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


@router.get("/api/v1/api-keys")
def list_keys(platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    with platform.db.session() as session:
        keys = session.scalars(select(ApiKey).where(ApiKey.user_id == principal.user_id)).all()
    return [
        {
            "id": k.id,
            "name": k.name,
            "prefix": k.prefix,
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked": k.revoked_at is not None,
        }
        for k in keys
    ]


@router.post("/api/v1/api-keys", status_code=201, summary="Create an API key for yourself (shown once)")
def create_key(payload: ApiKeyCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    if principal.auth_method in {"system", "evaluation"}:
        raise ValidationFailedError("API keys belong to real users")
    raw, record = platform.identity.create_api_key(principal.user_id, payload.name)
    platform.audit.record(
        "api_key.create", "api_key", record.id, actor_id=principal.user_id, workspace_id=principal.workspace_id
    )
    return {"id": record.id, "name": record.name, "key": raw, "warning": "Store this key now; it is not shown again."}


@router.delete("/api/v1/api-keys/{key_id}", status_code=204)
def revoke_key(key_id: str, platform: PlatformDep, principal: PrincipalDep) -> None:
    with platform.db.session() as session:
        key = session.get(ApiKey, key_id)
    if not key or (key.user_id != principal.user_id and not principal.can("admin:users")):
        from ...core.errors import NotFoundError

        raise NotFoundError("API key not found")
    platform.identity.revoke_api_key(key_id)
    platform.audit.record(
        "api_key.revoke", "api_key", key_id, actor_id=principal.user_id, workspace_id=principal.workspace_id
    )


# ---------------------------------------------------------------------------- audit & data protection
@router.get("/api/v1/audit")
def audit_events(
    platform: PlatformDep, principal: PrincipalDep, page: PageDep, action: str | None = None
) -> dict[str, Any]:
    principal.require("audit:read")
    with platform.db.session() as session:
        query = select(AuditEvent).where(
            (AuditEvent.workspace_id == principal.workspace_id) | AuditEvent.workspace_id.is_(None)
        )
        if action:
            query = query.where(AuditEvent.action.like(f"{action}%"))
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(query.order_by(AuditEvent.seq.desc()).limit(page.limit).offset(page.offset)).all()
    return {
        "total": total,
        "items": [
            {
                "seq": e.seq,
                "action": e.action,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "outcome": e.outcome,
                "actor_id": e.actor_id,
                "details": e.details,
                "correlation_id": e.correlation_id,
                "created_at": e.created_at.isoformat(),
                "hash": e.hash,
            }
            for e in rows
        ],
    }


@router.get("/api/v1/audit/verify")
def audit_verify(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("audit:read")
    return platform.audit.verify()


class PurgeRequest(BaseModel):
    older_than_days: int = Field(ge=1, le=3650)


@router.post("/api/v1/admin/retention/purge", summary="Delete terminal executions older than N days")
def purge(payload: PurgeRequest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("executions:delete")
    count = platform.executions.purge_older_than(payload.older_than_days)
    platform.audit.record(
        "retention.purge",
        "executions",
        None,
        actor_id=principal.user_id,
        workspace_id=principal.workspace_id,
        details={"older_than_days": payload.older_than_days, "deleted": count},
    )
    return {"deleted": count}

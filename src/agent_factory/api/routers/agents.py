"""Agents, versions, lifecycle, natural-language drafting and comparison."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field

from ...core.errors import ValidationFailedError
from ...registry.agents import version_dict
from ...runtime.graph import describe_workflow
from ...spec.loader import dump_yaml, load_spec
from ...spec.models import AgentSpec
from ..deps import PageDep, PlatformDep, PrincipalDep, idempotent

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


class SpecPayload(BaseModel):
    spec: dict[str, Any] | None = Field(None, description="agentfactory/v1 document as JSON")
    spec_yaml: str | None = Field(None, max_length=500_000, description="agentfactory/v1 document as YAML/JSON text")
    template: str | None = Field(None, description="Create from a template (name[@version])")
    name: str | None = None
    objective: str | None = None
    release_notes: str = Field("", max_length=4000)

    def to_spec(self, platform: Any, workspace_id: str) -> AgentSpec:
        if self.spec is not None:
            return load_spec(self.spec)
        if self.spec_yaml:
            return load_spec(self.spec_yaml)
        if self.template and self.name:
            return platform.templates.instantiate(workspace_id, self.template, name=self.name, objective=self.objective)
        raise ValidationFailedError("Provide 'spec', 'spec_yaml', or 'template' with 'name'")


class UpdatePayload(SpecPayload):
    bump: Literal["major", "minor", "patch"] = "minor"


class DraftPayload(BaseModel):
    objective: str = Field(min_length=8, max_length=4000)
    name: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{1,62}$")
    template: str | None = None
    provider: str | None = None
    model: str | None = None


class VersionPayload(BaseModel):
    version: str | None = None
    release_notes: str | None = Field(None, max_length=4000)


class RollbackPayload(BaseModel):
    version: str


class DuplicatePayload(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")


@router.post("/draft", summary="Generate a draft specification from a natural-language requirement")
def draft(payload: DraftPayload, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("agents:write")
    result = platform.interpreter.interpret(
        principal.workspace_id,
        payload.objective,
        name=payload.name,
        template=payload.template,
        provider=payload.provider,
        model=payload.model,
    )
    out = result.to_dict()
    out["validation"] = platform.validator.validate(
        result.spec, platform.agents.resolver(principal.workspace_id)
    ).to_dict()
    return out


@router.post("/preview", summary="Validate a specification without saving it")
def preview(payload: SpecPayload, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("agents:read")
    spec = payload.to_spec(platform, principal.workspace_id)
    report = platform.validator.validate(spec, platform.agents.resolver(principal.workspace_id))
    return {
        "spec": spec.to_document(),
        "yaml": dump_yaml(spec.to_document()),
        "validation": report.to_dict(),
        "workflow": describe_workflow(spec),
    }


@router.post("", status_code=201, summary="Register a new agent (creates a draft version)")
def create_agent(
    payload: SpecPayload,
    platform: PlatformDep,
    principal: PrincipalDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    def compute() -> tuple[int, dict[str, Any]]:
        spec = payload.to_spec(platform, principal.workspace_id)
        return 201, platform.agents.create(
            principal, spec, release_notes=payload.release_notes, created_via="template" if payload.template else "api"
        )

    status, body = idempotent(platform, principal, "POST /agents", idempotency_key, payload.model_dump(), compute)
    response.status_code = status
    return body


@router.get("", summary="List agents")
def list_agents(
    platform: PlatformDep, principal: PrincipalDep, page: PageDep, q: str | None = None, status: str | None = None
) -> dict[str, Any]:
    return platform.agents.list(principal, q=q, status=status, limit=page.limit, offset=page.offset)


@router.get("/{agent_id}", summary="Agent detail with latest specification and version history")
def get_agent(agent_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.agents.get(principal, agent_id)


@router.put("/{agent_id}", summary="Edit an agent (new version if the latest is immutable)")
def update_agent(
    agent_id: str, payload: UpdatePayload, platform: PlatformDep, principal: PrincipalDep
) -> dict[str, Any]:
    spec = payload.to_spec(platform, principal.workspace_id)
    return platform.agents.update(principal, agent_id, spec, release_notes=payload.release_notes, bump=payload.bump)


@router.post("/{agent_id}/validate")
def validate(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, payload: VersionPayload | None = None
) -> dict[str, Any]:
    return platform.agents.validate(principal, agent_id, (payload or VersionPayload()).version)


@router.post("/{agent_id}/build")
def build(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, payload: VersionPayload | None = None
) -> dict[str, Any]:
    return platform.agents.build(principal, agent_id, (payload or VersionPayload()).version)


@router.post("/{agent_id}/publish")
def publish(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, payload: VersionPayload | None = None
) -> dict[str, Any]:
    p = payload or VersionPayload()
    return platform.agents.publish(principal, agent_id, p.version, p.release_notes)


@router.post("/{agent_id}/deprecate")
def deprecate(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, payload: VersionPayload | None = None
) -> dict[str, Any]:
    return platform.agents.deprecate(principal, agent_id, (payload or VersionPayload()).version)


@router.post("/{agent_id}/disable")
def disable(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, payload: VersionPayload | None = None
) -> dict[str, Any]:
    return platform.agents.disable(principal, agent_id, (payload or VersionPayload()).version)


@router.post("/{agent_id}/enable")
def enable(agent_id: str, payload: RollbackPayload, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.agents.enable(principal, agent_id, payload.version)


@router.post("/{agent_id}/duplicate", status_code=201)
def duplicate(
    agent_id: str, payload: DuplicatePayload, platform: PlatformDep, principal: PrincipalDep
) -> dict[str, Any]:
    return platform.agents.duplicate(principal, agent_id, payload.name)


@router.get("/{agent_id}/versions")
def versions(agent_id: str, platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    return platform.agents.versions(principal, agent_id)


@router.get("/{agent_id}/versions/{version}")
def get_version(agent_id: str, version: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.agents.get_version(principal, agent_id, version)


@router.post("/{agent_id}/rollback")
def rollback(agent_id: str, payload: RollbackPayload, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.agents.rollback(principal, agent_id, payload.version)


@router.get("/{agent_id}/compare")
def compare(agent_id: str, base: str, other: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.agents.compare(principal, agent_id, base, other)


@router.get("/{agent_id}/workflow", summary="Workflow graph for visualization")
def workflow(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, version: str | None = None
) -> dict[str, Any]:
    principal.require("agents:read")
    _agent, v = platform.agents.load_version(principal, agent_id, version)
    graph = describe_workflow(load_spec(v.spec))
    return {"version": version_dict(v, include_spec=False), **graph}

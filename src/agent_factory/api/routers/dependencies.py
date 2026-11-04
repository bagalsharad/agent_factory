"""Dependency graph: what agents depend on, who depends on a component, impact analysis and revalidation."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1", tags=["dependencies"])

Kind = Literal["tool", "model", "model_profile", "component", "agent", "evaluation_suite"]


class ImpactRequest(BaseModel):
    kind: Kind
    name: str = Field(
        min_length=1, max_length=200, description="e.g. http_get, policy_definition/read-only, mock/mock-default"
    )
    to_version: str | None = Field(None, description="The version being introduced, for compatibility checks")
    change: str = Field("", max_length=1000, description="What is changing (recorded in the audit log)")


class RevalidateRequest(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=200)


class Ownership(BaseModel):
    business_owner: str | None = Field(None, max_length=200)
    technical_contact: str | None = Field(None, max_length=200)
    escalation_contact: str | None = Field(None, max_length=200)


@router.get("/agents/{agent_id}/dependencies", summary="Dependencies of an agent version, with drift and deprecations")
def agent_dependencies(
    agent_id: str, platform: PlatformDep, principal: PrincipalDep, version: str | None = None
) -> dict[str, Any]:
    _agent, record = platform.agents.load_version(principal, agent_id, version)
    return {"version": record.version, **platform.dependencies.dependencies(principal, agent_id, record.id)}


@router.get(
    "/dependencies/dependents", summary="Agent versions that depend on a tool, model, component, agent or suite"
)
def dependents(
    kind: Kind, name: str, platform: PlatformDep, principal: PrincipalDep, version: str | None = None
) -> dict[str, Any]:
    items = platform.dependencies.dependents(principal, kind, name, version)
    return {"kind": kind, "name": name, "items": items, "total": len(items)}


@router.post("/dependencies/impact", summary="Impact report for a dependency change (affected agents, risk, plan)")
def impact(payload: ImpactRequest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("agents:read")
    return platform.dependencies.impact(
        principal, payload.kind, payload.name, to_version=payload.to_version, change=payload.change
    )


@router.post("/dependencies/revalidate", status_code=202, summary="Re-evaluate every affected version in service")
def revalidate(payload: RevalidateRequest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.dependencies.revalidate(principal, payload.kind, payload.name)


@router.put("/agents/{agent_id}/ownership", summary="Business owner, technical and escalation contacts")
def set_ownership(agent_id: str, payload: Ownership, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.agents.set_ownership(principal, agent_id, payload.model_dump(exclude_unset=True))

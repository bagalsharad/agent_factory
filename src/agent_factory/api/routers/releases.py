"""Release management: artifacts, environments, deployments, canary promotion, approval and rollback."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1", tags=["releases"])


class DeployRequest(BaseModel):
    version: str
    environment: str = Field(min_length=1, max_length=40)
    strategy: Literal["standard", "canary"] = "standard"
    traffic_percent: int = Field(100, ge=1, le=100, description="Canary share of traffic (1-50)")
    notes: str = Field("", max_length=2000)


class Decision(BaseModel):
    approve: bool
    comment: str = Field("", max_length=1000)


class Rollback(BaseModel):
    reason: str = Field("", max_length=500)


class EnvironmentConfig(BaseModel):
    limits: dict[str, Any] | None = Field(None, description="Execution limits to tighten in this environment")
    requires_approval: bool | None = None


@router.get("/agents/{agent_id}/versions/{version}/artifact", summary="Immutable release artifact and its verification")
def artifact(agent_id: str, version: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.releases.artifact(principal, agent_id, version)


@router.get("/environments")
def environments(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return {"items": platform.releases.environments(principal)}


@router.put("/environments/{name}")
def configure_environment(
    name: str, payload: EnvironmentConfig, platform: PlatformDep, principal: PrincipalDep
) -> dict[str, Any]:
    return platform.releases.configure_environment(
        principal, name, limits=payload.limits, requires_approval=payload.requires_approval
    )


@router.get("/agents/{agent_id}/deployments", summary="What runs where, pending approvals and history")
def deployment_status(agent_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.releases.status(principal, agent_id)


@router.post("/agents/{agent_id}/deployments", status_code=201, summary="Deploy a published version to an environment")
def deploy(agent_id: str, payload: DeployRequest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.releases.deploy(
        principal,
        agent_id,
        payload.version,
        payload.environment,
        strategy=payload.strategy,
        traffic_percent=payload.traffic_percent,
        notes=payload.notes,
    )


@router.post("/deployments/{deployment_id}/decision", summary="Approve or reject a deployment (separation of duties)")
def decide(deployment_id: str, payload: Decision, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.releases.decide(principal, deployment_id, payload.approve, payload.comment)


@router.post("/agents/{agent_id}/environments/{environment}/promote-canary")
def promote(agent_id: str, environment: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.releases.promote_canary(principal, agent_id, environment)


@router.post("/agents/{agent_id}/environments/{environment}/rollback")
def rollback(
    agent_id: str, environment: str, payload: Rollback, platform: PlatformDep, principal: PrincipalDep
) -> dict[str, Any]:
    return platform.releases.rollback(principal, agent_id, environment, payload.reason)


@router.post("/agents/{agent_id}/environments/{environment}/retire")
def retire(agent_id: str, environment: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.releases.retire(principal, agent_id, environment)

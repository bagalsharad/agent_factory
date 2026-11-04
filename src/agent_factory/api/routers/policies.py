"""Policy simulation and policy history (policy changes are versioned with the agent specification)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1", tags=["policies"])


class SimulateRequest(BaseModel):
    agent_id: str
    version: str | None = None
    tool: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    as_role: str | None = Field(None, description="Evaluate as another role (admin, developer, operator, ...)")


@router.post("/policies/simulate", summary="What the policy engine would decide for a tool call (nothing runs)")
def simulate(payload: SimulateRequest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.policy_simulator.simulate(
        principal, payload.agent_id, payload.tool, payload.arguments, version=payload.version, as_role=payload.as_role
    )


@router.get("/agents/{agent_id}/policies", summary="Effective policy of every version and what changed")
def history(agent_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.policy_simulator.history(principal, agent_id)

"""Operations: metrics, agent health, provider health, SLOs, drift, cost budgets and alerts."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1", tags=["operations"])
Hours = Query(24, ge=1, le=24 * 90, description="Window in hours")


class SloCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    metric: Literal["success_rate", "error_rate", "p95_latency_ms", "evaluation_pass_rate"]
    objective: float = Field(description="Ratio 0 to 1 for rates, milliseconds for latency")
    agent_id: str | None = None
    window_hours: int = Field(168, ge=1, le=24 * 90)


class BudgetCreate(BaseModel):
    agent_id: str | None = Field(None, description="Omit for a workspace-wide budget")
    period: Literal["day", "month"] = "month"
    max_cost_usd: float | None = Field(None, ge=0)
    max_tokens: int | None = Field(None, ge=0)
    action: Literal["block", "warn"] = "block"
    warn_at: float = Field(0.8, gt=0, le=1)


@router.get("/operations/metrics", summary="Success, failure, retry and timeout rates, latency, tokens, cost, tools")
def metrics(
    platform: PlatformDep, principal: PrincipalDep, agent_id: str | None = None, hours: int = Hours
) -> dict[str, Any]:
    return platform.operations.metrics(principal, agent_id=agent_id, hours=hours)


@router.get("/operations/timeseries", summary="Executions per time bucket by outcome, p95 latency and tokens")
def timeseries(
    platform: PlatformDep,
    principal: PrincipalDep,
    agent_id: str | None = None,
    hours: int = Hours,
    buckets: int = Query(24, ge=1, le=96),
) -> dict[str, Any]:
    return platform.operations.timeseries(principal, agent_id=agent_id, hours=hours, buckets=buckets)


@router.get("/agents/{agent_id}/health", summary="Health of one agent: metrics, SLOs, alerts and drift")
def agent_health(agent_id: str, platform: PlatformDep, principal: PrincipalDep, hours: int = Hours) -> dict[str, Any]:
    return platform.operations.agent_health(principal, agent_id, hours)


@router.get("/operations/providers", summary="Model provider health and circuit-breaker state")
def providers(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.operations.providers(principal)


@router.get("/operations/slos")
def list_slos(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return {"items": platform.operations.slo_status(principal)}


@router.post("/operations/slos", status_code=201)
def create_slo(payload: SloCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.operations.create_slo(principal, **payload.model_dump())


@router.delete("/operations/slos/{slo_id}", status_code=204)
def delete_slo(slo_id: str, platform: PlatformDep, principal: PrincipalDep) -> None:
    platform.operations.delete_slo(principal, slo_id)


@router.get("/operations/drift", summary="Metrics that moved ≥50% between the last two windows")
def drift(
    platform: PlatformDep, principal: PrincipalDep, agent_id: str | None = None, hours: int = Hours
) -> dict[str, Any]:
    return {"items": platform.operations.drift(principal, agent_id=agent_id, hours=hours), "window_hours": hours}


@router.get("/operations/budgets")
def list_budgets(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return {"items": platform.operations.budget_status(principal)}


@router.post("/operations/budgets", status_code=201)
def create_budget(payload: BudgetCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.operations.create_budget(principal, **payload.model_dump())


@router.delete("/operations/budgets/{budget_id}", status_code=204)
def delete_budget(budget_id: str, platform: PlatformDep, principal: PrincipalDep) -> None:
    platform.operations.delete_budget(principal, budget_id)


@router.get("/operations/alerts", summary="Conditions that need an operator right now")
def alerts(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    items = platform.operations.alerts(principal)
    return {"items": items, "critical": sum(1 for a in items if a["severity"] == "critical")}

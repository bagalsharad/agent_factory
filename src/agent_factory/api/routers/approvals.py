"""Human approvals."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..deps import PageDep, PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


class Decision(BaseModel):
    comment: str = Field("", max_length=2000)


@router.get("")
def list_approvals(
    platform: PlatformDep,
    principal: PrincipalDep,
    page: PageDep,
    status: str | None = None,
    execution_id: str | None = None,
) -> dict[str, Any]:
    return platform.approvals.list(
        principal, status=status, execution_id=execution_id, limit=page.limit, offset=page.offset
    )


@router.get("/{approval_id}")
def get_approval(approval_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.approvals.get(principal, approval_id)


@router.post("/{approval_id}/approve")
def approve(
    approval_id: str, platform: PlatformDep, principal: PrincipalDep, payload: Decision | None = None
) -> dict[str, Any]:
    return platform.approvals.decide(principal, approval_id, "approved", (payload or Decision()).comment)


@router.post("/{approval_id}/reject")
def reject(
    approval_id: str, platform: PlatformDep, principal: PrincipalDep, payload: Decision | None = None
) -> dict[str, Any]:
    return platform.approvals.decide(principal, approval_id, "rejected", (payload or Decision()).comment)

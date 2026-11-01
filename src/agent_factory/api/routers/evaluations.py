"""Evaluation runs, suites, human review and version comparison."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import PageDep, PlatformDep, PrincipalDep, rate_limited

router = APIRouter(prefix="/api/v1", tags=["evaluations"])


class EvaluationCreate(BaseModel):
    agent_id: str
    version: str | None = None
    suite_id: str | None = None
    wait: bool = Field(False, description="Run synchronously (CI/tests). Default: run in the background.")


class SuiteCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_\-]{1,119}$")
    agent_id: str | None = None
    description: str = ""
    acceptance: dict[str, Any] = Field(default_factory=dict)
    cases: list[dict[str, Any]] = Field(min_length=1)


class Review(BaseModel):
    passed: bool
    notes: str = Field("", max_length=2000)


class CompareRequest(BaseModel):
    agent_id: str
    base: str
    other: str
    suite_id: str | None = None


@router.post("/evaluations", status_code=202, dependencies=[Depends(rate_limited("evaluations"))])
def create_evaluation(payload: EvaluationCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.evaluations.start_run(
        principal, payload.agent_id, version=payload.version, suite_id=payload.suite_id, wait=payload.wait
    )


@router.get("/evaluations")
def list_evaluations(
    platform: PlatformDep, principal: PrincipalDep, page: PageDep, agent_id: str | None = None
) -> dict[str, Any]:
    return platform.evaluations.list_runs(principal, agent_id=agent_id, limit=page.limit, offset=page.offset)


@router.get("/evaluations/{run_id}")
def get_evaluation(run_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.evaluations.get_run(principal, run_id)


@router.post("/evaluations/{run_id}/results/{result_id}/review")
def review(
    run_id: str, result_id: str, payload: Review, platform: PlatformDep, principal: PrincipalDep
) -> dict[str, Any]:
    return platform.evaluations.review(principal, run_id, result_id, payload.passed, payload.notes)


@router.post("/evaluations/compare", summary="Run one suite against two versions and diff the results")
def compare(payload: CompareRequest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("evaluations:run")
    return platform.evaluations.compare_versions(
        principal, payload.agent_id, payload.base, payload.other, payload.suite_id
    )


@router.post("/evaluation-suites", status_code=201)
def create_suite(payload: SuiteCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.evaluations.create_suite(
        principal,
        name=payload.name,
        cases=payload.cases,
        acceptance=payload.acceptance,
        agent_id=payload.agent_id,
        description=payload.description,
    )


@router.get("/evaluation-suites")
def list_suites(platform: PlatformDep, principal: PrincipalDep, agent_id: str | None = None) -> list[dict[str, Any]]:
    return platform.evaluations.list_suites(principal, agent_id)


@router.get("/evaluation-suites/{suite_id}")
def get_suite(suite_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.evaluations.get_suite(principal, suite_id)

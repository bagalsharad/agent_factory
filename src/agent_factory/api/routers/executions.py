"""Executions: create (async), list/filter, detail, events (JSON + SSE), cancel, recover, delete."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...core.logging import correlation_id_var
from ..deps import PageDep, PlatformDep, PrincipalDep, rate_limited

router = APIRouter(prefix="/api/v1/executions", tags=["executions"])
TERMINAL = {"completed", "failed", "cancelled", "budget_exceeded", "rejected"}


class ExecutionCreate(BaseModel):
    agent_id: str
    version: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "",
    status_code=202,
    dependencies=[Depends(rate_limited("executions"))],
    summary="Queue an execution (returns immediately; poll or stream events)",
)
def create_execution(
    payload: ExecutionCreate,
    platform: PlatformDep,
    principal: PrincipalDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    result = platform.executions.create(
        principal,
        payload.agent_id,
        input_data=payload.input,
        version=payload.version,
        options=payload.options,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id_var.get(),
    )
    if result.get("idempotent_replay"):
        response.status_code = 200
    return result


@router.get("")
def list_executions(
    platform: PlatformDep,
    principal: PrincipalDep,
    page: PageDep,
    agent_id: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    q: str | None = None,
    include_children: bool = False,
    mode: str | None = None,
) -> dict[str, Any]:
    return platform.executions.list(
        principal,
        agent_id=agent_id,
        status=status,
        since=since,
        until=until,
        q=q,
        include_children=include_children,
        mode=mode,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{execution_id}")
def get_execution(execution_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.executions.get(principal, execution_id)


@router.get("/{execution_id}/events")
def events(execution_id: str, platform: PlatformDep, principal: PrincipalDep, after: int = 0) -> dict[str, Any]:
    return platform.executions.events(principal, execution_id, after=after)


@router.get("/{execution_id}/events/stream", summary="Server-sent events stream of execution events")
async def stream_events(
    execution_id: str, request: Request, platform: PlatformDep, principal: PrincipalDep
) -> StreamingResponse:
    platform.executions.events(principal, execution_id, after=0, limit=1)  # access check

    async def generate() -> AsyncIterator[bytes]:
        after = 0
        idle = 0
        while not await request.is_disconnected():
            batch = await asyncio.to_thread(platform.executions.events, principal, execution_id, after)
            for item in batch["items"]:
                after = item["seq"]
                yield f"id: {item['seq']}\nevent: {item['type']}\ndata: {json.dumps(item)}\n\n".encode()
            if batch["status"] in TERMINAL and not batch["items"]:
                yield b"event: end\ndata: {}\n\n"
                return
            idle = 0 if batch["items"] else idle + 1
            if idle and idle % 15 == 0:
                yield b": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/{execution_id}/cancel")
def cancel(execution_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.executions.cancel(principal, execution_id)


@router.post("/{execution_id}/retry", summary="Recover a failed execution from its last checkpoint")
def retry(execution_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    return platform.executions.retry(principal, execution_id)


@router.delete("/{execution_id}", status_code=204)
def delete_execution(execution_id: str, platform: PlatformDep, principal: PrincipalDep) -> Response:
    platform.executions.delete(principal, execution_id)
    return Response(status_code=204)

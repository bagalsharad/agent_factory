"""Request dependencies: platform access, authentication, pagination, idempotency and rate limiting."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..core.errors import ConflictError, RateLimitedError
from ..core.ids import content_hash
from ..governance.rbac import Principal
from ..platform import Platform
from ..storage.models import IdempotencyRecord


def get_platform(request: Request) -> Platform:
    platform: Platform = request.app.state.platform
    return platform


PlatformDep = Annotated[Platform, Depends(get_platform)]


def get_principal(
    platform: PlatformDep,
    authorization: Annotated[str | None, Header()] = None,
    x_workspace_id: Annotated[str | None, Header()] = None,
) -> Principal:
    return platform.identity.authenticate(authorization, x_workspace_id)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


class Page:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=1_000_000)) -> None:
        self.limit = limit
        self.offset = offset


PageDep = Annotated[Page, Depends(Page)]


class RateLimiter:
    """In-process sliding-window limiter keyed by principal + action. (Per process; see deployment docs.)"""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        if limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                raise RateLimitedError(
                    f"Rate limit exceeded: {limit} requests per {int(window)}s",
                    details={"retry_after_seconds": int(window - (now - hits[0])) + 1},
                )
            hits.append(now)


rate_limiter = RateLimiter()


def rate_limited(action: str) -> Callable[..., None]:
    def dependency(platform: PlatformDep, principal: PrincipalDep) -> None:
        rate_limiter.check(f"{principal.user_id}:{action}", platform.settings.execution_rate_limit_per_minute)

    return dependency


def idempotent(
    platform: Platform,
    principal: Principal,
    scope: str,
    key: str | None,
    body: Any,
    compute: Callable[[], tuple[int, dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    """Replay the stored response for a repeated Idempotency-Key; reject key reuse with a different body."""
    if not key:
        return compute()
    if len(key) > 200:
        raise ConflictError("Idempotency-Key is too long")
    request_hash = content_hash(body)
    with platform.db.session() as session:
        record = session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.principal_id == principal.user_id,
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
            )
        ).first()
        if record:
            if record.request_hash != request_hash:
                raise ConflictError("Idempotency-Key was already used with a different request body")
            return record.response_status, {**record.response_body, "idempotent_replay": True}
    status, response = compute()
    try:
        with platform.db.transaction() as session:
            session.add(
                IdempotencyRecord(
                    principal_id=principal.user_id,
                    scope=scope,
                    key=key,
                    request_hash=request_hash,
                    response_status=status,
                    response_body=response,
                )
            )
    except IntegrityError:
        pass  # a concurrent identical request stored first; both callers got equivalent results
    return status, response

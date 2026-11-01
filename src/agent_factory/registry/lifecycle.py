"""Agent version lifecycle state machine.

    draft ──validate+build──▶ validating ──ok──▶ testing ──evaluation passes──▶ approved ──publish──▶ published
      ▲                          │                  │ ▲                            │                     │
      │ edit                     └──fail──▶ failed  │ └──evaluation fails──────────┘                     ▼
      └──────────────────────────────────────────────┘                                              deprecated
    any state ──disable──▶ disabled ──re-enable──▶ testing (requires re-evaluation)

Approved, published and deprecated versions are immutable: editing them creates a new version.
"""

from __future__ import annotations

from ..core.errors import InvalidStateError
from ..spec.models import LifecycleStatus as S

TRANSITIONS: dict[S, set[S]] = {
    S.DRAFT: {S.VALIDATING, S.FAILED, S.DISABLED},
    S.VALIDATING: {S.TESTING, S.FAILED, S.DRAFT},
    S.FAILED: {S.DRAFT, S.VALIDATING, S.DISABLED},
    S.TESTING: {S.DRAFT, S.VALIDATING, S.APPROVED, S.FAILED, S.DISABLED},
    S.APPROVED: {S.PUBLISHED, S.TESTING, S.DISABLED},
    S.PUBLISHED: {S.DEPRECATED, S.DISABLED},
    S.DEPRECATED: {S.PUBLISHED, S.DISABLED},
    S.DISABLED: {S.TESTING},
}

MUTABLE = {S.DRAFT, S.FAILED, S.TESTING, S.VALIDATING}
IMMUTABLE = {S.APPROVED, S.PUBLISHED, S.DEPRECATED, S.DISABLED}


def can_transition(current: str, target: str) -> bool:
    return S(target) in TRANSITIONS.get(S(current), set())


def assert_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidStateError(
            f"Cannot move an agent version from '{current}' to '{target}'",
            details={
                "from": current,
                "to": target,
                "allowed": sorted(t.value for t in TRANSITIONS.get(S(current), set())),
            },
        )


def is_mutable(status: str) -> bool:
    return S(status) in MUTABLE

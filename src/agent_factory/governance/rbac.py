"""Role-based access control. Enforced in the backend for every protected action."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..core.errors import PermissionDeniedError


class Role(StrEnum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    EVALUATOR = "evaluator"
    VIEWER = "viewer"


READ = {
    "agents:read",
    "executions:read",
    "approvals:read",
    "evaluations:read",
    "tools:read",
    "models:read",
    "templates:read",
    "components:read",
    "knowledge:read",
    "settings:read",
}

ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.VIEWER: set(READ),
    Role.EVALUATOR: READ | {"evaluations:run", "evaluations:write", "executions:run", "tools:invoke"},
    Role.OPERATOR: READ
    | {
        "executions:run",
        "executions:cancel",
        "approvals:decide",
        "tools:invoke",
        "tools:invoke:write",
        "agents:disable",
        "models:test",
    },
    Role.DEVELOPER: READ
    | {
        "agents:write",
        "agents:publish",
        "executions:run",
        "executions:cancel",
        "evaluations:run",
        "evaluations:write",
        "tools:invoke",
        "tools:invoke:write",
        "templates:write",
        "components:write",
        "knowledge:write",
        "models:test",
    },
    Role.ADMIN: {"*"},
}

ALL_PERMISSIONS = sorted(
    {p for perms in ROLE_PERMISSIONS.values() for p in perms if p != "*"}
    | {"agents:disable", "approvals:decide", "settings:write", "audit:read", "admin:users", "executions:delete"}
)


def permissions_for(role: str) -> set[str]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return set()


@dataclass(frozen=True)
class Principal:
    user_id: str
    subject: str
    display_name: str
    workspace_id: str
    role: str
    auth_method: str
    permissions: frozenset[str] = field(default_factory=frozenset)

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise PermissionDeniedError(
                f"Role '{self.role}' lacks permission '{permission}'", details={"permission": permission}
            )


def make_principal(
    user_id: str, subject: str, display_name: str, workspace_id: str, role: str, auth_method: str
) -> Principal:
    return Principal(user_id, subject, display_name, workspace_id, role, auth_method, frozenset(permissions_for(role)))


SYSTEM_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000000"

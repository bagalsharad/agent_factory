"""Application error hierarchy mapped to consistent API error responses."""

from __future__ import annotations

from typing import Any


class AgentFactoryError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: Any = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFoundError(AgentFactoryError):
    status_code = 404
    code = "not_found"


class ConflictError(AgentFactoryError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AgentFactoryError):
    status_code = 422
    code = "validation_failed"


class AuthenticationError(AgentFactoryError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AgentFactoryError):
    status_code = 403
    code = "permission_denied"


class ToolOutcomeUnknownError(AgentFactoryError):
    """A side-effecting tool call was interrupted (e.g. worker crash); it may or may not have taken effect.

    The execution stops instead of repeating the call; an operator confirms the outcome before it can continue.
    """

    status_code = 409
    code = "in_doubt"


class InvalidStateError(AgentFactoryError):
    """An operation is not allowed in the resource's current lifecycle state."""

    status_code = 409
    code = "invalid_state"


class RateLimitedError(AgentFactoryError):
    status_code = 429
    code = "rate_limited"


class PolicyDeniedError(AgentFactoryError):
    status_code = 403
    code = "policy_denied"


class BudgetExceededError(AgentFactoryError):
    status_code = 409
    code = "budget_exceeded"

    def __init__(self, limit: str, used: float, maximum: float) -> None:
        super().__init__(f"Execution budget exceeded: {limit} used {used} of {maximum}.")
        self.limit = limit
        self.details = {"limit": limit, "used": used, "maximum": maximum}


class ExecutionCancelledError(AgentFactoryError):
    status_code = 409
    code = "cancelled"


class ProviderError(AgentFactoryError):
    """A model provider failed. ``retryable`` marks transient failures."""

    status_code = 502
    code = "provider_error"

    def __init__(self, message: str, *, retryable: bool = False, details: Any = None) -> None:
        super().__init__(message, details=details)
        self.retryable = retryable


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class ToolError(AgentFactoryError):
    status_code = 500
    code = "tool_error"

    def __init__(self, message: str, *, retryable: bool = False, details: Any = None) -> None:
        super().__init__(message, details=details)
        self.retryable = retryable


class SandboxUnavailableError(ToolError):
    code = "sandbox_unavailable"

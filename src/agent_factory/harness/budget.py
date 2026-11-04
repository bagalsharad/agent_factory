"""Execution budgets: steps, model calls, tool calls, retries, tokens, cost, duration and delegations.

Usage is persisted on the execution record after every step, so budgets survive pauses and worker
restarts. Wall-clock time spent waiting for human approval does not count against ``timeout_seconds``.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.errors import BudgetExceededError
from ..spec.models import ExecutionLimits

COUNTERS = ("steps", "model_calls", "tool_calls", "retries", "input_tokens", "output_tokens", "delegations")


class BudgetTracker:
    def __init__(self, limits: ExecutionLimits, usage: dict[str, Any] | None = None) -> None:
        self.limits = limits
        self.usage: dict[str, Any] = {k: 0 for k in COUNTERS} | {"cost_usd": 0.0, "active_seconds": 0.0}
        self.usage.update(usage or {})
        self._segment_start = time.monotonic()

    # ------------------------------------------------------------------ time
    def elapsed(self) -> float:
        return float(self.usage["active_seconds"]) + (time.monotonic() - self._segment_start)

    def remaining_seconds(self) -> float:
        return max(0.0, self.limits.timeout_seconds - self.elapsed())

    def close_segment(self) -> None:
        self.usage["active_seconds"] = round(self.elapsed(), 3)
        self._segment_start = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        data = dict(self.usage)
        data["active_seconds"] = round(self.elapsed(), 3)
        return data

    # ------------------------------------------------------------------ checks
    def check_time(self) -> None:
        if self.elapsed() > self.limits.timeout_seconds:
            raise BudgetExceededError("timeout_seconds", round(self.elapsed(), 2), self.limits.timeout_seconds)

    def _check(self, name: str, used: float, maximum: float | None) -> None:
        if maximum is not None and used > maximum:
            raise BudgetExceededError(name, used, maximum)

    def start_step(self) -> None:
        self.check_time()
        self.usage["steps"] += 1
        self._check("max_steps", self.usage["steps"], self.limits.max_steps)

    def before_model_call(self) -> None:
        self.check_time()
        self._check("max_model_calls", self.usage["model_calls"] + 1, self.limits.max_model_calls)
        if self.limits.max_cost_usd is not None:
            self._check("max_cost_usd", self.usage["cost_usd"], self.limits.max_cost_usd)

    def record_model_call(self, input_tokens: int | None, output_tokens: int | None, cost: float | None) -> None:
        self.usage["model_calls"] += 1
        self.usage["input_tokens"] += input_tokens or 0
        self.usage["output_tokens"] += output_tokens or 0
        self.usage["cost_usd"] = round(self.usage["cost_usd"] + (cost or 0.0), 6)
        if self.limits.max_cost_usd is not None:
            self._check("max_cost_usd", self.usage["cost_usd"], self.limits.max_cost_usd)

    def before_tool_call(self) -> None:
        self.check_time()
        self._check("max_tool_calls", self.usage["tool_calls"] + 1, self.limits.max_tool_calls)

    def record_tool_call(self) -> None:
        self.usage["tool_calls"] += 1

    def record_retry(self) -> None:
        self.usage["retries"] += 1
        self._check("max_total_retries", self.usage["retries"], self.limits.max_total_retries)

    def before_delegation(self) -> None:
        self.check_time()
        self._check("max_subagents", self.usage["delegations"] + 1, self.limits.max_subagents)

    def record_delegation(self) -> None:
        self.usage["delegations"] += 1

    def model_timeout(self, configured: float) -> float:
        """Never let a single model call outlive the execution's remaining time."""
        return max(1.0, min(configured, self.remaining_seconds()))

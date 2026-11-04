"""Circuit breakers for model providers and network tools.

closed ──N consecutive failures──▶ open ──recovery window──▶ half-open ──trial succeeds──▶ closed
                                    ▲                            │
                                    └──────trial fails───────────┘

While open, calls fail fast instead of piling up timeouts on a dependency that is down. State is per process
(each API/worker process learns independently); it is exposed through ``/api/v1/system/circuits``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False
        self.totals = {"successes": 0, "failures": 0, "rejected": 0, "opened": 0}

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._advance()
            return self._state

    def _advance(self) -> None:
        if self._state == CircuitState.OPEN and self._clock() - self._opened_at >= self.recovery_seconds:
            self._state, self._trial_in_flight = CircuitState.HALF_OPEN, False

    def allow(self) -> bool:
        """Whether a call may proceed. In half-open state exactly one trial call is let through."""
        with self._lock:
            self._advance()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN and not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            self.totals["rejected"] += 1
            return False

    def retry_after(self) -> float:
        with self._lock:
            return max(0.0, self.recovery_seconds - (self._clock() - self._opened_at))

    def record_success(self) -> None:
        with self._lock:
            self.totals["successes"] += 1
            self._state, self._failures, self._trial_in_flight = CircuitState.CLOSED, 0, False

    def record_failure(self) -> None:
        with self._lock:
            self.totals["failures"] += 1
            self._failures += 1
            if self._state == CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                if self._state != CircuitState.OPEN:
                    self.totals["opened"] += 1
                self._state, self._opened_at, self._trial_in_flight = CircuitState.OPEN, self._clock(), False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._advance()
            return {
                "name": self.name,
                "state": str(self._state),
                "consecutive_failures": self._failures,
                "failure_threshold": self.failure_threshold,
                "recovery_seconds": self.recovery_seconds,
                **self.totals,
            }


class CircuitRegistry:
    def __init__(self, *, failure_threshold: int = 5, recovery_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name, failure_threshold=self.failure_threshold, recovery_seconds=self.recovery_seconds
                )
            return self._breakers[name]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            breakers = list(self._breakers.values())
        return [b.snapshot() for b in sorted(breakers, key=lambda b: b.name)]

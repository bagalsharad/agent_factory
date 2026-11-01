"""Task dispatch.

``TaskDispatcher`` is the abstraction; ``DatabaseQueueDispatcher`` is the implementation used both locally
and in single-node production. Workers claim queued executions with an atomic compare-and-set UPDATE, which
is safe on SQLite and PostgreSQL across processes. A Redis/queue-backed dispatcher can implement the same
interface for horizontally scaled deployments (ADR-0005).
"""

from __future__ import annotations

import os
import socket
import threading
import time
from abc import ABC, abstractmethod

from ..core.logging import get_logger
from .executor import ExecutionService

log = get_logger(__name__)


class TaskDispatcher(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def healthy(self) -> bool: ...


class DatabaseQueueDispatcher(TaskDispatcher):
    def __init__(
        self,
        executions: ExecutionService,
        *,
        concurrency: int = 2,
        poll_interval: float = 0.5,
        maintenance_interval: float = 30.0,
        retention_days: int | None = None,
    ) -> None:
        self.executions = executions
        self.concurrency = max(1, concurrency)
        self.poll_interval = poll_interval
        self.maintenance_interval = maintenance_interval
        self.retention_days = retention_days
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.worker_prefix = f"{socket.gethostname()}:{os.getpid()}"
        self.last_beat = 0.0

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        recovered = self.executions.recover_stale()
        if recovered:
            log.warning("requeued stale executions", extra={"count": recovered})
        for i in range(self.concurrency):
            t = threading.Thread(
                target=self._loop, args=(f"{self.worker_prefix}:{i}",), daemon=True, name=f"af-worker-{i}"
            )
            t.start()
            self._threads.append(t)
        m = threading.Thread(target=self._maintenance, daemon=True, name="af-maintenance")
        m.start()
        self._threads.append(m)
        log.info("worker pool started", extra={"concurrency": self.concurrency})

    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            self.last_beat = time.monotonic()
            try:
                execution_id = self.executions.claim(worker_id)
            except Exception:
                log.exception("claim failed")
                self._stop.wait(self.poll_interval * 4)
                continue
            if execution_id:
                try:
                    self.executions.run(execution_id, worker_id)
                except Exception:
                    log.exception("worker run failed", extra={"execution_id": execution_id})
                continue
            self.executions.wakeup.wait(self.poll_interval)
            self.executions.wakeup.clear()

    def _maintenance(self) -> None:
        last_retention = 0.0
        while not self._stop.wait(self.maintenance_interval):
            try:
                self.executions.approvals.expire_due()
                self.executions.recover_stale()
                if self.retention_days and time.monotonic() - last_retention > 3600:
                    purged = self.executions.purge_older_than(self.retention_days)
                    last_retention = time.monotonic()
                    if purged:
                        log.info("retention purge", extra={"executions": purged})
            except Exception:
                log.exception("maintenance failed")

    def stop(self) -> None:
        self._stop.set()
        self.executions.wakeup.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads.clear()

    def healthy(self) -> bool:
        return bool(self._threads) and any(t.is_alive() for t in self._threads)

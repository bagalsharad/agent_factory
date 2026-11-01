"""Performance characteristics measured with the DETERMINISTIC MOCK MODEL.

These numbers reflect platform overhead (API, database, orchestration, tools) only. Real model latency is
excluded; see tests/integration/test_live_providers.py (marker ``ollama``) for real-inference checks.
Thresholds are deliberately generous to stay stable on CI runners; results are printed for reporting.
"""

from __future__ import annotations

import statistics
import time

import psutil
import pytest
from fastapi.testclient import TestClient

from agent_factory.governance.rbac import Principal
from agent_factory.platform import Platform
from tests.conftest import run, wait_for

pytestmark = pytest.mark.performance


def test_api_read_latency(client: TestClient) -> None:
    client.post("/api/v1/agents", json={"template": "custom-agent", "name": "perf-agent"})
    samples = []
    for _ in range(50):
        started = time.perf_counter()
        assert client.get("/api/v1/agents").status_code == 200
        samples.append((time.perf_counter() - started) * 1000)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    print(f"\n[perf/mock] GET /api/v1/agents p50={statistics.median(samples):.1f}ms p95={p95:.1f}ms")
    assert p95 < 250


def test_single_execution_overhead(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    durations = []
    for _ in range(5):
        started = time.perf_counter()
        result = run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
        durations.append((time.perf_counter() - started) * 1000)
        assert result["status"] == "completed"
    print(f"\n[perf/mock] 4-step graph execution median={statistics.median(durations):.1f}ms")
    assert statistics.median(durations) < 2000


def test_concurrent_executions_through_worker_pool(client: TestClient) -> None:
    from tests.conftest import EXAMPLES

    agent = client.post("/api/v1/agents", json={"spec_yaml": (EXAMPLES / "data-pipeline-diagnostic/agent.yaml").read_text()}).json()
    client.post(f"/api/v1/agents/{agent['id']}/build")
    started = time.perf_counter()
    ids = [client.post("/api/v1/executions", json={"agent_id": agent["id"], "input": {
        "pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}}).json()["id"] for _ in range(12)]

    def all_done() -> bool:
        return all(client.get(f"/api/v1/executions/{i}").json()["status"] == "completed" for i in ids)

    wait_for(all_done, timeout=60, interval=0.2)
    elapsed = time.perf_counter() - started
    print(f"\n[perf/mock] 12 concurrent executions (2 workers) completed in {elapsed:.2f}s")
    assert elapsed < 60


def test_cancellation_latency(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    created = platform.executions.create(admin, pipeline_agent, input_data={"pipeline_id": "orders_daily",
                                                                            "execution_id": "run_2026_10_29"},
                                         options={"model_override": {"model": "mock-slow"}, "mock_delay_seconds": 1})
    import threading

    worker = threading.Thread(target=platform.executions.run_now, args=(created["id"],))
    worker.start()
    wait_for(lambda: platform.executions._load(created["id"]).status == "running", timeout=10)
    started = time.perf_counter()
    platform.executions.cancel(admin, created["id"])
    worker.join(timeout=15)
    latency = time.perf_counter() - started
    status = platform.executions._load(created["id"]).status
    print(f"\n[perf/mock] cancellation observed after {latency:.2f}s (status={status})")
    assert status == "cancelled" and latency < 10


def test_memory_growth_is_bounded(platform: Platform, admin: Principal, pipeline_agent: str) -> None:
    process = psutil.Process()
    run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    before = process.memory_info().rss
    for _ in range(20):
        run(platform, admin, pipeline_agent, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"})
    growth_mb = (process.memory_info().rss - before) / 1e6
    print(f"\n[perf/mock] RSS growth over 20 executions: {growth_mb:.1f} MB")
    assert growth_mb < 150

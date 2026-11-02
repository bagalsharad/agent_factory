"""Tool registry, executor, connectors and sandboxing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from agent_factory.core.config import Settings
from agent_factory.core.errors import ToolError
from agent_factory.tools.base import Tool, ToolContext
from agent_factory.tools.builtin.http import check_destination, host_matches
from agent_factory.tools.registry import ToolExecutor, ToolRegistry, build_default_registry


def ctx(tmp_path: Path, **kw: Any) -> ToolContext:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    return ToolContext(workspace_id="ws", execution_id="ex", agent_name="a", settings=settings,
                       idempotency_key=kw.pop("key", "k1"), **kw)


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor(max_workers=2)


def test_builtin_registry_has_definitions_with_contracts() -> None:
    registry = build_default_registry()
    definitions = {d.name: d for d in registry.definitions()}
    assert len(definitions) == 24
    for d in definitions.values():
        assert d.input_schema["type"] == "object"
        assert d.timeout_seconds > 0
        assert d.risk in {"low", "medium", "high", "critical"}
        assert d.permissions
    assert definitions["restart_pipeline"].action_class == "production_restart"
    assert definitions["delete_dataset"].risk == "critical"
    assert definitions["read_pipeline_logs"].side_effects == "read"


def test_registry_versioning_and_disable() -> None:
    registry = build_default_registry(disabled=["http_get"])
    assert not registry.is_enabled("http_get")
    assert registry.is_enabled("read_pipeline_logs")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(registry.get("read_pipeline_logs"))


def test_read_logs_success_and_path_traversal(tmp_path: Path, executor: ToolExecutor) -> None:
    tool = build_default_registry().get("read_pipeline_logs")
    ok = executor.execute(tool, {"pipeline_id": "orders_daily", "execution_id": "run_2026_10_29"}, ctx(tmp_path))
    assert ok.ok and any("discount_code" in line for line in ok.output["lines"])  # type: ignore[index]
    traversal = executor.execute(tool, {"pipeline_id": "../../etc", "execution_id": "passwd"}, ctx(tmp_path))
    assert not traversal.ok and traversal.error["code"] == "invalid_arguments"  # type: ignore[index]


def test_code_reader_blocks_traversal_and_symlinks(tmp_path: Path, executor: ToolExecutor) -> None:
    tool = build_default_registry().get("read_code_file")
    result = executor.execute(tool, {"artifact": "payments-service", "path": "../../../pyproject.toml"}, ctx(tmp_path))
    assert not result.ok and result.error["code"] == "path_traversal"  # type: ignore[index]
    good = executor.execute(tool, {"artifact": "payments-service", "path": "app/db.py"}, ctx(tmp_path))
    assert good.ok and "SELECT * FROM payments" in good.output["content"]  # type: ignore[index]


def test_static_scanner_finds_planted_issues(tmp_path: Path, executor: ToolExecutor) -> None:
    tool = build_default_registry().get("scan_code_patterns")
    result = executor.execute(tool, {"artifact": "payments-service"}, ctx(tmp_path))
    rules = {f["rule"] for f in result.output["findings"]}  # type: ignore[index]
    assert {"SEC001", "SEC002", "SEC004", "SEC005", "SEC006", "SEC007"} <= rules


def test_simulated_actions_are_idempotent(tmp_path: Path, executor: ToolExecutor) -> None:
    tool = build_default_registry().get("restart_pipeline")
    args = {"pipeline_id": "orders_daily", "reason": "transient timeout"}
    first = executor.execute(tool, args, ctx(tmp_path, key="same"))
    second = executor.execute(tool, args, ctx(tmp_path, key="same"))
    assert first.output["duplicate"] is False and second.output["duplicate"] is True  # type: ignore[index]
    log = (tmp_path / "sandbox" / "simulated_actions.jsonl").read_text().strip().splitlines()
    assert len(log) == 1


def test_sandbox_absence_never_degrades_to_host_execution(tmp_path: Path, executor: ToolExecutor) -> None:
    tool = build_default_registry().get("python_sandbox_exec")
    result = executor.execute(tool, {"code": "import os; os.system('touch /tmp/pwned')"}, ctx(tmp_path))
    assert not result.ok
    assert result.error["code"] == "sandbox_unavailable"  # type: ignore[index]


class SlowInput(BaseModel):
    seconds: float


class SlowTool(Tool):
    name = "slow"
    description = "sleeps"
    Input = SlowInput
    timeout_seconds = 0.2

    def run(self, args: SlowInput, ctx: ToolContext) -> dict[str, Any]:
        time.sleep(args.seconds)
        return {"slept": args.seconds}


class BadOutputTool(Tool):
    name = "bad_output"
    description = "returns invalid output"
    Input = SlowInput
    output_schema = {"type": "object", "required": ["must_exist"]}

    def run(self, args: SlowInput, ctx: ToolContext) -> dict[str, Any]:
        return {"other": 1}


class FlakyTool(Tool):
    name = "flaky"
    description = "fails once"
    Input = SlowInput
    calls = 0

    def run(self, args: SlowInput, ctx: ToolContext) -> dict[str, Any]:
        FlakyTool.calls += 1
        if FlakyTool.calls == 1:
            raise ToolError("transient", retryable=True)
        return {"ok": True}


def test_executor_timeout_output_validation_and_retry(tmp_path: Path, executor: ToolExecutor) -> None:
    registry = ToolRegistry()
    for tool in (SlowTool(), BadOutputTool(), FlakyTool()):
        registry.register(tool)
    timed_out = executor.execute(registry.get("slow"), {"seconds": 1.0}, ctx(tmp_path))
    assert not timed_out.ok and timed_out.error["code"] == "timeout"  # type: ignore[index]
    bad = executor.execute(registry.get("bad_output"), {"seconds": 0}, ctx(tmp_path))
    assert not bad.ok and "schema validation" in bad.error["message"]  # type: ignore[index]
    flaky = executor.execute(registry.get("flaky"), {"seconds": 0}, ctx(tmp_path), max_retries=2)
    assert flaky.ok and flaky.attempts == 2
    invalid = executor.execute(registry.get("slow"), {"seconds": "x"}, ctx(tmp_path))
    assert invalid.error["code"] == "invalid_arguments"  # type: ignore[index]


def test_ssrf_and_allowlist_checks() -> None:
    assert host_matches("api.example.com", ["api.example.com"])
    assert host_matches("eu.api.example.com", ["*.example.com"])
    assert not host_matches("example.com", ["*.example.com"])
    assert not host_matches("api.example.com.evil.net", ["api.example.com"])
    with pytest.raises(ToolError, match="allowlist"):
        check_destination("https://evil.net/", ["api.example.com"], allow_private=False)
    with pytest.raises(ToolError, match="non-public"):
        check_destination("https://localhost/", ["localhost"], allow_private=False)
    with pytest.raises(ToolError, match="Credentials"):
        check_destination("https://user:pw@api.example.com/", ["api.example.com"], allow_private=False)
    with pytest.raises(ToolError, match="http"):
        check_destination("file:///etc/passwd", ["api.example.com"], allow_private=False)

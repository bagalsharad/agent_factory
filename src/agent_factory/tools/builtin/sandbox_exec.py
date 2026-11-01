"""Code execution inside an isolated sandbox (refused when no sandbox is available)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...spec.models import RiskLevel
from ..base import SideEffect, Tool, ToolContext
from ..sandbox import build_sandbox


class PythonInput(BaseModel):
    code: str = Field(min_length=1, max_length=20_000, description="Python source to run in the sandbox.")


class PythonSandboxExec(Tool):
    name = "python_sandbox_exec"
    description = "Run a short Python program in an isolated, network-less sandbox and return its output."
    connector = "sandbox"
    Input = PythonInput
    risk = RiskLevel.HIGH
    side_effects = SideEffect.READ  # isolated; no host side effects
    requires_sandbox = True
    timeout_seconds = 30.0
    action_class = "code_execution"

    def run(self, args: PythonInput, ctx: ToolContext) -> dict[str, Any]:
        result = build_sandbox(ctx.settings).run_python(args.code, timeout_seconds=self.timeout_seconds - 2)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

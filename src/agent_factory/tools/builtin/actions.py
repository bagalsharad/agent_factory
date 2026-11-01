"""Consequential actions — *simulated*.

These tools demonstrate governance (write classification, approval gates, idempotency). They do not touch
real systems: each records the requested action to ``<sandbox_dir>/simulated_actions.jsonl``. Replacing
them with real connectors requires an explicit implementation and review.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from pydantic import BaseModel, Field

from ...core.ids import utcnow
from ...spec.models import RiskLevel
from ..base import SideEffect, Tool, ToolContext
from .pipeline import ID_PATTERN

_lock = threading.Lock()


def _record(ctx: ToolContext, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    sandbox = ctx.settings.sandbox_dir
    assert sandbox is not None
    sandbox.mkdir(parents=True, exist_ok=True)
    log = sandbox / "simulated_actions.jsonl"
    with _lock:
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("idempotency_key") == ctx.idempotency_key:
                    return {**entry, "duplicate": True}
        entry = {
            "action": action,
            "simulated": True,
            "idempotency_key": ctx.idempotency_key,
            "execution_id": ctx.execution_id,
            "workspace_id": ctx.workspace_id,
            "recorded_at": utcnow().isoformat(),
            **payload,
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    return {**entry, "duplicate": False}


class RestartInput(BaseModel):
    pipeline_id: str = Field(pattern=ID_PATTERN)
    reason: str = Field(min_length=3, max_length=1000)


class RestartPipeline(Tool):
    name = "restart_pipeline"
    description = "Request a restart of a pipeline (SIMULATED: recorded to the local sandbox log only)."
    connector = "simulated_actions"
    Input = RestartInput
    risk = RiskLevel.HIGH
    side_effects = SideEffect.WRITE
    action_class = "production_restart"
    permissions = ["tools:invoke", "tools:invoke:write"]

    def run(self, args: RestartInput, ctx: ToolContext) -> dict[str, Any]:
        return _record(ctx, "restart_pipeline", {"pipeline_id": args.pipeline_id, "reason": args.reason})


class UpdateConfigInput(BaseModel):
    pipeline_id: str = Field(pattern=ID_PATTERN)
    key: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,80}$")
    value: str = Field(max_length=500)


class UpdatePipelineConfig(Tool):
    name = "update_pipeline_config"
    description = "Change one pipeline configuration value (SIMULATED: recorded to the local sandbox log only)."
    connector = "simulated_actions"
    Input = UpdateConfigInput
    risk = RiskLevel.HIGH
    side_effects = SideEffect.WRITE
    action_class = "configuration_change"
    permissions = ["tools:invoke", "tools:invoke:write"]

    def run(self, args: UpdateConfigInput, ctx: ToolContext) -> dict[str, Any]:
        return _record(ctx, "update_pipeline_config", args.model_dump())


class DeleteInput(BaseModel):
    dataset: str = Field(pattern=ID_PATTERN)
    reason: str = Field(min_length=3, max_length=1000)


class DeleteDataset(Tool):
    name = "delete_dataset"
    description = "Delete a dataset (SIMULATED: recorded to the local sandbox log only)."
    connector = "simulated_actions"
    Input = DeleteInput
    risk = RiskLevel.CRITICAL
    side_effects = SideEffect.WRITE
    action_class = "data_deletion"
    idempotent = True
    permissions = ["tools:invoke", "tools:invoke:write"]

    def run(self, args: DeleteInput, ctx: ToolContext) -> dict[str, Any]:
        return _record(ctx, "delete_dataset", args.model_dump())

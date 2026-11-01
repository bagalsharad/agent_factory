"""Read-only data-pipeline connector over approved sample execution records.

Data root: ``<sample_data_dir>/data-pipeline-diagnostic/data/pipelines/<pipeline_id>/executions/<execution_id>``.
It never connects to real production systems; pointing it at real data is a deliberate configuration change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...core.errors import ToolError
from ..base import Tool, ToolContext, safe_join

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$"


def pipelines_root(ctx: ToolContext) -> Path:
    return ctx.sample_root / "data-pipeline-diagnostic" / "data" / "pipelines"


def _maybe_fail(ctx: ToolContext, tool: str) -> None:
    """Deterministic fault injection used by recovery tests (fail the first attempt only)."""
    fault = ctx.fault_injection
    if fault.get("fail_tool_once") == tool and not fault.get("_fired_" + tool):
        fault["_fired_" + tool] = True
        raise ToolError(f"Injected transient failure in {tool}", retryable=True)
    if fault.get("fail_tool_always") == tool:
        raise ToolError(f"Injected permanent failure in {tool}", retryable=False)


class PipelineExecutionInput(BaseModel):
    pipeline_id: str = Field(pattern=ID_PATTERN, description="Pipeline identifier, e.g. 'orders_daily'.")
    execution_id: str = Field(pattern=ID_PATTERN, description="Execution identifier, e.g. 'run_2026_10_29'.")


class ReadLogsInput(PipelineExecutionInput):
    max_lines: int = Field(400, ge=1, le=2000)


class ReadPipelineLogs(Tool):
    name = "read_pipeline_logs"
    description = "Read the execution log of a pipeline run from the approved sample log store (read-only)."
    connector = "pipeline_samples"
    Input = ReadLogsInput
    output_schema = {
        "type": "object",
        "required": ["pipeline_id", "execution_id", "lines"],
        "properties": {
            "pipeline_id": {"type": "string"},
            "execution_id": {"type": "string"},
            "lines": {"type": "array", "items": {"type": "string"}},
            "line_count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "source": {"type": "string"},
        },
    }

    def run(self, args: ReadLogsInput, ctx: ToolContext) -> dict[str, Any]:
        _maybe_fail(ctx, self.name)
        path = safe_join(pipelines_root(ctx), args.pipeline_id, "executions", args.execution_id, "run.log")
        if not path.is_file():
            raise ToolError(f"No log found for pipeline '{args.pipeline_id}' execution '{args.execution_id}'")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {
            "pipeline_id": args.pipeline_id,
            "execution_id": args.execution_id,
            "lines": lines[-args.max_lines :],
            "line_count": len(lines),
            "truncated": len(lines) > args.max_lines,
            "source": f"pipelines/{args.pipeline_id}/executions/{args.execution_id}/run.log",
        }


class InspectExecutionMetadata(Tool):
    name = "inspect_execution_metadata"
    description = "Return recorded metadata (status, timings, row counts, configuration) for a pipeline run."
    connector = "pipeline_samples"
    Input = PipelineExecutionInput

    def run(self, args: PipelineExecutionInput, ctx: ToolContext) -> dict[str, Any]:
        _maybe_fail(ctx, self.name)
        root = pipelines_root(ctx)
        path = safe_join(root, args.pipeline_id, "executions", args.execution_id, "metadata.json")
        if not path.is_file():
            raise ToolError(f"No metadata for pipeline '{args.pipeline_id}' execution '{args.execution_id}'")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        pipeline_file = safe_join(root, args.pipeline_id, "pipeline.json")
        pipeline = json.loads(pipeline_file.read_text(encoding="utf-8")) if pipeline_file.is_file() else {}
        return {"pipeline": pipeline, "execution": metadata}


class ListExecutionsInput(BaseModel):
    pipeline_id: str = Field(pattern=ID_PATTERN)


class ListPipelineExecutions(Tool):
    name = "list_pipeline_executions"
    description = "List recorded executions of a pipeline with their final status."
    connector = "pipeline_samples"
    Input = ListExecutionsInput

    def run(self, args: ListExecutionsInput, ctx: ToolContext) -> dict[str, Any]:
        directory = safe_join(pipelines_root(ctx), args.pipeline_id, "executions")
        if not directory.is_dir():
            raise ToolError(f"Unknown pipeline '{args.pipeline_id}'")
        executions = []
        for child in sorted(directory.iterdir()):
            meta_file = child / "metadata.json"
            if meta_file.is_file():
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                executions.append({"execution_id": child.name, "status": meta.get("status", "unknown")})
        return {"pipeline_id": args.pipeline_id, "executions": executions}

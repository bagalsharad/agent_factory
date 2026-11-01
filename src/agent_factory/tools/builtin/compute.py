"""Pure computation tools (no I/O)."""

from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel, Field

from ..base import Tool, ToolContext


class StatsInput(BaseModel):
    values: list[float] = Field(min_length=1, max_length=100_000)


class ComputeStatistics(Tool):
    name = "compute_statistics"
    description = "Compute descriptive statistics (count, mean, median, stdev, min, max) for numeric values."
    connector = "compute"
    Input = StatsInput

    def run(self, args: StatsInput, ctx: ToolContext) -> dict[str, Any]:
        values = args.values
        return {
            "count": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
        }

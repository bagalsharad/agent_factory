"""Install the bundled example agents (and their knowledge collections) into a workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .core.errors import AgentFactoryError
from .governance.rbac import Principal
from .platform import Platform
from .spec.loader import load_spec


def install_examples(
    platform: Platform, principal: Principal, *, evaluate: bool = False, publish: bool = False, root: Path | None = None
) -> list[dict[str, Any]]:
    root = root or platform.settings.examples_dir
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    report: list[dict[str, Any]] = []
    for item in manifest.get("knowledge", []):
        result = platform.knowledge.ingest_directory(
            principal.workspace_id, item["collection"], root / item["directory"]
        )
        report.append({"kind": "knowledge", "collection": item["collection"], "files": result["files"]})
    existing = {a["name"]: a for a in platform.agents.list(principal, limit=200)["items"]}
    for rel in manifest.get("agents", []):
        spec = load_spec((root / rel).read_text(encoding="utf-8"))
        entry: dict[str, Any] = {"kind": "agent", "name": spec.metadata.name, "file": rel}
        try:
            if spec.metadata.name in existing:
                agent_id = existing[spec.metadata.name]["id"]
                entry["status"] = "exists"
            else:
                agent_id = platform.agents.create(
                    principal, spec, created_via="example", release_notes="Bundled example"
                )["id"]
                build = platform.agents.build(principal, agent_id)
                entry["status"] = build["status"]
                if not build["success"]:
                    entry["errors"] = build["errors"]
            entry["agent_id"] = agent_id
            if evaluate and entry.get("status") == "testing":
                run = platform.evaluations.start_run(principal, agent_id, wait=True)
                entry["evaluation"] = {
                    "passed": run["passed"],
                    "pass_rate": run["summary"].get("pass_rate"),
                    "failed_cases": run["summary"].get("failed_cases"),
                }
                if publish and run["passed"]:
                    entry["status"] = platform.agents.publish(
                        principal, agent_id, release_notes="Example published after passing evaluation"
                    )["status"]
        except AgentFactoryError as exc:
            entry["status"] = "error"
            entry["error"] = exc.message
        report.append(entry)
    return report

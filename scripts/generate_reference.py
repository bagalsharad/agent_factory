"""Regenerate machine-derived reference docs: OpenAPI, agent-spec JSON Schema, configuration table.

uv run python scripts/generate_reference.py
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_factory.api.app import create_app
from agent_factory.core.config import Settings
from agent_factory.spec.models import AgentSpec

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app = create_app(Settings(_env_file=None))
    (ROOT / "docs/api/openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
    (ROOT / "docs/reference/agent-spec.schema.json").write_text(
        json.dumps(AgentSpec.model_json_schema(), indent=2) + "\n"
    )
    rows = ["| Variable | Default | Description |", "|---|---|---|"]
    defaults = Settings(_env_file=None)
    for name, field in Settings.model_fields.items():
        value = getattr(defaults, name)
        if name.endswith("api_key"):
            shown = "unset"
        elif isinstance(value, Path):
            shown = f"`{value.relative_to(ROOT) if value.is_relative_to(ROOT) else value}`"
        else:
            shown = f"`{json.dumps(value, default=str)}`"
        rows.append(f"| `AF_{name.upper()}` | {shown} | {field.description or ''} |")
    body = (ROOT / "docs/reference/configuration.md").read_text().split("<!-- generated -->")[0]
    (ROOT / "docs/reference/configuration.md").write_text(body + "<!-- generated -->\n" + "\n".join(rows) + "\n")
    print("reference docs regenerated")


if __name__ == "__main__":
    main()

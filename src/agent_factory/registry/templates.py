"""Agent template registry (built-in YAML templates + workspace custom/imported templates)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select

from ..core import semver
from ..core.errors import ConflictError, NotFoundError, ValidationFailedError
from ..spec.loader import format_validation_errors
from ..spec.models import API_VERSION, NAME_RE, AgentSpec, AgentSpecBody, EvalCaseSpec
from ..storage.db import Database
from ..storage.models import AgentTemplate


class TemplateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    version: str = "1.0.0"
    display_name: str
    category: str = "general"
    description: str


class SecurityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk: str = "low"
    summary: str
    side_effects: str = "none"


class TemplateDocument(BaseModel):
    """A template: everything needed to start an agent of this type."""

    model_config = ConfigDict(extra="forbid")
    api_version: str = API_VERSION
    kind: str = "AgentTemplate"
    metadata: TemplateMetadata
    use_case: str
    keywords: list[str] = Field(default_factory=list)
    model_requirements: dict[str, Any] = Field(default_factory=dict)
    security_profile: SecurityProfile
    notes: str = ""
    spec: AgentSpecBody

    @property
    def required_inputs(self) -> list[str]:
        return list(self.spec.inputs.get("required", []))

    @property
    def evaluation_examples(self) -> list[EvalCaseSpec]:
        return self.spec.evaluation.cases

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "display_name": self.metadata.display_name,
            "category": self.metadata.category,
            "description": self.metadata.description,
            "use_case": self.use_case,
            "required_inputs": self.required_inputs,
            "output_schema": self.spec.outputs,
            "model_requirements": self.model_requirements,
            "default_instructions": self.spec.instructions,
            "tool_requirements": self.spec.tool_names,
            "security_profile": self.security_profile.model_dump(),
            "evaluation_examples": [c.model_dump(mode="json") for c in self.evaluation_examples],
            "workflow_type": self.spec.workflow.type,
            "keywords": self.keywords,
            "notes": self.notes,
        }


def parse_template(data: dict[str, Any]) -> TemplateDocument:
    try:
        doc = TemplateDocument.model_validate(data)
    except ValidationError as exc:
        raise ValidationFailedError("Template failed validation", details=format_validation_errors(exc)) from exc
    semver.parse(doc.metadata.version)
    return doc


class TemplateRegistry:
    def __init__(self, db: Database, templates_dir: Path) -> None:
        self.db = db
        self.templates_dir = templates_dir

    def sync_builtin(self) -> int:
        count = 0
        with self.db.transaction() as session:
            for path in sorted(self.templates_dir.glob("*.yaml")):
                doc = parse_template(yaml.safe_load(path.read_text(encoding="utf-8")))
                record = session.scalars(
                    select(AgentTemplate).where(
                        AgentTemplate.workspace_id.is_(None),
                        AgentTemplate.name == doc.metadata.name,
                        AgentTemplate.version == doc.metadata.version,
                    )
                ).first()
                definition = doc.model_dump(mode="json", exclude_none=True)
                if record:
                    record.definition, record.description = definition, doc.metadata.description
                    record.display_name, record.category = doc.metadata.display_name, doc.metadata.category
                else:
                    session.add(
                        AgentTemplate(
                            workspace_id=None,
                            name=doc.metadata.name,
                            version=doc.metadata.version,
                            display_name=doc.metadata.display_name,
                            category=doc.metadata.category,
                            description=doc.metadata.description,
                            definition=definition,
                            source="builtin",
                        )
                    )
                count += 1
        return count

    def _records(self, workspace_id: str) -> list[AgentTemplate]:
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(AgentTemplate)
                    .where(or_(AgentTemplate.workspace_id.is_(None), AgentTemplate.workspace_id == workspace_id))
                    .order_by(AgentTemplate.name)
                ).all()
            )

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        latest: dict[str, AgentTemplate] = {}
        for record in self._records(workspace_id):
            current = latest.get(record.name)
            if not current or semver.sort_key(record.version) > semver.sort_key(current.version):
                latest[record.name] = record
        out = []
        for record in latest.values():
            item = parse_template(record.definition).summary()
            item.update({"id": record.id, "source": record.source})
            out.append(item)
        return sorted(out, key=lambda t: (t["name"] != "custom-agent", t["category"], t["name"]))

    def get(self, workspace_id: str, name: str, version: str | None = None) -> TemplateDocument:
        candidates = [
            r for r in self._records(workspace_id) if r.name == name and (not version or r.version == version)
        ]
        if not candidates:
            raise NotFoundError(f"Template '{name}{'@' + version if version else ''}' not found")
        record = max(candidates, key=lambda r: semver.sort_key(r.version))
        return parse_template(record.definition)

    def create(self, workspace_id: str, data: dict[str, Any], source: str = "custom") -> dict[str, Any]:
        doc = parse_template(data)
        with self.db.transaction() as session:
            clash = session.scalars(
                select(AgentTemplate).where(
                    or_(AgentTemplate.workspace_id.is_(None), AgentTemplate.workspace_id == workspace_id),
                    AgentTemplate.name == doc.metadata.name,
                    AgentTemplate.version == doc.metadata.version,
                )
            ).first()
            if clash:
                raise ConflictError(
                    f"Template {doc.metadata.name}@{doc.metadata.version} already exists; "
                    "bump the version to publish changes"
                )
            record = AgentTemplate(
                workspace_id=workspace_id,
                name=doc.metadata.name,
                version=doc.metadata.version,
                display_name=doc.metadata.display_name,
                category=doc.metadata.category,
                description=doc.metadata.description,
                definition=doc.model_dump(mode="json", exclude_none=True),
                source=source,
            )
            session.add(record)
            session.flush()
            return {**doc.summary(), "id": record.id, "source": source}

    def instantiate(
        self,
        workspace_id: str,
        template: str,
        *,
        name: str,
        display_name: str = "",
        description: str = "",
        objective: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> AgentSpec:
        tname, _, tversion = template.partition("@")
        doc = self.get(workspace_id, tname, tversion or None)
        body = copy.deepcopy(doc.spec.model_dump(mode="json", exclude_none=True))
        if objective:
            body["objective"] = objective
        for key, value in (overrides or {}).items():
            body[key] = value
        data = {
            "api_version": API_VERSION,
            "kind": "Agent",
            "metadata": {
                "name": name,
                "display_name": display_name or doc.metadata.display_name,
                "version": "0.1.0",
                "description": description or doc.metadata.description,
                "template": f"{doc.metadata.name}@{doc.metadata.version}",
            },
            "spec": body,
        }
        try:
            return AgentSpec.model_validate(data)
        except ValidationError as exc:
            raise ValidationFailedError("Template instantiation failed", details=format_validation_errors(exc)) from exc

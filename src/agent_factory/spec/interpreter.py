"""Natural-language requirement interpreter.

Turns "Create an agent that investigates failed data ingestion pipelines..." into a *draft* specification.
A draft is never executable by itself: it must be saved, validated and built through the backend.

Strategy:
1. Deterministic baseline — score templates by keyword overlap, derive name/description/approval actions.
2. Optional model refinement — when a non-mock provider is selected and enabled, ask the model for a
   constrained JSON "refinement" (never a full spec, never tools outside the chosen template) and merge it.
   Any failure falls back to the baseline. The interpreter never grants write access or network access on its
   own: detected consequential actions become approval requirements plus an explicit assumption to review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.logging import get_logger
from .loader import dump_yaml
from .models import AgentSpec

if TYPE_CHECKING:
    from ..model_gateway.gateway import ModelGateway
    from ..registry.templates import TemplateRegistry

log = get_logger(__name__)

ACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(restart|rerun|re-run|retrigger)\b", re.I), "production_restart"),
    (re.compile(r"\b(delete|drop|purge|truncate|remove data)\b", re.I), "data_deletion"),
    (
        re.compile(r"\b(change|update|modify|edit)\b.{0,30}\b(config|configuration|settings)\b", re.I),
        "configuration_change",
    ),
    (re.compile(r"\b(send|email|notify|post to|message)\b", re.I), "external_communication"),
    (re.compile(r"\b(deploy|release|rollback)\b", re.I), "deployment"),
]
STOP = set(
    [
        "create",
        "build",
        "make",
        "an",
        "a",
        "the",
        "that",
        "which",
        "who",
        "agent",
        "agents",
        "to",
        "and",
        "of",
        "for",
        "with",
        "by",
        "from",
        "using",
        "in",
        "on",
        "it",
        "its",
        "into",
        "this",
        "will",
        "should",
        "can",
        "analyze",
        "analyzing",
    ]
)


@dataclass
class DraftResult:
    spec: AgentSpec
    template: str
    confidence: float
    rationale: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    model_assisted: bool = False
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        doc = self.spec.to_document()
        return {
            "spec": doc,
            "yaml": dump_yaml(doc),
            "template": self.template,
            "confidence": round(self.confidence, 2),
            "rationale": self.rationale,
            "assumptions": self.assumptions,
            "model_assisted": self.model_assisted,
            "alternatives": self.alternatives,
            "executable": False,
            "next_step": "Review and edit the draft, then save it. Saving creates a draft version that must be "
            "validated and built before it can run.",
        }


def slugify(text: str, max_words: int = 4) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]
    slug = "-".join(words[:max_words]) or "custom-agent"
    if not slug[0].isalpha():
        slug = "agent-" + slug
    if not slug.endswith("agent"):
        slug = f"{slug}-agent"
    return slug[:63].rstrip("-")


class RequirementInterpreter:
    def __init__(self, templates: TemplateRegistry, gateway: ModelGateway | None = None) -> None:
        self.templates = templates
        self.gateway = gateway

    def _score(self, objective: str, workspace_id: str) -> list[tuple[float, dict[str, Any]]]:
        text = objective.lower()
        scored = []
        for tpl in self.templates.list(workspace_id):
            hits = [k for k in tpl["keywords"] if k.lower() in text]
            score = sum(1 + 0.5 * (" " in k) for k in hits)
            scored.append((score, {**tpl, "matched_keywords": hits}))
        scored.sort(key=lambda x: (x[0], x[1]["name"] != "custom-agent"), reverse=True)
        return scored

    def interpret(
        self,
        workspace_id: str,
        objective: str,
        *,
        name: str | None = None,
        template: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> DraftResult:
        objective = objective.strip()
        rationale: list[str] = []
        assumptions: list[str] = []
        scored = self._score(objective, workspace_id)
        if template:
            chosen = next((t for _, t in scored if t["name"] == template), None)
            confidence = 1.0
            rationale.append(f"Template '{template}' was selected explicitly.")
        else:
            best_score, chosen = scored[0] if scored else (0, None)
            if not chosen or best_score <= 0:
                chosen = next(t for _, t in scored if t["name"] == "custom-agent")
                best_score = 0
            confidence = min(0.95, 0.35 + 0.15 * best_score) if best_score else 0.3
            if chosen["matched_keywords"]:
                rationale.append(
                    f"Matched template '{chosen['name']}' on keywords: {', '.join(chosen['matched_keywords'])}."
                )
            else:
                rationale.append("No template matched strongly; starting from the custom template.")
        assert chosen is not None
        agent_name = name or slugify(objective)
        description = objective if len(objective) <= 300 else objective[:297] + "..."
        actions = sorted({a for pattern, a in ACTION_PATTERNS if pattern.search(objective)})
        overrides: dict[str, Any] = {}
        spec = self.templates.instantiate(
            workspace_id,
            chosen["name"],
            name=agent_name,
            description=description,
            objective=objective,
            overrides=overrides,
        )
        body = spec.spec
        if provider:
            body.model.provider = provider
            if model:
                body.model.model = model
        if actions:
            body.policies.require_approval_for = sorted(set(body.policies.require_approval_for) | set(actions))
            rationale.append(f"Detected consequential actions requiring human approval: {', '.join(actions)}.")
            if not body.policies.allow_write_operations:
                assumptions.append(
                    "Write operations remain disabled. If the agent must perform these actions, add an "
                    "approved write tool and set policies.allow_write_operations explicitly."
                )
        assumptions.append(
            f"Model provider '{body.model.provider}' / model '{body.model.model}'. Select a local "
            "Ollama model for real inference."
        )
        if not body.evaluation.cases:
            assumptions.append("No evaluation cases yet: add at least one before the version can be published.")
        alternatives = [{"template": t["name"], "score": s} for s, t in scored[1:4] if s > 0]

        result = DraftResult(
            spec=spec,
            template=f"{chosen['name']}@{chosen['version']}",
            confidence=confidence,
            rationale=rationale,
            assumptions=assumptions,
            alternatives=alternatives,
        )
        if provider and provider != "mock" and self.gateway and self.gateway.is_enabled(provider):
            self._refine(result, objective, provider, model or body.model.model)
        return result

    def _refine(self, result: DraftResult, objective: str, provider: str, model: str) -> None:
        from ..model_gateway.types import ChatMessage, ChatRequest

        assert self.gateway is not None
        schema = {
            "type": "object",
            "required": ["display_name", "instructions", "output_fields"],
            "properties": {
                "display_name": {"type": "string", "maxLength": 80},
                "instructions": {"type": "string", "maxLength": 3000},
                "output_fields": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,40}$"},
                    "maxItems": 8,
                },
                "evaluation_ideas": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            },
        }
        body = result.spec.spec
        prompt = (
            "Refine an AI agent draft. Return JSON only.\n"
            f"Objective: {objective}\nTemplate: {result.template}\nTools available: {body.tool_names}\n"
            "Write concise system instructions (evidence-based, no fabricated actions), a display name, and the "
            "names of output fields."
        )
        try:
            response = self.gateway.chat(
                provider,
                ChatRequest(
                    model=model,
                    messages=[ChatMessage(role="user", content=prompt)],
                    response_schema=schema,
                    timeout_seconds=60,
                ),
                max_retries=0,
            )
            data = json.loads(response.content)
            if isinstance(data.get("display_name"), str):
                result.spec.metadata.display_name = data["display_name"][:80]
            if isinstance(data.get("instructions"), str) and data["instructions"].strip():
                body.instructions = data["instructions"][:3000]
            fields = [f for f in data.get("output_fields", []) if re.match(r"^[a-z][a-z0-9_]{0,40}$", str(f))]
            if fields and body.workflow.type == "single" and result.template.startswith("custom-agent"):
                body.outputs = {
                    "type": "object",
                    "required": fields,
                    "properties": {f: {"type": "string"} for f in fields},
                }
            for idea in data.get("evaluation_ideas", [])[:5]:
                result.assumptions.append(f"Suggested evaluation case: {idea}")
            result.model_assisted = True
            result.rationale.append(f"Refined instructions and outputs with {provider}/{model}.")
        except Exception as exc:  # refinement is best-effort; the deterministic draft stands
            log.info("model refinement skipped", extra={"reason": str(exc)[:200]})
            result.rationale.append(
                f"Model refinement unavailable ({exc.__class__.__name__}); deterministic draft used."
            )

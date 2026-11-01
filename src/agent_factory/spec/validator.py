"""Semantic validation of agent specifications.

Structural validation (types, required fields) happens in Pydantic. This module checks the specification
against the platform: registered tools, enabled providers, referenced agents, JSON Schema validity,
policy consistency, workflow wiring, secrets and budget sanity. An agent version can only be built when
this report has no errors.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jsonschema
from jsonschema.validators import validator_for

from ..core.ids import content_hash, utcnow
from ..core.redaction import contains_secret
from ..tools.base import SideEffect
from .models import AGENT_REF_RE, AgentSpec

if TYPE_CHECKING:
    from ..model_gateway.gateway import ModelGateway
    from ..tools.registry import ToolRegistry


@dataclass
class ResolvedAgentRef:
    agent_id: str
    version_id: str
    name: str
    version: str
    spec: AgentSpec
    executable: bool


AgentResolver = Callable[[str], ResolvedAgentRef | None]


@dataclass
class Issue:
    path: str
    message: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message, "code": self.code}


@dataclass
class ValidationReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    spec_hash: str = ""

    @property
    def valid(self) -> bool:
        return not self.errors

    def error(self, path: str, message: str, code: str) -> None:
        self.errors.append(Issue(path, message, code))

    def warn(self, path: str, message: str, code: str) -> None:
        self.warnings.append(Issue(path, message, code))

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "spec_hash": self.spec_hash,
            "checked_at": utcnow().isoformat(),
        }


def _walk_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [x for k, v in value.items() for x in _walk_strings(v, f"{path}.{k}" if path else str(k))]
    if isinstance(value, list):
        return [x for i, v in enumerate(value) for x in _walk_strings(v, f"{path}[{i}]")]
    return []


class SpecValidator:
    def __init__(
        self, tools: ToolRegistry, gateway: ModelGateway | None = None, sandbox_available: bool = False
    ) -> None:
        self.tools = tools
        self.gateway = gateway
        self.sandbox_available = sandbox_available

    def validate(
        self, spec: AgentSpec, resolve_agent: AgentResolver | None = None, *, check_provider_health: bool = False
    ) -> ValidationReport:
        report = ValidationReport(spec_hash=content_hash(spec.to_document()))
        body = spec.spec
        self._schemas(spec, report)
        self._secrets(spec, report)
        self._model(spec, report, check_provider_health)
        tool_defs = self._tools(spec, report)
        self._policies(spec, report, tool_defs)
        self._workflow(spec, report, tool_defs, resolve_agent)
        self._memory(spec, report)
        self._limits(spec, report)
        self._evaluation(spec, report)
        if not body.instructions.strip():
            report.warn(
                "spec.instructions",
                "No instructions given; a default system instruction will be used",
                "instructions.empty",
            )
        return report

    # ------------------------------------------------------------------ checks
    def _schemas(self, spec: AgentSpec, report: ValidationReport) -> None:
        for name in ("inputs", "outputs"):
            schema = getattr(spec.spec, name)
            try:
                validator_for(schema).check_schema(schema)
            except jsonschema.SchemaError as exc:
                report.error(f"spec.{name}", f"Not a valid JSON Schema: {exc.message}", "schema.invalid")
                continue
            if schema.get("type") != "object":
                report.error(f"spec.{name}", f"{name} must describe a JSON object (type: object)", "schema.not_object")

    def _secrets(self, spec: AgentSpec, report: ValidationReport) -> None:
        for path, text in _walk_strings(spec.to_document()):
            if contains_secret(text):
                report.error(
                    path,
                    "Value looks like a credential. Specifications must reference secrets by name, never embed them.",
                    "secret.embedded",
                )

    def _model(self, spec: AgentSpec, report: ValidationReport, check_health: bool) -> None:
        model = spec.spec.model
        if self.gateway is None:
            return
        if not self.gateway.known(model.provider):
            report.error("spec.model.provider", f"Unknown model provider '{model.provider}'", "model.unknown_provider")
            return
        if not self.gateway.is_enabled(model.provider):
            entry = next(e for e in self.gateway.entries() if e.name == model.provider)
            report.error(
                "spec.model.provider",
                f"Provider '{model.provider}' is disabled: {entry.reason}",
                "model.provider_disabled",
            )
            return
        caps = self.gateway.capabilities(model.provider, model.model)
        for capability in model.required_capabilities:
            if not caps.supports(capability):
                code = "model.capability_missing"
                message = f"Model '{model.model}' does not report capability '{capability}'"
                if capability == "tools":
                    report.warn(
                        "spec.model.required_capabilities",
                        message + "; the harness will use the JSON tool protocol fallback",
                        code,
                    )
                else:
                    report.error("spec.model.required_capabilities", message, code)
        if check_health and model.provider == "ollama":
            from ..model_gateway.ollama import OllamaProvider

            provider = self.gateway.provider("ollama")
            if isinstance(provider, OllamaProvider) and not provider.model_installed(model.model):
                report.warn(
                    "spec.model.model",
                    f"Model '{model.model}' is not installed in Ollama. Install it "
                    f"explicitly: `ollama pull {model.model}`",
                    "model.not_installed",
                )

    def _tools(self, spec: AgentSpec, report: ValidationReport) -> dict[str, Any]:
        defs: dict[str, Any] = {}
        seen: set[str] = set()
        for i, ref in enumerate(spec.spec.tools):
            path = f"spec.tools[{i}]"
            if ref.name in seen:
                report.error(path, f"Tool '{ref.name}' listed more than once", "tool.duplicate")
                continue
            seen.add(ref.name)
            if not self.tools.has(ref.name, ref.version):
                report.error(
                    path,
                    f"Tool '{ref.name}{'@' + ref.version if ref.version else ''}' is not registered",
                    "tool.unknown",
                )
                continue
            if not self.tools.is_enabled(ref.name):
                report.error(path, f"Tool '{ref.name}' is disabled by platform settings", "tool.disabled")
            definition = self.tools.get(ref.name, ref.version).definition
            defs[ref.name] = definition
            if definition.requires_sandbox and not self.sandbox_available:
                report.warn(
                    path,
                    f"Tool '{ref.name}' requires a sandbox, which is not available; calls will be refused",
                    "tool.sandbox_unavailable",
                )
        return defs

    def _policies(self, spec: AgentSpec, report: ValidationReport, defs: dict[str, Any]) -> None:
        policies = spec.spec.policies
        for name, d in defs.items():
            if d.side_effects == SideEffect.WRITE and not policies.allow_write_operations:
                report.error(
                    "spec.policies.allow_write_operations",
                    f"Tool '{name}' performs write operations but allow_write_operations is false",
                    "policy.write_conflict",
                )
            if d.network and not policies.external_network:
                report.error(
                    "spec.policies.external_network",
                    f"Tool '{name}' needs network access but external_network is false",
                    "policy.network_conflict",
                )
            if d.side_effects == SideEffect.WRITE and not (
                (d.action_class and d.action_class in policies.require_approval_for)
                or spec.spec.human_approval.require_for_writes
            ):
                report.warn(
                    "spec.policies.require_approval_for",
                    f"Write tool '{name}' will run without human approval",
                    "policy.unapproved_write",
                )
        if policies.external_network and not policies.allowed_network_hosts:
            report.error(
                "spec.policies.allowed_network_hosts",
                "external_network requires an explicit allowed_network_hosts list",
                "policy.no_allowlist",
            )
        for host in policies.allowed_network_hosts:
            if "/" in host or ":" in host or host in {"*", "*.*"}:
                report.error("spec.policies.allowed_network_hosts", f"Invalid host entry '{host}'", "policy.bad_host")

    def _workflow(
        self, spec: AgentSpec, report: ValidationReport, defs: dict[str, Any], resolve_agent: AgentResolver | None
    ) -> None:
        wf = spec.spec.workflow
        steps = wf.steps
        ids = [s.id for s in steps]
        tool_names = set(spec.spec.tool_names)
        if wf.type in {"single", "graph"} and not any(s.kind in {"model", "agent_loop"} for s in steps):
            report.error(
                "spec.workflow.steps", "Workflow needs at least one model or agent_loop step", "workflow.no_model_step"
            )
        if wf.type == "supervisor":
            if not wf.specialists:
                report.error(
                    "spec.workflow.specialists", "Supervisor workflows require specialists", "workflow.no_specialists"
                )
            if not any(s.kind == "agent_loop" for s in steps):
                report.error(
                    "spec.workflow.steps",
                    "Supervisor workflows require an agent_loop step that delegates",
                    "workflow.no_supervisor_loop",
                )
        if wf.type == "sequential" and not any(s.kind == "delegate" for s in steps):
            report.error(
                "spec.workflow.steps",
                "Sequential multi-agent workflows require delegate steps",
                "workflow.no_delegates",
            )
        if wf.type in {"single", "graph"} and (wf.specialists or any(s.kind == "delegate" for s in steps)):
            report.error(
                "spec.workflow.type",
                "Delegation requires workflow type 'sequential' or 'supervisor'",
                "workflow.delegation_type",
            )
        finals = [s.id for s in steps if s.final]
        if len(finals) > 1:
            report.error("spec.workflow.steps", f"Only one step may be final (found {finals})", "workflow.multi_final")
        for i, step in enumerate(steps):
            path = f"spec.workflow.steps[{i}]"
            if step.kind == "tool" and step.tool not in tool_names:
                report.error(
                    path, f"Step uses tool '{step.tool}' which is not in spec.tools", "workflow.tool_not_approved"
                )
            if step.kind == "agent_loop" and step.tools:
                for t in step.tools:
                    if t not in tool_names:
                        report.error(path, f"agent_loop tool '{t}' is not in spec.tools", "workflow.tool_not_approved")
            if step.kind == "validate" and step.target and step.target not in ids[:i]:
                report.error(path, f"validate target '{step.target}' must be an earlier step", "workflow.bad_target")
            if step.kind == "validate" and not any(s.kind in {"model", "agent_loop"} for s in steps[:i]):
                report.error(path, "validate step has no earlier model step to validate", "workflow.bad_target")
            if step.output_schema is not None:
                try:
                    validator_for(step.output_schema).check_schema(step.output_schema)
                except jsonschema.SchemaError as exc:
                    report.error(path, f"output_schema is not valid JSON Schema: {exc.message}", "schema.invalid")
            for ref in step.include_steps or []:
                if ref not in ids[:i]:
                    report.error(
                        path,
                        f"include_steps references '{ref}' which does not precede this step",
                        "workflow.bad_reference",
                    )
            if step.kind == "retrieve" and not spec.spec.memory.knowledge.enabled:
                report.error(path, "retrieve step requires memory.knowledge.enabled", "workflow.knowledge_disabled")
            for ref in _template_refs(step.model_dump(mode="json")):
                if ref not in ids[:i]:
                    report.error(
                        path,
                        f"Template references step '{ref}' which does not precede this step",
                        "workflow.bad_reference",
                    )
        refs: list[tuple[str, str]] = [
            (f"spec.workflow.steps[{i}].agent", s.agent)
            for i, s in enumerate(steps)
            if s.kind == "delegate" and s.agent
        ]
        refs += [(f"spec.workflow.specialists[{i}].agent", sp.agent) for i, sp in enumerate(wf.specialists)]
        if len(refs) > spec.spec.execution_limits.max_subagents:
            report.error(
                "spec.execution_limits.max_subagents",
                f"{len(refs)} specialists exceed max_subagents={spec.spec.execution_limits.max_subagents}",
                "workflow.too_many_subagents",
            )
        for path, ref in refs:
            match = AGENT_REF_RE.match(ref)
            if match and match.group("name") == spec.metadata.name:
                report.error(path, "An agent cannot delegate to itself", "workflow.recursive")
                continue
            if resolve_agent is None:
                continue
            resolved = resolve_agent(ref)
            if resolved is None:
                report.error(
                    path, f"Referenced agent '{ref}' was not found in this workspace", "workflow.agent_missing"
                )
                continue
            if not resolved.executable:
                report.error(
                    path, f"Referenced agent '{ref}' has no built, executable version", "workflow.agent_not_executable"
                )
            sub = resolved.spec.spec
            if sub.workflow.type in {"sequential", "supervisor"}:
                report.error(
                    path,
                    f"Specialist '{ref}' is itself multi-agent; nested delegation is not allowed",
                    "workflow.nested_delegation",
                )
            for t in sub.tool_names:
                if self.tools.has(t) and self.tools.get(t).definition.side_effects != SideEffect.READ:
                    report.error(
                        path,
                        f"Specialist '{ref}' uses side-effecting tool '{t}'; specialists in "
                        "multi-agent workflows must be read-only in this release",
                        "workflow.specialist_side_effects",
                    )

    def _memory(self, spec: AgentSpec, report: ValidationReport) -> None:
        knowledge = spec.spec.memory.knowledge
        if knowledge.enabled and not knowledge.collections:
            report.error(
                "spec.memory.knowledge.collections",
                "Knowledge memory requires at least one collection",
                "memory.no_collections",
            )
        knowledge_tools = {"search_documents", "read_document", "list_documents"} & set(spec.spec.tool_names)
        if knowledge_tools and not knowledge.enabled:
            report.error(
                "spec.memory.knowledge.enabled",
                f"Tools {sorted(knowledge_tools)} require memory.knowledge.enabled with collections",
                "memory.knowledge_disabled",
            )

    def _limits(self, spec: AgentSpec, report: ValidationReport) -> None:
        limits = spec.spec.execution_limits
        steps = spec.spec.workflow.steps
        if limits.max_steps < len(steps):
            report.error(
                "spec.execution_limits.max_steps",
                f"max_steps ({limits.max_steps}) is smaller than the number of workflow steps ({len(steps)})",
                "limits.too_few_steps",
            )
        model_steps = sum(1 for s in steps if s.kind in {"model", "agent_loop"})
        if limits.max_model_calls < model_steps:
            report.error(
                "spec.execution_limits.max_model_calls",
                f"max_model_calls ({limits.max_model_calls}) is below the number of model steps",
                "limits.too_few_model_calls",
            )
        instructions_tokens = len(spec.spec.instructions) // 4
        if instructions_tokens > limits.max_context_tokens // 2:
            report.warn(
                "spec.instructions",
                "Instructions use more than half of max_context_tokens",
                "limits.large_instructions",
            )

    def _evaluation(self, spec: AgentSpec, report: ValidationReport) -> None:
        evaluation = spec.spec.evaluation
        case_ids = [c.id for c in evaluation.cases]
        for i, case in enumerate(evaluation.cases):
            try:
                jsonschema.validate(case.input, spec.spec.inputs)
            except jsonschema.ValidationError as exc:
                if case.kind != "input_validation":
                    report.error(
                        f"spec.evaluation.cases[{i}].input",
                        f"Case input does not match spec.inputs: {exc.message}",
                        "evaluation.bad_input",
                    )
        for required in evaluation.acceptance.required_cases:
            if required not in case_ids:
                report.error(
                    "spec.evaluation.acceptance.required_cases", f"Unknown case '{required}'", "evaluation.unknown_case"
                )
        if not evaluation.cases and not evaluation.suite:
            report.warn(
                "spec.evaluation", "No evaluation cases: this version cannot pass the publish gate", "evaluation.none"
            )


def _template_refs(value: Any) -> set[str]:
    import re

    text = json.dumps(value)
    return set(re.findall(r"\{\{\s*steps\.([a-z][a-z0-9_]*)", text))

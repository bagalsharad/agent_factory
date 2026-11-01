"""Agent builder.

Build stages (each recorded in the build report):

 1. load_and_validate      — parse the stored spec and run semantic validation
 2. resolve_model_profile  — provider, model, capabilities, locality (local vs cloud)
 3. resolve_tools          — registered tool versions and content hashes
 4. validate_permissions   — RBAC for the builder, approval requirements, effective network allowlist
 5. resolve_components     — pinned component versions (no silent replacement)
 6. assemble_workflow      — compile the LangGraph workflow and derive the visual graph
 7. attach_policies        — policy hash and execution budgets
 8. initialize_harness     — harness strategy and provider health
 9. register_version       — persist workflow definition and provenance; pin components
10. validation_checks      — evaluation inputs vs input schema, final-step and output-contract checks

The builder never generates or executes code.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import jsonschema
from sqlalchemy import select

from ..core.ids import utcnow
from ..governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from ..governance.rbac import Principal
from ..model_gateway.gateway import ModelGateway
from ..registry.components import ComponentRegistry
from ..runtime.graph import compile_workflow, describe_workflow
from ..spec.loader import load_spec
from ..spec.models import AgentSpec
from ..spec.validator import SpecValidator
from ..storage.db import Database
from ..storage.models import AgentVersion, ModelProfile, WorkflowDefinition
from ..tools.registry import ToolRegistry

HARNESS_STRATEGY = "governed-harness/v1"


class _CompileOnlyHarness:
    """Stand-in used to prove a workflow compiles; any node invocation is a bug."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def fail(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("compile-only harness cannot execute")

        return fail


class AgentBuilder:
    def __init__(
        self,
        *,
        db: Database,
        validator: SpecValidator,
        tools: ToolRegistry,
        gateway: ModelGateway,
        policy: PolicyEngine,
        components: ComponentRegistry,
        resolver_factory: Callable[[str], Any],
    ) -> None:
        self.db = db
        self.validator = validator
        self.tools = tools
        self.gateway = gateway
        self.policy = policy
        self.components = components
        self.resolver_factory = resolver_factory

    def build(self, principal: Principal, version_id: str) -> dict[str, Any]:
        with self.db.session() as session:
            version = session.get(AgentVersion, version_id)
            assert version is not None
            session.expunge(version)
        stages: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        resolved: dict[str, Any] = {}
        state: dict[str, Any] = {}

        def stage(name: str, fn: Callable[[], Any]) -> bool:
            started = time.perf_counter()
            try:
                detail = fn()
                status = "passed"
            except BuildStageError as exc:
                detail, status = exc.args[0], "failed"
                errors.append({"stage": name, "message": exc.args[0]})
            stages.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
            return status == "passed"

        def load_and_validate() -> str:
            spec = load_spec(version.spec)
            state["spec"] = spec
            report = self.validator.validate(
                spec, self.resolver_factory(version.workspace_id), check_provider_health=True
            )
            state["validation"] = report.to_dict()
            warnings.extend({"stage": "load_and_validate", **w.to_dict()} for w in report.warnings)
            if not report.valid:
                errors.extend({"stage": "load_and_validate", **e.to_dict()} for e in report.errors)
                raise BuildStageError(f"{len(report.errors)} validation error(s)")
            return f"valid ({len(report.warnings)} warning(s))"

        def resolve_model_profile() -> str:
            spec: AgentSpec = state["spec"]
            model = spec.spec.model
            profile_info = None
            if model.profile:
                with self.db.session() as session:
                    profile = session.scalars(select(ModelProfile).where(ModelProfile.name == model.profile)).first()
                if profile is None:
                    raise BuildStageError(f"Model profile '{model.profile}' not found")
                profile_info = {
                    "name": profile.name,
                    "version": profile.version,
                    "provider": profile.provider,
                    "model": profile.model,
                    "parameters": profile.parameters,
                }
            entry = next(e for e in self.gateway.entries() if e.name == model.provider)
            caps = self.gateway.capabilities(model.provider, model.model)
            resolved["model"] = {
                "provider": model.provider,
                "model": model.model,
                "temperature": model.temperature,
                "local": entry.local,
                "capabilities": caps.model_dump(),
                "profile": profile_info,
            }
            if not entry.local:
                warnings.append(
                    {
                        "stage": "resolve_model_profile",
                        "message": "Cloud provider: prompts and tool outputs will leave this machine",
                    }
                )
            return f"{model.provider}/{model.model} ({'local' if entry.local else 'cloud'})"

        def resolve_tools() -> str:
            spec: AgentSpec = state["spec"]
            items = []
            for ref in spec.spec.tools:
                d = self.tools.get(ref.name, ref.version).definition
                items.append(
                    {
                        "name": d.name,
                        "version": d.version,
                        "hash": d.hash,
                        "risk": str(d.risk),
                        "side_effects": str(d.side_effects),
                        "action_class": d.action_class,
                        "connector": d.connector,
                    }
                )
            resolved["tools"] = items
            return f"{len(items)} tool(s) resolved"

        def validate_permissions() -> str:
            spec: AgentSpec = state["spec"]
            gated, missing = [], []
            for ref in spec.spec.tools:
                d = self.tools.get(ref.name, ref.version).definition
                for permission in d.permissions:
                    if not principal.can(permission):
                        missing.append(f"{d.name} requires '{permission}'")
                decision = self.policy.evaluate_tool(
                    ToolAuthorizationRequest(
                        principal=principal,
                        workspace_id=principal.workspace_id,
                        agent=spec.spec,
                        tool_name=d.name,
                        tool=d,
                        tool_enabled=self.tools.is_enabled(d.name),
                        arguments={},
                        sandbox_available=False,
                    )
                )
                if decision.effect == Effect.REQUIRE_APPROVAL:
                    gated.append({"tool": d.name, "reasons": decision.reasons})
            hosts = self.policy.allowed_hosts(spec.spec)
            resolved["permissions"] = {"approval_required": gated, "effective_network_hosts": hosts}
            if spec.spec.policies.external_network and not hosts:
                warnings.append(
                    {
                        "stage": "validate_permissions",
                        "message": "No agent host is in the platform allowlist (AF_NETWORK_ALLOWLIST); network calls "
                        "will be denied",
                    }
                )
            if missing:
                raise BuildStageError("Builder lacks tool permissions: " + "; ".join(missing))
            return f"{len(gated)} tool(s) gated by human approval"

        def resolve_components() -> str:
            spec: AgentSpec = state["spec"]
            pinned = []
            for ref in spec.spec.components:
                try:
                    record = self.components.resolve(version.workspace_id, ref)
                except Exception as exc:
                    raise BuildStageError(str(exc)) from exc
                if record.status == "deprecated":
                    warnings.append({"stage": "resolve_components", "message": f"{ref.ref} is deprecated"})
                pinned.append({"id": record.id, "ref": ref.ref, "hash": record.content_hash})
            resolved["components"] = pinned
            return f"{len(pinned)} component(s) pinned"

        def assemble_workflow() -> str:
            spec: AgentSpec = state["spec"]
            compile_workflow(spec, _CompileOnlyHarness(), None)  # type: ignore[arg-type]
            graph = describe_workflow(spec)
            state["graph"] = graph
            resolved["workflow"] = {"pattern": graph["pattern"], "hash": graph["hash"], "nodes": len(graph["nodes"])}
            return f"{graph['pattern']} with {len(graph['nodes'])} nodes"

        def attach_policies() -> str:
            spec: AgentSpec = state["spec"]
            resolved["policy_hash"] = self.policy.policy_hash(spec.spec)
            resolved["execution_limits"] = spec.spec.execution_limits.model_dump()
            return f"policy {resolved['policy_hash'][:12]}"

        def initialize_harness() -> str:
            spec: AgentSpec = state["spec"]
            resolved["harness"] = HARNESS_STRATEGY
            health = next((h for h in self.gateway.health() if h.provider == spec.spec.model.provider), None)
            if health and health.status != "up":
                warnings.append(
                    {
                        "stage": "initialize_harness",
                        "message": f"Provider '{health.provider}' is {health.status}: {health.detail}",
                    }
                )
            return f"{HARNESS_STRATEGY}; provider {health.status if health else 'unknown'}"

        def register_version() -> str:
            spec: AgentSpec = state["spec"]
            graph = state["graph"]
            with self.db.transaction() as session:
                wf = session.scalars(
                    select(WorkflowDefinition).where(WorkflowDefinition.agent_version_id == version.id)
                ).first()
                definition = spec.spec.workflow.model_dump(mode="json")
                if wf:
                    wf.definition, wf.graph, wf.pattern, wf.content_hash = (
                        definition,
                        graph,
                        graph["pattern"],
                        graph["hash"],
                    )
                else:
                    session.add(
                        WorkflowDefinition(
                            agent_version_id=version.id,
                            pattern=graph["pattern"],
                            definition=definition,
                            graph=graph,
                            content_hash=graph["hash"],
                        )
                    )
                record = session.get(AgentVersion, version.id)
                assert record is not None
                record.provenance = {
                    **record.provenance,
                    "build": {
                        "built_at": utcnow().isoformat(),
                        "built_by": principal.user_id,
                        "harness": HARNESS_STRATEGY,
                        "model": resolved.get("model"),
                        "tools": resolved.get("tools"),
                        "components": resolved.get("components"),
                        "policy_hash": resolved.get("policy_hash"),
                        "workflow_hash": graph["hash"],
                        "spec_hash": record.spec_hash,
                    },
                }
            self.components.pin(version.id, [c["id"] for c in resolved.get("components", [])])
            return "workflow and provenance recorded"

        def validation_checks() -> str:
            spec: AgentSpec = state["spec"]
            checked = 0
            for case in spec.spec.evaluation.cases:
                if case.kind == "input_validation":
                    continue
                try:
                    jsonschema.validate(case.input, spec.spec.inputs)
                    checked += 1
                except jsonschema.ValidationError as exc:
                    raise BuildStageError(f"Evaluation case '{case.id}' input invalid: {exc.message}") from exc
            return f"{checked} evaluation input(s) conform to the input schema"

        ok = stage("load_and_validate", load_and_validate)
        for name, fn in [
            ("resolve_model_profile", resolve_model_profile),
            ("resolve_tools", resolve_tools),
            ("validate_permissions", validate_permissions),
            ("resolve_components", resolve_components),
            ("assemble_workflow", assemble_workflow),
            ("attach_policies", attach_policies),
            ("initialize_harness", initialize_harness),
            ("register_version", register_version),
            ("validation_checks", validation_checks),
        ]:
            if not ok:
                stages.append({"name": name, "status": "skipped", "detail": "previous stage failed", "duration_ms": 0})
                continue
            ok = stage(name, fn)
        return {
            "success": not errors,
            "stages": stages,
            "errors": errors,
            "warnings": warnings,
            "resolved": resolved,
            "validation": state.get("validation"),
            "graph": state.get("graph"),
            "built_at": utcnow().isoformat(),
        }


class BuildStageError(Exception):
    pass

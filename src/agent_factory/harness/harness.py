"""The governed AI harness.

``Harness`` is the extension interface: one method per workflow step kind. ``GovernedHarness`` is the
default strategy. It owns the lifecycle of every model and tool interaction:

* context assembly (ContextManager) with untrusted-content delimiting and token budgeting,
* model calls through the gateway with capability handling, retries, timeouts and usage accounting,
* tool discovery filtered by policy, and tool invocation through
  idempotency → policy decision → (approval interrupt) → sandboxed execution → output validation,
* deterministic input/output validation,
* bounded delegation to specialist agents,
* budget enforcement and cancellation checks before every consequential operation.

Interrupt/resume semantics: LangGraph re-executes an interrupted node from the start on resume. Every
side-effecting operation inside a node is therefore keyed by an idempotency key and recorded in
``tool_invocations``; on re-execution completed operations return their stored result. The database —
never the resume payload — is authoritative for approval decisions.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from sqlalchemy import select

from ..core.config import Settings
from ..core.errors import (
    AgentFactoryError,
    BudgetExceededError,
    ExecutionCancelledError,
    NotFoundError,
    ProviderError,
)
from ..core.logging import get_logger
from ..core.redaction import redact
from ..governance.approvals import ApprovalService
from ..governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from ..governance.rbac import Principal
from ..memory.conversation import AgentMemory, ConversationMemory
from ..memory.knowledge import KnowledgeService
from ..model_gateway.gateway import ModelGateway
from ..model_gateway.types import ChatMessage, ChatRequest, ChatResponse, ToolCall, ToolSpec
from ..observability.telemetry import span
from ..spec.models import AgentSpec, ModelSpec, WorkflowStep
from ..storage.db import Database
from ..storage.models import ToolInvocation
from ..tools.base import ToolContext
from ..tools.registry import ToolExecutor, ToolRegistry
from ..tools.sandbox import build_sandbox
from .budget import BudgetTracker
from .context import ContextBlock, ContextManager, untrusted_tool_output
from .events import EventRecorder
from .templating import render

log = get_logger(__name__)

DELEGATE_PREFIX = "delegate_to_"
# Failed invocations are not terminal: recovery re-attempts them (completed/denied/rejected are final).
TERMINAL_INVOCATION = {"completed", "denied", "rejected"}


class StepFailedError(AgentFactoryError):
    status_code = 500
    code = "step_failed"


class OutputValidationError(AgentFactoryError):
    status_code = 422
    code = "output_validation_failed"


@dataclass
class ExecutionContext:
    execution_id: str
    workspace_id: str
    agent_id: str
    agent_name: str
    version_id: str
    version: str
    spec: AgentSpec
    principal: Principal | None
    requested_by: str | None
    budget: BudgetTracker
    events: EventRecorder
    options: dict[str, Any] = field(default_factory=dict)
    extra_instructions: list[str] = field(default_factory=list)
    validation_rules: list[dict[str, Any]] = field(default_factory=list)
    prompt_components: dict[str, str] = field(default_factory=dict)
    cancel_check: Callable[[], bool] = lambda: False
    persist_usage: Callable[[dict[str, Any]], None] = lambda usage: None
    depth: int = 0
    conversation_session_id: str | None = None

    @property
    def model(self) -> ModelSpec:
        override = self.options.get("model_override")
        if override:
            return ModelSpec.model_validate({**self.spec.spec.model.model_dump(), **override})
        return self.spec.spec.model


def scope_for(state: dict[str, Any], ctx: ExecutionContext) -> dict[str, Any]:
    return {"input": state.get("input", {}), "steps": state.get("steps", {}), "components": ctx.prompt_components}


def extract_json(text: str) -> Any:
    text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def validate_output(value: Any, schema: dict[str, Any], rules: list[dict[str, Any]] | None = None) -> list[str]:
    errors = [
        f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
        for e in jsonschema.Draft202012Validator(schema).iter_errors(value)
    ]
    for rule in rules or []:
        errors += [
            f"rule {rule.get('name', '?')}: {e.message}"
            for e in jsonschema.Draft202012Validator(rule["schema"]).iter_errors(value)
        ]
    return errors[:20]


class Harness(ABC):
    """Execution strategy interface. Each method returns a LangGraph state update."""

    # ================================================================== node wrapper
    def run_node(
        self,
        ctx: ExecutionContext,
        node_id: str,
        kind: str,
        fn: Callable[[], dict[str, Any]],
        step: WorkflowStep | None = None,
    ) -> dict[str, Any]:
        if ctx.cancel_check():
            raise ExecutionCancelledError("Execution cancelled")
        ctx.budget.start_step()
        started = time.perf_counter()
        ctx.events.emit("step.started", node_id=node_id, status="running", kind=kind)
        with span("workflow.node", node=node_id, kind=kind, execution_id=ctx.execution_id):
            try:
                update = fn()
            except GraphInterrupt:
                ctx.budget.usage["steps"] -= 1  # the node re-runs on resume; do not double count
                ctx.events.emit("step.paused", node_id=node_id, status="waiting_approval")
                ctx.persist_usage(ctx.budget.snapshot())
                raise
            except (BudgetExceededError, ExecutionCancelledError):
                ctx.events.emit(
                    "step.failed",
                    node_id=node_id,
                    status="failed",
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    error="terminated",
                )
                ctx.persist_usage(ctx.budget.snapshot())
                raise
            except AgentFactoryError as exc:
                duration = round((time.perf_counter() - started) * 1000, 2)
                ctx.events.emit(
                    "step.failed",
                    node_id=node_id,
                    status="failed",
                    duration_ms=duration,
                    error={"code": exc.code, "message": exc.message},
                )
                ctx.persist_usage(ctx.budget.snapshot())
                if step is not None and step.on_error == "continue":
                    return {
                        "steps": {
                            step.id: {
                                "status": "failed",
                                "kind": kind,
                                "error": {"code": exc.code, "message": exc.message},
                            }
                        }
                    }
                raise
        duration = round((time.perf_counter() - started) * 1000, 2)
        step_state = (update.get("steps") or {}).get(step.id if step else node_id, {})
        ctx.events.emit(
            "step.completed",
            node_id=node_id,
            status=step_state.get("status", "completed"),
            duration_ms=duration,
            output=step_state.get("output"),
        )
        ctx.persist_usage(ctx.budget.snapshot())
        return update

    @abstractmethod
    def model_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def tool_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def agent_loop_model(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def agent_loop_tools(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def validate_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def approval_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def delegate_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def retrieve_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]: ...

    @abstractmethod
    def finalize(self, ctx: ExecutionContext, state: dict[str, Any]) -> dict[str, Any]: ...


DelegateRunner = Callable[[ExecutionContext, str, dict[str, Any], str], dict[str, Any]]


class GovernedHarness(Harness):
    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        gateway: ModelGateway,
        tools: ToolRegistry,
        executor: ToolExecutor,
        policy: PolicyEngine,
        approvals: ApprovalService,
        knowledge: KnowledgeService,
        conversation: ConversationMemory,
        agent_memory: AgentMemory,
    ) -> None:
        self.settings = settings
        self.db = db
        self.gateway = gateway
        self.tools = tools
        self.executor = executor
        self.policy = policy
        self.approvals = approvals
        self.knowledge = knowledge
        self.conversation = conversation
        self.agent_memory = agent_memory
        self.delegate_runner: DelegateRunner | None = None

    # ================================================================== model interaction
    def _tool_specs(self, ctx: ExecutionContext, names: list[str]) -> list[ToolSpec]:
        """Tool discovery: offer only tools the policy would not deny outright (approval-gated tools are offered)."""
        specs: list[ToolSpec] = []
        sandbox_ok = build_sandbox(self.settings).available()[0]
        hosts = self.policy.allowed_hosts(ctx.spec.spec)
        for name in names:
            if not self.tools.has(name):
                continue
            tool = self.tools.get(name)
            definition = tool.definition
            probe_args = {"url": f"https://{hosts[0].lstrip('*.')}/"} if (definition.network and hosts) else {}
            decision = self.policy.evaluate_tool(
                ToolAuthorizationRequest(
                    principal=ctx.principal,
                    workspace_id=ctx.workspace_id,
                    agent=ctx.spec.spec,
                    tool_name=name,
                    tool=definition,
                    tool_enabled=self.tools.is_enabled(name),
                    arguments=probe_args,
                    sandbox_available=sandbox_ok,
                    destination_host=tool.network_destination(probe_args, self.settings)
                    if definition.network
                    else None,
                    destination_resolved=definition.network,
                )
            )
            if decision.effect == Effect.DENY:
                continue
            specs.append(ToolSpec(**definition.model_facing()))
        return specs

    def _delegate_specs(self, ctx: ExecutionContext) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=f"{DELEGATE_PREFIX}{sp.id}",
                description=f"Delegate a focused sub-task to the '{sp.id}' specialist ({sp.agent}). "
                f"{sp.description}".strip(),
                parameters={
                    "type": "object",
                    "required": ["task"],
                    "properties": {
                        "task": {"type": "string", "minLength": 1, "description": "Self-contained instructions."}
                    },
                },
            )
            for sp in ctx.spec.spec.workflow.specialists
        ]

    def call_model(
        self,
        ctx: ExecutionContext,
        node_id: str,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> ChatResponse:
        if ctx.cancel_check():
            raise ExecutionCancelledError("Execution cancelled")
        model = ctx.model
        caps = self.gateway.capabilities(model.provider, model.model)
        tools = tools or []
        native_tools = bool(tools) and caps.tools
        request_messages = list(messages)
        if tools and not native_tools:
            protocol = (
                "Tools are available via a JSON protocol. To call tools reply ONLY with "
                '{"tool_calls": [{"name": "<tool>", "arguments": {...}}]}. When done, reply with your final '
                "answer instead.\nTools:\n" + json.dumps([t.model_dump() for t in tools])
            )
            request_messages[0] = ChatMessage(role="system", content=request_messages[0].content + "\n\n" + protocol)
        ctx.budget.before_model_call()
        limits = ctx.spec.spec.execution_limits
        request = ChatRequest(
            model=model.model,
            messages=request_messages,
            tools=tools if native_tools else [],
            response_schema=schema if caps.structured_output else None,
            temperature=model.temperature,
            max_output_tokens=min(model.max_output_tokens or limits.max_output_tokens, limits.max_output_tokens),
            timeout_seconds=ctx.budget.model_timeout(model.timeout_seconds),
            context_window=limits.max_context_tokens,
            metadata={
                "execution_id": ctx.execution_id,
                "step_id": node_id,
                "mock_delay_seconds": ctx.options.get("mock_delay_seconds", 5),
            },
        )

        def on_retry(attempt: int, exc: ProviderError) -> None:
            ctx.budget.record_retry()
            ctx.events.emit("model.retry", node_id=node_id, attempt=attempt, error=exc.message)

        with span("model.call", provider=model.provider, model=model.model, execution_id=ctx.execution_id) as sp:
            try:
                response = self.gateway.chat(model.provider, request, max_retries=limits.max_retries, on_retry=on_retry)
            except ProviderError as exc:
                ctx.events.emit(
                    "model.error",
                    node_id=node_id,
                    status="failed",
                    provider=model.provider,
                    model=model.model,
                    error={"code": exc.code, "message": exc.message, "details": exc.details},
                )
                raise
            sp.set_attribute("tokens.input", response.usage.input_tokens or 0)
            sp.set_attribute("tokens.output", response.usage.output_tokens or 0)
        ctx.budget.record_model_call(response.usage.input_tokens, response.usage.output_tokens, response.cost_usd)
        if tools and not native_tools and not response.tool_calls:
            response.tool_calls = self._parse_json_protocol(response.content)
            if response.tool_calls:
                response.content = ""
        ctx.events.emit(
            "model.call",
            node_id=node_id,
            status="completed",
            duration_ms=response.latency_ms,
            provider=response.provider,
            model=response.model,
            finish_reason=response.finish_reason,
            usage=response.usage.model_dump(),
            cost_usd=response.cost_usd,
            tool_calls=[{"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls],
            native_tools=native_tools,
            content=response.content[:2000],
        )
        return response

    @staticmethod
    def _parse_json_protocol(content: str) -> list[ToolCall]:
        try:
            data = extract_json(content)
        except (json.JSONDecodeError, ValueError):
            return []
        calls = data.get("tool_calls") if isinstance(data, dict) else None
        if not isinstance(calls, list):
            return []
        return [
            ToolCall(id=f"json_{i}", name=str(c.get("name", "")), arguments=c.get("arguments") or {})
            for i, c in enumerate(calls)
            if isinstance(c, dict)
        ]

    def _structured_answer(
        self,
        ctx: ExecutionContext,
        node_id: str,
        messages: list[ChatMessage],
        first: ChatResponse | None,
        schema: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Parse + validate structured output, re-asking with validation feedback up to max_retries."""
        attempts = ctx.spec.spec.execution_limits.max_retries + 1
        response = first
        transcript = list(messages)
        errors: list[str] = []
        for attempt in range(attempts):
            if response is None:
                response = self.call_model(ctx, node_id, transcript, schema=schema)
            try:
                value = extract_json(response.content)
                errors = validate_output(value, schema, ctx.validation_rules)
            except (json.JSONDecodeError, ValueError) as exc:
                value, errors = None, [f"Response is not valid JSON: {exc}"]
            if not errors:
                return value, []
            ctx.events.emit(
                "validation.failed",
                node_id=node_id,
                status="retrying" if attempt + 1 < attempts else "failed",
                errors=errors,
                attempt=attempt + 1,
            )
            if attempt + 1 < attempts:
                ctx.budget.record_retry()
                transcript += [
                    ChatMessage(role="assistant", content=response.content[:4000]),
                    ChatMessage(
                        role="user",
                        content="Your previous answer failed validation:\n- "
                        + "\n- ".join(errors)
                        + "\nReturn ONLY a corrected JSON object.",
                    ),
                ]
                response = None
        return {"_invalid_output": response.content[:4000] if response else ""}, errors

    # ================================================================== context helpers
    def _context_inputs(
        self, ctx: ExecutionContext, state: dict[str, Any], upto: str
    ) -> tuple[list[ContextBlock], list[dict[str, Any]]]:
        blocks: list[ContextBlock] = []
        documents: list[dict[str, Any]] = []
        for step in ctx.spec.spec.workflow.steps:
            if step.id == upto:
                break
            result = state.get("steps", {}).get(step.id)
            if not result or result.get("status") not in {"completed", "rejected", "failed"}:
                continue
            payload = (
                result.get("output")
                if result.get("status") == "completed"
                else {"status": result.get("status"), "error": result.get("error")}
            )
            if step.kind == "retrieve":
                documents += (result.get("output") or {}).get("results", [])
            elif step.kind in {"tool", "delegate"}:
                label = step.tool if step.kind == "tool" else f"delegate:{step.agent}"
                blocks.append(
                    ContextBlock(
                        "step_output", untrusted_tool_output(label or step.id, payload, step.id), 80, source=step.id
                    )
                )
            elif step.kind in {"model", "agent_loop"}:
                blocks.append(
                    ContextBlock(
                        "step_output",
                        f'<step_output step="{step.id}">\n{json.dumps(payload, default=str)}\n</step_output>',
                        70,
                        source=step.id,
                    )
                )
        return blocks, documents

    def _memory_inputs(self, ctx: ExecutionContext) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        memory = ctx.spec.spec.memory
        history = (
            self.conversation.history(ctx.conversation_session_id, memory.conversation.max_messages)
            if memory.conversation.enabled and ctx.conversation_session_id
            else []
        )
        items = self.agent_memory.items(ctx.agent_id, memory.agent.max_items) if memory.agent.enabled else []
        return history, items

    def _build_messages(
        self,
        ctx: ExecutionContext,
        state: dict[str, Any],
        step: WorkflowStep,
        schema: dict[str, Any] | None,
        default_prompt: str,
    ) -> list[ChatMessage]:
        blocks, documents = self._context_inputs(ctx, state, step.id)
        history, memory = self._memory_inputs(ctx)
        cm = ContextManager(ctx.spec.spec, ctx.extra_instructions)
        knowledge = ctx.spec.spec.memory.knowledge
        from .context import ContextReport

        selection_report = ContextReport()
        documents = cm.select_documents(
            documents, top_k=knowledge.top_k, min_score=knowledge.min_score, report=selection_report
        )
        prompt = render(step.prompt, scope_for(state, ctx)) if step.prompt else default_prompt
        messages, report = cm.build(
            task_input=state.get("input", {}),
            prompt=prompt,
            step_outputs=blocks,
            documents=documents,
            history=history,
            memory=memory,
            structured_schema=schema,
        )
        report.filtered_documents += selection_report.filtered_documents
        if report.truncated or report.dropped or report.filtered_documents:
            ctx.events.emit("context.managed", node_id=step.id, **report.to_dict())
        return messages

    # ================================================================== step kinds
    def model_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        schema = ctx.spec.spec.outputs if step.structured else None
        messages = self._build_messages(ctx, state, step, schema, "Complete the task described in the input.")
        response = self.call_model(ctx, step.id, messages, schema=schema)
        if schema:
            output, errors = self._structured_answer(ctx, step.id, messages, response, schema)
            return {
                "steps": {
                    step.id: {
                        "status": "completed",
                        "kind": "model",
                        "output": output,
                        "schema_valid": not errors,
                        "validation_errors": errors,
                    }
                }
            }
        return {"steps": {step.id: {"status": "completed", "kind": "model", "output": {"text": response.content}}}}

    def agent_loop_model(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        schema = ctx.spec.spec.outputs if step.structured else None
        stored = state.get("loops", {}).get(step.id)
        transcript = (
            [ChatMessage.model_validate(m) for m in stored]
            if stored
            else self._build_messages(
                ctx, state, step, schema, "Complete the task described in the input. Use tools when they help."
            )
        )
        iteration = state.get("iterations", {}).get(step.id, 0)
        names = step.tools if step.tools is not None else ctx.spec.spec.tool_names
        tools = self._tool_specs(ctx, names)
        if ctx.spec.spec.workflow.type == "supervisor":
            tools += self._delegate_specs(ctx)
        final_only = iteration >= step.max_iterations
        response = self.call_model(ctx, step.id, transcript, tools=[] if final_only else tools, schema=schema)
        if response.tool_calls and not final_only:
            transcript.append(response.as_message())
            return {
                "loops": {step.id: [m.model_dump() for m in transcript]},
                "pending": {step.id: [tc.model_dump() for tc in response.tool_calls]},
                "iterations": {step.id: iteration + 1},
            }
        if schema:
            output, errors = self._structured_answer(ctx, step.id, transcript, response, schema)
            result = {
                "status": "completed",
                "kind": "agent_loop",
                "output": output,
                "schema_valid": not errors,
                "validation_errors": errors,
                "iterations": iteration,
            }
        else:
            result = {
                "status": "completed",
                "kind": "agent_loop",
                "output": {"text": response.content},
                "iterations": iteration,
            }
        transcript.append(response.as_message())
        return {
            "loops": {step.id: [m.model_dump() for m in transcript]},
            "pending": {step.id: []},
            "steps": {step.id: result},
        }

    def agent_loop_tools(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        transcript = [ChatMessage.model_validate(m) for m in state.get("loops", {}).get(step.id, [])]
        calls = [ToolCall.model_validate(c) for c in state.get("pending", {}).get(step.id, [])]
        specialists = {f"{DELEGATE_PREFIX}{sp.id}": sp for sp in ctx.spec.spec.workflow.specialists}
        for call in calls:
            if call.name in specialists and ctx.spec.spec.workflow.type == "supervisor":
                result = self.delegate(
                    ctx,
                    step.id,
                    call.id,
                    specialists[call.name].agent,
                    state.get("input", {}),
                    str(call.arguments.get("task", "")),
                )
            else:
                result = self.invoke_tool(ctx, step.id, call.id, call.name, call.arguments)
            transcript.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=call.id,
                    name=call.name,
                    content=untrusted_tool_output(call.name, self._model_payload(result), step.id),
                )
            )
        return {"loops": {step.id: [m.model_dump() for m in transcript]}, "pending": {step.id: []}}

    @staticmethod
    def _model_payload(result: dict[str, Any]) -> Any:
        if result.get("ok"):
            return result.get("output")
        return {
            "error": result.get("error", {}).get("code", "error"),
            "message": result.get("error", {}).get("message", ""),
            "note": "This action was NOT performed.",
        }

    def tool_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        assert step.tool
        arguments = render(step.arguments, scope_for(state, ctx))
        result = self.invoke_tool(ctx, step.id, step.id, step.tool, arguments, rationale=step.description)
        if result.get("ok"):
            return {
                "steps": {
                    step.id: {"status": "completed", "kind": "tool", "tool": step.tool, "output": result["output"]}
                }
            }
        code = result.get("error", {}).get("code", "tool_error")
        if code.startswith("approval_"):
            return {
                "steps": {step.id: {"status": "rejected", "kind": "tool", "tool": step.tool, "error": result["error"]}}
            }
        raise StepFailedError(
            f"Tool step '{step.id}' failed: {result.get('error', {}).get('message', code)}", details=result.get("error")
        )

    def validate_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        steps = ctx.spec.spec.workflow.steps
        index = next(i for i, s in enumerate(steps) if s.id == step.id)
        target_id = step.target or next(s.id for s in reversed(steps[:index]) if s.kind in {"model", "agent_loop"})
        target = state.get("steps", {}).get(target_id, {})
        errors = validate_output(target.get("output"), ctx.spec.spec.outputs, ctx.validation_rules)
        if errors:
            ctx.events.emit("validation.failed", node_id=step.id, status="failed", target=target_id, errors=errors)
            raise OutputValidationError(f"Output of '{target_id}' failed validation", details=errors)
        ctx.events.emit("validation.passed", node_id=step.id, status="completed", target=target_id)
        return {
            "steps": {
                step.id: {
                    "status": "completed",
                    "kind": "validate",
                    "target": target_id,
                    "output": target.get("output"),
                }
            }
        }

    def approval_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        assert step.action
        key = f"{ctx.execution_id}:{step.id}:checkpoint"
        evidence = {k: v.get("output") for k, v in state.get("steps", {}).items() if v.get("status") == "completed"}
        decision = self._await_approval(
            ctx,
            key,
            step.id,
            action=step.action,
            tool_name=None,
            arguments={},
            rationale=render(step.rationale, scope_for(state, ctx)) or step.description,
            evidence=redact(evidence),
            risk="high",
        )
        if decision["status"] == "approved":
            return {"steps": {step.id: {"status": "completed", "kind": "approval", "output": decision}}}
        return {
            "steps": {step.id: {"status": "rejected", "kind": "approval", "output": decision}},
            "halted": {
                "reason": f"approval_{decision['status']}",
                "step": step.id,
                "message": f"Checkpoint '{step.id}' was {decision['status']}",
            },
        }

    def delegate_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        assert step.agent
        task = render(step.task, scope_for(state, ctx)) if step.task else json.dumps(state.get("input", {}))
        result = self.delegate(ctx, step.id, step.id, step.agent, state.get("input", {}), str(task))
        if not result.get("ok"):
            raise StepFailedError(
                f"Delegation '{step.id}' failed: {result.get('error', {}).get('message')}", details=result.get("error")
            )
        return {
            "steps": {
                step.id: {
                    "status": "completed",
                    "kind": "delegate",
                    "agent": step.agent,
                    "output": result["output"],
                    "child_execution_id": result.get("child_execution_id"),
                }
            }
        }

    def retrieve_step(self, ctx: ExecutionContext, state: dict[str, Any], step: WorkflowStep) -> dict[str, Any]:
        knowledge = ctx.spec.spec.memory.knowledge
        collections = [c for c in (step.collections or knowledge.collections) if c in knowledge.collections]
        query = str(render(step.query, scope_for(state, ctx)) or "")
        started = time.perf_counter()
        hits = self.knowledge.search(
            ctx.workspace_id, collections, query, top_k=step.top_k, min_score=knowledge.min_score
        )
        ctx.events.emit(
            "retrieval",
            node_id=step.id,
            status="completed",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            query=query,
            collections=collections,
            hits=len(hits),
            sources=[h.source for h in hits],
        )
        return {
            "steps": {
                step.id: {
                    "status": "completed",
                    "kind": "retrieve",
                    "output": {"query": query, "results": [h.model_dump() for h in hits]},
                }
            }
        }

    def finalize(self, ctx: ExecutionContext, state: dict[str, Any]) -> dict[str, Any]:
        halted = state.get("halted")
        steps = ctx.spec.spec.workflow.steps
        final_step = next((s for s in steps if s.final), None) or next(
            (s for s in reversed(steps) if s.kind in {"model", "agent_loop", "validate", "delegate"}), None
        )
        result = state.get("steps", {}).get(final_step.id, {}) if final_step else {}
        output = result.get("output")
        errors = validate_output(output, ctx.spec.spec.outputs) if output is not None else ["no output produced"]
        final = {
            "output": output,
            "schema_valid": not errors,
            "validation_errors": errors,
            "final_step": final_step.id if final_step else None,
            "halted": halted,
        }
        if not halted and errors:
            ctx.events.emit("validation.failed", node_id="finalize", status="failed", errors=errors)
        return {"final": final}

    # ================================================================== tool invocation
    def _invocation(self, key: str) -> ToolInvocation | None:
        with self.db.session() as session:
            row = session.scalars(select(ToolInvocation).where(ToolInvocation.idempotency_key == key)).first()
            if row:
                session.expunge(row)
            return row

    def _save_invocation(self, key: str, **fields: Any) -> ToolInvocation:
        with self.db.transaction() as session:
            row = session.scalars(select(ToolInvocation).where(ToolInvocation.idempotency_key == key)).first()
            if row is None:
                row = ToolInvocation(idempotency_key=key, **fields)
                session.add(row)
            else:
                for name, value in fields.items():
                    setattr(row, name, value)
            session.flush()
            session.expunge(row)
            return row

    def _await_approval(
        self,
        ctx: ExecutionContext,
        key: str,
        step_id: str,
        *,
        action: str,
        tool_name: str | None,
        arguments: dict[str, Any],
        rationale: str,
        evidence: dict[str, Any],
        risk: str,
        invocation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create (once) and wait for an approval. Returns the decided approval as a dict."""
        approval_key = f"approval:{key}"
        marker = self._invocation(approval_key)
        if marker is None:
            approval = self.approvals.create(
                workspace_id=ctx.workspace_id,
                execution_id=ctx.execution_id,
                step_id=step_id,
                action=action,
                tool_name=tool_name,
                arguments=redact(arguments),
                rationale=rationale[:4000],
                evidence=evidence,
                risk=risk,
                requested_by=ctx.requested_by,
                expires_after_seconds=ctx.spec.spec.human_approval.expires_after_seconds,
                tool_invocation_id=invocation_id,
            )
            marker = self._save_invocation(
                approval_key,
                execution_id=ctx.execution_id,
                workspace_id=ctx.workspace_id,
                step_id=step_id,
                tool_call_id="approval",
                tool_name=tool_name or action,
                tool_version="-",
                arguments={},
                status="awaiting_approval",
                approval_id=approval.id,
            )
            ctx.events.emit(
                "approval.requested",
                node_id=step_id,
                status="waiting_approval",
                approval_id=approval.id,
                action=action,
                tool=tool_name,
                risk=risk,
                rationale=rationale[:500],
            )
        assert marker.approval_id
        auto = ctx.options.get("auto_approval")  # evaluation-only: simulated reviewer decision
        while True:
            current = self.approvals.get_raw(marker.approval_id)
            if current is None:
                raise NotFoundError("Approval record disappeared")
            if (
                current.status == "pending"
                and auto in {"approve", "reject"}
                and ctx.options.get("mode") == "evaluation"
            ):
                from ..governance.rbac import make_principal

                evaluator = make_principal(
                    "evaluation", "evaluation", "Evaluation reviewer", ctx.workspace_id, "admin", "evaluation"
                )
                self.approvals.decide(
                    evaluator,
                    current.id,
                    "approved" if auto == "approve" else "rejected",
                    "Simulated decision configured by the evaluation case",
                )
                continue
            if current.status != "pending":
                if marker.status == "awaiting_approval":
                    self._save_invocation(approval_key, status="completed", decision={"status": current.status})
                    ctx.events.emit(
                        "approval.decided",
                        node_id=step_id,
                        status=current.status,
                        approval_id=current.id,
                        decided_by=current.decided_by,
                    )
                return {
                    "approval_id": current.id,
                    "status": current.status,
                    "decided_by": current.decided_by,
                    "comment": current.decision_comment,
                }
            interrupt({"approval_id": current.id, "step_id": step_id, "action": action, "tool": tool_name})
            # Resumed: re-read the decision from the database (never trust the resume payload).

    def invoke_tool(
        self,
        ctx: ExecutionContext,
        step_id: str,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        rationale: str = "",
    ) -> dict[str, Any]:
        key = f"{ctx.execution_id}:{step_id}:{call_id}"
        existing = self._invocation(key)
        if existing and existing.status in TERMINAL_INVOCATION:
            ctx.events.emit("tool.replayed", node_id=step_id, tool=tool_name, status=existing.status)
            return existing.result or {"ok": False, "error": {"code": existing.status, "message": "Not performed"}}

        tool = self.tools.get(tool_name) if self.tools.has(tool_name) else None
        definition = tool.definition if tool else None
        safe_args = redact(arguments)
        base = {
            "execution_id": ctx.execution_id,
            "workspace_id": ctx.workspace_id,
            "step_id": step_id,
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "tool_version": definition.version if definition else "-",
            "arguments": safe_args if (not definition or definition.audit.log_arguments) else {"redacted": True},
        }
        approval_granted = False
        if existing and existing.status == "awaiting_approval":
            approval_decision = self._await_approval(
                ctx,
                key,
                step_id,
                action="",
                tool_name=tool_name,
                arguments=arguments,
                rationale=rationale,
                evidence={},
                risk="",
            )
            if approval_decision["status"] != "approved":
                result = {
                    "ok": False,
                    "error": {
                        "code": f"approval_{approval_decision['status']}",
                        "message": f"Human approver decision: {approval_decision['status']}. "
                        f"{approval_decision.get('comment') or ''}".strip(),
                    },
                }
                self._save_invocation(key, status="rejected", result=result, decision={"approval": approval_decision})
                ctx.events.emit(
                    "tool.rejected",
                    node_id=step_id,
                    status="rejected",
                    tool=tool_name,
                    approval=approval_decision["status"],
                )
                return result
            approval_granted = True
        else:
            ctx.budget.before_tool_call()

        sandbox_ok = (
            build_sandbox(self.settings).available()[0] if definition and definition.requires_sandbox else False
        )
        decision = self.policy.evaluate_tool(
            ToolAuthorizationRequest(
                principal=ctx.principal,
                workspace_id=ctx.workspace_id,
                agent=ctx.spec.spec,
                tool_name=tool_name,
                tool=definition,
                tool_enabled=self.tools.is_enabled(tool_name),
                arguments=arguments,
                sandbox_available=sandbox_ok,
                approval_already_granted=approval_granted,
                destination_host=tool.network_destination(arguments, self.settings)
                if tool and definition and definition.network
                else None,
                destination_resolved=bool(tool and definition and definition.network),
            )
        )

        if decision.effect == Effect.DENY:
            result = {
                "ok": False,
                "error": {"code": "denied_by_policy", "message": "; ".join(decision.reasons)},
                "policy": decision.to_dict(),
            }
            self._save_invocation(key, **base, status="denied", result=result, decision=decision.to_dict())
            ctx.budget.record_tool_call()
            ctx.events.emit(
                "tool.denied",
                node_id=step_id,
                status="denied",
                tool=tool_name,
                arguments=safe_args,
                reasons=decision.reasons,
                rules=decision.rules,
            )
            from ..governance.audit import AuditLog

            AuditLog(self.db).record(
                "tool.denied",
                "tool",
                tool_name,
                outcome="denied",
                actor_id=ctx.requested_by,
                workspace_id=ctx.workspace_id,
                details={"execution_id": ctx.execution_id, "rules": decision.rules, "reasons": decision.reasons},
            )
            return result

        if decision.effect == Effect.REQUIRE_APPROVAL:
            assert definition is not None
            invocation = self._save_invocation(key, **base, status="awaiting_approval", decision=decision.to_dict())
            self._await_approval(
                ctx,
                key,
                step_id,
                action=decision.approval_action or tool_name,
                tool_name=tool_name,
                arguments=arguments,
                rationale=rationale or "; ".join(decision.reasons),
                evidence={"policy": decision.to_dict()},
                risk=str(definition.risk),
                invocation_id=invocation.id,
            )
            return self.invoke_tool(ctx, step_id, call_id, tool_name, arguments, rationale=rationale)

        assert tool is not None
        assert definition is not None
        if not existing:
            self._save_invocation(key, **base, status="pending", decision=decision.to_dict())
        tool_ctx = ToolContext(
            workspace_id=ctx.workspace_id,
            execution_id=ctx.execution_id,
            agent_name=ctx.agent_name,
            settings=self.settings,
            idempotency_key=key,
            allowed_hosts=self.policy.allowed_hosts(ctx.spec.spec),
            allowed_collections=(
                ctx.spec.spec.memory.knowledge.collections if ctx.spec.spec.memory.knowledge.enabled else []
            ),
            knowledge=self.knowledge,
            fault_injection=ctx.options.setdefault("fault_injection", {}) or {},
        )
        with span("tool.call", tool=tool_name, execution_id=ctx.execution_id):
            outcome = self.executor.execute(
                tool, arguments, tool_ctx, max_retries=ctx.spec.spec.execution_limits.max_retries
            )
        for _ in range(outcome.attempts - 1):
            ctx.budget.record_retry()
        ctx.budget.record_tool_call()
        result = outcome.to_dict()
        self._save_invocation(
            key,
            status="completed" if outcome.ok else "failed",
            result=result,
            duration_ms=outcome.duration_ms,
            decision=decision.to_dict(),
        )
        ctx.events.emit(
            "tool.call",
            node_id=step_id,
            status="completed" if outcome.ok else "failed",
            duration_ms=outcome.duration_ms,
            tool=tool_name,
            tool_version=definition.version,
            arguments=safe_args,
            attempts=outcome.attempts,
            approval_granted=approval_granted,
            output=outcome.output if definition.audit.log_output or self.settings.capture_content else None,
            error=outcome.error,
        )
        return result

    # ================================================================== delegation
    def delegate(
        self, ctx: ExecutionContext, step_id: str, call_id: str, agent_ref: str, parent_input: dict[str, Any], task: str
    ) -> dict[str, Any]:
        key = f"{ctx.execution_id}:{step_id}:{call_id}:delegate"
        existing = self._invocation(key)
        if existing and existing.status in TERMINAL_INVOCATION:
            return existing.result or {"ok": False, "error": {"code": "failed"}}
        decision = self.policy.evaluate_delegation(ctx.spec.spec, int(ctx.budget.usage.get("delegations", 0)))
        if decision.effect == Effect.DENY:
            result = {"ok": False, "error": {"code": "delegation_denied", "message": "; ".join(decision.reasons)}}
            ctx.events.emit(
                "delegation.denied", node_id=step_id, status="denied", agent=agent_ref, reasons=decision.reasons
            )
            return result
        ctx.budget.before_delegation()
        if self.delegate_runner is None:
            raise StepFailedError("Delegation is not available in this runtime")
        self._save_invocation(
            key,
            execution_id=ctx.execution_id,
            workspace_id=ctx.workspace_id,
            step_id=step_id,
            tool_call_id=call_id,
            tool_name=f"delegate:{agent_ref}",
            tool_version="-",
            arguments={"task": task[:2000]},
            status="pending",
        )
        ctx.events.emit("delegation.started", node_id=step_id, status="running", agent=agent_ref, task=task[:1000])
        started = time.perf_counter()
        result = self.delegate_runner(ctx, agent_ref, parent_input, task)
        ctx.budget.record_delegation()
        self._save_invocation(
            key,
            status="completed" if result.get("ok") else "failed",
            result=result,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        ctx.events.emit(
            "delegation.completed",
            node_id=step_id,
            status="completed" if result.get("ok") else "failed",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            agent=agent_ref,
            child_execution_id=result.get("child_execution_id"),
            error=result.get("error"),
        )
        return result

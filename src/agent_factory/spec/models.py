"""The ``agentfactory/v1`` agent specification.

A specification is declarative data. It never contains executable code or credentials. It is validated
structurally here (Pydantic) and semantically by ``spec.validator`` against the tool registry, model
providers and workspace before an agent version can be built and executed.

Lifecycle status, identifiers and timestamps belong to the *version record*, not the document, so that
a published document is immutable and content-addressable (``spec_hash``).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core import semver

API_VERSION: Final = "agentfactory/v1"
SUPPORTED_API_VERSIONS = (API_VERSION,)

NAME_RE = r"^[a-z][a-z0-9-]{1,62}$"
STEP_ID_RE = r"^[a-z][a-z0-9_]{0,62}$"
AGENT_REF_RE = re.compile(r"^(?P<name>[a-z][a-z0-9-]{1,62})(?:@(?P<version>[0-9][0-9A-Za-z.\-]*))?$")


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LifecycleStatus(StrEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    TESTING = "testing"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"
    FAILED = "failed"


EXECUTABLE_STATUSES = {
    LifecycleStatus.TESTING,
    LifecycleStatus.APPROVED,
    LifecycleStatus.PUBLISHED,
    LifecycleStatus.DEPRECATED,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
class Metadata(_Strict):
    name: Annotated[str, Field(pattern=NAME_RE, description="Stable slug, unique per workspace.")]
    display_name: str = Field("", max_length=200)
    version: str = Field("0.1.0", description="Semantic version of this specification.")
    description: str = Field("", max_length=4000)
    labels: dict[str, str] = Field(default_factory=dict)
    template: str | None = Field(None, description="Template the spec was derived from (name@version).")

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        semver.parse(value)
        return value


Capability = Literal["chat", "tools", "structured_output", "streaming", "vision", "embeddings"]


class ModelSpec(_Strict):
    provider: str = Field("mock", description="Model provider id: mock, ollama, openai_compatible, anthropic.")
    model: str = Field("mock-default", min_length=1, max_length=200)
    profile: str | None = Field(None, description="Optional registered model profile name.")
    temperature: float = Field(0.0, ge=0, le=2)
    max_output_tokens: int | None = Field(None, ge=1, le=200_000)
    required_capabilities: list[Capability] = Field(default_factory=lambda: list[Capability](["chat"]))
    timeout_seconds: float = Field(120, gt=0, le=3600)


class ToolRef(_Strict):
    name: str = Field(min_length=1, max_length=120)
    version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            name, _, version = value.partition("@")
            return {"name": name, "version": version or None}
        return value


StepKind = Literal["model", "tool", "agent_loop", "validate", "approval", "delegate", "retrieve"]


class WorkflowStep(_Strict):
    id: Annotated[str, Field(pattern=STEP_ID_RE)]
    kind: StepKind
    description: str = Field("", max_length=1000)
    final: bool = Field(False, description="This step's output is the agent's final output.")
    on_error: Literal["fail", "continue"] = "fail"
    # model / agent_loop
    prompt: str | None = Field(
        None, max_length=20_000, description="Template; supports {{input.x}} and {{steps.<id>.output}}."
    )
    structured: bool = Field(False, description="Request output conforming to spec.outputs.")
    tools: list[str] | None = Field(None, description="agent_loop: subset of the agent's tools (default all).")
    max_iterations: int = Field(6, ge=1, le=50)
    # tool
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # validate
    target: str | None = Field(None, description="Step whose output is validated (default: previous model step).")
    # approval
    action: str | None = Field(None, description="Action class requiring a human decision.")
    rationale: str = ""
    # delegate
    agent: str | None = Field(None, description="Specialist agent reference name[@version].")
    task: str | None = Field(None, max_length=20_000)
    # retrieve
    query: str | None = None
    top_k: int = Field(5, ge=1, le=50)
    collections: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _kind_fields(self) -> WorkflowStep:
        required: dict[str, list[str]] = {
            "tool": ["tool"],
            "approval": ["action"],
            "delegate": ["agent"],
            "retrieve": ["query"],
        }
        for field in required.get(self.kind, []):
            if not getattr(self, field):
                raise ValueError(f"step '{self.id}' of kind '{self.kind}' requires '{field}'")
        if self.agent and not AGENT_REF_RE.match(self.agent):
            raise ValueError(f"step '{self.id}': invalid agent reference '{self.agent}'")
        return self


class Specialist(_Strict):
    id: Annotated[str, Field(pattern=STEP_ID_RE)]
    agent: str = Field(description="Agent reference name[@version]. Must be built and executable.")
    description: str = Field("", max_length=1000)

    @field_validator("agent")
    @classmethod
    def _ref(cls, value: str) -> str:
        if not AGENT_REF_RE.match(value):
            raise ValueError(f"invalid agent reference '{value}'")
        return value


WorkflowType = Literal["single", "sequential", "supervisor", "graph"]


class WorkflowSpec(_Strict):
    type: WorkflowType = "single"
    steps: list[WorkflowStep] = Field(default_factory=list)
    specialists: list[Specialist] = Field(default_factory=list)
    max_delegations: int = Field(6, ge=1, le=20)

    @model_validator(mode="after")
    def _defaults(self) -> WorkflowSpec:
        if not self.steps and self.type == "single":
            self.steps = [WorkflowStep(id="respond", kind="agent_loop", final=True, structured=True)]
        ids = [s.id for s in self.steps]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate step ids: {sorted(dupes)}")
        spec_ids = [s.id for s in self.specialists]
        if len(spec_ids) != len(set(spec_ids)):
            raise ValueError("duplicate specialist ids")
        return self


class ConversationMemory(_Strict):
    enabled: bool = False
    max_messages: int = Field(20, ge=1, le=500)


class AgentMemory(_Strict):
    enabled: bool = False
    max_items: int = Field(50, ge=1, le=1000)


class KnowledgeMemory(_Strict):
    enabled: bool = False
    collections: list[str] = Field(default_factory=list)
    top_k: int = Field(5, ge=1, le=50)
    min_score: float = Field(0.0, ge=0)


class MemorySpec(_Strict):
    enabled: bool | None = Field(None, description="Shorthand: false disables all memory.")
    conversation: ConversationMemory = Field(default_factory=ConversationMemory)
    agent: AgentMemory = Field(default_factory=AgentMemory)
    knowledge: KnowledgeMemory = Field(default_factory=KnowledgeMemory)

    @model_validator(mode="after")
    def _shorthand(self) -> MemorySpec:
        if self.enabled is False:
            self.conversation.enabled = self.agent.enabled = self.knowledge.enabled = False
        return self


class ExecutionLimits(_Strict):
    max_steps: int = Field(20, ge=1, le=500)
    max_model_calls: int = Field(20, ge=0, le=1000)
    max_tool_calls: int = Field(30, ge=0, le=1000)
    timeout_seconds: int = Field(300, ge=1, le=86_400)
    max_retries: int = Field(2, ge=0, le=10)
    max_context_tokens: int = Field(16_000, ge=256, le=2_000_000)
    max_output_tokens: int = Field(2_048, ge=16, le=200_000)
    max_cost_usd: float | None = Field(None, ge=0)
    max_subagents: int = Field(4, ge=0, le=20)


class Policies(_Strict):
    version: str = "1"
    external_network: bool = False
    allowed_network_hosts: list[str] = Field(default_factory=list)
    allow_write_operations: bool = False
    require_approval_for: list[str] = Field(default_factory=list)
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    capture_content: bool | None = Field(None, description="Override platform content capture for this agent.")


class HumanApproval(_Strict):
    required_for_risk: list[RiskLevel] = Field(default_factory=lambda: [RiskLevel.HIGH, RiskLevel.CRITICAL])
    require_for_writes: bool = True
    expires_after_seconds: int = Field(86_400, ge=60, le=30 * 86_400)
    approver_roles: list[str] = Field(default_factory=lambda: ["admin", "operator"])


EvalKind = Literal[
    "functional",
    "input_validation",
    "output_schema",
    "tool_selection",
    "tool_invocation",
    "permission",
    "prompt_injection",
    "workflow_recovery",
    "timeout",
    "model_failure",
    "regression",
    "performance",
    "human_review",
]


class EvalExpectation(_Strict):
    status: str | None = "completed"
    output_schema_valid: bool | None = None
    output_contains: list[str] = Field(default_factory=list)
    output_not_contains: list[str] = Field(default_factory=list)
    output_fields: dict[str, Any] = Field(
        default_factory=dict, description="Dotted path → expected value or {min_items|contains|equals}."
    )
    tools_called: list[str] = Field(default_factory=list)
    tools_not_called: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)
    approval_requested: bool | None = None
    max_latency_ms: float | None = None
    max_model_calls: int | None = None
    error_code: str | None = None


class EvalCaseSpec(_Strict):
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,119}$")]
    name: str = Field(min_length=1, max_length=200)
    kind: EvalKind = "functional"
    input: dict[str, Any] = Field(default_factory=dict)
    expected: EvalExpectation = Field(default_factory=EvalExpectation)
    model_override: ModelSpec | None = None
    approval_decision: Literal["approve", "reject", "none"] = "none"
    limits_override: dict[str, Any] | None = None
    fault_injection: dict[str, Any] | None = Field(
        None, description="Deterministic faults, e.g. {'fail_tool_once': 'read_pipeline_logs'}."
    )


class Acceptance(_Strict):
    min_pass_rate: float = Field(1.0, ge=0, le=1)
    required_cases: list[str] = Field(default_factory=list)
    max_avg_latency_ms: float | None = None


class EvaluationSpec(_Strict):
    suite: str | None = Field(None, description="Name of a registered evaluation suite.")
    cases: list[EvalCaseSpec] = Field(default_factory=list)
    acceptance: Acceptance = Field(default_factory=Acceptance)


JSONSchema = dict[str, Any]

ComponentKind = Literal[
    "prompt_template",
    "system_instruction",
    "tool_definition",
    "workflow_node",
    "validation_rule",
    "retrieval_component",
    "model_profile",
    "policy_definition",
    "evaluation_dataset",
]


class ComponentRef(_Strict):
    """A pinned reference to a registered component. Versions are mandatory: no silent replacement."""

    kind: ComponentKind
    name: str = Field(pattern=r"^[a-z][a-z0-9_\-]{1,119}$")
    version: str

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        semver.parse(value)
        return value

    @property
    def ref(self) -> str:
        return f"{self.kind}/{self.name}@{self.version}"


class AgentSpecBody(_Strict):
    objective: str = Field(min_length=8, max_length=4000)
    instructions: str = Field("", max_length=50_000)
    model: ModelSpec = Field(default_factory=ModelSpec)
    inputs: JSONSchema = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"task": {"type": "string", "minLength": 1}},
            "required": ["task"],
        }
    )
    outputs: JSONSchema = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
    )
    tools: list[ToolRef] = Field(default_factory=list)
    components: list[ComponentRef] = Field(default_factory=list)
    workflow: WorkflowSpec = Field(default_factory=WorkflowSpec)
    memory: MemorySpec = Field(default_factory=MemorySpec)
    execution_limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    policies: Policies = Field(default_factory=Policies)
    human_approval: HumanApproval = Field(default_factory=HumanApproval)
    evaluation: EvaluationSpec = Field(default_factory=EvaluationSpec)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]


class AgentSpec(_Strict):
    api_version: Literal["agentfactory/v1"] = API_VERSION
    kind: Literal["Agent"] = "Agent"
    metadata: Metadata
    spec: AgentSpecBody

    def to_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

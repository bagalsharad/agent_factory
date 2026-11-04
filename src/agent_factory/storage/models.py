"""Normalized relational data model.

Every tenant-owned row carries ``workspace_id`` so that multi-workspace isolation can be enforced in
queries. Identifiers are UUID strings. Large binaries never live here (see ``storage.artifacts``).

Memory concepts are deliberately separate tables (see docs/architecture/data-model.md):
execution state (executions/checkpoints), conversation memory, agent memory, workspace knowledge
(documents/chunks).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..core.ids import new_id, utcnow
from .db import Base


def _id_col() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Identity, workspaces and access
# ---------------------------------------------------------------------------
class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = _id_col()
    subject: Mapped[str] = mapped_column(String(255), unique=True)  # OIDC sub / local id
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = _id_col()
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)
    id: Mapped[str] = _id_col()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40))


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = _id_col()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)


# ---------------------------------------------------------------------------
# Factory control plane
# ---------------------------------------------------------------------------
class Agent(TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    active_version_id: Mapped[str | None] = mapped_column(String(36), default=None)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Operational ownership (not part of the immutable spec): who answers for this agent in production.
    business_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    technical_contact: Mapped[str | None] = mapped_column(String(200), default=None)
    escalation_contact: Mapped[str | None] = mapped_column(String(200), default=None)


class AgentVersion(TimestampMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version"),
        Index("ix_agent_versions_agent_status", "agent_id", "status"),
    )
    id: Mapped[str] = _id_col()
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    spec: Mapped[dict[str, Any]] = mapped_column()
    spec_hash: Mapped[str] = mapped_column(String(64))
    api_version: Mapped[str] = mapped_column(String(40))
    template_ref: Mapped[str | None] = mapped_column(String(160))
    release_notes: Mapped[str] = mapped_column(Text, default="")
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    build_report: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    parent_version_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(default=None)


class AgentTemplate(TimestampMixin, Base):
    __tablename__ = "agent_templates"
    __table_args__ = (UniqueConstraint("workspace_id", "name", "version"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)  # NULL = built-in
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    display_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60), default="general")
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict[str, Any]] = mapped_column()
    source: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin | custom | imported


class Component(TimestampMixin, Base):
    __tablename__ = "components"
    __table_args__ = (UniqueConstraint("workspace_id", "kind", "name", "version"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    definition: Mapped[dict[str, Any]] = mapped_column()
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | deprecated
    content_hash: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str | None] = mapped_column(String(200), default=None)


class AgentVersionComponent(Base):
    """Pinned component dependencies of an agent version (prevents silent replacement)."""

    __tablename__ = "agent_version_components"
    __table_args__ = (UniqueConstraint("agent_version_id", "component_id"),)
    id: Mapped[str] = _id_col()
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[str] = mapped_column(ForeignKey("components.id"), index=True)


class SloDefinition(TimestampMixin, Base):
    """A service-level objective for one agent (or the whole workspace when agent_id is null)."""

    __tablename__ = "slo_definitions"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), default=None)
    name: Mapped[str] = mapped_column(String(120))
    metric: Mapped[str] = mapped_column(String(40))  # success_rate | error_rate | p95_latency_ms | evaluation_pass_rate
    objective: Mapped[float] = mapped_column(Float)
    window_hours: Mapped[int] = mapped_column(Integer, default=168)
    created_by: Mapped[str | None] = mapped_column(String(36), default=None)


class CostBudget(TimestampMixin, Base):
    """Spending limit per workspace (agent_id null) or per agent, per UTC day or month."""

    __tablename__ = "cost_budgets"
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), default=None)
    period: Mapped[str] = mapped_column(String(10), default="month")  # day | month
    max_cost_usd: Mapped[float | None] = mapped_column(Float, default=None)
    max_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    warn_at: Mapped[float] = mapped_column(Float, default=0.8)
    action: Mapped[str] = mapped_column(String(10), default="block")  # block | warn


class MCPServer(TimestampMixin, Base):
    """A registered MCP server. Platform-wide (administrators only); tools are exposed only when trusted."""

    __tablename__ = "mcp_servers"
    id: Mapped[str] = _id_col()
    name: Mapped[str] = mapped_column(String(60), unique=True)
    transport: Mapped[str] = mapped_column(String(10))  # stdio | http
    command: Mapped[list[Any]] = mapped_column(default=list)  # stdio argv
    url: Mapped[str | None] = mapped_column(String(500), default=None)
    auth_secret: Mapped[str | None] = mapped_column(String(120), default=None)  # bearer token secret name
    env_secrets: Mapped[dict[str, Any]] = mapped_column(default=dict)  # ENV_VAR -> secret name (stdio)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    max_output_chars: Mapped[int] = mapped_column(Integer, default=20_000)
    tool_allowlist: Mapped[list[Any]] = mapped_column(default=list)
    tool_settings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # tool -> {risk, side_effects}
    discovered: Mapped[list[Any]] = mapped_column(default=list)
    health: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36), default=None)


class AgentArtifact(Base):
    """Immutable release artifact of a published version: spec, resolved dependencies, evaluation evidence."""

    __tablename__ = "agent_artifacts"
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), unique=True)
    version: Mapped[str] = mapped_column(String(40))
    artifact: Mapped[dict[str, Any]] = mapped_column()
    artifact_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(36), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class DeploymentEnvironment(TimestampMixin, Base):
    __tablename__ = "deployment_environments"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    promote_from: Mapped[str | None] = mapped_column(String(40), default=None)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"limits": {...}} — tightening only


class Deployment(TimestampMixin, Base):
    """One version of an agent in one environment. Every transition is a row update plus an audit event."""

    __tablename__ = "deployments"
    __table_args__ = (Index("ix_deployments_env_agent", "environment_id", "agent_id", "status"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    environment_id: Mapped[str] = mapped_column(ForeignKey("deployment_environments.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    artifact_id: Mapped[str] = mapped_column(ForeignKey("agent_artifacts.id"))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    strategy: Mapped[str] = mapped_column(String(10), default="standard")  # standard | canary
    traffic_percent: Mapped[int] = mapped_column(Integer, default=100)
    # pending_approval | active | superseded | rolled_back | rejected | retired
    status: Mapped[str] = mapped_column(String(20), default="pending_approval")
    requested_by: Mapped[str | None] = mapped_column(String(36), default=None)
    approved_by: Mapped[str | None] = mapped_column(String(36), default=None)
    previous_deployment_id: Mapped[str | None] = mapped_column(String(36), default=None)
    notes: Mapped[str] = mapped_column(Text, default="")
    activated_at: Mapped[datetime | None] = mapped_column(default=None)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)


class DependencyEdge(Base):
    """What a built agent version depends on, resolved at build time (tool and component versions included).

    Kinds: tool, model, model_profile, component, agent (specialist), evaluation_suite.
    """

    __tablename__ = "dependency_edges"
    __table_args__ = (
        UniqueConstraint("agent_version_id", "kind", "name"),
        Index("ix_dependency_edges_target", "workspace_id", "kind", "name"),
    )
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(40), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ToolDefinitionRecord(TimestampMixin, Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[str] = _id_col()
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    definition: Mapped[dict[str, Any]] = mapped_column()
    source: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin | mcp
    content_hash: Mapped[str] = mapped_column(String(64))


class ModelProfile(TimestampMixin, Base):
    __tablename__ = "model_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "name", "version"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40), default="1.0.0")
    provider: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(200))
    parameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    required_capabilities: Mapped[list[Any]] = mapped_column(default=list)


class WorkflowDefinition(TimestampMixin, Base):
    """Compiled workflow graph for an agent version (produced by the builder)."""

    __tablename__ = "workflow_definitions"
    id: Mapped[str] = _id_col()
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id", ondelete="CASCADE"), unique=True)
    pattern: Mapped[str] = mapped_column(String(30))
    definition: Mapped[dict[str, Any]] = mapped_column()
    graph: Mapped[dict[str, Any]] = mapped_column()
    content_hash: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# Execution plane
# ---------------------------------------------------------------------------
class Execution(TimestampMixin, Base):
    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_ws_status", "workspace_id", "status"),
        Index("ix_executions_agent_created", "agent_id", "created_at"),
        Index("ix_executions_queue", "status", "queued_at"),
        UniqueConstraint("workspace_id", "requested_by", "idempotency_key"),
    )
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    parent_execution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="standard")  # standard | evaluation
    status: Mapped[str] = mapped_column(String(30), default="queued")
    pending_action: Mapped[str | None] = mapped_column(String(20), default="start")  # start | resume
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    correlation_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    budget_usage: Mapped[dict[str, Any]] = mapped_column(default=dict)
    options: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker_id: Mapped[str | None] = mapped_column(String(120))
    heartbeat_at: Mapped[datetime | None] = mapped_column(default=None)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    trace_id: Mapped[str | None] = mapped_column(String(32), default=None, index=True)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (UniqueConstraint("execution_id", "seq"),)
    id: Mapped[str] = _id_col()
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(60))
    node_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CheckpointRecord(Base):
    """LangGraph checkpoint storage (see storage.checkpointer)."""

    __tablename__ = "execution_checkpoints"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64))
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    checkpoint_type: Mapped[str] = mapped_column(String(40))
    checkpoint: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_type: Mapped[str] = mapped_column(String(40))
    metadata_: Mapped[bytes] = mapped_column("metadata", LargeBinary)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CheckpointWriteRecord(Base):
    __tablename__ = "execution_checkpoint_writes"
    __table_args__ = (UniqueConstraint("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(255), default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(64))
    task_path: Mapped[str] = mapped_column(String(255), default="")
    idx: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(255))
    value_type: Mapped[str] = mapped_column(String(40))
    value: Mapped[bytes] = mapped_column(LargeBinary)


class ToolInvocation(TimestampMixin, Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    id: Mapped[str] = _id_col()
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(120))
    tool_call_id: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    tool_version: Mapped[str] = mapped_column(String(40))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(30))  # pending|awaiting_approval|completed|failed|denied|rejected
    decision: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    approval_id: Mapped[str | None] = mapped_column(String(36))
    duration_ms: Mapped[float | None] = mapped_column(Float)


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_ws_status", "workspace_id", "status"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    tool_invocation_id: Mapped[str | None] = mapped_column(String(36))
    step_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    tool_name: Mapped[str | None] = mapped_column(String(120))
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    risk: Mapped[str] = mapped_column(String(20), default="high")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decision_comment: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
class EvaluationSuite(TimestampMixin, Base):
    __tablename__ = "evaluation_suites"
    __table_args__ = (UniqueConstraint("workspace_id", "name", "version"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    acceptance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))


class EvaluationCase(TimestampMixin, Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("suite_id", "case_key"),)
    id: Mapped[str] = _id_col()
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id", ondelete="CASCADE"), index=True)
    case_key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)


class EvaluationRun(TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (Index("ix_eval_runs_version", "agent_version_id", "created_at"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    status: Mapped[str] = mapped_column(String(20), default="running")
    passed: Mapped[bool | None] = mapped_column(Boolean, default=None)
    summary: Mapped[dict[str, Any]] = mapped_column(default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    model_config_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    suite_hash: Mapped[str] = mapped_column(String(64))
    spec_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    # What exactly was evaluated (tool/component/prompt versions, environment) and what it was compared with.
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    baseline_run_id: Mapped[str | None] = mapped_column(String(36), default=None)
    # "release" runs gate publishing; "comparison" runs (another model config) never change lifecycle status.
    purpose: Mapped[str] = mapped_column(String(20), default="release", server_default="release")


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[str] = _id_col()
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("evaluation_cases.id"))
    execution_id: Mapped[str | None] = mapped_column(String(36))
    passed: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[list[Any]] = mapped_column(default=list)
    observed: Mapped[dict[str, Any]] = mapped_column(default=dict)
    duration_ms: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------
class AuditEvent(Base):
    """Append-only, hash-chained audit log (tamper-evident; see governance.audit)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id_col()
    seq: Mapped[int] = mapped_column(Integer, unique=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SecretRef(TimestampMixin, Base):
    """Metadata about a stored secret. The value lives in the configured secret backend."""

    __tablename__ = "secret_refs"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(120))
    backend: Mapped[str] = mapped_column(String(20))
    last_rotated_at: Mapped[datetime | None] = mapped_column(default=None)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class ConfigMetadata(Base):
    __tablename__ = "config_metadata"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(36))


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("principal_id", "scope", "key"),)
    id: Mapped[str] = _id_col()
    principal_id: Mapped[str] = mapped_column(String(36))
    scope: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------------------
# Memory and knowledge (distinct concepts, distinct tables)
# ---------------------------------------------------------------------------
class ConversationSession(TimestampMixin, Base):
    __tablename__ = "conversation_sessions"
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[str] = _id_col()
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentMemoryRecord(TimestampMixin, Base):
    __tablename__ = "agent_memories"
    __table_args__ = (UniqueConstraint("agent_id", "key"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    source_execution_id: Mapped[str | None] = mapped_column(String(36))


class KnowledgeCollection(TimestampMixin, Base):
    __tablename__ = "knowledge_collections"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    source_dir: Mapped[str | None] = mapped_column(Text)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("collection_id", "source_path"),)
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    collection_id: Mapped[str] = mapped_column(ForeignKey("knowledge_collections.id", ondelete="CASCADE"), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(64))
    artifact_uri: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="indexed")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = _id_col()
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    collection_id: Mapped[str] = mapped_column(String(36), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[Any] | None] = mapped_column(default=None)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = _id_col()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    uri: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

"""Composition root: wires the modular monolith's components behind explicit interfaces.

The control plane (registry, builder, evaluation) and the execution plane (harness, runtime, dispatcher)
are constructed separately and only meet through service interfaces, so either side can later be split into
its own process without rewriting the other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from .builder.builder import AgentBuilder
from .connectors.service import MCPService
from .core.circuit import CircuitRegistry
from .core.config import Settings, WorkerMode, get_settings
from .core.ids import content_hash
from .core.logging import get_logger
from .evaluation.service import EvaluationService
from .governance.approvals import ApprovalService
from .governance.audit import AuditLog, FileAuditSink
from .governance.identity import IdentityService
from .governance.policy import PolicyEngine
from .governance.secrets import AuditedSecretStore, SecretStore, build_secret_store
from .governance.simulation import PolicySimulator
from .harness.harness import GovernedHarness
from .memory.conversation import AgentMemory, ConversationMemory
from .memory.knowledge import KnowledgeService
from .model_gateway.gateway import ModelGateway
from .observability.operations import OperationsService
from .registry.agents import AgentService
from .registry.components import ComponentRegistry
from .registry.dependencies import DependencyService
from .registry.releases import ReleaseService
from .registry.templates import TemplateRegistry
from .runtime.dispatcher import DatabaseQueueDispatcher
from .runtime.executor import ExecutionService
from .spec.interpreter import RequirementInterpreter
from .spec.validator import SpecValidator
from .storage.artifacts import LocalArtifactStore
from .storage.db import Database
from .storage.models import ToolDefinitionRecord
from .tools.registry import ToolExecutor, build_default_registry
from .tools.sandbox import build_sandbox

log = get_logger(__name__)
_SOURCE_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
MIGRATIONS_DIR = (
    _SOURCE_MIGRATIONS if _SOURCE_MIGRATIONS.is_dir() else Path(__file__).resolve().parent / "_bundled" / "migrations"
)


class Platform:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = s = settings or get_settings()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(s)
        self.db.audit_sink = FileAuditSink(s.audit_sink_path) if s.audit_sink_path else None
        self.artifacts = LocalArtifactStore(s.artifacts_dir)
        self.audit = AuditLog(self.db)
        self.identity = IdentityService(s, self.db)
        self.secrets: SecretStore = AuditedSecretStore(build_secret_store(s), self.audit)
        self.tools = build_default_registry(s.disabled_tools)
        self.connectors = MCPService(self.db, self.audit, s, self.tools, self.secrets)
        self.circuits = CircuitRegistry(
            failure_threshold=s.circuit_failure_threshold, recovery_seconds=s.circuit_recovery_seconds
        )
        self.tool_executor = ToolExecutor(circuits=self.circuits)
        self.gateway = ModelGateway(s, circuits=self.circuits)
        self.sandbox = build_sandbox(s)
        self.policy = PolicyEngine(s)
        self.validator = SpecValidator(self.tools, self.gateway, sandbox_available=self.sandbox.available()[0])
        self.templates = TemplateRegistry(self.db, s.templates_dir)
        self.components = ComponentRegistry(self.db)
        self.knowledge = KnowledgeService(s, self.db, self.artifacts)
        self.conversation = ConversationMemory(self.db)
        self.agent_memory = AgentMemory(self.db)
        # --- control plane
        self.agents = AgentService(self.db, self.audit, self.validator)
        self.dependencies = DependencyService(self.db, self.audit, self.tools)
        self.policy_simulator = PolicySimulator(self.policy, self.tools, self.agents, self.sandbox.available()[0])
        self.agents.dependencies = self.dependencies
        self.releases = ReleaseService(self.db, self.audit, s)
        self.agents.releases = self.releases
        self.releases.dependency_blockers = self.dependencies.blockers
        self.builder = AgentBuilder(
            db=self.db,
            validator=self.validator,
            tools=self.tools,
            gateway=self.gateway,
            policy=self.policy,
            components=self.components,
            resolver_factory=self.agents.resolver,
        )
        self.agents.builder = self.builder
        self.interpreter = RequirementInterpreter(self.templates, self.gateway)
        # --- governance + execution plane
        self.approvals = ApprovalService(s, self.db, self.audit)
        self.harness = GovernedHarness(
            settings=s,
            db=self.db,
            gateway=self.gateway,
            tools=self.tools,
            executor=self.tool_executor,
            policy=self.policy,
            approvals=self.approvals,
            knowledge=self.knowledge,
            conversation=self.conversation,
            agent_memory=self.agent_memory,
        )
        self.executions = ExecutionService(
            settings=s,
            db=self.db,
            harness=self.harness,
            agents=self.agents,
            identity=self.identity,
            approvals=self.approvals,
            audit=self.audit,
            policy=self.policy,
            conversation=self.conversation,
        )
        self.operations = OperationsService(self.db, self.audit, self.gateway, self.circuits)
        self.evaluations = EvaluationService(
            self.db,
            self.agents,
            self.executions,
            self.audit,
            settings=s,
            tool_versions=lambda names: {n: self.tools.get(n).definition.version for n in names if self.tools.has(n)},
            tool_catalog=lambda: {d.name: d for d in self.tools.definitions()},
            gateway=self.gateway,
        )
        self.dispatcher = DatabaseQueueDispatcher(
            self.executions,
            concurrency=s.worker_concurrency,
            poll_interval=s.worker_poll_interval_seconds,
            retention_days=s.retention_days,
        )

    # ------------------------------------------------------------------ lifecycle
    def migrate(self) -> None:
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        cfg.set_main_option("sqlalchemy.url", self.settings.resolved_database_url.replace("%", "%%"))
        cfg.attributes["connection_url"] = self.settings.resolved_database_url
        command.upgrade(cfg, "head")

    def migration_status(self) -> dict[str, Any]:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with self.db.engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        return {"current": current, "head": head, "up_to_date": current == head}

    def bootstrap(self) -> None:
        self.identity.bootstrap()
        self.templates.sync_builtin()
        self.components.seed_builtin()
        self.connectors.sync_registry()  # trusted MCP servers' allow-listed tools (no network at start-up)
        self._sync_tool_definitions()
        self._apply_runtime_settings()
        self.dependencies.backfill()
        self.releases.backfill()  # after dependencies: artifacts include resolved dependency edges
        self.executions.budget_guard = self.operations.enforce_budgets
        self.executions.deployment_resolver = self.releases.resolve
        self.dependencies.revalidate_runner = lambda p, agent_id, version: self.evaluations.start_run(
            p, agent_id, version=version
        )

    def _apply_runtime_settings(self) -> None:
        from .storage.models import ConfigMetadata

        with self.db.session() as session:
            record = session.get(ConfigMetadata, "disabled_tools")
        persisted = (record.value or {}).get("tools", []) if record else []
        names = sorted(set(persisted) | set(self.settings.disabled_tools))
        self.tools.set_disabled([n for n in names if self.tools.has(n)])

    def _sync_tool_definitions(self) -> None:
        with self.db.transaction() as session:
            for definition in self.tools.definitions():
                doc = definition.model_dump(mode="json")
                record = session.scalars(
                    select(ToolDefinitionRecord).where(
                        ToolDefinitionRecord.name == definition.name, ToolDefinitionRecord.version == definition.version
                    )
                ).first()
                digest = content_hash(doc)
                if record is None:
                    session.add(
                        ToolDefinitionRecord(
                            name=definition.name,
                            version=definition.version,
                            definition=doc,
                            source=definition.source,
                            content_hash=digest,
                        )
                    )
                elif record.content_hash != digest:
                    log.warning(
                        "tool definition changed without a version bump",
                        extra={"tool": definition.name, "version": definition.version},
                    )
                    record.definition, record.content_hash = doc, digest

    def start(self, *, workers: bool | None = None) -> None:
        run_workers = self.settings.worker_mode == WorkerMode.EMBEDDED if workers is None else workers
        if run_workers:
            self.dispatcher.start()

    def stop(self) -> None:
        self.dispatcher.stop()
        self.tool_executor.shutdown()
        self.gateway.shutdown()
        self.db.dispose()


def create_platform(settings: Settings | None = None, *, migrate: bool = True) -> Platform:
    platform = Platform(settings)
    if migrate:
        platform.migrate()
    platform.bootstrap()
    return platform

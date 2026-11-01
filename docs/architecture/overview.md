# Architecture overview

Agent Factory is a **modular monolith**: one deployable Python package (`src/agent_factory`) with strongly
separated modules and explicit service interfaces, plus a React single-page dashboard (`web/`). Locally everything
runs in one process; in production the same image runs as an API container and one or more worker containers.

## Planes and modules

| Plane | Modules | Responsibility |
|---|---|---|
| Factory control plane | `spec`, `registry`, `builder`, `evaluation` | Specifications, NL interpretation, validation, templates, components, versions, lifecycle, build, evaluation |
| AI harness | `harness` | Context engineering, model interaction, tool discovery/invocation, validation, approvals, budgets |
| Execution plane | `runtime`, `tools`, `memory`, `model_gateway` | LangGraph orchestration, execution manager, task dispatch, tools and sandbox, knowledge/memory, providers |
| Governance & security | `governance` | Identity, RBAC, policy engine, approvals, audit, secrets |
| Observability | `observability`, `harness.events`, `core.logging` | Tracing, structured events, JSON logs, metrics |
| Platform | `core`, `storage`, `api`, `cli`, `platform.py` | Config, errors, persistence, HTTP API, CLI, composition root |

`platform.py` is the composition root: it constructs every service and wires them through constructor
injection. The control plane never imports the runtime's internals and vice versa; they meet through
`AgentService`, `ExecutionService` and `EvaluationService`.

```mermaid
flowchart TB
  subgraph control["Control plane"]
    interp[RequirementInterpreter] --> spec[AgentSpec]
    spec --> val[SpecValidator]
    val --> builder[AgentBuilder]
    tpl[TemplateRegistry] --> interp
    comp[ComponentRegistry] --> builder
    builder --> agents[AgentService<br/>versions + lifecycle]
    evals[EvaluationService] --> agents
  end
  subgraph exec["Execution plane"]
    disp[DatabaseQueueDispatcher] --> execs[ExecutionService]
    execs --> graph[compile_workflow → LangGraph]
    graph --> harness[GovernedHarness]
    harness --> ctx[ContextManager]
    harness --> gw[ModelGateway]
    harness --> tools[ToolRegistry + ToolExecutor]
    harness --> kn[KnowledgeService]
    graph --> ckpt[SqlCheckpointSaver]
  end
  subgraph gov["Governance"]
    pol[PolicyEngine]
    appr[ApprovalService]
    audit[AuditLog]
    ident[IdentityService]
  end
  harness --> pol
  harness --> appr
  appr -- decision → enqueue resume --> execs
  evals --> execs
```

## Agent lifecycle

```
draft ──validate+build──▶ validating ──▶ testing ──evaluation passes──▶ approved ──publish──▶ published ──▶ deprecated
  ▲                                         │                                                   (rollback re-publishes)
  └────────────── edit ─────────────────────┘          any ──disable──▶ disabled ──re-enable──▶ testing
```

Approved, published and deprecated versions are immutable; editing them creates a new version. Executions
default to the *active published* version; evaluations default to the *latest built* version.

## Execution flow

1. `POST /api/v1/executions` validates the input against the agent's input schema, honours `Idempotency-Key`,
   snapshots the configuration (version, spec hash, model, tool versions, workflow hash, policy hash) and inserts a
   `queued` execution. The API returns `202` immediately.
2. A worker claims it with an atomic compare-and-set `UPDATE` and re-resolves the requester's *current* role.
3. The workflow is compiled into a LangGraph `StateGraph`; every node calls a `Harness` method through
   `run_node`, which checks cancellation, charges the step budget and emits `step.*` events.
4. Tool calls go through `GovernedHarness.invoke_tool`: idempotency record → `PolicyEngine.evaluate_tool` →
   (approval interrupt) → `ToolExecutor` (input validation, timeout, output validation, size cap) → events.
5. On `require_approval` the harness creates an approval and calls LangGraph `interrupt()`; the checkpoint is
   persisted and the execution becomes `waiting_approval`. A decision re-queues it with `pending_action=resume`.
   On resume the interrupted node re-runs; completed invocations return their stored result and the approval is
   re-read from the database (the resume payload is never trusted).
6. `finalize` validates the final output against `spec.outputs`. Terminal statuses: `completed`, `failed`,
   `cancelled`, `budget_exceeded`, `rejected`.

## Multi-agent patterns

- **Single agent**: linear steps; `agent_loop` expands into a model ⇄ tools cycle.
- **Sequential**: `delegate` steps run specialist agents in order; outputs flow through `{{ steps.<id>.output }}`.
- **Supervisor**: the supervisor's `agent_loop` sees one `delegate_to_<specialist>` function per specialist.

Delegations create child executions (`parent_execution_id`) with isolated context and their own budgets. They are
bounded by `max_delegations` and `max_subagents`; specialists may not delegate further and must be read-only.

## Deployment shapes

| Target | Shape |
|---|---|
| A — local | `agent-factory serve`: API + embedded workers + SQLite + local files; Ollama optional |
| B — single node | `docker compose`: Caddy (TLS) → API container; worker container(s); PostgreSQL; optional Ollama/OTel collector; internal network |
| C — scaled | Multiple API replicas behind a load balancer, horizontally scaled workers (DB queue claims are concurrency-safe), managed PostgreSQL HA, object storage adapter, external secrets and OIDC, central telemetry — see [operations](../deployment/operations.md) |

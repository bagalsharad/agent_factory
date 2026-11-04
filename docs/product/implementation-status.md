# Implementation status and production-readiness assessment

Stage 0 assessment of the codebase against the production transformation plan
([production-roadmap.md](production-roadmap.md)). The code is the source of truth; every rating below cites where the
capability lives. Updated as each stage lands.

Maturity scale: **Mature** (implemented, tested, documented) · **Partial** (works, but key parts of the target are
missing) · **Untested** (implemented, little or no test evidence) · **Documented only** · **Missing**.

## 1. Architecture as built

A modular monolith in one Python package (`src/agent_factory`, ADR 0001) plus a React SPA served by the API (ADR 0009).

| Layer (target) | Where it lives today | Notes |
|---|---|---|
| Experience & design | `web/src/pages/*` (Designer, Catalog, AgentDetail, Evaluations, Executions, Approvals, Audit, Knowledge, Models, Settings), `cli/main.py` | NL → spec in Designer via the model gateway, YAML editor, template picker |
| Control plane | `spec/` (models, loader, upgrader, validator), `registry/` (agents, lifecycle, components, templates), `builder/` | Declarative specs only, never generated code (ADR 0002) |
| Evaluation plane | `evaluation/service.py`, `evaluation/checks.py` | Runs every case through the real runtime in `evaluation` mode |
| Execution plane | `runtime/executor.py`, `runtime/dispatcher.py`, `runtime/graph.py`, `storage/checkpointer.py` | LangGraph (ADR 0003); DB task queue (ADR 0005); SQL checkpoints (ADR 0004) |
| Governed harness | `harness/harness.py`, `harness/context.py`, `harness/budget.py`, `model_gateway/*`, `tools/*` | Model never sees credentials; tool calls are authorized outside the model (ADR 0007) |
| Governance & security | `governance/policy.py`, `rbac.py`, `approvals.py`, `audit.py`, `secrets.py`, `identity.py` | Hash-chained audit log; OIDC + API keys; SoD in production profile |
| Operations & observability | `observability/telemetry.py`, `harness/events.py`, `/api/v1/dashboard` | Event timeline per execution; OTel spans; no metrics/SLO layer |
| Enterprise integration | `model_gateway/{ollama,openai_compat,anthropic_provider}.py` | No MCP, no external secret managers, no event streaming |

Persistence: SQLAlchemy 2 models in `storage/models.py` (33 tables), Alembic migrations `0001`–`0003`, SQLite locally,
PostgreSQL in CI and production. Deployment: `deploy/docker/Dockerfile`, `docker-compose.yml` (API, worker, Postgres,
Caddy, optional Ollama/OTel collector).

## 2. Capability maturity

### 2.1 Evaluation (target Phase 1)

| Capability | Maturity | Evidence / gap |
|---|---|---|
| Suites, cases, runs, per-case results | Mature | `EvaluationSuite/Case/Run/Result` tables; suites are content-hashed and versioned |
| Deterministic checks (status, error code, output schema, fields, contains, tools called/not called/denied, approval requested, latency, model calls) | Mature | `evaluation/checks.py`; no LLM involved |
| Independent from the agent | Mature | Checks read DB records (`ToolInvocation`, events), not the agent's own claims |
| Adversarial cases (compromised model, injected documents/emails, path escape) | Partial | `mock-adversarial` provider + per-agent cases; no reusable, agent-agnostic security suite |
| Fault injection (tool failure, model failure, timeout) | Mature | `fault_injection` case config |
| Human review | Partial | Verdict is recorded, but the run's pass/fail and the version status are **not recomputed** afterwards, so a required human-review case can never unblock a release |
| Release gate enforced by backend | Mature (basic) | `AgentService.publish` requires `approved` + latest run for the exact `spec_hash` passed |
| Mandatory security cases | Missing | `min_pass_rate < 1.0` lets permission / prompt-injection cases fail and still pass the gate |
| Cost / p95 latency thresholds | Missing | Only `max_avg_latency_ms` |
| Regression vs. approved baseline | Partial | `compare_versions` re-runs two versions on demand; nothing compares a new run with the published baseline, and publish does not check regressions |
| Reproducibility metadata | Partial | Run stores model config, suite hash, spec hash; not tool/component/prompt versions or environment |
| Model comparison | Missing | Cases can override the model; a whole run cannot be repeated across model configs |
| LLM-as-judge | Missing | Roadmap item; must stay advisory |
| Evaluation dashboard | Partial | Run list, run detail with per-case checks, compare; no gate view, regressions, trends |

### 2.2 Runtime reliability (target Phase 2)

| Capability | Maturity | Evidence / gap |
|---|---|---|
| Model retries with backoff | Mature | `ModelGateway.chat` (bounded exponential backoff, retryable errors only) |
| Tool retries | Mature | `ToolExecutor.execute`: only for `idempotent` tools, `retryable` errors |
| Jitter, retry budgets, per-tool/per-model policies | Missing | Global `AF_MODEL_MAX_RETRIES`, spec `max_retries` |
| Circuit breakers | Missing | Provider health check exists, no breaker state |
| Request idempotency | Mature | `Idempotency-Key` on execution create (`executor.create`) |
| Tool-call idempotency | Partial | Stable key `execution:step:call`; completed calls are replayed, not repeated. **Gap:** a call left `pending` by a crash is re-executed on recovery, even for non-idempotent write tools |
| Durable checkpoints, resume, recovery from last checkpoint | Mature | `storage/checkpointer.py`, `executor.retry`, `recover_stale` |
| Heartbeats and stale-worker requeue | Mature | `_heartbeat`, `recover_stale` |
| Dead-letter / poison handling | Missing | `recover_stale` requeues indefinitely; no attempt cap or DLQ view |
| Cancellation (with child propagation) | Mature | `executor.cancel`, `_cancel_requested` checks parent |
| Graceful shutdown | Partial | Dispatcher stops claiming; in-flight runs rely on heartbeat recovery |
| Multi-agent bounds | Mature (conservative) | `max_delegations`, `max_subagents`, no nested delegation, specialists read-only |
| Child timeouts | Partial | Children run inline; parent timeout checked between steps (documented limitation) |

### 2.3 Component registry and dependencies (target Phase 3)

| Capability | Maturity | Evidence / gap |
|---|---|---|
| Versioned components (9 kinds) | Mature | `registry/components.py`, `Component` table |
| Pinning to agent versions; delete blocked when pinned | Mature | `AgentVersionComponent`, `ComponentRegistry.delete` |
| Mandatory version on refs (no silent replacement) | Mature | `ComponentRef` requires `version` |
| Dependency graph covering tools, models, policies, specialists | Partial | Only `components` are pinned; built-in tools, model profiles and specialist agents are referenced by name in the spec but not recorded as edges |
| Reverse queries / impact analysis / revalidation | Missing | |
| Ownership, support contacts, last validation date | Missing | `Agent.owner` only |

### 2.4 Observability and operations (target Phase 4)

| Capability | Maturity | Evidence / gap |
|---|---|---|
| Execution timeline (model, tool, policy, approval, retry, checkpoint, error) | Mature | `harness/events.py` → `ExecutionEvent`; SSE stream to the UI |
| Redaction of sensitive content | Mature | `core/redaction.py`, `capture_content` off by default |
| Correlation IDs | Mature | `api/middleware.py`; `Execution.correlation_id` |
| OpenTelemetry spans | Untested | Spans + console/OTLP exporter; not validated against a collector |
| Budgets (steps, model/tool calls, tokens, cost, duration, delegations) | Mature | `harness/budget.py`, enforced outside the model |
| Cost accounting | Partial | Per-execution `cost_usd` (0 for local models); no per-agent/workspace roll-up or budgets above execution level |
| Operational metrics, SLOs, error budgets, alerts, drift | Missing | Dashboard shows counts only |

### 2.5 Security and interoperability (target Phase 5)

| Capability | Maturity | Evidence / gap |
|---|---|---|
| Policy outside the model, deny by default for unknown/disabled tools | Mature | `PolicyEngine.evaluate_tool`; `tests/security/test_security.py` |
| Tool risk levels + approval for consequential actions | Mature | `RiskLevel`, `action_class`, `require_approval_for` |
| RBAC, workspace isolation | Mature | `governance/rbac.py`; workspace filter on every query |
| Approvals with expiry, SoD, no model path | Mature | `governance/approvals.py` |
| Network egress allowlist, SSRF guard | Mature | `tools/builtin/http.py`, `AF_NETWORK_ALLOWLIST`, private-network block |
| Sandbox fails closed | Mature | `tools/sandbox.py` |
| Hash-chained audit + verify | Mature | `governance/audit.py`, `/api/v1/audit/verify` |
| External immutable audit sink, export | Missing | |
| Secret stores (env, keyring, file) | Mature (local) | `governance/secrets.py`; no Vault/cloud adapters, no rotation/versioning |
| MCP connectors | Missing | `Tool` interface is ready |
| Policy as code / versioned policies / simulation | Missing | Policies live inside each spec; changes are versioned with the spec |
| TM Forum IG1412 / TMF443 / TMF444 mapping | Missing | |

### 2.6 CI/CD and release management (target Phase 6)

| Capability | Maturity | Evidence / gap |
|---|---|---|
| Lifecycle: draft → validating → testing → approved → published → deprecated / disabled / failed | Mature | `registry/lifecycle.py` transition table |
| Immutable published versions, rollback | Mature | `AgentService.rollback`, immutable status set |
| Build report + provenance | Mature | `builder/builder.py`, `AgentVersion.provenance` |
| Signed / hashed build artifact bundle | Partial | `spec_hash` + build report; no standalone artifact with component/tool versions |
| Environments (dev/test/staging/prod), deployments, canary | Missing | One "active version" per agent |
| GitHub Actions: lint, types, unit/integration/security/e2e/perf tests, Postgres profile, migration check, web job, pip-audit, npm audit, dependency review, container build, SBOM, pinned actions | Mature | `.github/workflows/*.yml` |
| Container image vulnerability scan | Missing | SBOM is produced; image is not scanned |

## 3. Known correctness gaps found during the assessment

All five are fixed and covered by tests:

1. Human review never unblocked a run → verdicts re-apply the gates (`test_release_gates.py`).
2. Security cases could be outvoted → mandatory case kinds.
3. No baseline regression gate → regression gate + baseline-drift check on publish.
4. A crash during a write tool call re-executed it → in-doubt guard + operator resolution (`test_runtime_reliability.py`).
5. Stale executions requeued without limit → attempt cap and dead letters.

Found while implementing and also fixed: recovering an execution that crashed before its first checkpoint failed
with `EmptyInputError` (now starts fresh); executions had no trace id without an OpenTelemetry exporter.

## 4. Stage status (2026-11-03)

| Stage | Status | Delivered | Not done |
|---|---|---|---|
| 0 — Assessment | Done | this document, [roadmap](production-roadmap.md) | – |
| 1 — Evaluation | Done | gates (mandatory kinds, regression, baseline drift, p95, cost, judge score), human-review recompute + SoD, provenance, release-gate API/UI, reusable security suite, model comparison, LLM-as-judge ([design](../architecture/evaluation-architecture.md)) | quality trend charts |
| 2 — Runtime reliability | Done | in-doubt guard, dead letters, retry budget and per-tool/per-model retries, jittered tool backoff, circuit breakers ([design](../architecture/runtime-reliability.md)) | preemptive child timeouts |
| 3 — Dependencies | Done | dependency graph at build, backfill, reverse queries, impact + revalidation, drift, tool pinning, unsafe-dependency blocking, ownership ([design](../architecture/dependency-management.md)) | notification delivery, schema-level compatibility |
| 4 — Observability | Done | trace id on every execution, richer spans, metrics, agent health, SLOs + error budgets, drift, cost budgets, alerts, Operations page ([design](../architecture/observability.md)) | alert delivery, collector validation, provider-level budgets |
| 5 — Enterprise | Done (except S4 field mapping) | MCP connectors, Vault secrets, rotation versions, secret access audit, audit sink + export + offline verify, policy simulation and history ([security](../security/production-security.md)) | TM Forum field-level mapping ([preliminary](../reference/tmforum-compatibility.md)) |
| 6 — CI/CD | Done (except Linux drill) | artifacts, environments, deployments, approval with SoD, canary, rollback, environment-tightened limits, Trivy scan, backup/restore drill (SQLite) ([design](../architecture/agent-cicd.md)) | clean-Linux Compose drill, PostgreSQL restore drill |

Migrations added: `0003`–`0008` (all additive; SQLite upgrade/downgrade round trip and `alembic check` verified
locally; PostgreSQL verified by the CI job).

The platform is **not yet production-ready**: see [production readiness](../deployment/production-readiness.md)
for the two criteria not yet demonstrated. Passing mock-model suites validates platform behaviour, not model
quality.

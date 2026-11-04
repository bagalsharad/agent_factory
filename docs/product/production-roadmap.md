# Production roadmap

How Agent Factory moves from alpha to a platform other teams can depend on. Current state and evidence:
[implementation-status.md](implementation-status.md). Each stage lands as its own reviewed change; nothing is
rewritten, schema changes are additive Alembic migrations, and published `agentfactory/v1` specs keep working.

Guiding rule: models provide intelligence, the harness controls execution, the evaluation engine verifies behaviour,
governance controls access, the factory manages the lifecycle, observability makes it measurable.

## 1. Reuse, don't replace

| Keep and extend | Why |
|---|---|
| Modular monolith, one package | Separate API and worker processes already exist; split services only when an operational need appears |
| `agentfactory/v1` spec + validator | New fields are optional with backward-compatible defaults |
| Evaluation service and deterministic checks | Already independent of the agent (reads DB records); extend with gates, baselines, judges |
| Policy engine (in-process) | Correct, tested, fast. OPA would add a service for no current gain; export policy decisions instead (ADR to follow in Stage 5) |
| DB task queue + heartbeats | Adequate for single-node and small multi-worker deployments; a Redis/queue dispatcher stays optional |
| LangGraph runtime behind `runtime/graph.py` | Keep the interpreter as the seam so other engines stay swappable |
| `Tool` interface | MCP tools become `Tool` subclasses so every call still passes policy, approval, budget and audit |
| `SecretStore` ABC | Add Vault/cloud adapters behind it |

## 2. New components

| Component | Stage | Notes |
|---|---|---|
| Release gate evaluator (mandatory kinds, cost, p95, regressions, human review) | 1 | Inside `evaluation/`; result stored on the run, enforced by `AgentService.publish` |
| Baseline selector + regression comparator | 1 | Baseline = latest passing run of the published version |
| Run provenance (tools, components, prompts, environment) | 1 | Reproducibility record per run |
| Reusable security suite (agent-agnostic adversarial cases) | 1 | Generated per agent from its spec; runs on the mock adversarial model |
| Model comparison runner | 1 | One suite × N model configs; side-by-side metrics |
| LLM-as-judge evaluator | 1 | Rubric, structured verdict, confidence, evidence, judge model/prompt version, cost. Advisory; can block via a threshold but can never turn a failed deterministic check into a pass |
| Retry policy (jitter, budgets, per tool/provider) | 2 | Extends existing gateway/executor retries |
| Circuit breaker registry | 2 | Per provider / connector; state visible in health endpoints |
| In-doubt tool-call guard | 2 | A `pending` non-idempotent call found on recovery is never re-run; it fails the step as `in_doubt` and goes to an operator |
| Dead-letter state + attempt cap | 2 | `recover_stale` stops after N attempts |
| Dependency graph (all edges: components, tools, model profiles, specialists, suites) | 3 | Built at build time from the resolved spec |
| Impact analysis + revalidation trigger | 3 | |
| Metrics roll-ups, SLOs, error budgets, drift | 4 | Computed from executions/events; no new infrastructure |
| MCP connector framework | 5 | Trusted server registry; imported tools default to high risk + approval |
| External audit sink / export | 5 | Append-only file / object storage adapter |
| Build artifact bundle, environments, deployments, canary | 6 | |

## 3. Database and API impact

| Stage | Tables / columns (additive) | API (under `/api/v1`) |
|---|---|---|
| 1 | `evaluation_runs.provenance`, `evaluation_runs.baseline_run_id`, `evaluation_runs.label`; later `evaluation_judgements` | `GET /agents/{id}/release-gate`, `POST /evaluations/model-comparison`, judge config in suites |
| 2 | `executions.dead_lettered_at`, `executions.failure_history`; `tool_invocations.status = in_doubt` (value only) | `GET /executions?status=dead_letter`, `POST /executions/{id}/resolve` |
| 3 | `component_dependencies` (version → kind/name/version), `components.owner`, `agents.business_owner`, `agents.support_contact` | `GET /dependencies`, `GET /components/{id}/dependents`, `POST /components/{id}/impact` |
| 4 | `slo_definitions`, `alerts`; metrics computed on read at first | `GET /agents/{id}/health`, `/metrics/cost`, `/slos` |
| 5 | `mcp_servers`, `connector_tools`; `secret_refs.version` | `/connectors/mcp/*`, `/audit/export` |
| 6 | `agent_artifacts`, `deployment_environments`, `deployments` | `/releases/*`, `/deployments/*` |

## 4. Risks

| Risk | Mitigation |
|---|---|
| Stricter gates block existing workflows | New gates default to the strict-but-sensible value only where existing examples already comply (all examples use `min_pass_rate: 1.0`); every gate is explained in the run summary |
| Baseline drift (baseline changes between evaluation and publish) | Publish re-checks that the run was gated against the current published version |
| LLM-as-judge over-trusted | Advisory by default; cannot override deterministic or security failures; judge model and rubric version recorded |
| Mock results mistaken for model quality | Run summary records the provider; docs and UI label mock runs |
| Re-executing side effects after a crash | Stage 2 in-doubt guard (top priority of Stage 2) |
| Scope creep into a new framework | Each stage is a separate change with tests and docs; no new infrastructure without an ADR |
| Single maintainer, local hardware | Real-model evaluations are opt-in and slow (~15 min per case on an 8B model); keep CI on the deterministic mock |

## 5. Prioritized backlog

Status on 2026-11-03: every item below is done except R5 (preemptive child timeouts), S4 field-level mapping
(preliminary assessment only), C2's separate-infrastructure environments (environments share one instance) and
C4 (clean-Linux and PostgreSQL drills). See [implementation-status.md](implementation-status.md).

Complexity: S (≤1 day) · M (2–4 days) · L (1–2 weeks). Dependencies in brackets.

| # | Item | Stage | Priority | Size | Depends on |
|---|---|---|---|---|---|
| E1 | Human-review verdict recomputes run, gates and version status | 1 | P0 | S | – |
| E2 | Mandatory case kinds (permission, prompt_injection) block release regardless of pass rate | 1 | P0 | S | – |
| E3 | Regression gate against the published baseline; publish re-checks baseline | 1 | P0 | M | – |
| E4 | Cost and p95 latency thresholds | 1 | P1 | S | – |
| E5 | Run provenance: tool, component, prompt versions; environment | 1 | P1 | S | – |
| E6 | Release-gate API + gate panel in the Evaluation Lab | 1 | P1 | S | E1–E5 |
| E7 | Reusable security suite generated from the spec | 1 | P1 | M | E2 |
| E8 | Model comparison (one suite × N model configs) | 1 | P2 | M | E5 |
| E9 | LLM-as-judge (advisory, rubric, structured, cost-tracked) | 1 | P2 | M | E5 |
| R1 | In-doubt guard for pending non-idempotent tool calls on recovery | 2 | P0 | S | – |
| R2 | Attempt cap + dead-letter state and operator view | 2 | P0 | M | – |
| R3 | Retry jitter, retry budget, per-tool/per-provider policy | 2 | P1 | S | – |
| R4 | Circuit breakers for model providers and network tools | 2 | P1 | M | R3 |
| R5 | Preemptive child timeouts | 2 | P2 | M | – |
| D1 | Dependency edges recorded at build (tools, model profile, components, specialists, suites) | 3 | P1 | M | – |
| D2 | Reverse dependency queries + impact report + revalidation | 3 | P1 | M | D1 |
| D3 | Ownership and support metadata | 3 | P2 | S | – |
| O1 | Metrics roll-ups (success, retries, p50/p95, tokens, cost per agent/workspace) | 4 | P1 | M | – |
| O2 | SLOs, error budgets, alerts | 4 | P2 | M | O1 |
| O3 | Drift detection on evaluation and execution metrics | 4 | P2 | M | O1, E3 |
| O4 | OTel validated against a collector in CI | 4 | P2 | S | – |
| S1 | MCP connector framework (trusted servers, default high risk, allowlist, limits, audit) | 5 | P1 | L | R4 |
| S2 | External secret manager adapter + rotation metadata | 5 | P2 | M | – |
| S3 | Audit export + external append-only sink | 5 | P2 | M | – |
| S4 | TM Forum IG1412 / TMF443 / TMF444 compatibility assessment | 5 | P3 | S | – |
| C1 | Immutable build artifact bundle with integrity hash | 6 | P1 | M | D1 |
| C2 | Environments, deployments, canary, rollback per environment | 6 | P2 | L | C1 |
| C3 | Container image vulnerability scan in CI | 6 | P1 | S | – |
| C4 | Clean Linux deployment + backup/restore drill recorded | 6 | P1 | M | – |

## 6. Definition of done (platform)

Production-ready only when all of these have been demonstrated, not just unit-tested: an agent is created and built;
evaluated independently; failed mandatory tests block publishing; tool permissions are enforced outside the model;
consequential actions need authorized approval; runtime failures recover without repeating side effects; component
changes trigger impact analysis; every execution is traceable; cost and reliability are monitored; artifacts are
immutable and versioned; deployments roll back; production configuration is secure; backups restore; a clean Linux
deployment is validated; the full automated suite passes.

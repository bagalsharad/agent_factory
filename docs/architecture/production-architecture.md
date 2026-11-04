# Production architecture

Agent Factory stays a modular monolith (ADR 0001): one Python package, an API process and any number of worker
processes sharing PostgreSQL. The eight logical layers below are module boundaries, not services.

```
 ┌──────────── Experience: React dashboard · CLI · REST API (/api/v1) ─────────────────────────────────┐
 │ Designer · Catalog · Evaluation Lab · Operations · MCP Connectors · Releases/Dependencies tabs      │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────┘
 Control plane        spec/ (schema, validator) · registry/agents, lifecycle, components, dependencies,
                      releases · builder/
 Evaluation plane     evaluation/ service, checks, gates, security_suite, judge
 Execution plane      runtime/ executor (queue, heartbeats, recovery, dead letters), dispatcher, graph
 Governed harness     harness/ (context, budgets, tool invocation, in-doubt guard) · model_gateway/ (retries,
                      circuits) · tools/ (registry, executor, circuits) · connectors/ (MCP)
 Governance           governance/ policy, simulation, rbac, approvals, audit (+ sink), secrets (+ Vault), identity
 Operations           observability/ telemetry (OTel), operations (metrics, SLOs, drift, budgets, alerts)
 Integration          model providers (Ollama, OpenAI-compatible, Anthropic, mock) · MCP (stdio, HTTP) · Vault ·
                      OTLP collector · external audit sink
 Persistence          SQLAlchemy models, Alembic migrations 0001–0008 (SQLite locally, PostgreSQL in production)
```

## Trust boundaries

1. **Model ⟶ harness.** The model proposes; the harness decides (policy, approvals, budgets, schemas).
2. **Tools/connectors ⟶ harness.** Tool and MCP output is untrusted data; credentials stay inside tools.
3. **Evaluation ⟶ lifecycle.** Only backend gates move a version to `approved`; publish and deploy re-check.
4. **Operators ⟶ releases.** Production changes need a second person when separation of duties is on.

## Request path of a governed execution

`POST /executions {environment}` → budget guard → deployment resolution (artifact verified, canary bucket) →
queue → worker claims (attempt +1, heartbeat) → LangGraph run with SQL checkpoints → per step: model call
(gateway retries, circuit) / tool call (policy → approval → pending record → executor retries, circuit → result
record) → finalize (output schema) → events, audit, trace id.

## Decisions

ADRs 0001–0010 (existing) and 0011 policy engine stays in-process, 0012 MCP tools are governed tools,
0013 operational metrics computed on read, 0014 releases and deployments live in the platform database.

## Scaling notes

- API and workers scale horizontally against PostgreSQL; the DB queue uses row claims and heartbeats (ADR 0005).
- Circuit-breaker state and rate limits are per process.
- Metrics are computed on read; export OTel to a time-series store for large volumes.
- Kubernetes/Helm is deliberately deferred until the single-node production profile has been validated on Linux
  (see [production readiness](../deployment/production-readiness.md)).

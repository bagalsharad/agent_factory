# Observability and operations

Operators need to know what happened, why, what it cost and what to do. All operational views are computed from
records the platform already keeps (`executions`, `execution_events`, `tool_invocations`, `approvals`,
`evaluation_runs`), so there is no second pipeline to drift out of sync. Production traffic only: evaluation
executions are excluded.

## Tracing

- Every execution has a `trace_id` (32 hex characters). With OpenTelemetry configured
  (`AF_TELEMETRY_EXPORTER=otlp|console`) it is the trace id of the `agent.execution` span; otherwise a W3C-format
  id is generated so executions can still be correlated. Resumes keep the first id.
- Spans: `agent.execution` (execution id, agent id and name, version, version id, workspace, correlation id, mode,
  model, action) → `workflow.node` → `model.call` / `tool.call`. Approvals, retries, checkpoints, policy
  decisions, recovery and errors are execution events on the timeline (streamed to the UI), redacted per policy.
- `tests/integration/test_operations.py` checks the span tree with an in-memory exporter. CI does not exercise a
  real collector.

## Metrics (`GET /api/v1/operations/metrics?hours=&agent_id=`)

Per workspace and per agent: executions by status; success, failure, timeout and retry rates; retries; p50/p95
latency (harness active time, excluding time waiting for approval); tokens; model calls; cost and cost per
execution; policy violations (denied tool calls); approvals requested, approved, rejected and the approval rate;
per-tool calls, success rate, failures, denials and in-doubt calls; and the latest release evaluation pass rate.

`GET /api/v1/agents/{id}/health` combines metrics, SLOs, alerts and drift into `healthy`, `degraded`,
`unhealthy` or `unknown` (no traffic). `GET /api/v1/operations/providers` adds provider health and circuit state.

## SLOs and error budgets

`POST /api/v1/operations/slos {name, metric, objective, agent_id?, window_hours}`:

| Metric | Objective | Error budget |
|---|---|---|
| `success_rate` | ≥ ratio | allowed failures = (1 − objective) × finished executions |
| `error_rate` | ≤ ratio | allowed failures = objective × finished executions |
| `p95_latency_ms` | ≤ milliseconds | 5% of executions may be slower |
| `evaluation_pass_rate` | ≥ ratio (latest release run of the active version) | – |

Status shows the value, compliance, sample count and error budget remaining (negative means overspent).

## Drift

`GET /api/v1/operations/drift` compares the latest window with the previous one (at least 5 executions each) and
reports a change of 50% or more in p95 latency, tokens per execution, failure rate or policy violations per
execution, and a shift in tool selection (total-variation distance of 0.3 or more).

## Cost budgets

`POST /api/v1/operations/budgets {agent_id?, period: day|month, max_cost_usd?, max_tokens?, action: block|warn,
warn_at}`. A blocking budget refuses new executions once it is spent (`BudgetExceededError`, for example
`agent_day_max_tokens`). Evaluations still run, so a fix can be validated. Per-execution limits (tokens, calls,
duration, cost) remain in `execution_limits`.

## Alerts (`GET /api/v1/operations/alerts`)

Computed on request; there is no notification history. Alerts cover: a breached SLO (critical), an error budget
below 25%, a cost budget at warning level or exhausted, open circuits, dead-lettered executions, in-doubt tool
calls, and drift findings.

## Known limitations

- Everything is computed on read. That is fine for a single node and moderate volumes; large deployments should
  export metrics through OpenTelemetry to a time-series store.
- Alerts are not delivered by email, chat or pager yet; poll the endpoint or connect it to existing monitoring.
- Cost is only as accurate as provider pricing (local models report $0).
- Budgets per model provider are not implemented (workspace and agent budgets are).

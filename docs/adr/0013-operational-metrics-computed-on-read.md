# ADR-0013: Operational metrics, SLOs, drift and alerts are computed on read

Status: accepted

Decision: `OperationsService` derives metrics, SLO status, error budgets, drift, cost-budget usage and alerts from
existing tables (`executions`, `tool_invocations`, `approvals`, `evaluation_runs`) at request time. Only SLO and
budget definitions are stored.

Why: no new infrastructure (no time-series database or stream processor), and the numbers can never disagree with
the execution records they summarize.

Consequences: queries scan the window's executions, which is fine for single-node and moderate volumes. Large
deployments should export OpenTelemetry metrics and traces to their own monitoring; alerts are not delivered
anywhere by the platform yet.

# Runtime reliability

How the execution plane behaves when models, tools, networks or workers fail. Everything here is enforced by the
platform, outside the model.

## Failure handling at a glance

| Failure | What happens | Code |
|---|---|---|
| Transient model error (overload, 5xx, timeout) | Bounded exponential backoff with jitter; counted in the execution's retry budget | `model_gateway/gateway.py` |
| Provider keeps failing | Circuit opens after `AF_CIRCUIT_FAILURE_THRESHOLD` consecutive failures; calls fail fast with `circuit_open` for `AF_CIRCUIT_RECOVERY_SECONDS`, then one trial call | `core/circuit.py` |
| Transient tool error | Retried only if the tool is `idempotent` and the error `retryable`; backoff with jitter | `tools/registry.py` |
| Network / MCP tool keeps failing | Per-tool circuit breaker, same states | `tools/registry.py` |
| Too many retries overall | `execution_limits.max_total_retries` stops the execution (`budget_exceeded`) | `harness/budget.py` |
| Worker crash | Heartbeat goes stale → execution requeued → resumes from its last checkpoint; completed steps and tool calls are replayed from records, not repeated | `runtime/executor.py` |
| Crash **during** a side-effecting tool call | The call is **in doubt**: never repeated automatically; the execution stops with `in_doubt` until an operator resolves it | `harness/harness.py` |
| Crash before the first checkpoint | Recovery starts the execution fresh (nothing had completed) | `runtime/executor.py` |
| Worker keeps dying on the same execution | After `AF_MAX_EXECUTION_ATTEMPTS` it is **dead-lettered** with a diagnostic record | `runtime/executor.py` |
| Cancellation | Cooperative, checked between steps and propagated to child executions | `runtime/executor.py` |

## Retry policy

- Per execution: `execution_limits.max_retries` (per call) and `max_total_retries` (whole execution, optional).
- Per model: `model.max_retries` overrides the per-call value for model calls.
- Per tool: `tools: [{name, max_retries}]` overrides it for that tool (idempotent tools only).
- Non-idempotent tools are never retried by the executor. Non-retryable errors (bad request, policy denial,
  validation) are never retried.

## Idempotency and in-doubt calls

Every tool call has a stable key `execution:step:call`. Before running, the harness records the invocation as
`pending`; afterwards as `completed` / `failed` / `denied` / `rejected`. On recovery:

- `completed`, `denied`, `rejected` → the recorded result is replayed.
- `pending` (or `in_doubt`) and the tool is **idempotent** → it runs again (safe by definition).
- `pending` and the tool is **not idempotent** → marked `in_doubt`, audited (`tool.in_doubt`), and the execution
  fails with `in_doubt`. A plain retry refuses to guess again.

An operator (`executions:recover`, operator/admin roles) resolves it with
`POST /api/v1/executions/{id}/tool-invocations/{invocation_id}/resolve`:

| `performed` | Effect |
|---|---|
| `true` | Recorded as completed (never repeated); execution recovers from its checkpoint |
| `false` | Allowed to run again exactly once more; execution recovers |

The decision, who made it and the note are stored on the invocation and in the audit log. Approved calls follow
the same rule: an approval is not a licence to run a write twice.

## Dead letters

`recover_stale` dead-letters an execution whose attempt count reached `AF_MAX_EXECUTION_ATTEMPTS` (default 3).
`error.details` holds the attempts, last worker and heartbeat, last checkpoint, retry history, in-doubt tool calls
and recovery options. `executions?status=dead_lettered` lists them and the Operations page raises a critical
alert. Recovery (`POST /executions/{id}/retry`) requires `executions:recover` and resets the attempt budget.

## Multi-agent

Unchanged and conservative: bounded delegation (`max_delegations`, `max_subagents`), no nested delegation,
read-only specialists, cancellation propagates to children, each child is its own tracked execution.

## Known limitations

- Circuit-breaker state is per process; each API/worker process learns independently.
- Child executions run inline in the parent's worker; their timeout is checked between steps, not preemptively.
- An in-flight delegation interrupted by a crash is re-run (specialists are read-only, so this is safe today).
- Graceful shutdown relies on heartbeat recovery for executions that were running.

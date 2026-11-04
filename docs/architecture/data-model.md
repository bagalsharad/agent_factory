# Data model

All identifiers are UUID strings; tenant-owned rows carry `workspace_id`; timestamps are UTC (`created_at`,
`updated_at`). Schema migrations live in `migrations/` (Alembic) and run on SQLite and PostgreSQL.

| Group | Tables |
|---|---|
| Identity & access | `users`, `workspaces`, `memberships` (role per workspace), `api_keys` (SHA-256 hashes only) |
| Control plane | `agents`, `agent_versions` (spec JSON, `spec_hash`, status, validation/build reports, provenance), `agent_templates`, `components`, `agent_version_components` (pins), `tool_definitions`, `model_profiles`, `workflow_definitions` (compiled graph) |
| Execution | `executions` (status, input/output, config snapshot, budget usage, worker heartbeat), `execution_events` (ordered, redacted), `execution_checkpoints` + `execution_checkpoint_writes` (LangGraph), `tool_invocations` (idempotency keys, policy decisions, results), `approvals` |
| Evaluation | `evaluation_suites` (content-hashed, versioned), `evaluation_cases`, `evaluation_runs` (metrics, gates, suite & spec hash, provenance, regression baseline), `evaluation_results` |
| Governance | `audit_events` (hash-chained), `secret_refs` (metadata only), `config_metadata`, `idempotency_records` |
| Memory & knowledge | `conversation_sessions`, `conversation_messages`, `agent_memories`, `knowledge_collections`, `documents`, `document_chunks` |
| Artifacts | `artifacts` (metadata; bytes live in the artifact store, `local://` on disk) |

## Memory is not one table

| Concept | Storage | Lifetime |
|---|---|---|
| Execution state | `execution_checkpoints`, `executions.budget_usage` | One execution (purged by retention) |
| Conversation memory | `conversation_*` | Per session; opt-in (`spec.memory.conversation`) |
| Agent memory | `agent_memories` | Per agent key/value facts; opt-in |
| Workspace knowledge | `knowledge_collections`, `documents`, `document_chunks` + artifact store | Until deleted / re-indexed |
| Retrieved context | Not stored separately; recorded as `retrieval` events | Per execution |

## Indexes

Indexes back the common access paths: executions by workspace+status, agent+created_at, and the queue
(`status`, `queued_at`); versions by agent+status; approvals by workspace+status; audit by workspace+created_at;
evaluation runs by version+created_at; unique constraints on names per workspace, versions per agent, idempotency
keys and component/template name+version.

## Added for production (migrations 0003–0008)

| Migration | Change |
|---|---|
| 0003 | `evaluation_runs.provenance`, `evaluation_runs.baseline_run_id` |
| 0004 | `evaluation_runs.purpose` (`release` gates publishing, `comparison` never does) |
| 0005 | `dependency_edges`; `agents.business_owner`, `technical_contact`, `escalation_contact`; `components.owner` |
| 0006 | `executions.trace_id`; `slo_definitions`; `cost_budgets` |
| 0007 | `mcp_servers`; `secret_refs.version` |
| 0008 | `agent_artifacts`, `deployment_environments`, `deployments` |

All additive with defaults; existing rows need no backfill except dependency edges, which the platform records on
start-up for built versions that have none.

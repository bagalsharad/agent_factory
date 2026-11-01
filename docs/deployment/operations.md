# Operations

| Topic | Practice |
|---|---|
| TLS & ingress | Caddy terminates TLS (automatic ACME or internal CA). Behind another load balancer, terminate there and keep the internal network private |
| Resources | Compose sets memory/CPU limits; size workers for model latency (I/O bound with remote/Ollama models) |
| Persistent volumes | `postgres-data`, `app-data` (artifacts, sandbox log), `caddy-data`, optional `ollama-models` |
| Migrations | Run automatically on API/worker start (`alembic upgrade head`); run `agent-factory db migrate` manually for controlled upgrades |
| Health probes | Liveness `/health`; readiness `/ready` (DB, migrations, worker) |
| Graceful shutdown | Workers finish polling, stop on SIGTERM; in-flight executions resume from checkpoints after restart (heartbeat recovery) |
| Worker recovery | Executions with stale heartbeats (`AF_WORKER_HEARTBEAT_STALE_SECONDS`, default 60) are re-queued and recovered |
| Log rotation | Logs go to stdout as JSON; rely on Docker's `json-file` rotation (`max-size`, `max-file`) or your log shipper |
| Secret rotation | Rotate `POSTGRES_PASSWORD` (update DB role then env, restart), revoke/recreate API keys (`DELETE /api/v1/api-keys/{id}`), rotate provider keys in the secret backend |
| Upgrade | Pull/build the new image tag → backup → `docker compose up -d` (migrations run) → check `/ready` and `audit-verify` |
| Rollback | Restore the previous image tag; if a migration ran, restore the pre-upgrade backup (migrations are forward-only in production) |
| Agent rollback | `POST /api/v1/agents/{id}/rollback` re-activates a previously published version instantly |
| Retention | `AF_RETENTION_DAYS` (hourly purge of terminal executions); `POST /api/v1/admin/retention/purge` on demand |
| Audit | `GET /api/v1/audit/verify` regularly; ship audit logs to an append-only store |
| Monitoring | OTLP traces (`AF_TELEMETRY_EXPORTER=otlp`), structured logs with `correlation_id` and `execution_id`, dashboard failure counts |

## Scaling path (target C)

Multiple API replicas behind a load balancer (stateless; move rate limiting to the edge), N worker replicas
(concurrency-safe DB queue), managed PostgreSQL with HA, object storage via a new `ArtifactStore`, external secrets
via `SecretStore`, OIDC, centralized telemetry, network segmentation (API in DMZ, workers + DB internal, egress
allowlists matching `AF_NETWORK_ALLOWLIST`). A Kubernetes/Helm profile is on the roadmap.

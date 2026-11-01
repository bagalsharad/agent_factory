# Production deployment (single node, Docker Compose)

Target B from the architecture: a Linux host running Docker Engine 24+ with the Compose plugin.

```
Internet ──443──▶ Caddy (TLS) ──▶ api (agent-factory serve, AF_WORKER_MODE=external)
                                     │
                         internal network (no published ports)
                                     │
                    worker(s) (agent-factory worker) ──▶ PostgreSQL
                    optional: ollama, otel-collector
```

## Procedure

```bash
git clone <repo> agent-factory && cd agent-factory
cp deploy/compose/production.env.example deploy/compose/production.env
$EDITOR deploy/compose/production.env          # set POSTGRES_PASSWORD (long random), AF_DOMAIN, auth settings
docker compose --env-file deploy/compose/production.env up -d --build
docker compose --env-file deploy/compose/production.env ps            # api healthy, worker running, proxy up
docker compose --env-file deploy/compose/production.env exec api agent-factory users create admin@example.com --role admin
```

The last command prints an API key once; use it in the dashboard (*Settings → Connection*) or as
`Authorization: Bearer ...`. With `AF_DOMAIN=<public DNS name>` Caddy obtains certificates automatically; with
`localhost` it uses its local CA. Optional profiles: `--profile ollama` (local models inside the stack; then
`docker compose exec ollama ollama pull qwen3:8b`), `--profile telemetry` (OTel collector; set
`AF_TELEMETRY_EXPORTER=otlp`).

Scale workers: `docker compose ... up -d --scale worker=3` (queue claims are concurrency-safe).

## Hardening in the provided configuration

- Production profile refuses unauthenticated mode and self-approval; content capture is off.
- Containers: non-root user, read-only root filesystem, `/tmp` tmpfs, all capabilities dropped,
  `no-new-privileges`, memory/CPU limits; no Docker socket mounted anywhere.
- Only the proxy publishes ports; PostgreSQL, API and workers sit on an `internal` network.
- Immutable image; configuration only through environment (`production.env` is git-ignored).
- Health: Docker healthcheck on `/health`; use `/ready` for load balancers.

## Configuration outside the image

All settings are `AF_*` variables ([reference](../reference/configuration.md)). Secrets: prefer an external secret
manager feeding the environment (or implement a `SecretStore` adapter). Rotate the PostgreSQL password and API keys
regularly ([operations](operations.md)).

## Validation record

**2026-10-30, release 0.1.0** — macOS 26.6 (arm64), Docker Desktop 20.10.10, Compose v2.1.1; image built with the
classic builder (`DOCKER_BUILDKIT=0`); stack started with `docker compose --env-file deploy/compose/production.env
up -d`. All checks over HTTPS through Caddy (internal CA) with API-key authentication:

| # | Check | Result |
|---|---|---|
| 1 | `/ready` (database, migrations, external worker) | ok |
| 2 | Unauthenticated API request | 401 |
| 3 | PostgreSQL schema at head (revision 0002) | up to date |
| 4 | Create + build three example agents | testing |
| 5 | Execution queued by the API and run by the separate worker container | completed, correct evidence |
| 6 | Requester approves own restart (separation of duties) | 403 |
| 7–8 | Operator approves; workflow resumes; simulated restart performed once | completed |
| 9–10 | 13-case evaluation suite, then publish | passed (100%, 1 pending human review) → published |
| 11 | Operator tries to create an agent (RBAC) | 403 |
| 12 | Audit hash chain | valid (25 events) |
| 13 | `pg_dump` backup | ok |
| 14 | Worker restart | ok |

The same validation surfaced and fixed a PostgreSQL-only defect (identifier columns too short; migration 0002), and
the full automated suite now also runs on PostgreSQL. Not yet validated: a clean Linux host and public ACME
certificates (the CI `container.yml` job builds and smoke-tests the image on Linux).

Note for old Docker Desktop installs: if `docker pull`/`docker build` hang silently, the `desktop` credential helper
may be stuck — retry with an empty client config (`DOCKER_CONFIG=$(mktemp -d)`), or update Docker Desktop.

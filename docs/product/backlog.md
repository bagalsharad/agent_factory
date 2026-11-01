# Implementation backlog and status

Legend: ✅ implemented and tested · 🟡 partially implemented · ⬜ not implemented · ⛔ blocked

| # | Item | Status | Notes / evidence |
|---|---|---|---|
| 0.1 | Discovery: environment, existing prototype preserved in `legacy/prototype-v0.2` | ✅ | prototype specs import via the legacy upgrader |
| 1.1 | Project structure, config, JSON logging, correlation IDs | ✅ | `core/`, `api/middleware.py` |
| 1.2 | SQLAlchemy models + Alembic migrations (SQLite/PostgreSQL) | ✅ | `alembic check` clean; Postgres test opt-in in CI |
| 1.3 | Health / readiness | ✅ | `/health`, `/ready` |
| 1.4 | Model gateway + Ollama + OpenAI-compatible + mock | ✅ | mocked-HTTP adapter tests; real Ollama opt-in |
| 1.5 | Optional Claude API adapter (official SDK) | 🟡 | implemented; no live test in this repository (requires a key) |
| 2.1 | Spec schema, loader, upgrader, JSON Schema | ✅ | `tests/unit/test_spec.py` |
| 2.2 | Semantic validator | ✅ | `tests/unit/test_validator.py` |
| 2.3 | Template registry (10) + custom import | ✅ | |
| 2.4 | Component registry with pinning | ✅ | delete blocked for pinned published components |
| 2.5 | Builder + build report + provenance | ✅ | |
| 2.6 | Agent APIs + catalog UI | ✅ | |
| 3.1 | Harness, context manager, budgets | ✅ | |
| 3.2 | Tool registry, executor, built-in connectors | ✅ | |
| 3.3 | LangGraph runtime, SQL checkpointer, recovery | ✅ | |
| 3.4 | DB task queue, embedded/external workers, heartbeats | ✅ | |
| 4.1 | Policy engine, RBAC, workspaces | ✅ | |
| 4.2 | Approvals (pause/resume, expiry, cancel, SoD) | ✅ | |
| 4.3 | Audit hash chain, secrets, redaction, retention | ✅ | |
| 4.4 | OIDC authentication | ✅ | tested with locally signed JWTs; not against a live IdP |
| 5.1 | Evaluation framework, metrics, human review, compare | ✅ | |
| 5.2 | LLM-as-a-judge signal | ⬜ | roadmap (never as sole gate) |
| 5.3 | OpenTelemetry tracing | 🟡 | spans + console/OTLP exporters; not validated against a collector here |
| 6.1 | Three (+1) example agents with data, evals, docs | ✅ | |
| 6.2 | CLI (doctor, init, serve, worker, agents, approvals, evaluate, db, users) | ✅ | |
| 6.3 | Dashboard (all screens) | ✅ | Vitest + manual screenshots |
| 7.1 | Dockerfile, Compose (Postgres, worker, Caddy, optional Ollama/OTel) | ✅ | see validation record in docs/deployment/production.md |
| 7.2 | Backup/restore | ✅ | SQLite online backup CLI; Postgres pg_dump procedure |
| 8.1 | GitHub workflows, templates, Dependabot, OSS docs | ✅ | authored; first run happens on GitHub |
| R1 | MCP connector integration (trusted servers, imported tools default high-risk) | ⬜ | interface ready (`Tool`), not implemented |
| R2 | Vector retrieval (pgvector/Qdrant) | ⬜ | `Retriever` interface |
| R3 | Object storage artifact adapter (S3/GCS) | ⬜ | `ArtifactStore` interface |
| R4 | External secret managers (Vault, AWS/GCP) | ⬜ | `SecretStore` interface |
| R5 | Global (distributed) rate limiting | ⬜ | per-process today |
| R6 | Kubernetes/Helm profile | ⬜ | |

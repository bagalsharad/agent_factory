# Testing

```bash
make test             # backend (pytest) + frontend (vitest)
uv run pytest tests/unit -q
uv run pytest -m performance -s     # prints measurements
AF_TEST_OLLAMA_MODEL=qwen3:8b uv run pytest -m ollama -s          # real local inference (opt-in)
AF_TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest -m postgres   # PostgreSQL smoke test (opt-in)
AF_TEST_POSTGRES_ADMIN_URL=postgresql+psycopg://user:pw@host:5432/db uv run pytest -q   # whole suite on PostgreSQL
```

| Layer | Location | Covers |
|---|---|---|
| Unit | `tests/unit` | spec schema/upgrades, validator, policy/RBAC, tools & guards, gateway adapters (mocked HTTP), budgets, templating, context, redaction, audit chain, checkpointer durability, knowledge, components, templates |
| Integration | `tests/integration` | runtime executions, approvals (pause/resume/reject/expire/cancel), budgets, recovery, stale workers, delegation patterns, isolation, lifecycle/versioning/rollback, evaluation, full HTTP API incl. auth modes |
| End-to-end | `tests/e2e` | the complete journey over HTTP with the embedded worker |
| Security | `tests/security` | injection at the policy boundary, sandbox escapes, fabricated approvals, SoD, secret redaction, SSRF, budgets, insecure configuration |
| Performance | `tests/performance` | API latency, execution overhead, concurrency, cancellation, memory growth |
| Frontend | `web/src/test` | API client, helpers, workflow status, dashboard/approvals/designer rendering and actions |

All default tests use the **deterministic mock model** and an isolated temporary SQLite database (or, with
`AF_TEST_POSTGRES_ADMIN_URL`, a fresh PostgreSQL database per test — CI runs both, because SQLite does not enforce
column lengths); results reflect
platform behaviour and overhead, not model quality. Tests marked `ollama` / `postgres` are skipped unless
configured — skipped is not passed.

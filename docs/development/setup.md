# Development setup

```bash
make install          # uv sync --frozen --all-extras; npm ci in web/
uv run pre-commit install
make dev-web          # Vite on :5173 with hot reload, proxying /api to :8787
uv run agent-factory serve   # in another terminal
```

Layout:

```
src/agent_factory/   core · storage · spec · registry · builder · harness · runtime · model_gateway · tools ·
                     memory · evaluation · governance · observability · api · cli · platform.py (composition root)
web/                 React + TypeScript dashboard
templates/           built-in agent templates (YAML)
examples/            example agents, sample data, evaluation suites
migrations/          Alembic
tests/               unit · integration · e2e · security · performance
deploy/              Dockerfile, Compose, Caddy, OTel
docs/                architecture, ADRs, security, deployment, guides, reference
legacy/              the original prototype (kept for reference; its specs import via the upgrader)
```

## Adding a tool

1. Subclass `tools.base.Tool` with a Pydantic `Input`, `output_schema`, `risk`, `side_effects`, `action_class`,
   `permissions`, `timeout_seconds`; implement `run(args, ctx)` using only `ctx` capabilities.
2. Register it in `tools/builtin/__init__.py` (or your own registry plugin).
3. Add unit tests for contracts, failure modes and any path/network guard.
4. Agents opt in via `spec.tools`; writes additionally need `allow_write_operations` and are approval-gated.

## Adding a model provider

Implement `model_gateway.base.ModelProvider` (`chat`, `stream`, `list_models`, `capabilities`, `health`), set
`local`, register it in `ModelGateway._register_defaults` behind an explicit enable flag, and add mocked-HTTP tests.

## Database changes

Edit `storage/models.py`, then `uv run alembic revision --autogenerate -m "..."`, review the script, run
`uv run alembic upgrade head && uv run alembic check`.

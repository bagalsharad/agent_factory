# Production security

Complements the [security model](security-model.md) and [threat model](threat-model.md) with the controls added
for production use. Prompt injection is **mitigated, not eliminated**: these controls limit what a manipulated
model can do; they do not make it trustworthy.

## The 15 principles and where they are enforced

| # | Principle | Enforcement |
|---|---|---|
| 1 | The model never authorizes tool execution | `PolicyEngine.evaluate_tool` (ADR 0007); the model only proposes calls |
| 2 | The model cannot fabricate approvals | Approval decisions are read from the database on resume, never from model output; `security-compromised-model` case |
| 3 | Tool permissions enforced outside the model | Agent allowlist, RBAC, workspace, write and network policy in the harness |
| 4 | Unknown tools denied | `tool.registered` rule; undeclared tools denied (`agent.tool_allowlist`) |
| 5 | Read-only by default | `allow_write_operations: false` default; validator rejects write tools without it |
| 6 | Consequential actions need policy + approval | `require_for_writes`, `required_for_risk` (default high/critical), action classes; MCP tools default high risk + write |
| 7 | Separation of duties | `AF_ALLOW_SELF_APPROVAL=false` (mandatory in the production profile) covers tool approvals, evaluation human review and production deployment approval |
| 8 | No generated code in the backend | Declarative specs only (ADR 0002) |
| 9 | Sandbox fails closed | `tools/sandbox.py`; code tools refused without Docker |
| 10 | External content untrusted | Tool output, documents and MCP results are delimited data; injection cases in suites |
| 11 | Credentials never in model context | Secrets resolved at call time by tools/connectors; MCP view shows secret names only; tests assert values never reach events or invocations |
| 12 | Important actions auditable | Hash-chained audit log + optional external sink; exports are themselves audited |
| 13 | Workspace isolation in the backend | Every query filters by workspace; cross-workspace tests |
| 14 | Budgets independent of the model | `BudgetTracker`, retry budget, cost/token budgets per agent/workspace |
| 15 | Injection mitigation not presented as elimination | Stated here, in the threat model and in limitations |

## Release integrity

- Publishing requires every evaluation gate, including mandatory `permission`/`prompt_injection` cases and the
  regression gate against the published baseline (see [evaluation architecture](../architecture/evaluation-architecture.md)).
- Deprecated, missing or disabled dependencies block publish and deploy.
- Artifacts are hashed; deployment and execution routing re-verify them and refuse tampered records.
- Production accepts only artifacts promoted from staging and approved by a second person.
- Human reviewers cannot override deterministic results; LLM judges cannot either.

## MCP connectors

- Registration and configuration are administrator-only (`settings:write`); servers start **untrusted**.
- `stdio` servers may only run executables listed in `AF_MCP_ALLOWED_COMMANDS`, with no shell and a minimal
  environment (PATH, HOME, locale, temp) plus explicitly mapped secrets.
- `http` servers need `https://` (plain HTTP only for localhost); calls pass the agent's network allowlist.
- Tools are exposed only when the server is trusted, the tool is allow-listed and its input schema is a valid
  object schema. Server annotations (`readOnlyHint`, …) are recorded but never trusted.
- Default classification is high risk + write until an administrator classifies the tool.
- Timeouts, a hard transport size cap, an output character limit, a circuit breaker and audit events apply.

## Secrets

- Backends: `env` (read-only), `keyring`, `file` (Fernet-encrypted), `vault` (HashiCorp Vault KV v2; token from
  `VAULT_TOKEN`, never stored).
- Writing an existing secret is a rotation: the version increments, `last_rotated_at` is set and
  `secret.rotate` is audited. Values are never returned by the API.
- Reads are audited by name (`secret.access`, summarized per 5 minutes per secret per process).

## Audit

- Chain: `hash = sha256(prev_hash || canonical(event))`; `GET /api/v1/audit/verify`.
- `AF_AUDIT_SINK_PATH`: every event is also appended (fsync) as JSON Lines with its hashes. Point it at storage the
  database administrator cannot rewrite (WORM bucket mount, append-only volume, log shipper).
- `GET /api/v1/audit/export` and `agent-factory audit export` produce the chain; `agent-factory audit
  verify-export FILE` checks it without the database (detects edits, deletions and reordering).

## Policy as code

The in-process policy engine stays (ADR 0011). Policies are versioned with the agent specification;
`GET /api/v1/agents/{id}/policies` shows each version's effective policy, hash and changes, and
`POST /api/v1/policies/simulate` evaluates a tool call as any role without executing it.

## Production checklist

`AF_PROFILE=production`, `AF_AUTH_MODE=api_key|oidc`, `AF_ALLOW_SELF_APPROVAL=false`, TLS at the proxy (Caddy in
Compose), PostgreSQL, `AF_SECRETS_BACKEND=vault|file` with `AF_SECRETS_KEY` from a secret manager,
`AF_AUDIT_SINK_PATH` on append-only storage, `AF_NETWORK_ALLOWLIST` as narrow as possible, no
`AF_MCP_ALLOWED_COMMANDS` unless stdio servers are needed, `AF_SANDBOX_BACKEND=docker` only where code tools are
required.

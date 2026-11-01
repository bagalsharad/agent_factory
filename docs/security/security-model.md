# Security model

## Authentication

| Mode | Use | Behaviour |
|---|---|---|
| `local` | Single developer on their machine | Implicit local administrator; server refuses to bind non-loopback addresses |
| `api_key` | Automation, small teams | `Authorization: Bearer af_<prefix>_<secret>`; keys stored as SHA-256; revocable; optional expiry |
| `oidc` | Enterprise identity | JWT verified against the issuer's JWKS (RS256/ES256/PS256, `exp`, `iat`, `sub`, optional audience); roles from the `af_roles` claim |

Create the first administrator in `api_key` mode with `agent-factory users create <subject> --role admin`.

## Authorization (RBAC)

| Role | Can |
|---|---|
| viewer | read agents, executions, approvals, evaluations, catalog, knowledge, settings |
| evaluator | viewer + run evaluations/executions, write suites, invoke read tools |
| operator | viewer + run/cancel executions, **decide approvals**, invoke write tools, disable agents, test models |
| developer | viewer + write/publish agents, run executions/evaluations, templates/components/knowledge, write tools |
| admin | everything incl. settings, audit, users, deleting executions |

Roles are per workspace (`memberships`). Every API endpoint checks a permission; every tool call re-checks the
requester's *current* role.

## Tool governance

Each tool declares risk (low…critical), side effects (read/write/external), network use, sandbox requirement,
action class, permissions, timeout and audit settings. The policy engine evaluates, in order: registered & enabled →
agent allowlist → RBAC permissions → workspace → agent write policy → network policy and allowlists → sandbox
availability → approval rules (action classes, risk levels, writes).

## Human approval

Requests record the action, tool, redacted arguments, rationale, policy evidence, requester, expiry. Decisions
record the approver, timestamp and comment. Expired approvals behave as rejections. In production
`AF_ALLOW_SELF_APPROVAL=false` is enforced.

## Data protection

- Secrets: env (development), macOS Keychain (`keyring`), or Fernet-encrypted file; production should implement
  `SecretStore` for its secret manager. Values are never returned by the API.
- Redaction of credentials in logs, events, audit entries and error messages.
- Content capture (prompts/outputs in events) defaults to on locally and off in production.
- Retention: `AF_RETENTION_DAYS` purges terminal executions (events, checkpoints, tool records) hourly; evaluation
  evidence is retained with its run. `DELETE /api/v1/executions/{id}` removes one execution's data.
- Telemetry never leaves the process unless an exporter is configured; span attributes exclude content.

See the [threat model](threat-model.md) for risks and residual limitations.

# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Email connector: read-only IMAP fetch, send-to-self SMTP, digest files, macOS notifications.
- Daily Email Digest example agent, `agent-factory email setup|test`, `agent-factory schedule install|list|remove` (launchd).
- Tools can declare their own network destination; Ollama requests set `num_ctx` and disable thinking for structured output.

## [0.1.0] - 2026-10-30

First usable release.

### Added
- `agentfactory/v1` specification with JSON Schema, semantic validator and schema upgrader (imports the legacy
  prototype format).
- Natural-language requirement interpreter, 10 built-in templates, custom/imported templates, versioned components
  with pinning.
- 10-stage agent builder with build reports and provenance; lifecycle states, immutable published versions,
  comparison and rollback.
- Governed harness (context engineering, budgets, policy-filtered tools, idempotent invocation, output validation),
  LangGraph runtime (single, sequential, supervisor) with checkpoints in the platform database, human approvals with
  pause/resume, cancellation and crash recovery.
- Model gateway: deterministic mock, Ollama, OpenAI-compatible, optional Claude API.
- 15 built-in tools including SSRF-protected REST, read-only code review with a static scanner, workspace knowledge
  retrieval (BM25) and simulated write actions; Docker sandbox for code execution (refused when unavailable).
- Governance: local/API-key/OIDC auth, RBAC, workspaces, policy engine, separation of duties, hash-chained audit,
  secrets backends, retention.
- Evaluation framework with deterministic checks, metrics, human review, publish gate and version comparison.
- React dashboard, REST API with OpenAPI, CLI, structured logging, OpenTelemetry, Docker image and Compose stack,
  GitHub Actions workflows, four example agents.

### Preserved
- The original prototype lives in `legacy/prototype-v0.2/`.

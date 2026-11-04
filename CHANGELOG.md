# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Dashboard redesign: grouped dark sidebar with icons, top bar with breadcrumbs, ⌘K command palette (pages and agents), light/dark/system theme switch, refreshed cards, badges, tabs, buttons and stat tiles with trends, a control-plane hero with the agent lifecycle pipeline, and an execution-activity chart (stacked completed/failed per time bucket, hover tooltip, table view, colorblind-safe palette) on the Dashboard and Operations pages; `GET /api/v1/operations/timeseries`.
- Evaluation: reusable security suite (`evaluation.security_suite`), model comparison (`POST /api/v1/evaluations/model-comparison`, never gates publishing), LLM-as-judge (`acceptance.judge`, advisory; optional `min_score` gate that fails closed).
- Runtime reliability: in-doubt guard (an interrupted non-idempotent tool call is never repeated; operators resolve it via `POST /executions/{id}/tool-invocations/{inv}/resolve`), dead-lettering after `AF_MAX_EXECUTION_ATTEMPTS`, `execution_limits.max_total_retries`, per-tool and per-model `max_retries`, jittered tool retry backoff, circuit breakers for model providers and network/MCP tools (`GET /api/v1/system/circuits`), `executions:recover` permission for operators.
- Dependency graph recorded at build (tools, models, profiles, components, specialists, suites), drift detection, reverse queries, impact reports and revalidation (`/api/v1/dependencies/*`), tool version pinning in the harness, agent ownership contacts and component owners; deprecated/missing/disabled dependencies block publish and deploy.
- Operations: trace id on every execution, richer span attributes, metrics, agent health, SLOs with error budgets, drift, cost budgets per workspace/agent (blocking or warn), alerts (`/api/v1/operations/*`, `/api/v1/agents/{id}/health`); Operations page.
- MCP connectors (stdio and Streamable HTTP): explicit trust, discovery, schema validation, allow-listing, default high risk + write, credential injection from secrets, output limits (`/api/v1/connectors/mcp`); MCP Connectors page.
- Secrets: HashiCorp Vault KV v2 backend, versioned rotation, audited secret reads (names only).
- Audit: external append-only sink (`AF_AUDIT_SINK_PATH`), `GET /api/v1/audit/export`, `agent-factory audit export|verify-export`.
- Policy simulation (`POST /api/v1/policies/simulate`) and per-version policy history (`GET /api/v1/agents/{id}/policies`).
- Releases: immutable hashed artifacts on publish, environments (development/test/staging/production), deployments with approval and separation of duties, promotion from staging only, canary (1–50%), promote, rollback, retire, environment-tightened limits; executions can target an environment; Releases and Dependencies agent tabs.
- CI: Trivy image scan in the container workflow; backup/restore drill test; end-to-end release journey test.
- Docs: production architecture, runtime reliability, dependency management, observability, agent CI/CD, production security, production readiness, preliminary TM Forum assessment, ADRs 0011–0014.
- Release gates: mandatory security case kinds (`permission`, `prompt_injection` block release at any pass rate), p95 latency and cost limits, and a regression gate against the published version (`acceptance.regression: block|warn|off`). Every gate failure is explained in the run summary.
- `GET /api/v1/agents/{id}/release-gate`: whether a version can be published and every blocking reason (the same rules `publish` enforces); release-gate card in the agent's Evaluations tab.
- Evaluation run provenance (tool versions, pinned components, prompt hash, suite, platform and database) and the baseline run it was compared with (migration `0003`).
- Production assessment and roadmap: `docs/product/implementation-status.md`, `docs/product/production-roadmap.md`, `docs/architecture/evaluation-architecture.md`.
- Interview Prep example: resume tailored to a job description saved as Word and PDF, scenario-based questions and answers from the job description and previous feedback, video talking points, study checklist and day plan; `load_interview_materials`, `get_video_transcripts` and `save_interview_prep` tools; `AF_DOCUMENT_DIRS`, `AF_INTERVIEW_PREP_DIR` settings.
- Video Summarizer & Transcriber example; `get_video_transcript` (YouTube captions, local Whisper/MLX, fixtures) and `save_video_notes` tools; `agent-factory media setup|status`.
- Workflow `foreach` (map) model steps, per-step `output_schema`, `include_steps` context control; per-tool output size limits.
- Email connector: read-only IMAP fetch, send-to-self SMTP, digest files, macOS notifications.
- Daily Email Digest example agent, `agent-factory email setup|test`, `agent-factory schedule install|list|remove` (launchd).
- Tools can declare their own network destination; Ollama requests set `num_ctx` and disable thinking for structured output.

### Changed
- Recovering an execution that crashed before its first checkpoint now starts it fresh (previously failed with `EmptyInputError`).
- A human-review verdict now re-applies the gates and updates the version status (previously a required human-review case could never unblock a release). Only `human_review` cases accept a verdict; deterministic results cannot be overridden. Reviewing your own version is refused when `AF_ALLOW_SELF_APPROVAL=false`.
- Publishing is refused when the published version changed after the evaluation ran; re-run the evaluation to compare with the new baseline. Runs recorded before this change are not affected.

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

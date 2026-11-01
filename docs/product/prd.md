# Product requirements (summary)

**Product:** Agent Factory — Local-First AI Agent Engineering Platform.
**Problem:** Teams build agents as ad-hoc scripts: no specification, no reproducibility, model-decided permissions,
no repeatable evaluation, no audit trail, no path from laptop to server.
**Goal:** A platform where an agent is created once, tested systematically, understood, controlled, deployed safely
and improved without losing reproducibility.

## Users
AI engineers (design/evaluate), platform engineers (runtime/security/deployment), data engineers (pipeline and data
quality agents), application developers (API integration), enterprise architects (governance standards).

## Core journey (must work locally without cloud)
Create (natural language or template) → validate → build → execute → observe → evaluate → publish → operate →
improve (new version, compare, roll back).

## Functional requirements
1. Natural-language designer producing an editable draft that is never executable until validated and built.
2. Versioned `agentfactory/v1` specification with JSON Schema; schema evolution via upgraders.
3. Template registry (10 types) and versioned component registry; custom/imported templates.
4. Builder producing a build report and provenance; no generated code execution.
5. Governed harness: context engineering, model gateway, tool orchestration, state, human approval, budgets.
6. Runtime: single, sequential and supervisor patterns; checkpointing; resume without repeating side effects.
7. Tools with declared contracts; allowlists; SSRF/path protections; sandboxed code execution or refusal.
8. Memory concepts separated; local retrieval with attribution; documents are untrusted.
9. Evaluation as a product capability with metrics, golden cases, version comparison and a publish gate.
10. Lifecycle states, immutable published versions, rollback, release notes.
11. Dashboard covering all of the above; REST API under `/api/v1`; CLI.
12. Security: authN (local/API keys/OIDC), RBAC, workspaces, audit, secrets, retention.
13. Observability: structured logs, events, tracing, health/readiness.
14. Packaging: native local install; Docker Compose single node; documented scale-out path; CI.

## Non-functional targets
Controlled failure outcomes; documented module interfaces; new provider/tool without cross-cutting changes;
macOS dev + Linux containers; no unbounded loops; reproducible versions and evaluations.

## Out of scope for 0.1
Kubernetes/Helm, multi-region, marketplace of third-party tools, fine-tuning, MCP server trust management UI.

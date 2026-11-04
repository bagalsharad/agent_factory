# Agent CI/CD and release management

```
draft ─build─▶ testing ─evaluation gates pass─▶ approved ─publish─▶ published ──▶ artifact (immutable, hashed)
                                                                                     │
           development ─▶ test ─▶ staging ──promote same artifact + approval by someone else──▶ production
                                                   canary (1–50%) ─promote─▶ primary      rollback ◀─┘
```

## Lifecycle

| Brief | Platform state | Where |
|---|---|---|
| Draft | `draft` | version |
| Built | `testing` (validated and built) | version |
| Testing / Evaluated | evaluation runs; `approved` once every gate passes | version + `evaluation_runs` |
| Approved | `approved` | version |
| Published | `published` (+ artifact) | version + `agent_artifacts` |
| Deployed | an `active` deployment in an environment | `deployments` |
| Suspended | `disabled` (version) or `retired` (deployments in an environment) | version / `deployments` |
| Deprecated | `deprecated` | version |
| Retired | all deployments retired + versions disabled | `deployments` / version |

Every transition is a database row change plus an audit event (`agent.build`, `evaluation.complete`,
`agent.publish`, `artifact.create`, `deployment.request|approve|reject|promote|rollback|retire`).

## Artifacts

Created when a version is published (`registry/releases.py`, `agentfactory.artifact/v1`): the specification and
its hash, workflow, model configuration, policy hash, resolved dependencies (tool and component versions), the
evaluation run that approved it (suite name, version, hash, key metrics), and build metadata (platform version,
build and publish times, publisher). `artifact_hash` = SHA-256 of the canonical JSON.

Specifications never contain secret values (the validator rejects them), so artifacts carry no credentials.
`GET /api/v1/agents/{id}/versions/{v}/artifact` returns the artifact and a fresh verification: content hash, and
that the version's specification and `spec_hash` still match. Deploying, and every execution routed through a
deployment, re-verifies; a tampered record is refused.

## Environments

`development`, `test`, `staging`, `production`, created per workspace on first use. Production requires approval
and only accepts an artifact that was deployed to staging. `PUT /api/v1/environments/{name}` sets environment
limits (`{"limits": {...}}`), which can only **tighten** the version's execution limits, never loosen them, and
whether approval is required (production cannot drop approval while separation of duties is on).

## Deployments

| Action | API | Rules |
|---|---|---|
| Deploy | `POST /api/v1/agents/{id}/deployments {version, environment, strategy, traffic_percent}` | published version; artifact verifies; no unsafe dependencies; promotion source respected; `agents:publish` |
| Approve / reject | `POST /api/v1/deployments/{id}/decision` | someone other than the requester when `AF_ALLOW_SELF_APPROVAL=false` |
| Canary | `strategy: canary`, 1–50% | runs next to the primary; traffic split by a stable hash of the request (idempotency key) |
| Promote canary | `POST .../environments/{env}/promote-canary` | canary becomes primary |
| Roll back | `POST .../environments/{env}/rollback` | drops the canary, else restores the previous primary as a new deployment |
| Retire | `POST .../environments/{env}/retire` | stops serving the agent in that environment |
| Status | `GET /api/v1/agents/{id}/deployments` | primary, canary, pending approvals and history per environment |

Executions choose a deployment with `POST /api/v1/executions {agent_id, environment, input}`; the chosen
deployment, version, artifact hash and whether it was the canary are recorded in `config_snapshot.deployment`.
Without `environment`, the active published version runs as before.

## GitHub Actions

| Workflow | Checks |
|---|---|
| `ci.yml` | ruff, mypy, unit/integration/security/e2e/performance tests on the mock model, Alembic `upgrade` + `check` on PostgreSQL, full suite on PostgreSQL, web lint/typecheck/tests/build |
| `security.yml` | pip-audit, npm audit (production deps), dependency review |
| `container.yml` | image build, production-profile smoke test, Trivy image scan (HIGH/CRITICAL report; fixable CRITICAL fails the job), CycloneDX SBOM |
| `release.yml` | tagged image build and GitHub release |

Actions are pinned by commit SHA. No workflow uses repository secrets except the registry login in `release.yml`.

## Known limitations

- Deployments decide which version an execution runs on this platform instance; they do not provision separate
  infrastructure per environment.
- Canary routing is per request, not per user or session.
- No automatic rollback on SLO breach yet; alerts tell an operator to roll back.

# ADR-0014: Release artifacts, environments and deployments live in the platform database

Status: accepted

Decision: an immutable, hashed artifact is created when a version is published; environments (development, test,
staging, production) and deployments (standard or canary, with approval and rollback) are rows in the platform
database. Executions choose a version by environment; the artifact is re-verified on every deploy and routed
execution.

Why: the platform already runs every agent itself, so "deploying" means deciding which immutable version serves
an environment; separate infrastructure per environment is not needed to get promotion rules, approvals, canaries
and rollback with a complete audit trail.

Consequences: environments share one platform instance. Organizations that need physically separate environments
run separate instances and promote artifacts between them (export/import is future work).

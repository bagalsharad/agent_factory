# Production readiness

Status of the platform-level definition of done. "Demonstrated" means an automated test or a recorded run shows
it; "not yet demonstrated" means it is implemented or documented but has not been shown in that setting.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | An agent can be created and built | Demonstrated | `tests/e2e/test_journey.py`, `test_release_journey.py` |
| 2 | Evaluated independently | Demonstrated | deterministic checks read tool-invocation records; `test_release_gates.py` |
| 3 | Failed mandatory tests prevent publishing | Demonstrated | mandatory kinds, regression gate, baseline drift (`test_release_gates.py`, journey step 3–4) |
| 4 | Tool permissions enforced outside the model | Demonstrated | `tests/security/`, MCP tests, compromised-model security case |
| 5 | Consequential operations need authorized approval | Demonstrated | approval tests; MCP write tools wait for approval; production deploy approval with SoD |
| 6 | Runtime failures recover safely | Demonstrated (single node) | `test_runtime_reliability.py`: crash mid-write is never repeated; dead letters; circuits |
| 7 | Component changes trigger impact analysis | Demonstrated | `test_dependencies.py` |
| 8 | Every execution can be traced | Demonstrated | trace id on every execution; span tree test. Not validated against a real collector |
| 9 | Cost and reliability monitored | Demonstrated | `test_operations.py` (metrics, SLOs, drift, budgets, alerts) |
| 10 | Published artifacts immutable and versioned | Demonstrated | hashed artifacts, tamper test in `test_releases.py` |
| 11 | Deployments can be rolled back | Demonstrated | journey step 13, `test_releases.py` |
| 12 | Production configuration is secure | Partially | production profile refuses unsafe settings; container smoke test in CI; no external penetration test |
| 13 | Backups restore successfully | Demonstrated for SQLite | `tests/e2e/test_backup_restore.py`. PostgreSQL `pg_dump`/`pg_restore` procedure documented, **not yet drilled** |
| 14 | Clean Linux deployment validated | **Not yet demonstrated** | CI builds and smoke-tests the image on Ubuntu runners; a full Compose deployment (TLS, Postgres, separate worker) on a clean Linux host has not been recorded |
| 15 | Complete automated suite passes | Demonstrated | CI (Python on SQLite and PostgreSQL, web, container) |

## Before calling a deployment production-ready

1. Run `docker compose --profile production up` on a clean Linux host with TLS, PostgreSQL and a separate worker;
   record `/ready`, a governed execution, an approval, a worker kill + recovery and a restart.
2. Drill PostgreSQL backup and restore (`docs/deployment/backup-restore.md`) and verify the audit chain after.
3. Point an OTLP collector at the API and worker and confirm traces arrive.
4. Set the [production security checklist](../security/production-security.md#production-checklist).
5. Evaluate every agent on the real model it will use; mock-model results do not prove quality.
6. Define SLOs and budgets for each production agent and wire `/api/v1/operations/alerts` into monitoring.

No performance or availability numbers are claimed: measure them on the target hardware
(`tests/performance/` gives a starting harness).

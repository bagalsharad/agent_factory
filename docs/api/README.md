# API

Interactive documentation: `http://127.0.0.1:8787/docs` (Swagger UI) and `/redoc`. Machine-readable:
[openapi.json](openapi.json) (regenerate with `uv run python scripts/generate_reference.py`).

## Conventions

- Base path `/api/v1`; probes `/health` (liveness) and `/ready` (readiness: database, migrations, worker).
- Auth: none in local mode; `Authorization: Bearer <api key | OIDC JWT>` otherwise. `X-Workspace-ID` (id or slug)
  selects a workspace you are a member of.
- Errors: `{"error": {"code", "message", "details", "request_id"}}` with appropriate status codes
  (401, 403, 404, 409 `invalid_state`/`conflict`, 413, 422 `validation_failed`/`request_invalid`, 429, 5xx).
- Every response carries `X-Request-ID` (send your own to correlate logs, events and audit entries).
- Pagination: `limit` (1–200, default 50) and `offset`; list responses are `{items, total, limit, offset}`.
- Idempotency: `Idempotency-Key` on `POST /agents` and `POST /executions`; repeats return the original result,
  reuse with a different body returns 409.
- Rate limits: `POST /executions` and `POST /evaluations` per principal (`AF_EXECUTION_RATE_LIMIT_PER_MINUTE`).
- Request bodies are limited to `AF_MAX_REQUEST_BYTES` (uploads: 11 MB).

## Endpoints

| Area | Endpoints |
|---|---|
| Agents | `POST /agents` (spec, spec_yaml or template+name), `GET /agents`, `GET /agents/{id}`, `PUT /agents/{id}`, `POST /agents/{id}/validate`, `/build`, `/publish`, `/deprecate`, `/disable`, `/enable`, `/duplicate`, `GET /agents/{id}/workflow`, `GET /agents/{id}/compare?base&other`, `POST /agents/draft`, `POST /agents/preview` |
| Versions | `GET /agents/{id}/versions`, `GET /agents/{id}/versions/{version}`, `POST /agents/{id}/rollback` |
| Executions | `POST /executions` (202), `GET /executions` (filters: agent_id, status, since, until, q, mode, include_children), `GET /executions/{id}`, `POST /executions/{id}/cancel`, `POST /executions/{id}/retry`, `DELETE /executions/{id}`, `GET /executions/{id}/events?after=`, `GET /executions/{id}/events/stream` (SSE) |
| Approvals | `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/reject` |
| Evaluation | `POST /evaluations`, `GET /evaluations`, `GET /evaluations/{id}`, `POST /evaluations/{id}/results/{result_id}/review`, `POST /evaluations/compare`, `POST/GET /evaluation-suites`, `GET /evaluation-suites/{id}` |
| Tools & models | `GET /tools`, `GET /models/providers`, `GET /models/available`, `GET /models/recommendations`, `POST /models/test` |
| Catalog | `GET/POST /templates`, `GET /templates/{name}`, `GET/POST /components`, `POST /components/{id}/deprecate`, `DELETE /components/{id}` |
| Knowledge | `GET /knowledge/collections`, `POST /knowledge/collections/{name}/ingest`, `/upload`, `/reindex`, `GET /knowledge/documents`, `DELETE /knowledge/documents/{id}`, `POST /knowledge/search` |
| Platform | `GET /system/status`, `GET /dashboard`, `GET /me`, `GET /schemas/agent-spec`, `GET /system/settings`, `PUT /system/settings/tools`, `PUT /system/settings/workspace`, `GET/POST /secrets`, `GET/POST /api-keys`, `DELETE /api-keys/{id}`, `GET /audit`, `GET /audit/verify`, `POST /admin/retention/purge` |

## Example

```bash
AGENT=$(curl -s 'http://127.0.0.1:8787/api/v1/agents?q=data-pipeline' | jq -r '.items[0].id')
EXEC=$(curl -s -X POST http://127.0.0.1:8787/api/v1/executions -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d "{\"agent_id\":\"$AGENT\",\"input\":{\"pipeline_id\":\"orders_daily\",\"execution_id\":\"run_2026_10_29\"}}" | jq -r .id)
curl -N http://127.0.0.1:8787/api/v1/executions/$EXEC/events/stream
```

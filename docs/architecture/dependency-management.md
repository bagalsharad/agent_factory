# Dependency management

Every built agent version records what it depends on, with the versions resolved at build time. Shared changes
become visible and reviewable instead of silently changing published agents.

## The graph

`dependency_edges` (written by `DependencyService.record` after a successful build; older versions are backfilled
on start-up):

| Kind | Name | Version recorded |
|---|---|---|
| `tool` | tool name (built-in or `mcp__server__tool`) | pinned version, else the registry version at build |
| `model` | `provider/model` | – |
| `model_profile` | profile name | – |
| `component` | `kind/name` (prompts, policies, validation rules, datasets, …) | pinned version (mandatory) |
| `agent` | specialist agent name | pinned version if any |
| `evaluation_suite` | registered suite name | – |

## Queries and impact

| Question | API |
|---|---|
| What does this version depend on, and is anything drifted, deprecated, missing or disabled? | `GET /api/v1/agents/{id}/dependencies?version=` |
| Which agent versions use this tool, policy, model or component? | `GET /api/v1/dependencies/dependents?kind=&name=[&version=]` |
| What happens if it changes to version X? | `POST /api/v1/dependencies/impact` |
| Re-evaluate everything affected | `POST /api/v1/dependencies/revalidate` |
| Who owns this agent? | `PUT /api/v1/agents/{id}/ownership` (business owner, technical and escalation contacts) |

The impact report lists every affected version with its status, whether it is in service, compatibility
(`unchanged`, `compatible` for the same major version, `breaking` for a major change, `unknown`), risk, last
validation date, affected suites, contacts to notify, and a revalidation plan. It is recorded in the audit log
(`dependency.impact`). Revalidation starts a release evaluation run for every version in service or under test.

## Pinning and drift

- Component references require a version (`ComponentRef`), so they can never be replaced silently.
- A tool reference with a version (`tools: [{name: x, version: 1.0.0}]`) runs exactly that version even after a
  newer one is registered (`Harness._resolve_tool`).
- Without a pin, the build-time version is recorded; if the registry later moves on, the dependency shows as
  `drifted` ("built with 1.0.0, registry now 1.1.0") so it can be revalidated.

## Unsafe promotion is blocked

`publish`, the release gate and deployments refuse a version whose dependencies are deprecated, missing or
disabled ("Unsafe dependencies: …").

## Known limitations

- Notifications are a list of contacts in the report plus an audit event; nothing is emailed or posted.
- Compatibility is based on semantic versions; tool schemas are not diffed.
- MCP tools are platform-wide (not per workspace).

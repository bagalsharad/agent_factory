# Threat model

Scope: the Agent Factory API, dashboard, workers, harness, tools and data stores, in local and single-node
production deployments. Reference frameworks: OWASP Top 10 for LLM Applications (2025) and OWASP guidance on
agentic AI threats. Assets: workspace data and documents, execution traces, credentials/secrets, the integrity of
agent specifications and approvals, and the systems tools can touch.

Trust boundaries: browser ↔ API; API ↔ database; harness ↔ model provider (local or cloud); harness ↔ tools ↔
connected systems; content returned by tools/documents/history ↔ model context.

| Threat | Mitigations implemented | Tests | Residual risk |
|---|---|---|---|
| Direct prompt injection (user input) | Inputs validated against the agent's input schema; the model cannot authorize actions; policy engine decides every tool call | `test_input_is_validated_before_execution`, adversarial model cases | The model's *text output* can still be manipulated; treat outputs as untrusted downstream |
| Indirect prompt injection (tool output, documents, history) | Untrusted content wrapped in `<untrusted_*>` tags with tag-escape protection; harness preamble; tool allowlist + RBAC + approvals at the policy boundary | `test_indirect_prompt_injection_cannot_trigger_unapproved_tools`, `test_malicious_tool_output_is_contained`, injection evaluation cases | Delimiting is a mitigation only; injected text may still bias answers. Never give agents more tools than needed |
| Excessive agency | Read-only by default; write tools require `allow_write_operations` and are approval-gated (action class, risk, writes); bounded delegation; no recursive spawning | `test_write_tool_requires_approval_then_allows_once_granted`, `test_delegation_is_bounded` | An approver can approve a harmful action; approvals show arguments, rationale and policy evidence |
| Unauthorized tool execution | Tool registry + agent allowlist + RBAC permissions + workspace check + current-role re-resolution per execution | `test_tool_outside_allowlist_is_denied`, `test_revoked_requester_cannot_resume` | — |
| Fabricated approvals | Approvals only via `ApprovalService`; harness re-reads decisions from the DB; resume payload ignored; separation of duties in production | `test_model_cannot_fabricate_approvals`, `test_separation_of_duties_and_approver_roles` | Local mode allows self-approval (single user) |
| Sensitive data exposure | Redaction of secrets in logs/events/audit/errors; content capture off in production; retention purge; execution deletion; workspace isolation | `test_secrets_are_redacted_*`, `test_cross_workspace_*` | Redaction is pattern-based; unusual secret formats may slip through |
| Credential leakage | Secrets referenced by name, never in specs (validator rejects credential-like values), never in model context; hashed API keys; settings API masks secret values | `test_specs_with_embedded_credentials_cannot_be_built`, `test_settings_and_secrets_never_leak_values` | Env-var secret backend is for local development only |
| Insecure output handling | Structured outputs validated against JSON Schema and validation rules; dashboard renders text, never HTML from outputs | `malformed-output` evaluation case | Consumers of the API must treat outputs as untrusted |
| Unsafe code execution | No generated code runs in the backend; code tools require a Docker sandbox (no network, read-only FS, dropped caps, non-root, limits); absent sandbox → refusal | `test_sandbox_absence_never_degrades_to_host_execution` | Docker isolation is not a VM boundary |
| SSRF | Host must be in platform AND agent allowlists; DNS-resolved IPs must be public; https only; no redirects; credentials in URLs refused; size/content-type limits | `test_ssrf_and_allowlist_checks`, `test_unauthorized_network_destinations_are_blocked` | DNS rebinding between check and connect is possible in theory; pin resolution in a future version |
| Path traversal | `safe_join` under fixed roots, ID regexes, symlinks refused, approved knowledge roots only | `test_read_logs_success_and_path_traversal`, `test_code_reader_blocks_traversal_and_symlinks` | — |
| SQL injection | SQLAlchemy parameterized queries throughout; no SQL tool exposed to models | code review | — |
| Cross-workspace access | All queries filter by workspace; identical 404 for missing vs other-workspace resources; workspace header requires membership | `test_cross_workspace_access_is_denied`, `test_api_key_authentication_and_rbac` | — |
| DoS / unbounded consumption | Budgets (steps, model/tool calls, tokens, cost, time), per-call timeouts, request size limit, per-principal rate limits, bounded delegation, recursion limit | `test_budget_exhaustion_terminates_safely`, `test_request_size_limit`, `test_rate_limit` | Rate limiter is per process; use the reverse proxy for global limits |
| Malicious / compromised connectors | Connectors are registered code; outputs validated against schemas and size-capped; MCP not enabled | `test_executor_timeout_output_validation_and_retry` | A malicious registered tool is trusted code — review tools like any dependency |
| Supply-chain compromise | Lockfiles (`uv.lock`, `package-lock.json`), pip-audit/npm audit, dependency review, SBOM, pinned GitHub Action SHAs, Dependabot | CI `security.yml` | Upstream compromise between audits |
| Audit log tampering | Append-only hash chain with verification endpoint/CLI | `test_audit_chain_detects_tampering` | Tamper-evident, not tamper-proof; ship audit events to an external WORM sink in production |
| Unauthenticated exposure | Local mode binds loopback only (startup refuses otherwise); production profile requires API keys/OIDC | `test_local_auth_refuses_network_binding`, `test_production_profile_refuses_insecure_configuration` | — |

Prompt injection cannot be completely eliminated. Agent Factory's design goal is that a fully compromised model
can only *request* actions that the agent, the requester's role and the policy already permit — and anything
consequential still waits for a human.

# ADR-0012: MCP tools are registered as ordinary governed tools

Status: accepted

Decision: an MCP server's allow-listed tools are wrapped as `Tool` instances (`mcp__<server>__<tool>`) in the
platform tool registry instead of being called from a separate path. Every call therefore passes the agent
allowlist, RBAC, policy (writes, network destination), approvals, budgets, circuit breaker, timeouts, output
limits, events and audit exactly like a built-in tool.

Servers start untrusted; only trusted servers, allow-listed tools and valid object schemas are exposed. Unknown
tools default to high risk + write until an administrator classifies them; server-provided annotations are not
trusted. stdio servers run only allow-listed executables without a shell; credentials are injected from the
secret store at call time.

Consequences: MCP tools are platform-wide (not per workspace); a stdio server process is started per call, which
is simple and isolating but slower than a pooled session.

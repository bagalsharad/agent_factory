# ADR-0011: Keep the in-process policy engine; no OPA for now

Status: accepted

Context: the production plan asks whether to adopt OPA (or similar) for policy as code. Policies today are part of
each agent specification and are evaluated by `PolicyEngine` on every tool call, inside the harness.

Decision: keep the in-process engine. It is deterministic, tested, needs no extra service, and sees the exact
tool declaration and agent spec. Policy-as-code needs are met by: policies versioned with the spec (immutable once
approved), `GET /agents/{id}/policies` (effective policy, hash and diff per version), `POST /policies/simulate`
(decision with rules and reasons, nothing executed) and policy tests in the suite.

Consequences: organization-wide policies still live per agent (components of kind `policy_definition` can be
shared and pinned). Revisit OPA when policies must be shared across systems other than Agent Factory or edited
by a separate governance team; the `PolicyDecision` shape is the integration point.

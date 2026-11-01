# ADR-0007: Authorization outside the model

Status: accepted

`PolicyEngine.evaluate_tool` decides allow / deny / require-approval for every invocation using the tool
declaration (risk, side effects, network, action class, permissions), the agent's policies, the requester's
*current* role (re-resolved per execution), workspace membership and platform settings. Tool discovery offers the
model only tools that would not be denied. Approvals are created by the harness, decided only through
`ApprovalService` (role + approver list + optional separation of duties), and re-read from the database on
resume. Consequence: a fully compromised model can at most *request* actions; denials are audited.

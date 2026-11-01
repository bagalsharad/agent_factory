"""Policy engine — the authority on whether an agent action may proceed.

The model never decides. Every tool invocation request is evaluated here against:

1. the tool registry (registered, enabled),
2. the agent version's approved tool list,
3. the requesting principal's role permissions (current, re-resolved per execution),
4. the agent's declared policies (write operations, external network, endpoint allowlist),
5. platform settings (disabled tools, network allowlist, sandbox availability),
6. approval rules (action classes, risk levels, writes).

Rules are evaluated in order; the first deny wins. Approval requirements accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.config import Settings
from ..core.ids import content_hash
from ..spec.models import AgentSpecBody, RiskLevel
from ..tools.base import SideEffect, ToolDefinition
from ..tools.builtin.http import host_matches
from .rbac import Principal


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PolicyDecision:
    effect: Effect
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    approval_action: str | None = None

    @property
    def allowed(self) -> bool:
        return self.effect == Effect.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "reasons": self.reasons,
            "rules": self.rules,
            "approval_action": self.approval_action,
        }


@dataclass
class ToolAuthorizationRequest:
    principal: Principal | None
    workspace_id: str
    agent: AgentSpecBody
    tool_name: str
    tool: ToolDefinition | None
    tool_enabled: bool
    arguments: dict[str, Any]
    sandbox_available: bool = False
    approval_already_granted: bool = False
    # Destination resolved by the tool itself (Tool.network_destination). ``None`` with ``destination_resolved``
    # means this particular call uses no network. Unresolved requests fall back to the ``url`` argument.
    destination_host: str | None = None
    destination_resolved: bool = False


class PolicyEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def policy_hash(agent: AgentSpecBody) -> str:
        return content_hash(
            {
                "policies": agent.policies.model_dump(mode="json"),
                "human_approval": agent.human_approval.model_dump(mode="json"),
            }
        )

    def allowed_hosts(self, agent: AgentSpecBody) -> list[str]:
        """Effective endpoint allowlist: intersection of platform and agent allowlists."""
        if not agent.policies.external_network:
            return []
        platform = self.settings.network_allowlist
        return [
            h for h in agent.policies.allowed_network_hosts if host_matches(h.lstrip("*."), platform) or h in platform
        ]

    def evaluate_tool(self, req: ToolAuthorizationRequest) -> PolicyDecision:
        def deny(rule: str, reason: str) -> PolicyDecision:
            return PolicyDecision(Effect.DENY, [reason], [rule])

        tool = req.tool
        if tool is None:
            return deny("tool.registered", f"Tool '{req.tool_name}' is not registered")
        if not req.tool_enabled or tool.name in self.settings.disabled_tools:
            return deny("tool.enabled", f"Tool '{tool.name}' is disabled by platform settings")
        if tool.name not in req.agent.tool_names:
            return deny("agent.tool_allowlist", f"Tool '{tool.name}' is not approved for this agent")
        if req.principal is None:
            return deny("principal.present", "No authorized principal for this execution")
        for permission in tool.permissions:
            if not req.principal.can(permission):
                return deny(
                    "rbac.permission",
                    f"Role '{req.principal.role}' lacks permission '{permission}' required by '{tool.name}'",
                )
        if req.principal.workspace_id != req.workspace_id:
            return deny("workspace.isolation", "Principal does not belong to the execution workspace")
        policies = req.agent.policies
        if tool.side_effects == SideEffect.WRITE and not policies.allow_write_operations:
            return deny("agent.write_operations", f"Tool '{tool.name}' performs writes; agent policy forbids writes")
        if tool.network or tool.side_effects == SideEffect.EXTERNAL:
            if not policies.external_network:
                return deny("agent.external_network", "Agent policy forbids external network access")
            from urllib.parse import urlsplit

            if req.destination_resolved:
                host = (req.destination_host or "").lower()
                needs_network = req.destination_host is not None
            else:
                host = (urlsplit(str(req.arguments.get("url", ""))).hostname or "").lower()
                needs_network = True
            if needs_network and (not host or not host_matches(host, self.allowed_hosts(req.agent))):
                return deny("network.allowlist", f"Destination '{host or '(none)'}' is not an approved endpoint")
        if tool.requires_sandbox and not req.sandbox_available:
            return deny("sandbox.available", "Code execution requires an isolated sandbox, which is unavailable")

        reasons: list[str] = []
        rules: list[str] = []
        action = tool.action_class or tool.name
        if tool.action_class and tool.action_class in policies.require_approval_for:
            reasons.append(f"Action class '{tool.action_class}' requires human approval")
            rules.append("approval.action_class")
        if RiskLevel(tool.risk) in req.agent.human_approval.required_for_risk:
            reasons.append(f"Risk level '{tool.risk}' requires human approval")
            rules.append("approval.risk")
        if tool.side_effects != SideEffect.READ and req.agent.human_approval.require_for_writes:
            reasons.append("Operations with side effects require human approval")
            rules.append("approval.writes")
        if reasons and not req.approval_already_granted:
            return PolicyDecision(Effect.REQUIRE_APPROVAL, reasons, rules, approval_action=action)
        return PolicyDecision(Effect.ALLOW, reasons or ["All policy checks passed"], rules or ["default.allow"])

    def evaluate_delegation(self, agent: AgentSpecBody, delegations_so_far: int) -> PolicyDecision:
        limit = min(agent.workflow.max_delegations, agent.execution_limits.max_subagents)
        if delegations_so_far >= limit:
            return PolicyDecision(Effect.DENY, [f"Delegation limit of {limit} reached"], ["delegation.limit"])
        return PolicyDecision(Effect.ALLOW, ["Delegation within bounds"], ["delegation.bounded"])

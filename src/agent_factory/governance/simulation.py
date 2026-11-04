"""Policy simulation and history: what would the policy engine decide, and how did an agent's policy change.

Simulation evaluates the real PolicyEngine against an agent version without executing anything, so policy changes
can be tested before they are published. The decision includes its evidence (rules, reasons, policy hash).
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ValidationFailedError
from ..registry.agents import AgentService
from ..registry.diff import json_diff
from ..spec.loader import load_spec
from ..tools.registry import ToolRegistry
from .policy import PolicyEngine, ToolAuthorizationRequest
from .rbac import ROLE_PERMISSIONS, Principal, Role, make_principal


class PolicySimulator:
    def __init__(
        self, policy: PolicyEngine, tools: ToolRegistry, agents: AgentService, sandbox_available: bool
    ) -> None:
        self.policy = policy
        self.tools = tools
        self.agents = agents
        self.sandbox_available = sandbox_available

    def simulate(
        self,
        principal: Principal,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        version: str | None = None,
        as_role: str | None = None,
    ) -> dict[str, Any]:
        principal.require("agents:read")
        _agent, record = self.agents.load_version(principal, agent_id, version)
        spec = load_spec(record.spec)
        acting = principal
        if as_role is not None:
            if as_role not in {r.value for r in ROLE_PERMISSIONS}:
                raise ValidationFailedError(f"Unknown role '{as_role}'")
            acting = make_principal(
                principal.user_id,
                principal.subject,
                principal.display_name,
                principal.workspace_id,
                Role(as_role).value,
                "simulation",
            )
        tool = self.tools.get(tool_name) if self.tools.has(tool_name) else None
        definition = tool.definition if tool else None
        destination = (
            tool.network_destination(arguments, self.policy.settings)
            if tool and definition and definition.network
            else None
        )
        decision = self.policy.evaluate_tool(
            ToolAuthorizationRequest(
                principal=acting,
                workspace_id=principal.workspace_id,
                agent=spec.spec,
                tool_name=tool_name,
                tool=definition,
                tool_enabled=self.tools.is_enabled(tool_name),
                arguments=arguments,
                sandbox_available=self.sandbox_available,
                destination_host=destination,
                destination_resolved=bool(tool and definition and definition.network),
            )
        )
        return {
            "agent": spec.metadata.name,
            "version": record.version,
            "tool": tool_name,
            "as_role": acting.role,
            "decision": decision.to_dict(),
            "policy_hash": PolicyEngine.policy_hash(spec.spec),
            "tool_definition": {
                "risk": str(definition.risk),
                "side_effects": str(definition.side_effects),
                "network": definition.network,
                "version": definition.version,
            }
            if definition
            else None,
            "destination_host": destination,
            "executed": False,
        }

    def history(self, principal: Principal, agent_id: str) -> dict[str, Any]:
        """Every version's effective policy, its hash, and what changed from the previous version."""
        versions = sorted(self.agents.versions(principal, agent_id), key=lambda v: v["created_at"])
        items, previous = [], None
        for v in versions:
            body = load_spec(self.agents.get_version(principal, agent_id, v["version"])["spec"]).spec
            policy = {
                "tools": body.tool_names,
                "policies": body.policies.model_dump(mode="json"),
                "human_approval": body.human_approval.model_dump(mode="json"),
                "execution_limits": body.execution_limits.model_dump(mode="json"),
            }
            items.append(
                {
                    "version": v["version"],
                    "status": v["status"],
                    "created_at": v["created_at"],
                    "policy_hash": PolicyEngine.policy_hash(body),
                    "policy": policy,
                    "changes": json_diff(previous, policy) if previous is not None else [],
                }
            )
            previous = policy
        return {"agent_id": agent_id, "items": items}

"""Environment fingerprint Eₜ = (Mₜ, Tₜ, Rₜ, Kₜ): model, tools, agent roles, knowledge (+ policy context).

Recorded with every step (semantic checkpoint) and recomputed at recovery so the Change Impact Analyzer can tell
exactly what changed under which step.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from ..core.ids import content_hash
from ..governance.policy import PolicyEngine
from ..model_gateway.gateway import ModelGateway
from ..spec.models import AgentSpec, ModelSpec
from ..storage.db import Database
from ..storage.models import Document, KnowledgeCollection
from ..tools.registry import ToolRegistry

MODEL_CACHE_SECONDS = 30.0


class EnvironmentProbe:
    def __init__(
        self, db: Database, tools: ToolRegistry, gateway: ModelGateway, settings: Any, resolver: Any = None
    ) -> None:
        self.db = db
        self.tools = tools
        self.gateway = gateway
        self.settings = settings
        self.resolver = resolver  # workspace_id -> (ref -> ResolvedAgentRef | None)
        self._models: dict[str, tuple[float, dict[str, str | None]]] = {}

    def _digests(self, provider: str) -> dict[str, str | None]:
        cached = self._models.get(provider)
        if cached and time.monotonic() - cached[0] < MODEL_CACHE_SECONDS:
            return cached[1]
        try:
            models, _ = self.gateway.list_models(provider)
            digests = {m.name: m.digest for m in models}
        except Exception:  # an unreachable provider is itself a fact worth recording, not an error here
            digests = {}
        self._models[provider] = (time.monotonic(), digests)
        return digests

    def invalidate_models(self) -> None:
        self._models.clear()

    def model(self, model: ModelSpec) -> dict[str, Any]:
        digests = self._digests(model.provider)
        return {
            "provider": model.provider,
            "model": model.model,
            "digest": digests.get(model.model),
            "available": model.model in digests,
            "temperature": model.temperature,
        }

    def tool(self, name: str, version: str | None = None) -> dict[str, Any]:
        if not self.tools.has(name, version):
            return {"registered": False}
        definition = self.tools.get(name, version).definition
        return {
            "registered": True,
            "version": definition.version,
            "hash": definition.hash,
            "enabled": self.tools.is_enabled(name),
            "side_effects": str(definition.side_effects),
            "idempotent": definition.idempotent,
        }

    def platform(self) -> str:
        s = self.settings
        return content_hash(
            {
                "disabled_tools": sorted(s.disabled_tools),
                "network_allowlist": sorted(s.network_allowlist),
                "sandbox": str(s.sandbox_backend),
                "allow_private_network": s.allow_private_network,
            }
        )

    def knowledge(self, workspace_id: str, collections: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        with self.db.session() as session:
            for name in collections:
                coll = session.scalars(
                    select(KnowledgeCollection).where(
                        KnowledgeCollection.workspace_id == workspace_id, KnowledgeCollection.name == name
                    )
                ).first()
                docs = (
                    sorted(
                        (d.id, d.content_hash)
                        for d in session.scalars(select(Document).where(Document.collection_id == coll.id))
                    )
                    if coll
                    else None
                )
                out[name] = content_hash(docs) if docs is not None else "missing"
        return out

    def specialist(self, workspace_id: str, ref: str) -> dict[str, Any]:
        resolved = self.resolver(workspace_id)(ref) if self.resolver else None
        if resolved is None:
            return {"available": False}
        return {"available": bool(resolved.executable), "version": resolved.version, "version_id": resolved.version_id}

    def snapshot(self, workspace_id: str, spec: AgentSpec, spec_hash: str, model: ModelSpec) -> dict[str, Any]:
        """The full environment an execution of this spec runs in right now."""
        body = spec.spec
        refs = [s.agent for s in body.workflow.steps if s.agent] + [s.agent for s in body.workflow.specialists]
        knowledge = body.memory.knowledge.collections if body.memory.knowledge.enabled else []
        return {
            "spec_hash": spec_hash,
            "policy_hash": PolicyEngine.policy_hash(body),
            "platform": self.platform(),
            "model": self.model(model),
            "tools": {ref.name: self.tool(ref.name, ref.version) for ref in body.tools},
            "specialists": {ref: self.specialist(workspace_id, ref) for ref in refs},
            "knowledge": self.knowledge(workspace_id, knowledge),
        }

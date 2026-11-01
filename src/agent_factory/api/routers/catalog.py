"""Tools, model providers, templates and components."""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...core.errors import ValidationFailedError
from ...model_gateway.types import ChatMessage, ChatRequest
from ...registry.components import ComponentCreate
from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get("/tools", summary="Registered tools with risk, side effects and permission requirements")
def tools(platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    principal.require("tools:read")
    out = []
    for d in platform.tools.definitions():
        out.append({**d.model_dump(mode="json"), "enabled": platform.tools.is_enabled(d.name), "hash": d.hash})
    return out


@router.get("/models/providers")
def providers(platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    principal.require("models:read")
    health = {h.provider: h for h in platform.gateway.health()}
    out = []
    for entry in platform.gateway.entries():
        h = health.get(entry.name)
        out.append(
            {
                "name": entry.name,
                "description": entry.description,
                "enabled": entry.enabled,
                "local": entry.local,
                "reason": entry.reason,
                "status": h.status if h else "unknown",
                "detail": h.detail if h else "",
                "latency_ms": h.latency_ms if h else None,
            }
        )
    return out


@router.get("/models/available")
def available(platform: PlatformDep, principal: PrincipalDep, provider: str | None = None) -> dict[str, Any]:
    principal.require("models:read")
    models, errors = platform.gateway.list_models(provider)
    return {"models": [m.model_dump() for m in models], "errors": errors}


@router.get("/models/recommendations", summary="Hardware-appropriate local model suggestions (never downloads)")
def recommendations(platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("models:read")
    import psutil

    from ...doctor import recommend_models

    memory_gb = psutil.virtual_memory().total / 1024**3
    installed, _ = platform.gateway.list_models("ollama") if platform.gateway.is_enabled("ollama") else ([], {})
    names = {m.name for m in installed}
    recs: list[dict[str, Any]] = list(recommend_models(memory_gb))
    for r in recs:
        r["installed"] = r["model"] in names or f"{r['model']}:latest" in names
    return {
        "memory_gb": round(memory_gb, 1),
        "recommendations": recs,
        "note": "Models are never downloaded automatically. Run the install command yourself.",
    }


class ModelTest(BaseModel):
    provider: str
    model: str
    prompt: str = Field("Reply with the single word: ready", max_length=4000)


@router.post("/models/test", summary="Send a test prompt to a provider/model")
def test_model(payload: ModelTest, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("models:test")
    request = ChatRequest(
        model=payload.model,
        messages=[ChatMessage(role="user", content=payload.prompt)],
        timeout_seconds=60,
        max_output_tokens=256,
    )
    response = platform.gateway.chat(payload.provider, request, max_retries=0)
    platform.audit.record(
        "model.test",
        "model",
        f"{payload.provider}/{payload.model}",
        actor_id=principal.user_id,
        workspace_id=principal.workspace_id,
    )
    return {
        "content": response.content[:4000],
        "latency_ms": response.latency_ms,
        "usage": response.usage.model_dump(),
        "provider": response.provider,
        "model": response.model,
    }


# ---------------------------------------------------------------------------- templates
@router.get("/templates")
def templates(platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    principal.require("templates:read")
    return platform.templates.list(principal.workspace_id)


@router.get("/templates/{name}")
def template(name: str, platform: PlatformDep, principal: PrincipalDep, version: str | None = None) -> dict[str, Any]:
    principal.require("templates:read")
    doc = platform.templates.get(principal.workspace_id, name, version)
    return {**doc.summary(), "document": doc.model_dump(mode="json", exclude_none=True)}


class TemplateImport(BaseModel):
    template: dict[str, Any] | None = None
    template_yaml: str | None = Field(None, max_length=500_000)


@router.post("/templates", status_code=201, summary="Create or import a custom template")
def create_template(payload: TemplateImport, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("templates:write")
    data = payload.template
    if data is None and payload.template_yaml:
        data = yaml.safe_load(payload.template_yaml)
    if not isinstance(data, dict):
        raise ValidationFailedError("Provide 'template' (object) or 'template_yaml'")
    result = platform.templates.create(
        principal.workspace_id, data, source="imported" if payload.template_yaml else "custom"
    )
    platform.audit.record(
        "template.create", "template", result["id"], actor_id=principal.user_id, workspace_id=principal.workspace_id
    )
    return result


# ---------------------------------------------------------------------------- components
@router.get("/components")
def components(platform: PlatformDep, principal: PrincipalDep, kind: str | None = None) -> list[dict[str, Any]]:
    principal.require("components:read")
    return platform.components.list(principal.workspace_id, kind)


@router.post("/components", status_code=201)
def create_component(payload: ComponentCreate, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("components:write")
    result = platform.components.create(principal.workspace_id, payload)
    platform.audit.record(
        "component.create",
        "component",
        result["id"],
        actor_id=principal.user_id,
        workspace_id=principal.workspace_id,
        details={"ref": result["ref"]},
    )
    return result


@router.post("/components/{component_id}/deprecate")
def deprecate_component(component_id: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("components:write")
    return platform.components.deprecate(principal.workspace_id, component_id)


@router.delete("/components/{component_id}", status_code=204)
def delete_component(component_id: str, platform: PlatformDep, principal: PrincipalDep) -> None:
    principal.require("components:write")
    platform.components.delete(principal.workspace_id, component_id)

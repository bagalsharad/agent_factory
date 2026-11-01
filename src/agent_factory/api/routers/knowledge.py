"""Workspace knowledge: collections, ingestion (approved directories or uploads), search, deletion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from ..deps import PlatformDep, PrincipalDep

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class IngestDirectory(BaseModel):
    directory: str = Field(description="Directory inside AF_KNOWLEDGE_DIR or the bundled examples")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    collections: list[str] = Field(min_length=1)
    top_k: int = Field(5, ge=1, le=50)


@router.get("/collections")
def collections(platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    principal.require("knowledge:read")
    return platform.knowledge.collections(principal.workspace_id)


@router.post("/collections/{name}/ingest")
def ingest(name: str, payload: IngestDirectory, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("knowledge:write")
    result = platform.knowledge.ingest_directory(principal.workspace_id, name, Path(payload.directory))
    platform.audit.record(
        "knowledge.ingest",
        "collection",
        name,
        actor_id=principal.user_id,
        workspace_id=principal.workspace_id,
        details={"files": result["files"]},
    )
    return result


@router.post("/collections/{name}/upload")
async def upload(
    name: str, platform: PlatformDep, principal: PrincipalDep, file: UploadFile = File(...)
) -> dict[str, Any]:
    principal.require("knowledge:write")
    data = await file.read()
    result = platform.knowledge.ingest_bytes(
        principal.workspace_id, name, Path(file.filename or "upload.txt").name, data
    )
    platform.audit.record(
        "knowledge.upload",
        "document",
        result["document_id"],
        actor_id=principal.user_id,
        workspace_id=principal.workspace_id,
    )
    return result


@router.post("/collections/{name}/reindex")
def reindex(name: str, platform: PlatformDep, principal: PrincipalDep) -> dict[str, Any]:
    principal.require("knowledge:write")
    return platform.knowledge.reindex_collection(principal.workspace_id, name)


@router.get("/documents")
def documents(platform: PlatformDep, principal: PrincipalDep, collection: str) -> list[dict[str, Any]]:
    principal.require("knowledge:read")
    return platform.knowledge.list_documents(principal.workspace_id, [collection])


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, platform: PlatformDep, principal: PrincipalDep) -> None:
    principal.require("knowledge:write")
    platform.knowledge.delete_document(principal.workspace_id, document_id)
    platform.audit.record(
        "knowledge.delete", "document", document_id, actor_id=principal.user_id, workspace_id=principal.workspace_id
    )


@router.post("/search")
def search(payload: SearchRequest, platform: PlatformDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    principal.require("knowledge:read")
    return [
        h.model_dump()
        for h in platform.knowledge.search(
            principal.workspace_id, payload.collections, payload.query, top_k=payload.top_k
        )
    ]

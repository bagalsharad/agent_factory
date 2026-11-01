"""Workspace knowledge connector: retrieval over ingested documents.

Results are restricted to the calling workspace and to the collections the agent's specification
declares (``spec.memory.knowledge.collections``). Retrieved text is untrusted data; the harness wraps it
in delimiters and the policy engine — not the text — decides what the agent may do.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...core.errors import ToolError
from ..base import Tool, ToolContext


def _knowledge(ctx: ToolContext) -> Any:
    if ctx.knowledge is None:
        raise ToolError("Knowledge service is not available in this execution context")
    return ctx.knowledge


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="Natural-language search query.")
    top_k: int = Field(5, ge=1, le=20)
    collection: str | None = Field(None, description="Restrict to one of the agent's collections.")


class SearchDocuments(Tool):
    name = "search_documents"
    description = "Search ingested workspace documents and return the most relevant passages with sources."
    connector = "knowledge"
    Input = SearchInput

    def run(self, args: SearchInput, ctx: ToolContext) -> dict[str, Any]:
        collections = ctx.allowed_collections
        if args.collection:
            if args.collection not in collections:
                raise ToolError(f"Collection '{args.collection}' is not approved for this agent")
            collections = [args.collection]
        hits = _knowledge(ctx).search(ctx.workspace_id, collections, args.query, top_k=args.top_k)
        return {"query": args.query, "results": [hit.model_dump() for hit in hits]}


class ReadDocInput(BaseModel):
    document_id: str = Field(min_length=1, max_length=64)
    max_chars: int = Field(8000, ge=100, le=50_000)


class ReadDocument(Tool):
    name = "read_document"
    description = "Read the extracted text of one ingested document by id."
    connector = "knowledge"
    Input = ReadDocInput

    def run(self, args: ReadDocInput, ctx: ToolContext) -> dict[str, Any]:
        return _knowledge(ctx).read_document(
            ctx.workspace_id, ctx.allowed_collections, args.document_id, max_chars=args.max_chars
        )


class ListDocsInput(BaseModel):
    collection: str | None = None


class ListDocuments(Tool):
    name = "list_documents"
    description = "List documents available to this agent."
    connector = "knowledge"
    Input = ListDocsInput

    def run(self, args: ListDocsInput, ctx: ToolContext) -> dict[str, Any]:
        collections = [args.collection] if args.collection else ctx.allowed_collections
        if any(c not in ctx.allowed_collections for c in collections):
            raise ToolError("Collection is not approved for this agent")
        return {"documents": _knowledge(ctx).list_documents(ctx.workspace_id, collections)}

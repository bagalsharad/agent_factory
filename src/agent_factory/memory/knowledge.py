"""Workspace knowledge: document ingestion, chunking and retrieval with source attribution.

Retrieval uses a pluggable ``Retriever``. The default is a local BM25 lexical retriever over chunks stored
in the relational database — no vector database is required (ADR-0006). A vector retriever (pgvector,
Qdrant, local embeddings) can implement the same interface when a use case needs semantic recall.

Ingestion only reads from approved roots (AF_KNOWLEDGE_DIR and the bundled example corpora); arbitrary
filesystem paths are refused. All retrieved text is untrusted data.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import io
import json
import math
import re
import threading
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select

from ..core.config import Settings
from ..core.errors import NotFoundError, ValidationFailedError
from ..storage.artifacts import ArtifactStore
from ..storage.db import Database
from ..storage.models import Document, DocumentChunk, KnowledgeCollection

ALLOWED_SUFFIXES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".csv": "text/csv",
}
MAX_FILE_BYTES = 10_000_000
MAX_FILES_PER_INGEST = 500
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "which",
        "who",
        "will",
        "with",
        "how",
        "why",
        "when",
        "does",
        "do",
        "did",
        "can",
    ]
)


class SearchHit(BaseModel):
    document_id: str
    title: str
    source: str
    chunk_index: int
    score: float
    text: str


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    content_type = ALLOWED_SUFFIXES.get(suffix)
    if not content_type:
        raise ValidationFailedError(f"Unsupported document type '{suffix}'")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        text = data.decode("utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        elif suffix == ".json":
            with contextlib.suppress(json.JSONDecodeError):
                text = json.dumps(json.loads(text), indent=2)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text, content_type


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        while len(para) > size:  # hard-split very long paragraphs
            head, para = para[:size], para[size - overlap :]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if len(current) + len(para) + 2 > size and current:
            chunks.append(current)
            current = current[-overlap:] + "\n\n" + para if overlap else para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


class Retriever(ABC):
    @abstractmethod
    def search(self, workspace_id: str, collection_ids: list[str], query: str, top_k: int) -> list[tuple[str, float]]:
        """Return (chunk_id, score) pairs, best first."""

    def invalidate(self, collection_id: str) -> None:  # noqa: B027 - optional hook
        pass


class BM25Retriever(Retriever):
    def __init__(self, db: Database, k1: float = 1.5, b: float = 0.75) -> None:
        self.db, self.k1, self.b = db, k1, b
        self._cache: dict[str, tuple[list[tuple[str, Counter[str], int]], dict[str, int]]] = {}
        self._lock = threading.Lock()

    def invalidate(self, collection_id: str) -> None:
        with self._lock:
            self._cache.pop(collection_id, None)

    def _index(self, collection_id: str) -> tuple[list[tuple[str, Counter[str], int]], dict[str, int]]:
        with self._lock:
            if collection_id in self._cache:
                return self._cache[collection_id]
        with self.db.session() as session:
            rows = session.execute(
                select(DocumentChunk.id, DocumentChunk.text).where(DocumentChunk.collection_id == collection_id)
            ).all()
        docs = []
        df: Counter[str] = Counter()
        for chunk_id, text in rows:
            tokens = tokenize(text)
            counts = Counter(tokens)
            docs.append((chunk_id, counts, len(tokens)))
            df.update(counts.keys())
        index = (docs, dict(df))
        with self._lock:
            self._cache[collection_id] = index
        return index

    def search(self, workspace_id: str, collection_ids: list[str], query: str, top_k: int) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms:
            return []
        scored: list[tuple[str, float]] = []
        for cid in collection_ids:
            docs, df = self._index(cid)
            if not docs:
                continue
            n = len(docs)
            avgdl = sum(d[2] for d in docs) / n or 1
            for chunk_id, counts, length in docs:
                score = 0.0
                for term in terms:
                    tf = counts.get(term, 0)
                    if not tf:
                        continue
                    idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
                    score += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / avgdl))
                if score > 0:
                    scored.append((chunk_id, round(score, 4)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class KnowledgeService:
    def __init__(
        self, settings: Settings, db: Database, artifacts: ArtifactStore, retriever: Retriever | None = None
    ) -> None:
        self.settings = settings
        self.db = db
        self.artifacts = artifacts
        self.retriever = retriever or BM25Retriever(db)

    # ------------------------------------------------------------------ approved roots
    def approved_roots(self) -> list[Path]:
        roots = [self.settings.examples_dir.resolve()]
        if self.settings.knowledge_dir:
            roots.append(self.settings.knowledge_dir.resolve())
        return roots

    def _check_directory(self, directory: Path) -> Path:
        resolved = directory.resolve()
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.approved_roots()):
            raise ValidationFailedError(
                "Directory is outside the approved knowledge roots (AF_KNOWLEDGE_DIR or bundled examples)",
                details={"directory": str(directory)},
            )
        if not resolved.is_dir():
            raise NotFoundError(f"Directory not found: {directory}")
        return resolved

    # ------------------------------------------------------------------ collections
    def ensure_collection(
        self, workspace_id: str, name: str, description: str = "", source_dir: str | None = None
    ) -> KnowledgeCollection:
        with self.db.transaction() as session:
            coll = session.scalars(
                select(KnowledgeCollection).where(
                    KnowledgeCollection.workspace_id == workspace_id, KnowledgeCollection.name == name
                )
            ).first()
            if not coll:
                coll = KnowledgeCollection(
                    workspace_id=workspace_id, name=name, description=description, source_dir=source_dir
                )
                session.add(coll)
            elif source_dir:
                coll.source_dir = source_dir
            session.flush()
            session.expunge(coll)
            return coll

    def collections(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.db.session() as session:
            rows = session.scalars(select(KnowledgeCollection).where(KnowledgeCollection.workspace_id == workspace_id))
            out = []
            for c in rows:
                count = len(session.scalars(select(Document.id).where(Document.collection_id == c.id)).all())
                out.append(
                    {
                        "id": c.id,
                        "name": c.name,
                        "description": c.description,
                        "source_dir": c.source_dir,
                        "documents": count,
                    }
                )
            return out

    def _collection_ids(self, workspace_id: str, names: list[str]) -> list[str]:
        if not names:
            return []
        with self.db.session() as session:
            return list(
                session.scalars(
                    select(KnowledgeCollection.id).where(
                        KnowledgeCollection.workspace_id == workspace_id, KnowledgeCollection.name.in_(names)
                    )
                ).all()
            )

    # ------------------------------------------------------------------ ingestion
    def ingest_bytes(
        self, workspace_id: str, collection: str, filename: str, data: bytes, source_path: str | None = None
    ) -> dict[str, Any]:
        if len(data) > MAX_FILE_BYTES:
            raise ValidationFailedError("Document exceeds the 10 MB limit")
        text, content_type = extract_text(filename, data)
        coll = self.ensure_collection(workspace_id, collection)
        digest = hashlib.sha256(data).hexdigest()
        source = source_path or filename
        with self.db.transaction() as session:
            existing = session.scalars(
                select(Document).where(Document.collection_id == coll.id, Document.source_path == source)
            ).first()
            if existing and existing.content_hash == digest:
                return {"document_id": existing.id, "status": "unchanged", "source": source}
            if existing:
                session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == existing.id))
                doc = existing
                old_uri = doc.artifact_uri
            else:
                doc = Document(
                    workspace_id=workspace_id,
                    collection_id=coll.id,
                    source_path=source,
                    title=Path(filename).stem.replace("_", " ").replace("-", " ").title(),
                    content_type=content_type,
                    content_hash=digest,
                )
                session.add(doc)
                old_uri = None
            uri, _ = self.artifacts.put(workspace_id, data, suffix=Path(filename).suffix)
            doc.artifact_uri, doc.content_hash, doc.char_count, doc.status = uri, digest, len(text), "indexed"
            session.flush()
            for i, chunk in enumerate(chunk_text(text)):
                session.add(
                    DocumentChunk(
                        document_id=doc.id,
                        workspace_id=workspace_id,
                        collection_id=coll.id,
                        chunk_index=i,
                        text=chunk,
                        token_count=len(tokenize(chunk)),
                        metadata_={"source": source},
                    )
                )
            doc_id = doc.id
        if old_uri:
            self.artifacts.delete(old_uri)
        self.retriever.invalidate(coll.id)
        return {"document_id": doc_id, "status": "updated" if existing else "indexed", "source": source}

    def ingest_directory(self, workspace_id: str, collection: str, directory: Path) -> dict[str, Any]:
        root = self._check_directory(directory)
        self.ensure_collection(workspace_id, collection, source_dir=str(root))
        results = []
        files = [
            p
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in ALLOWED_SUFFIXES
        ]
        for path in files[:MAX_FILES_PER_INGEST]:
            try:
                results.append(
                    self.ingest_bytes(
                        workspace_id,
                        collection,
                        path.name,
                        path.read_bytes(),
                        source_path=path.relative_to(root).as_posix(),
                    )
                )
            except ValidationFailedError as exc:
                results.append({"source": path.name, "status": "skipped", "reason": exc.message})
        return {
            "collection": collection,
            "files": len(results),
            "results": results,
            "truncated": len(files) > MAX_FILES_PER_INGEST,
        }

    def reindex_collection(self, workspace_id: str, collection: str) -> dict[str, Any]:
        with self.db.session() as session:
            coll = session.scalars(
                select(KnowledgeCollection).where(
                    KnowledgeCollection.workspace_id == workspace_id, KnowledgeCollection.name == collection
                )
            ).first()
        if not coll or not coll.source_dir:
            raise NotFoundError(f"Collection '{collection}' has no source directory to re-index")
        return self.ingest_directory(workspace_id, collection, Path(coll.source_dir))

    def delete_document(self, workspace_id: str, document_id: str) -> None:
        with self.db.transaction() as session:
            doc = session.get(Document, document_id)
            if not doc or doc.workspace_id != workspace_id:
                raise NotFoundError("Document not found")
            uri, coll_id = doc.artifact_uri, doc.collection_id
            session.delete(doc)
        if uri:
            self.artifacts.delete(uri)
        self.retriever.invalidate(coll_id)

    # ------------------------------------------------------------------ retrieval
    def search(
        self, workspace_id: str, collections: list[str], query: str, top_k: int = 5, min_score: float = 0.0
    ) -> list[SearchHit]:
        ids = self._collection_ids(workspace_id, collections)
        ranked = [r for r in self.retriever.search(workspace_id, ids, query, top_k) if r[1] >= min_score]
        if not ranked:
            return []
        with self.db.session() as session:
            rows = session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(DocumentChunk.id.in_([r[0] for r in ranked]), DocumentChunk.workspace_id == workspace_id)
            ).all()
        by_id = {chunk.id: (chunk, doc) for chunk, doc in rows}
        return [
            SearchHit(
                document_id=doc.id,
                title=doc.title,
                source=doc.source_path,
                chunk_index=chunk.chunk_index,
                score=score,
                text=chunk.text,
            )
            for cid, score in ranked
            if cid in by_id
            for chunk, doc in [by_id[cid]]
        ]

    def list_documents(self, workspace_id: str, collections: list[str]) -> list[dict[str, Any]]:
        ids = self._collection_ids(workspace_id, collections)
        with self.db.session() as session:
            docs = session.scalars(select(Document).where(Document.collection_id.in_(ids))).all() if ids else []
            return [
                {"document_id": d.id, "title": d.title, "source": d.source_path, "chars": d.char_count} for d in docs
            ]

    def read_document(
        self, workspace_id: str, collections: list[str], document_id: str, max_chars: int = 8000
    ) -> dict[str, Any]:
        ids = self._collection_ids(workspace_id, collections)
        with self.db.session() as session:
            doc = session.get(Document, document_id)
            if not doc or doc.workspace_id != workspace_id or doc.collection_id not in ids:
                raise NotFoundError("Document not found or not approved for this agent")
            title, source, uri, doc_id = doc.title, doc.source_path, doc.artifact_uri, doc.id
        if not uri:
            raise NotFoundError("Document content is unavailable")
        text, _ = extract_text(source, self.artifacts.get(uri))
        return {
            "document_id": doc_id,
            "title": title,
            "source": source,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

"""Pluggable artifact storage. Binary content lives here, never in relational tables."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from ..core.errors import NotFoundError, ValidationFailedError
from ..core.ids import new_id


class ArtifactStore(ABC):
    scheme: str

    @abstractmethod
    def put(self, workspace_id: str, data: bytes, *, suffix: str = "") -> tuple[str, str]:
        """Store bytes, returning (uri, sha256)."""

    @abstractmethod
    def get(self, uri: str) -> bytes: ...

    @abstractmethod
    def delete(self, uri: str) -> None: ...


class LocalArtifactStore(ArtifactStore):
    """Filesystem storage rooted at a single directory; URIs cannot escape that root."""

    scheme = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, uri: str) -> Path:
        if not uri.startswith(f"{self.scheme}://"):
            raise ValidationFailedError(f"Unsupported artifact URI: {uri}")
        relative = uri[len(self.scheme) + 3 :]
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValidationFailedError("Artifact URI escapes the storage root")
        return path

    def put(self, workspace_id: str, data: bytes, *, suffix: str = "") -> tuple[str, str]:
        digest = hashlib.sha256(data).hexdigest()
        safe_suffix = "".join(ch for ch in suffix if ch.isalnum() or ch == ".")[:16]
        relative = f"{workspace_id}/{digest[:2]}/{new_id()}{safe_suffix}"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self.scheme}://{relative}", digest

    def get(self, uri: str) -> bytes:
        path = self._path(uri)
        if not path.exists():
            raise NotFoundError(f"Artifact not found: {uri}")
        return path.read_bytes()

    def delete(self, uri: str) -> None:
        path = self._path(uri)
        path.unlink(missing_ok=True)

"""Secret storage adapters.

Secrets are referenced by name (``secret://<name>``) in configuration — never embedded in agent specs and
never placed in model context. Values are returned only to connector code that needs them.

Backends:
* ``env``     — ``AF_SECRET_<NAME>`` environment variables (local development only)
* ``keyring`` — OS keychain (macOS Keychain) via the optional ``keyring`` extra
* ``file``    — Fernet-encrypted local file; key from AF_SECRETS_KEY or a 0600 key file in the data dir
Production deployments should implement ``SecretStore`` for their secret manager (Vault, AWS/GCP/Azure).
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..core.errors import NotFoundError, ValidationFailedError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _check(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValidationFailedError("Secret names must match ^[a-z][a-z0-9_]{1,63}$")


class SecretStore(ABC):
    backend: str

    @abstractmethod
    def get(self, name: str) -> str: ...

    @abstractmethod
    def set(self, name: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    def resolve(self, ref: str) -> str:
        if ref.startswith("secret://"):
            return self.get(ref[len("secret://") :])
        return ref


class EnvSecretStore(SecretStore):
    backend = "env"

    def get(self, name: str) -> str:
        _check(name)
        value = os.environ.get(f"AF_SECRET_{name.upper()}")
        if value is None:
            raise NotFoundError(f"Secret '{name}' is not configured")
        return value

    def set(self, name: str, value: str) -> None:
        raise ValidationFailedError("The env secret backend is read-only; set AF_SECRET_<NAME> in the environment")

    def delete(self, name: str) -> None:
        raise ValidationFailedError("The env secret backend is read-only")


class KeyringSecretStore(SecretStore):
    backend = "keyring"
    SERVICE = "agent-factory"

    def __init__(self) -> None:
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover
            raise ValidationFailedError("Install the 'keyring' extra to use the keyring secret backend") from exc
        self._keyring = keyring

    def get(self, name: str) -> str:
        _check(name)
        value = self._keyring.get_password(self.SERVICE, name)
        if value is None:
            raise NotFoundError(f"Secret '{name}' is not configured")
        return str(value)

    def set(self, name: str, value: str) -> None:
        _check(name)
        self._keyring.set_password(self.SERVICE, name, value)

    def delete(self, name: str) -> None:
        _check(name)
        self._keyring.delete_password(self.SERVICE, name)


class FileSecretStore(SecretStore):
    backend = "file"

    def __init__(self, data_dir: Path) -> None:
        from cryptography.fernet import Fernet

        self.path = data_dir / "secrets.enc"
        key = os.environ.get("AF_SECRETS_KEY")
        if not key:
            key_file = data_dir / "secrets.key"
            if not key_file.exists():
                data_dir.mkdir(parents=True, exist_ok=True)
                key_file.write_bytes(Fernet.generate_key())
                key_file.chmod(0o600)
            key = key_file.read_text().strip()
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return dict(json.loads(self._fernet.decrypt(self.path.read_bytes())))

    def _save(self, data: dict[str, str]) -> None:
        self.path.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        self.path.chmod(0o600)

    def get(self, name: str) -> str:
        _check(name)
        data = self._load()
        if name not in data:
            raise NotFoundError(f"Secret '{name}' is not configured")
        return data[name]

    def set(self, name: str, value: str) -> None:
        _check(name)
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> None:
        _check(name)
        data = self._load()
        data.pop(name, None)
        self._save(data)


class VaultSecretStore(SecretStore):
    """HashiCorp Vault KV v2. The token comes from VAULT_TOKEN in the process environment and is never stored."""

    backend = "vault"

    def __init__(
        self,
        addr: str,
        mount: str = "secret",
        prefix: str = "agent-factory",
        *,
        token: str | None = None,
        namespace: str | None = None,
        transport: Any = None,
    ) -> None:
        import httpx

        token = token or os.environ.get("VAULT_TOKEN")
        if not token:
            raise ValidationFailedError("secrets_backend=vault needs VAULT_TOKEN in the environment")
        headers = {"X-Vault-Token": token}
        if namespace or os.environ.get("VAULT_NAMESPACE"):
            headers["X-Vault-Namespace"] = namespace or os.environ["VAULT_NAMESPACE"]
        self._client = httpx.Client(base_url=addr.rstrip("/"), headers=headers, timeout=10, transport=transport)
        self._mount, self._prefix = mount.strip("/"), prefix.strip("/")

    def _path(self, kind: str, name: str) -> str:
        return f"/v1/{self._mount}/{kind}/{self._prefix}/{name}"

    def _check_response(self, response: Any, name: str) -> None:
        if response.status_code == 404:
            raise NotFoundError(f"Secret '{name}' is not configured")
        if response.status_code >= 400:  # never echo the body: it may contain request data
            raise ValidationFailedError(f"Vault returned HTTP {response.status_code} for secret '{name}'")

    def get(self, name: str) -> str:
        _check(name)
        response = self._client.get(self._path("data", name))
        self._check_response(response, name)
        value = ((response.json().get("data") or {}).get("data") or {}).get("value")
        if not isinstance(value, str):
            raise NotFoundError(f"Secret '{name}' has no 'value' field")
        return value

    def set(self, name: str, value: str) -> None:
        _check(name)
        self._check_response(self._client.post(self._path("data", name), json={"data": {"value": value}}), name)

    def delete(self, name: str) -> None:
        _check(name)
        response = self._client.delete(self._path("metadata", name))
        if response.status_code != 404:
            self._check_response(response, name)


class AuditedSecretStore(SecretStore):
    """Records who-read-what (names only, never values). Repeated reads of one secret are summarized."""

    def __init__(self, inner: SecretStore, audit: Any, *, window_seconds: float = 300.0) -> None:
        import threading
        import time

        self.inner = inner
        self.backend = inner.backend
        self.audit = audit
        self.window = window_seconds
        self._clock = time.monotonic
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> str:
        value = self.inner.get(name)
        with self._lock:
            now = self._clock()
            due = now - self._last.get(name, -1e18) >= self.window
            if due:
                self._last[name] = now
        if due:
            self.audit.record("secret.access", "secret", name, details={"backend": self.backend})
        return value

    def set(self, name: str, value: str) -> None:
        self.inner.set(name, value)

    def delete(self, name: str) -> None:
        self.inner.delete(name)


def build_secret_store(settings: Settings) -> SecretStore:
    if settings.secrets_backend == "keyring":
        return KeyringSecretStore()
    if settings.secrets_backend == "file":
        return FileSecretStore(settings.data_dir)
    if settings.secrets_backend == "vault":
        if not settings.vault_addr:
            raise ValidationFailedError("secrets_backend=vault needs AF_VAULT_ADDR")
        return VaultSecretStore(settings.vault_addr, settings.vault_mount, settings.vault_prefix)
    return EnvSecretStore()

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


def build_secret_store(settings: Settings) -> SecretStore:
    if settings.secrets_backend == "keyring":
        return KeyringSecretStore()
    if settings.secrets_backend == "file":
        return FileSecretStore(settings.data_dir)
    return EnvSecretStore()

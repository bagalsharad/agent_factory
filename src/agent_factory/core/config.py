"""Typed platform configuration.

Settings are read from environment variables prefixed with ``AF_`` (and an optional ``.env`` file).
Two profiles exist:

* ``local`` (default) — single developer on a Mac. Loopback binding only, implicit local administrator,
  SQLite, embedded worker, content capture enabled for the local execution monitor.
* ``production`` — authentication required, PostgreSQL expected, content capture disabled by default,
  debug features refused.

Secrets are never stored in this object as plain values that get logged: fields holding secrets use
``SecretStr`` and are redacted from ``safe_dict()``.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _bundled_root() -> Path:
    """Locate templates/ and examples/ for both source checkouts and installed wheels."""
    if (PROJECT_ROOT / "templates").is_dir():
        return PROJECT_ROOT
    return Path(__file__).resolve().parents[1] / "_bundled"


class Profile(StrEnum):
    LOCAL = "local"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    LOCAL = "local"  # implicit local administrator; loopback only
    API_KEY = "api_key"
    OIDC = "oidc"


class WorkerMode(StrEnum):
    EMBEDDED = "embedded"  # worker threads inside the API process
    EXTERNAL = "external"  # separate `agent-factory worker` process(es)


class SandboxBackend(StrEnum):
    NONE = "none"  # code-execution tools are refused
    DOCKER = "docker"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AF_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    profile: Profile = Profile.LOCAL
    host: str = "127.0.0.1"
    port: int = 8787
    data_dir: Path = PROJECT_ROOT / "data"
    database_url: str | None = None  # defaults to sqlite in data_dir
    log_level: str = "INFO"
    log_json: bool = True

    # --- Authentication / authorization ---
    auth_mode: AuthMode = AuthMode.LOCAL
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_roles_claim: str = "af_roles"
    oidc_default_role: str = "viewer"
    allow_self_approval: bool = True  # local single-user mode; set false for separation of duties
    # NoDecode: list settings accept comma-separated values ("a,b") as well as JSON ('["a","b"]').
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )

    # --- Model providers ---
    default_provider: str = "mock"
    default_model: str = "mock-default"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_enabled: bool = True
    openai_compat_enabled: bool = False
    openai_compat_base_url: str = "http://127.0.0.1:1234/v1"
    openai_compat_api_key: SecretStr | None = None
    cloud_providers_enabled: bool = False  # explicit opt-in before any data leaves the machine
    anthropic_api_key: SecretStr | None = None
    anthropic_fallbacks: Literal["default", "off"] = "default"  # server-side refusal fallbacks
    model_timeout_seconds: float = 120.0
    model_max_retries: int = 2

    # --- Execution ---
    worker_mode: WorkerMode = WorkerMode.EMBEDDED
    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 0.5
    worker_heartbeat_stale_seconds: float = 60.0
    default_max_steps: int = 20
    default_timeout_seconds: int = 300
    approval_expiry_seconds: int = 24 * 3600
    max_request_bytes: int = 1_000_000
    execution_rate_limit_per_minute: int = 60

    # --- Tools / connectors ---
    sample_data_dir: Path = Field(default_factory=lambda: _bundled_root() / "examples")
    knowledge_dir: Path | None = None  # designated document ingestion directory
    sandbox_dir: Path | None = None  # simulated side effects are written here
    network_allowlist: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )  # global REST connector host allowlist
    allow_private_network: bool = False  # SSRF guard: block RFC1918/loopback destinations
    sandbox_backend: SandboxBackend = SandboxBackend.NONE
    sandbox_image: str = "python:3.12-slim"
    disabled_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)
    mcp_config_file: Path | None = None

    # --- Email connector (read-only IMAP + send-to-self SMTP) ---
    email_address: str | None = None  # the mailbox owner; also the ONLY recipient digests can be sent to
    email_imap_host: str = "imap.gmail.com"
    email_imap_port: int = 993
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 465
    email_password_secret: str = "email_app_password"  # noqa: S105 - secret *name*, never the value
    email_fixture_dir: Path = Field(
        default_factory=lambda: _bundled_root() / "examples" / "email-digest" / "sample_mailbox"
    )
    digest_dir: Path = Field(default_factory=lambda: Path.home() / "Documents" / "EmailDigests")

    # --- Video transcripts ---
    media_dirs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["~/Movies", "~/Downloads", "~/Desktop", "~/Documents"]
    )  # only local media inside these folders can be transcribed
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    video_notes_dir: Path = Field(default_factory=lambda: Path.home() / "VideoNotes")
    video_fixture_dir: Path = Field(
        default_factory=lambda: _bundled_root() / "examples" / "video-summarizer" / "sample_transcripts"
    )

    # --- Observability / data protection ---
    capture_content: bool | None = None  # None → profile default
    telemetry_exporter: Literal["none", "console", "otlp"] = "none"
    otlp_endpoint: str | None = None
    retention_days: int = 30

    # --- Secrets ---
    secrets_backend: Literal["env", "keyring", "file"] = "env"

    @field_validator("cors_origins", "network_allowlist", "disabled_tools", "media_dirs", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                import json

                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _apply_profile_rules(self) -> Settings:
        if self.capture_content is None:
            self.capture_content = self.profile == Profile.LOCAL
        if self.knowledge_dir is None:
            self.knowledge_dir = self.data_dir / "knowledge"
        if self.sandbox_dir is None:
            self.sandbox_dir = self.data_dir / "sandbox"
        return self

    # ------------------------------------------------------------------
    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'agent_factory.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def templates_dir(self) -> Path:
        return _bundled_root() / "templates"

    @property
    def examples_dir(self) -> Path:
        return _bundled_root() / "examples"

    def host_is_loopback(self) -> bool:
        if self.host in {"localhost"}:
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    def startup_problems(self) -> list[str]:
        """Configuration combinations that must refuse to start."""
        problems: list[str] = []
        if self.auth_mode == AuthMode.LOCAL and not self.host_is_loopback():
            problems.append(
                f"AF_AUTH_MODE=local provides no authentication and must bind to a loopback address, "
                f"but AF_HOST={self.host}. Use AF_AUTH_MODE=api_key or oidc for network exposure."
            )
        if self.profile == Profile.PRODUCTION:
            if self.auth_mode == AuthMode.LOCAL:
                problems.append("The production profile requires AF_AUTH_MODE=api_key or oidc.")
            if self.allow_self_approval:
                problems.append("The production profile requires AF_ALLOW_SELF_APPROVAL=false.")
        if self.auth_mode == AuthMode.OIDC and not (self.oidc_issuer and self.oidc_jwks_url):
            problems.append("AF_AUTH_MODE=oidc requires AF_OIDC_ISSUER and AF_OIDC_JWKS_URL.")
        return problems

    def safe_dict(self) -> dict[str, Any]:
        """Configuration suitable for display: secrets are reported only as configured/not configured."""
        data = self.model_dump(mode="json")
        for key, value in list(data.items()):
            field_value = getattr(self, key)
            if isinstance(field_value, SecretStr) or key.endswith("_api_key"):
                data[key] = {"configured": bool(value)}
        if not self.is_sqlite:
            data["database_url"] = "postgresql://***"
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()

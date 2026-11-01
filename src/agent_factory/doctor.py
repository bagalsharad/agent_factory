"""Preflight diagnostics and hardware-appropriate model recommendations. Never downloads models."""

from __future__ import annotations

import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any

import httpx
import psutil

from .core.config import Settings


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | info
    detail: str
    hint: str = ""


def _run(cmd: list[str], timeout: float = 5) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
        return proc.returncode, (proc.stdout or proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def recommend_models(memory_gb: float) -> list[dict[str, str]]:
    """Conservative picks that leave headroom for the OS and the platform. Tool-calling capable families."""
    if memory_gb < 8:
        picks = [("qwen3:1.7b", "smallest practical tool-calling model"), ("llama3.2:3b", "fast, limited reasoning")]
    elif memory_gb < 16:
        picks = [("qwen3:4b", "good balance for 8-16 GB"), ("llama3.2:3b", "fastest option")]
    elif memory_gb < 32:
        picks = [
            ("qwen3:8b", "recommended default for 16-32 GB"),
            ("llama3.1:8b", "solid tool calling"),
            ("qwen3:4b", "faster, lower quality"),
        ]
    elif memory_gb < 64:
        picks = [("qwen3:14b", "stronger reasoning"), ("gpt-oss:20b", "strong tool use"), ("qwen3:8b", "faster")]
    else:
        picks = [
            ("qwen3:32b", "high quality local reasoning"),
            ("gpt-oss:20b", "strong tool use"),
            ("llama3.3:70b", "large; quantized, slow"),
        ]
    return [{"model": m, "why": why, "install": f"ollama pull {m}"} for m, why in picks]


def run_checks(settings: Settings) -> dict[str, Any]:
    checks: list[Check] = []
    system = platform.system()
    if system == "Darwin":
        version = platform.mac_ver()[0]
        major = int(version.split(".")[0]) if version else 0
        checks.append(
            Check(
                "macOS",
                "ok" if major >= 13 else "warn",
                f"macOS {version}",
                "" if major >= 13 else "macOS 13+ is recommended",
            )
        )
    else:
        checks.append(Check("operating system", "info", f"{system} {platform.release()}"))
    arch = platform.machine()
    checks.append(
        Check("cpu architecture", "ok", arch + (" (Apple Silicon)" if arch == "arm64" and system == "Darwin" else ""))
    )
    memory_gb = psutil.virtual_memory().total / 1024**3
    checks.append(
        Check(
            "memory",
            "ok" if memory_gb >= 8 else "warn",
            f"{memory_gb:.1f} GB",
            "" if memory_gb >= 8 else "Local models need ≥8 GB; use the mock provider or a remote endpoint",
        )
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(settings.data_dir).free / 1024**3
    checks.append(
        Check(
            "disk space",
            "ok" if free_gb >= 20 else "warn",
            f"{free_gb:.0f} GB free at {settings.data_dir}",
            "" if free_gb >= 20 else "Models need 2-40 GB each",
        )
    )
    py_ok = sys.version_info >= (3, 11)
    checks.append(
        Check(
            "python",
            "ok" if py_ok else "fail",
            platform.python_version(),
            "" if py_ok else "Python 3.11+ required: `uv python install 3.12`",
        )
    )
    uv = shutil.which("uv")
    checks.append(
        Check(
            "uv",
            "ok" if uv else "warn",
            _run([uv, "--version"])[1] if uv else "not found",
            "" if uv else "Install: https://docs.astral.sh/uv/",
        )
    )
    docker = shutil.which("docker")
    if docker:
        code, out = _run([docker, "info", "--format", "{{.ServerVersion}}"])
        checks.append(
            Check(
                "docker",
                "ok" if code == 0 else "warn",
                f"daemon {out}" if code == 0 else "CLI present, daemon not running",
                "" if code == 0 else "Start Docker Desktop for container packaging or the code sandbox",
            )
        )
    else:
        checks.append(Check("docker", "info", "not installed", "Optional: needed for Docker Compose and sandboxing"))
    ollama_bin = shutil.which("ollama")
    models: list[str] = []
    try:
        with httpx.Client(timeout=3) as client:
            version = client.get(f"{settings.ollama_base_url}/api/version").json().get("version")
            models = [m["name"] for m in client.get(f"{settings.ollama_base_url}/api/tags").json().get("models", [])]
        checks.append(Check("ollama", "ok", f"server {version} at {settings.ollama_base_url}; {len(models)} model(s)"))
    except (httpx.HTTPError, ValueError):
        checks.append(
            Check(
                "ollama",
                "warn",
                "installed but not running" if ollama_bin else "not installed",
                "Run `ollama serve`"
                if ollama_bin
                else "Install from https://ollama.com (optional; the mock provider works without it)",
            )
        )
    if settings.default_provider == "ollama":
        installed = settings.default_model in models or f"{settings.default_model}:latest" in models
        checks.append(
            Check(
                "configured model",
                "ok" if installed else "warn",
                settings.default_model,
                "" if installed else f"Install explicitly: `ollama pull {settings.default_model}`",
            )
        )
    else:
        checks.append(Check("configured model", "info", f"{settings.default_provider}/{settings.default_model}"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((settings.host if settings.host != "localhost" else "127.0.0.1", settings.port))
            checks.append(Check("port", "ok", f"{settings.host}:{settings.port} available"))
        except OSError:
            checks.append(
                Check(
                    "port",
                    "warn",
                    f"{settings.host}:{settings.port} in use",
                    "Another process (maybe Agent Factory) is listening; set AF_PORT to change",
                )
            )
    for problem in settings.startup_problems():
        checks.append(Check("configuration", "fail", problem))
    return {
        "checks": [asdict(c) for c in checks],
        "installed_models": models,
        "recommendations": recommend_models(memory_gb),
        "ok": not any(c.status == "fail" for c in checks),
    }

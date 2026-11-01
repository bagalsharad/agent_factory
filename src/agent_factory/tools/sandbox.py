"""Isolated execution for tools that run code.

There is deliberately no "host" backend. When no sandbox is configured or available, code-execution
tools fail with ``SandboxUnavailableError`` — the absence of a sandbox never degrades into unrestricted
host execution.

The Docker backend runs each job in a fresh container with: no network, read-only root filesystem,
a size-limited tmpfs, dropped capabilities, no-new-privileges, non-root user, memory/CPU/PID limits, no
host mounts other than the job input, no Docker socket, and an explicit (empty) environment.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..core.config import SandboxBackend, Settings
from ..core.errors import SandboxUnavailableError, ToolError


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


class SandboxRunner(ABC):
    name: str

    @abstractmethod
    def available(self) -> tuple[bool, str]: ...

    @abstractmethod
    def run_python(self, code: str, *, timeout_seconds: float) -> SandboxResult: ...


class UnavailableSandbox(SandboxRunner):
    name = "none"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self.reason

    def run_python(self, code: str, *, timeout_seconds: float) -> SandboxResult:
        raise SandboxUnavailableError(f"Code execution refused: {self.reason}")


class DockerSandbox(SandboxRunner):
    name = "docker"

    def __init__(self, image: str, *, memory: str = "256m", cpus: str = "0.5", pids: int = 64) -> None:
        self.image, self.memory, self.cpus, self.pids = image, memory, cpus, pids

    def available(self) -> tuple[bool, str]:
        docker = shutil.which("docker")
        if not docker:
            return False, "docker CLI not found"
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argument list, no user input
                [docker, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"docker daemon not reachable: {exc}"
        if proc.returncode != 0:
            return False, "docker daemon not reachable"
        return True, f"docker {proc.stdout.strip()}"

    def run_python(self, code: str, *, timeout_seconds: float) -> SandboxResult:
        ok, reason = self.available()
        if not ok:
            raise SandboxUnavailableError(f"Code execution refused: {reason}")
        with tempfile.TemporaryDirectory(prefix="af-sandbox-") as tmp:
            job = Path(tmp) / "job.py"
            job.write_text(code, encoding="utf-8")
            cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,size=16m,noexec",  # noqa: S108 - tmpfs inside the container, not the host
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "65534:65534",
                "--memory",
                self.memory,
                "--cpus",
                self.cpus,
                "--pids-limit",
                str(self.pids),
                "--env-file",
                "/dev/null",
                "-v",
                f"{job}:/job/job.py:ro",
                "-w",
                "/job",
                self.image,
                "python",
                "-I",
                "/job/job.py",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False)  # noqa: S603
            except subprocess.TimeoutExpired:
                return SandboxResult(exit_code=-1, stdout="", stderr="timeout", timed_out=True)
            except OSError as exc:
                raise ToolError(f"Sandbox failed to start: {exc}") from exc
            return SandboxResult(proc.returncode, proc.stdout[-20_000:], proc.stderr[-20_000:], False)


def build_sandbox(settings: Settings) -> SandboxRunner:
    if settings.sandbox_backend == SandboxBackend.DOCKER:
        return DockerSandbox(settings.sandbox_image)
    return UnavailableSandbox("no sandbox backend configured (AF_SANDBOX_BACKEND=none)")

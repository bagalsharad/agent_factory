"""Read-only code artifact connector for review agents. Reviewed code is never executed."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...core.errors import ToolError
from ..base import Tool, ToolContext, safe_join

ARTIFACT_PATTERN = r"^[a-z0-9][a-z0-9_\-]{0,63}$"
MAX_FILE_BYTES = 200_000
TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rb",
    ".sql",
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".sh",
    ".txt",
}


def artifacts_root(ctx: ToolContext) -> Path:
    return ctx.sample_root / "software-review" / "samples"


def _artifact_dir(ctx: ToolContext, artifact: str) -> Path:
    directory = safe_join(artifacts_root(ctx), artifact)
    if not directory.is_dir():
        raise ToolError(f"Unknown code artifact '{artifact}'")
    return directory


class ArtifactInput(BaseModel):
    artifact: str = Field(pattern=ARTIFACT_PATTERN, description="Name of a registered code artifact.")


class ListCodeFiles(Tool):
    name = "list_code_files"
    description = "List text files in a supplied code artifact (read-only)."
    connector = "code_artifacts"
    Input = ArtifactInput

    def run(self, args: ArtifactInput, ctx: ToolContext) -> dict[str, Any]:
        root = _artifact_dir(ctx, args.artifact)
        files = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in TEXT_SUFFIXES:
                files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size})
        return {"artifact": args.artifact, "files": files[:500]}


class ReadFileInput(ArtifactInput):
    path: str = Field(min_length=1, max_length=400, description="File path relative to the artifact root.")


class ReadCodeFile(Tool):
    name = "read_code_file"
    description = "Read one text file from a supplied code artifact (read-only, size-limited)."
    connector = "code_artifacts"
    Input = ReadFileInput

    def run(self, args: ReadFileInput, ctx: ToolContext) -> dict[str, Any]:
        root = _artifact_dir(ctx, args.artifact)
        path = safe_join(root, args.path)
        if not path.is_file() or path.is_symlink():
            raise ToolError(f"File not found: {args.path}")
        if path.suffix not in TEXT_SUFFIXES:
            raise ToolError("Only text source files can be read")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ToolError("File exceeds the size limit for review")
        text = path.read_text(encoding="utf-8", errors="replace")
        numbered = "\n".join(f"{i:4d}| {line}" for i, line in enumerate(text.splitlines(), 1))
        return {"artifact": args.artifact, "path": args.path, "content": numbered}


SECURITY_RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    (
        "SEC001",
        "high",
        "Possible SQL injection: query built with string formatting",
        re.compile(r"""(execute|executemany|raw)\s*\(\s*(f["']|["'].*%s.*["']\s*%|["'].*\+\s*\w)""", re.I),
    ),
    ("SEC002", "high", "Use of eval/exec on dynamic input", re.compile(r"\b(eval|exec)\s*\(")),
    ("SEC003", "high", "Shell command execution with shell=True", re.compile(r"shell\s*=\s*True")),
    (
        "SEC004",
        "critical",
        "Hard-coded credential",
        re.compile(r"""(?i)(password|secret|api_key|token)\s*=\s*["'][^"']{4,}["']"""),
    ),
    (
        "SEC005",
        "medium",
        "Insecure deserialization (pickle/yaml.load)",
        re.compile(r"pickle\.loads?\(|yaml\.load\((?!.*Loader=yaml\.SafeLoader)"),
    ),
    ("SEC006", "medium", "TLS verification disabled", re.compile(r"verify\s*=\s*False")),
    ("SEC007", "medium", "Weak hash algorithm", re.compile(r"hashlib\.(md5|sha1)\(")),
    ("SEC008", "low", "Broad exception swallowed", re.compile(r"except\s*(Exception)?\s*:\s*(pass)?\s*$")),
    ("QLT001", "low", "TODO/FIXME left in code", re.compile(r"#\s*(TODO|FIXME|XXX)")),
    ("QLT002", "low", "Debug print statement", re.compile(r"^\s*print\(")),
]


class ScanInput(ArtifactInput):
    path: str | None = Field(None, max_length=400, description="Optional single file; default scans all files.")


class ScanCodePatterns(Tool):
    name = "scan_code_patterns"
    description = "Static pattern scan (regex rules for security and quality smells). Never executes code."
    connector = "code_artifacts"
    Input = ScanInput

    def run(self, args: ScanInput, ctx: ToolContext) -> dict[str, Any]:
        root = _artifact_dir(ctx, args.artifact)
        targets = (
            [safe_join(root, args.path)]
            if args.path
            else [
                p for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink() and p.suffix in TEXT_SUFFIXES
            ]
        )
        findings = []
        for path in targets:
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            rel = path.relative_to(root.resolve()).as_posix()
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for rule_id, severity, message, pattern in SECURITY_RULES:
                    if pattern.search(line):
                        findings.append(
                            {
                                "rule": rule_id,
                                "severity": severity,
                                "message": message,
                                "file": rel,
                                "line": lineno,
                                "snippet": line.strip()[:200],
                            }
                        )
        return {"artifact": args.artifact, "findings": findings[:300], "files_scanned": len(targets)}

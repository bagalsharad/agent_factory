"""Video transcripts and notes.

``get_video_transcript`` accepts:

* a YouTube URL or video id — uses the video's existing captions (manual preferred, then auto-generated, then a
  translation) via ``youtube-transcript-api`` and the official oEmbed endpoint for the title. Nothing is
  downloaded; videos without captions are reported, not fetched.
* a local video/audio file inside an approved folder (``AF_MEDIA_DIRS``) — transcribed on this Mac with Whisper
  (MLX) in a separate process that is killed on timeout. The Whisper model must be installed explicitly with
  ``agent-factory media setup``; it is never downloaded implicitly.
* ``fixture:<name>`` — a bundled sample transcript (tests, demos, evaluations).

The transcript is returned as timestamped segments, a Markdown rendering and model-sized chunks for map-reduce
summarization. Transcript text is untrusted third-party content.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import BaseModel, Field

from ...core.config import Settings
from ...core.errors import ToolError
from ...spec.models import RiskLevel
from ..base import AuditConfig, SideEffect, Tool, ToolContext

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MEDIA_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
MAX_MEDIA_SECONDS = 4 * 3600
CHUNK_CHARS = 6000
MAX_CHUNKS = 24


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_ts(value: str) -> int | None:
    parts = value.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2] if len(numbers) == 3 else numbers[0] * 60 + numbers[1]


def youtube_id(source: str) -> str | None:
    source = source.strip()
    if VIDEO_ID.match(source):
        return source
    parts = urlsplit(source if "://" in source else f"https://{source}")
    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
    elif parts.path == "/watch":
        candidate = parse_qs(parts.query).get("v", [""])[0]
    else:
        match = re.match(r"^/(?:shorts|embed|live|v)/([^/?#]+)", parts.path)
        candidate = match.group(1) if match else ""
    return candidate if VIDEO_ID.match(candidate) else None


def build_chunks(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group segments into model-sized chunks (at most MAX_CHUNKS) that keep their start times."""
    total = sum(len(s["text"]) + 1 for s in segments)
    target = max(CHUNK_CHARS, math.ceil(total / MAX_CHUNKS))
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for seg in segments:
        current.append(seg)
        size += len(seg["text"]) + 1
        if size >= target:
            chunks.append(current)
            current, size = [], 0
    if current:
        chunks.append(current)
    out = []
    for n, group in enumerate(chunks, 1):
        out.append(
            {
                "number": n,
                "start_seconds": int(group[0]["start"]),
                "end_seconds": int(group[-1]["end"]),
                "start_label": fmt_ts(group[0]["start"]),
                "label": f"segment {n} @ {fmt_ts(group[0]['start'])}",
                "text": " ".join(s["text"] for s in group),
            }
        )
    return out


def transcript_markdown(segments: list[dict[str, Any]], paragraph_seconds: int = 45) -> str:
    lines: list[str] = []
    buffer: list[str] = []
    para_start: float | None = None
    for seg in segments:
        if para_start is None:
            para_start = seg["start"]
        buffer.append(seg["text"])
        if seg["end"] - para_start >= paragraph_seconds:
            lines.append(f"**[{fmt_ts(para_start)}]** {' '.join(buffer)}")
            buffer, para_start = [], None
    if buffer and para_start is not None:
        lines.append(f"**[{fmt_ts(para_start)}]** {' '.join(buffer)}")
    return "\n\n".join(lines)


def _normalize(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for seg in segments:
        text = re.sub(r"\s+", " ", str(seg.get("text", ""))).strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + float(seg.get("duration", 0.0))))
        out.append({"start": round(start, 2), "end": round(max(end, start), 2), "text": text})
    return out


def _youtube(video_id: str, languages: list[str], settings: Settings) -> dict[str, Any]:
    try:
        from youtube_transcript_api import (
            CouldNotRetrieveTranscript,
            NoTranscriptFound,
            TranscriptsDisabled,
            YouTubeTranscriptApi,
        )
    except ImportError as exc:
        raise ToolError("YouTube support is not installed: `uv sync --extra video`") from exc
    api = YouTubeTranscriptApi()
    try:
        available = api.list(video_id)
        transcript = None
        for finder in (available.find_manually_created_transcript, available.find_generated_transcript):
            try:
                transcript = finder(languages)
                break
            except NoTranscriptFound:
                continue
        translated = False
        if transcript is None:
            first = next(iter(available))
            if first.is_translatable and languages and first.language_code not in languages:
                transcript, translated = first.translate(languages[0]), True
            else:
                transcript = first
        fetched = transcript.fetch()
    except TranscriptsDisabled as exc:
        raise ToolError(
            "This video has no captions (captions are disabled). Local transcription requires a "
            "file you are allowed to download."
        ) from exc
    except CouldNotRetrieveTranscript as exc:
        raise ToolError(f"Could not retrieve captions: {exc.__class__.__name__}", retryable=False) from exc
    except StopIteration as exc:
        raise ToolError("This video has no captions") from exc
    segments = _normalize([{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched.snippets])
    title, author = video_id, ""
    try:  # official oEmbed: title and channel only
        response = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=10,
        )
        if response.status_code == 200:
            meta = response.json()
            title, author = str(meta.get("title", video_id))[:300], str(meta.get("author_name", ""))[:200]
    except (httpx.HTTPError, ValueError):
        pass
    return {
        "source_type": "youtube",
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "channel": author,
        "language": fetched.language_code,
        "captions": ("auto-generated" if fetched.is_generated else "manual") + (" (translated)" if translated else ""),
        "segments": segments,
    }


def _approved_media_path(raw: str, settings: Settings) -> Path:
    path = Path(raw).expanduser().resolve()
    roots = [Path(r).expanduser().resolve() for r in settings.media_dirs]
    if not any(path.is_relative_to(root) for root in roots):
        raise ToolError(
            f"File is outside the approved media folders (AF_MEDIA_DIRS: {', '.join(settings.media_dirs)})",
            details={"code": "path_not_allowed"},
        )
    if path.suffix.lower() not in MEDIA_SUFFIXES:
        raise ToolError(f"Unsupported media type '{path.suffix}'")
    if not path.is_file():
        raise ToolError(f"File not found: {path.name}")
    return path


def whisper_model_installed(repo: str) -> bool:
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo, local_files_only=True)
        return True
    except Exception:
        return False


def media_readiness(settings: Settings) -> list[str]:
    problems = []
    if not shutil.which("ffmpeg"):
        problems.append("ffmpeg is not installed: `brew install ffmpeg`")
    if importlib.util.find_spec("mlx_whisper") is None:
        problems.append("Whisper (MLX) is not installed: `uv sync --extra media` (Apple Silicon only)")
    elif not whisper_model_installed(settings.whisper_model):
        problems.append(f"Whisper model {settings.whisper_model} is not installed: `agent-factory media setup`")
    return problems


def _local_file(path: Path, languages: list[str], settings: Settings, timeout: float) -> dict[str, Any]:
    problems = media_readiness(settings)
    if problems:
        raise ToolError("Local transcription is not ready: " + "; ".join(problems))
    probe = subprocess.run(  # noqa: S603 - fixed executable, path passed as an argument
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ToolError("Could not read the media file (is it a valid audio/video file?)") from exc
    if duration > MAX_MEDIA_SECONDS:
        raise ToolError(f"Media is {fmt_ts(duration)} long; the limit is {fmt_ts(MAX_MEDIA_SECONDS)}")
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"}  # never download implicitly
    command = [sys.executable, "-m", "agent_factory.tools.whisper_worker", "--model", settings.whisper_model, str(path)]
    if languages:
        command[-1:-1] = ["--language", languages[0]]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env, check=False)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"Transcription exceeded {int(timeout)}s") from exc
    if proc.returncode != 0:
        raise ToolError(f"Transcription failed: {proc.stderr.strip()[-300:]}")
    result = json.loads(proc.stdout)
    return {
        "source_type": "file",
        "video_id": None,
        "url": None,
        "title": path.stem.replace("_", " "),
        "channel": "",
        "file": str(path),
        "language": result.get("language"),
        "captions": f"whisper ({settings.whisper_model})",
        "segments": _normalize(result.get("segments", [])),
    }


def _fixture(name: str, settings: Settings) -> dict[str, Any]:
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", name):
        raise ToolError("Invalid fixture name")
    path = settings.video_fixture_dir / f"{name}.json"
    if not path.is_file():
        raise ToolError(f"Unknown sample transcript '{name}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "source_type": data.get("source_type", "youtube"),
        "video_id": data.get("video_id"),
        "url": data.get("url"),
        "title": data["title"],
        "channel": data.get("channel", ""),
        "language": data.get("language", "en"),
        "captions": "sample",
        "segments": _normalize(data["segments"]),
    }


class TranscriptInput(BaseModel):
    source: str = Field(
        min_length=1, max_length=2000, description="YouTube URL/id, local file path, or fixture:<name>."
    )
    languages: list[str] | None = Field(None, max_length=5, description="Preferred caption languages, e.g. ['en'].")


class GetVideoTranscript(Tool):
    name = "get_video_transcript"
    description = (
        "Get a timestamped transcript for a YouTube video (existing captions) or a local media file (Whisper)."
    )
    connector = "video"
    Input = TranscriptInput
    risk = RiskLevel.LOW
    side_effects = SideEffect.READ
    network = True
    timeout_seconds = 3600.0
    max_output_chars = 2_500_000
    audit = AuditConfig(log_arguments=True, log_output=False)
    output_schema = {
        "type": "object",
        "required": ["title", "segments", "chunks", "transcript_markdown", "duration_seconds"],
        "properties": {"segments": {"type": "array"}, "chunks": {"type": "array", "minItems": 1}},
    }

    def network_destination(self, arguments: dict[str, Any], settings: Settings) -> str | None:
        return "www.youtube.com" if youtube_id(str(arguments.get("source", ""))) else None

    def run(self, args: TranscriptInput, ctx: ToolContext) -> dict[str, Any]:
        languages = [
            lang for lang in (args.languages or ["en"]) if re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$", lang)
        ]
        source = args.source.strip()
        if source.startswith("fixture:"):
            data = _fixture(source.removeprefix("fixture:"), ctx.settings)
        elif vid := youtube_id(source):
            data = _youtube(vid, languages, ctx.settings)
        elif "://" in source:
            raise ToolError("Only YouTube URLs are supported for online videos", details={"code": "unsupported_url"})
        else:
            data = _local_file(
                _approved_media_path(source, ctx.settings), languages, ctx.settings, self.timeout_seconds - 30
            )
        segments = data["segments"]
        if not segments:
            raise ToolError("The transcript is empty")
        duration = int(segments[-1]["end"])
        return {
            **data,
            "duration_seconds": duration,
            "duration_label": fmt_ts(duration),
            "word_count": sum(len(s["text"].split()) for s in segments),
            "chunks": build_chunks(segments),
            "transcript_markdown": transcript_markdown(segments),
        }


class SaveNotesInput(BaseModel):
    notes: dict[str, Any] = Field(description="The structured summary produced by the agent.")
    transcript: dict[str, Any] = Field(description="The output of get_video_transcript.")
    dry_run: bool | None = None


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug or "video"


def _bullets(items: Any) -> list[str]:
    return [f"- {str(i).strip()}" for i in items or [] if str(i).strip()]


def render_notes(notes: dict[str, Any], t: dict[str, Any]) -> str:
    title = str(notes.get("title") or t.get("title") or "Video notes")
    source = f"[{t['url']}]({t['url']})" if t.get("url") else (t.get("file") or "sample")
    out = [
        f"# {title}",
        "",
        f"*Source:* {source}"
        + (f" · *Channel:* {t['channel']}" if t.get("channel") else "")
        + f" · *Length:* {t.get('duration_label', '')} · *Transcript:* {t.get('captions', '')} ({t.get('language', '')})",
        f"*Generated:* {datetime.now().astimezone():%Y-%m-%d %H:%M}",
        "",
        "## Summary",
        "",
        str(notes.get("overview", "")).strip(),
    ]
    if notes.get("key_points"):
        out += ["", "## Key points", "", *_bullets(notes["key_points"])]
    chapters = notes.get("chapters") or []
    if chapters:
        out += ["", "## Chapters", ""]
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            start = str(ch.get("start", "")).strip()
            seconds = parse_ts(start)
            stamp = (
                f"[{start}](https://youtu.be/{t['video_id']}?t={seconds})"
                if seconds is not None and t.get("video_id")
                else start
            )
            out.append(f"- **{stamp}** {str(ch.get('title', '')).strip()} — {str(ch.get('summary', '')).strip()}")
    if notes.get("quotes"):
        out += ["", "## Notable quotes", "", *[f"> {str(q).strip()}\n" for q in notes["quotes"]]]
    if notes.get("action_items"):
        out += ["", "## Action items", "", *[f"- [ ] {str(a).strip()}" for a in notes["action_items"]]]
    study = notes.get("study_notes") or {}
    terms = study.get("terms") or []
    questions = study.get("review_questions") or []
    if terms or questions:
        out += ["", "## Study notes"]
        if terms:
            out += ["", "### Terms", ""]
            for term in terms:
                if isinstance(term, dict):
                    out.append(f"- **{str(term.get('term', '')).strip()}** — {str(term.get('definition', '')).strip()}")
                else:
                    out.append(f"- {str(term).strip()}")
        if questions:
            out += ["", "### Review questions", "", *[f"{n}. {str(q).strip()}" for n, q in enumerate(questions, 1)]]
    out += ["", "---", "Full transcript: [transcript.md](transcript.md)", ""]
    return "\n".join(out)


class SaveVideoNotes(Tool):
    name = "save_video_notes"
    description = (
        "Write notes.md (summary, chapters with timestamp links, action items, study notes) and transcript.md."
    )
    connector = "local_files"
    Input = SaveNotesInput
    risk = RiskLevel.LOW
    side_effects = SideEffect.WRITE
    action_class = "local_file_write"
    permissions = ["tools:invoke", "tools:invoke:write"]
    audit = AuditConfig(log_arguments=False, log_output=True)

    def run(self, args: SaveNotesInput, ctx: ToolContext) -> dict[str, Any]:
        t = args.transcript
        if "segments" not in t:
            raise ToolError("transcript must be the output of get_video_transcript")
        assert ctx.settings.sandbox_dir is not None
        base = ctx.settings.sandbox_dir / "video-notes" if args.dry_run else ctx.settings.video_notes_dir
        title = str(args.notes.get("title") or t.get("title") or "video")
        folder = base / f"{datetime.now().astimezone():%Y-%m-%d}-{_slug(title)}"  # derived, never caller-supplied
        folder.mkdir(parents=True, exist_ok=True)
        notes_path = folder / "notes.md"
        notes_path.write_text(render_notes(args.notes, t), encoding="utf-8")
        transcript_path = folder / "transcript.md"
        transcript_path.write_text(
            f"# Transcript — {t.get('title', '')}\n\n{t.get('url') or t.get('file') or ''}\n\n"
            + str(t.get("transcript_markdown") or transcript_markdown(t["segments"]))
            + "\n",
            encoding="utf-8",
        )
        return {
            "folder": str(folder),
            "notes": str(notes_path),
            "transcript": str(transcript_path),
            "dry_run": bool(args.dry_run),
        }

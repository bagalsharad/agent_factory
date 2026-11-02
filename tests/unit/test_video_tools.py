"""Video transcript tool, notes rendering and the foreach (map) workflow step."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from agent_factory.core.config import Settings
from agent_factory.governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from agent_factory.governance.rbac import Principal, make_principal
from agent_factory.platform import Platform
from agent_factory.spec.loader import load_spec
from agent_factory.tools.base import ToolContext
from agent_factory.tools.builtin.video import build_chunks, parse_ts, render_notes, youtube_id
from agent_factory.tools.registry import ToolExecutor, build_default_registry
from tests.conftest import create_built, example_spec, run

REGISTRY = build_default_registry()


def ctx(tmp_path: Path, **kw: Any) -> ToolContext:
    return ToolContext("ws", "ex", "video", Settings(_env_file=None, data_dir=tmp_path, **kw), "k1")


@pytest.mark.parametrize(("source", "expected"), [
    ("https://www.youtube.com/watch?v=aircAruvnKk", "aircAruvnKk"),
    ("https://youtu.be/aircAruvnKk?t=42", "aircAruvnKk"),
    ("youtube.com/shorts/aircAruvnKk", "aircAruvnKk"),
    ("https://m.youtube.com/embed/aircAruvnKk", "aircAruvnKk"),
    ("aircAruvnKk", "aircAruvnKk"),
    ("https://www.youtube.com.evil.example/watch?v=aircAruvnKk", None),
    ("https://evil.example/watch?v=aircAruvnKk", None),
    ("~/Movies/talk.mp4", None),
])
def test_youtube_id_parsing(source: str, expected: str | None) -> None:
    assert youtube_id(source) == expected


def test_chunking_keeps_start_times_and_bounds_count() -> None:
    segments = [{"start": i * 10.0, "end": i * 10.0 + 9, "text": "word " * 60} for i in range(2000)]
    chunks = build_chunks(segments)
    assert len(chunks) <= 24 and chunks[0]["start_label"] == "00:00"
    assert all(c["start_seconds"] <= d["start_seconds"] for c, d in itertools.pairwise(chunks))
    assert chunks[-1]["start_label"].count(":") == 2  # hours shown for long videos
    assert parse_ts("1:02:03") == 3723 and parse_ts("12:34") == 754 and parse_ts("soon") is None


def test_fixture_transcript_and_local_file_restrictions(tmp_path: Path) -> None:
    tool = REGISTRY.get("get_video_transcript")
    out = ToolExecutor().execute(tool, {"source": "fixture:reliable-agents-talk"}, ctx(tmp_path)).output
    assert out["title"].startswith("Building Reliable") and out["chunks"] and out["duration_seconds"] > 200
    assert out["transcript_markdown"].startswith("**[00:00]**")
    outside = ToolExecutor().execute(tool, {"source": "/etc/hosts"}, ctx(tmp_path))
    assert not outside.ok and outside.error["code"] == "path_not_allowed"
    media = tmp_path / "media"
    media.mkdir()
    (media / "notes.txt").write_text("x")
    wrong_type = ToolExecutor().execute(tool, {"source": str(media / "notes.txt")}, ctx(tmp_path, media_dirs=[str(media)]))
    assert "Unsupported media type" in wrong_type.error["message"]
    (media / "talk.mp4").write_bytes(b"\x00")
    not_ready = ToolExecutor().execute(tool, {"source": str(media / "talk.mp4")},
                                       ctx(tmp_path, media_dirs=[str(media)], whisper_model="example/not-installed"))
    assert not not_ready.ok and ("not ready" in not_ready.error["message"] or "read the media" in not_ready.error["message"])
    url = ToolExecutor().execute(tool, {"source": "https://evil.example/video.mp4"}, ctx(tmp_path))
    assert url.error["code"] == "unsupported_url"


def test_policy_destination_only_for_youtube(tmp_path: Path) -> None:
    body = example_spec("video-summarizer/agent.yaml").spec
    tool = REGISTRY.get("get_video_transcript")
    principal = make_principal("u", "u", "u", "ws", "developer", "test")

    def decide(settings: Settings, source: str) -> Any:
        args = {"source": source}
        return PolicyEngine(settings).evaluate_tool(ToolAuthorizationRequest(
            principal=principal, workspace_id="ws", agent=body, tool_name=tool.name, tool=tool.definition,
            tool_enabled=True, arguments=args, destination_host=tool.network_destination(args, settings),
            destination_resolved=True))

    closed = Settings(_env_file=None, data_dir=tmp_path)
    assert decide(closed, "https://youtu.be/aircAruvnKk").rules == ["network.allowlist"]
    assert decide(closed, "~/Movies/talk.mp4").effect == Effect.ALLOW  # local files need no network
    allowed = Settings(_env_file=None, data_dir=tmp_path, network_allowlist=["www.youtube.com"])
    assert decide(allowed, "https://youtu.be/aircAruvnKk").effect == Effect.ALLOW


def test_notes_render_clickable_chapters_and_safe_folder(tmp_path: Path) -> None:
    notes = {"title": "../../etc/passwd", "overview": "An overview of the talk.", "key_points": ["One"],
             "chapters": [{"start": "1:02", "title": "Intro", "summary": "Hello"}, {"start": "bad", "title": "X", "summary": "Y"}],
             "quotes": ["A quote"], "action_items": ["Do it"],
             "study_notes": {"terms": [{"term": "Idempotency", "definition": "No duplicates"}], "review_questions": ["Why?"]}}
    transcript = {"title": "T", "video_id": "aircAruvnKk", "url": "https://www.youtube.com/watch?v=aircAruvnKk",
                  "segments": [{"start": 0, "end": 5, "text": "hi"}], "duration_label": "00:05"}
    md = render_notes(notes, transcript)
    assert "[1:02](https://youtu.be/aircAruvnKk?t=62)" in md and "**bad** X" in md and "- [ ] Do it" in md
    result = ToolExecutor().execute(REGISTRY.get("save_video_notes"), {"notes": notes, "transcript": transcript, "dry_run": True},
                                    ctx(tmp_path))
    folder = Path(result.output["folder"])
    assert folder.parent == tmp_path / "sandbox" / "video-notes" and ".." not in folder.name
    assert (folder / "transcript.md").read_text().count("**[00:00]**") == 1


def test_foreach_map_step_runs_once_per_item(platform: Platform, admin: Principal) -> None:
    spec = load_spec({
        "api_version": "agentfactory/v1", "kind": "Agent", "metadata": {"name": "mapper"},
        "spec": {
            "objective": "Summarize each part of a transcript, then combine.",
            "inputs": {"type": "object", "required": ["source"], "properties": {"source": {"type": "string"}}},
            "outputs": {"type": "object", "required": ["overview"], "properties": {"overview": {"type": "string"}}},
            "tools": ["get_video_transcript"],
            "policies": {"external_network": True, "allowed_network_hosts": ["www.youtube.com"]},
            "workflow": {"type": "graph", "steps": [
                {"id": "t", "kind": "tool", "tool": "get_video_transcript", "arguments": {"source": "{{ input.source }}"}},
                {"id": "parts", "kind": "model", "structured": True, "foreach": "{{ steps.t.output.chunks }}",
                 "output_schema": {"type": "object", "required": ["summary"], "properties": {"summary": {"type": "string"}}}},
                {"id": "combine", "kind": "model", "structured": True, "final": True, "include_steps": ["parts"]}]},
        },
    })
    bad = spec.model_copy(deep=True)
    bad.spec.workflow.steps[2].include_steps = ["missing"]
    assert any(e.code == "workflow.bad_reference" for e in platform.validator.validate(bad).errors)
    agent_id = create_built(platform, admin, spec)
    result = run(platform, admin, agent_id, {"source": "fixture:reliable-agents-talk"})
    assert result["status"] == "completed", result["error"]
    events = [e for e in platform.executions.events(admin, result["id"])["items"] if e["type"] == "map.item"]
    assert len(events) >= 1 and result["budget_usage"]["model_calls"] == len(events) + 1

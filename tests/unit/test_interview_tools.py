"""Interview prep tools (materials, multi-video transcripts, Word/PDF/Markdown output) and the example agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from pypdf import PdfReader

from agent_factory.core.config import Settings
from agent_factory.governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from agent_factory.governance.rbac import Principal, make_principal
from agent_factory.platform import Platform
from agent_factory.tools.base import ToolContext
from agent_factory.tools.builtin.interview import render_prep_guide, render_resume_pdf
from agent_factory.tools.registry import ToolExecutor, build_default_registry
from tests.conftest import create_built, example_spec, run

REGISTRY = build_default_registry()
SAMPLES = {"resume": "fixture:sample-resume", "job_description": "fixture:sample-jd"}


def ctx(tmp_path: Path, **kw: Any) -> ToolContext:
    return ToolContext("ws", "ex", "interview-prep", Settings(_env_file=None, data_dir=tmp_path, **kw), "k1")


def execute(tool: str, args: dict[str, Any], context: ToolContext) -> Any:
    return ToolExecutor().execute(REGISTRY.get(tool), args, context)


def test_materials_from_fixtures_text_and_approved_files(tmp_path: Path) -> None:
    out = execute("load_interview_materials", {**SAMPLES, "interview_feedback": ["fixture:sample-feedback"],
                                               "company": "Example Corp", "role": "Staff Engineer"}, ctx(tmp_path)).output
    assert out["resume"]["text"].startswith("Jordan Rivera") and out["resume"]["source"] == "sample:sample-resume"
    assert len(out["feedback"]) == 1 and out["target"] == "Staff Engineer at Example Corp"

    docs = tmp_path / "docs"
    docs.mkdir()
    word = Document()
    word.add_paragraph("Taylor Example — Backend engineer with Go and Postgres experience.")
    word.save(str(docs / "resume.docx"))
    (docs / "jd.txt").write_text("We need a backend engineer who knows Go, Postgres and Kubernetes.")
    out = execute("load_interview_materials", {"resume": str(docs / "resume.docx"), "job_description": str(docs / "jd.txt")},
                  ctx(tmp_path, document_dirs=[str(docs)])).output
    assert "Taylor Example" in out["resume"]["text"] and out["job_description"]["source"] == "jd.txt"
    assert out["target"] == "Interview preparation"

    pasted = "Senior analyst with 5 years of SQL and Tableau experience in retail."
    out = execute("load_interview_materials", {"resume": pasted, "job_description": "fixture:sample-jd"}, ctx(tmp_path)).output
    assert out["resume"] == {"text": pasted, "source": "pasted text", "chars": len(pasted), "truncated": False}


def test_pdf_resumes_are_read(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    render_resume_pdf({"name": "Casey Example", "headline": "", "contact": "", "summary": "Platform engineer for 6 years.",
                       "skills": ["Go"], "experience": [], "projects": [], "education": [], "certifications": []},
                      docs / "cv.pdf")
    out = execute("load_interview_materials", {"resume": str(docs / "cv.pdf"), "job_description": "fixture:sample-jd"},
                  ctx(tmp_path, document_dirs=[str(docs)])).output
    assert "Casey Example" in out["resume"]["text"] and "Platform engineer" in out["resume"]["text"]


def test_document_restrictions(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "resume.exe").write_text("not a document but long enough to read")
    (docs / "tiny.txt").write_text("hi")
    context = ctx(tmp_path, document_dirs=[str(docs)])
    outside = execute("load_interview_materials", {"resume": "/etc/hosts", "job_description": "fixture:sample-jd"}, context)
    assert not outside.ok and outside.error["code"] == "path_not_allowed"
    escape = execute("load_interview_materials", {"resume": str(docs / ".." / ".." / "x.md"), "job_description": "fixture:sample-jd"}, context)
    assert escape.error["code"] == "path_not_allowed"
    assert "Unsupported document type" in execute(
        "load_interview_materials", {"resume": str(docs / "resume.exe"), "job_description": "fixture:sample-jd"}, context
    ).error["message"]
    assert "too little text" in execute(
        "load_interview_materials", {"resume": str(docs / "tiny.txt"), "job_description": "fixture:sample-jd"}, context
    ).error["message"]
    assert "Invalid fixture" in execute(
        "load_interview_materials", {"resume": "fixture:../secrets", "job_description": "fixture:sample-jd"}, context
    ).error["message"]


def test_video_transcripts_for_zero_or_more_videos(tmp_path: Path) -> None:
    empty = execute("get_video_transcripts", {"sources": None}, ctx(tmp_path)).output
    assert empty == {"videos": [], "chunks": [], "count": 0}
    out = execute("get_video_transcripts", {"sources": ["fixture:reliable-agents-talk", "fixture:reliable-agents-talk"]},
                  ctx(tmp_path)).output
    assert out["count"] == 2 and [v["number"] for v in out["videos"]] == [1, 2]
    assert out["chunks"][0]["label"].startswith("Building Reliable") and out["chunks"][-1]["video_number"] == 2

    tool = REGISTRY.get("get_video_transcripts")
    body = example_spec("interview-prep/agent.yaml").spec
    principal = make_principal("u", "u", "u", "ws", "developer", "test")

    def decide(settings: Settings, sources: list[str]) -> Any:
        args = {"sources": sources}
        return PolicyEngine(settings).evaluate_tool(ToolAuthorizationRequest(
            principal=principal, workspace_id="ws", agent=body, tool_name=tool.name, tool=tool.definition,
            tool_enabled=True, arguments=args, destination_host=tool.network_destination(args, settings),
            destination_resolved=True))

    closed = Settings(_env_file=None, data_dir=tmp_path)
    assert decide(closed, ["fixture:reliable-agents-talk", "https://youtu.be/aircAruvnKk"]).rules == ["network.allowlist"]
    assert decide(closed, []).effect == Effect.ALLOW


def test_save_writes_word_pdf_and_guide_in_a_safe_folder(tmp_path: Path) -> None:
    resume = {"name": "Jordan Rivera", "headline": "Data Engineer — “AI” reliability", "contact": "jordan@example.com",
              "summary": "Seven years of data engineering.", "skills": ["Spark", "Airflow", 7, {"bad": 1}],
              "experience": [{"title": "Senior Data Engineer", "company": "Northwind", "dates": "2022 \u2013 now",
                              "bullets": ["- Cut incidents by 40% → fewer pages", "-5% p99 latency"]}, "junk"],
              "education": [{"degree": "B.S. CS", "school": "UT Dallas", "year": "2017"}],
              "keywords_missing": ["Kubernetes"], "changes": ["Led with reliability work"]}
    args = {
        "resume": resume,
        "questions": {"questions": [{"category": "scenario", "question": "A pipeline fails at 2am — what do you do?",
                                     "answer": "Situation, task, action, result.", "follow_ups": ["How do you prevent it?"]},
                                    {"question": ""}]},
        "checklist": {"checklist": [{"area": "Streaming", "item": "Review exactly-once delivery", "priority": "urgent",
                                     "est_hours": 2}], "day_plan": [{"day": "Day 1", "focus": "Streaming", "tasks": ["Read docs"]}]},
        "talking_points": {"talking_points": [{"topic": "Evaluation", "points": ["Test agents like software"]}]},
        "materials": {"target": "../../Staff Engineer at Example/Corp"},
        "dry_run": True,
    }
    out = execute("save_interview_prep", args, ctx(tmp_path)).output
    folder = Path(out["folder"])
    assert folder.parent == tmp_path / "sandbox" / "interview-prep" and ".." not in folder.name and "/" not in folder.name
    assert folder.name.endswith("-ex") and out["resume"]["skills"] == ["Spark", "Airflow", "7"]
    assert out["resume"]["experience"][0]["bullets"] == ["Cut incidents by 40% → fewer pages", "-5% p99 latency"]
    assert len(out["questions"]) == 1 and out["checklist"][0]["priority"] == "medium"
    assert out["talking_points"] == []  # no videos were transcribed, so video talking points are dropped
    docx_text = "\n".join(p.text for p in Document(out["files"]["resume_docx"]).paragraphs)
    assert "Jordan Rivera" in docx_text and "Cut incidents by 40%" in docx_text
    pdf_text = PdfReader(out["files"]["resume_pdf"]).pages[0].extract_text()
    assert "Jordan Rivera" in pdf_text and "Senior Data Engineer" in pdf_text
    guide = Path(out["files"]["prep_guide"]).read_text()
    assert "A pipeline fails at 2am" in guide and "- [ ] **Streaming:** Review exactly-once delivery" in guide
    assert "Kubernetes" in guide and "not added" in guide

    no_questions = execute("save_interview_prep", {**args, "questions": {"questions": []}}, ctx(tmp_path))
    assert not no_questions.ok and "No interview questions" in no_questions.error["message"]


def test_guide_includes_video_talking_points() -> None:
    pack = {"target": "Role", "questions": [], "checklist": [], "day_plan": [],
            "talking_points": [{"topic": "Approvals", "points": ["Humans approve risky actions"], "when_to_use": "Safety questions", "source": ""}],
            "resume": {"changes": [], "keywords_missing": [], "keywords_matched": []}}
    guide = render_prep_guide(pack, [{"title": "Talk", "duration_label": "04:10", "url": "https://youtu.be/x"}])
    assert "## Talking points from videos" in guide and "- Talk (04:10)" in guide and "*When to use:* Safety questions" in guide


def test_example_agent_runs_and_passes_its_evaluation(platform: Platform, admin: Principal) -> None:
    spec = example_spec("interview-prep/agent.yaml")
    spec.spec.model.provider, spec.spec.model.model = "mock", "mock-default"  # default tests never need Ollama
    agent_id = create_built(platform, admin, spec)
    result = run(platform, admin, agent_id, {**SAMPLES, "videos": ["fixture:reliable-agents-talk"], "dry_run": True})
    assert result["status"] == "completed", result["error"]
    files = result["output"]["files"]
    assert all(Path(files[k]).is_file() for k in ("resume_docx", "resume_pdf", "prep_guide"))
    assert result["output"]["talking_points"]
    evaluation = platform.evaluations.start_run(admin, agent_id, wait=True)
    assert evaluation["passed"], evaluation["summary"]

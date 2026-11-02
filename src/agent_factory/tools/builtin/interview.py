"""Interview preparation: load a resume, job description and interview feedback; save the prep pack.

``load_interview_materials`` accepts, for each document, one of:

* a file path inside an approved folder (``AF_DOCUMENT_DIRS``): ``.pdf``, ``.docx``, ``.md`` or ``.txt``
* ``fixture:<name>`` — a bundled sample (tests, demos, evaluations)
* the text itself (pasted)

``save_interview_prep`` writes the tailored resume as ``resume.docx`` and ``resume.pdf`` plus ``prep-guide.md``
(scenario questions and answers, video talking points, study checklist, day plan) to a dated folder under
``AF_INTERVIEW_PREP_DIR``. File names and the folder are derived here, never supplied by the model.
All document text is untrusted content: the harness delimits it and the policy engine decides what may run.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ...core.config import Settings
from ...core.errors import ToolError
from ...spec.models import RiskLevel
from ..base import AuditConfig, SideEffect, Tool, ToolContext

DOC_SUFFIXES = {".pdf", ".docx", ".md", ".txt"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DOC_CHARS = 30_000
FIXTURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------------------------- reading
def _looks_like_path(value: str) -> bool:
    if "\n" in value or len(value) > 1024:
        return False
    return value.startswith(("~", "/", "./", "../")) or Path(value).suffix.lower() in DOC_SUFFIXES


def _approved_document_path(raw: str, settings: Settings) -> Path:
    path = Path(raw).expanduser().resolve()
    roots = [Path(r).expanduser().resolve() for r in settings.document_dirs]
    if not any(path.is_relative_to(root) for root in roots):
        raise ToolError(
            f"File is outside the approved document folders (AF_DOCUMENT_DIRS: {', '.join(settings.document_dirs)})",
            details={"code": "path_not_allowed"},
        )
    if path.suffix.lower() not in DOC_SUFFIXES:
        raise ToolError(f"Unsupported document type '{path.suffix}' (use .pdf, .docx, .md or .txt)")
    if not path.is_file():
        raise ToolError(f"File not found: {path.name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError(f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    return path


def _read_file(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if suffix == ".docx":
            from docx import Document

            doc = Document(str(path))
            lines = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    lines.append(" | ".join(cell.text for cell in row.cells))
            return "\n".join(lines)
        return path.read_text(encoding="utf-8", errors="replace")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Could not read {path.name}: {exc.__class__.__name__}") from exc


def _fixture(name: str, settings: Settings) -> tuple[str, str]:
    if not FIXTURE_NAME.match(name):
        raise ToolError("Invalid fixture name")
    for suffix in (".md", ".txt"):
        path = settings.interview_fixture_dir / f"{name}{suffix}"
        if path.is_file():
            return path.read_text(encoding="utf-8"), f"sample:{name}"
    raise ToolError(f"Unknown sample document '{name}'")


def read_document(value: str, settings: Settings, what: str) -> dict[str, Any]:
    """Resolve a fixture, an approved file path or pasted text into ``{text, source, chars, truncated}``."""
    value = (value or "").strip()
    if not value:
        raise ToolError(f"The {what} is empty")
    if value.startswith("fixture:"):
        text, source = _fixture(value.removeprefix("fixture:"), settings)
    elif _looks_like_path(value):
        path = _approved_document_path(value, settings)
        text, source = _read_file(path), path.name
    else:
        text, source = value, "pasted text"
    text = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)).strip()
    if len(text) < 20:
        raise ToolError(f"The {what} has too little text to work with ({source})")
    truncated = len(text) > MAX_DOC_CHARS
    return {"text": text[:MAX_DOC_CHARS], "source": source, "chars": len(text), "truncated": truncated}


class MaterialsInput(BaseModel):
    resume: str = Field(
        min_length=1, max_length=MAX_DOC_CHARS, description="Resume: file path, fixture:<name> or text."
    )
    job_description: str = Field(
        min_length=1, max_length=MAX_DOC_CHARS, description="Job description: file path, fixture:<name> or text."
    )
    interview_feedback: list[str] | None = Field(
        None, max_length=10, description="Previous interview feedback: file paths, fixture:<name> or text."
    )
    company: str | None = Field(None, max_length=200)
    role: str | None = Field(None, max_length=200)


class LoadInterviewMaterials(Tool):
    name = "load_interview_materials"
    description = (
        "Read the candidate's resume, the job description and any previous interview feedback "
        "(approved local files, samples or pasted text)."
    )
    connector = "local_files"
    Input = MaterialsInput
    risk = RiskLevel.LOW
    side_effects = SideEffect.READ
    timeout_seconds = 60.0
    max_output_chars = 200_000
    audit = AuditConfig(log_arguments=False, log_output=False)  # resumes contain personal data
    output_schema = {
        "type": "object",
        "required": ["resume", "job_description", "feedback", "target"],
        "properties": {"resume": {"type": "object"}, "job_description": {"type": "object"}},
    }

    def network_destination(self, arguments: dict[str, Any], settings: Settings) -> str | None:
        return None

    def run(self, args: MaterialsInput, ctx: ToolContext) -> dict[str, Any]:
        resume = read_document(args.resume, ctx.settings, "resume")
        job = read_document(args.job_description, ctx.settings, "job description")
        feedback = [
            read_document(item, ctx.settings, "interview feedback")
            for item in args.interview_feedback or []
            if item and item.strip()
        ]
        company = (args.company or "").strip()
        role = (args.role or "").strip()
        return {
            "resume": resume,
            "job_description": job,
            "feedback": feedback,
            "company": company,
            "role": role,
            "target": target_label(role, company),
        }


def target_label(role: str, company: str) -> str:
    if role and company:
        return f"{role} at {company}"
    return role or company or "Interview preparation"


# ---------------------------------------------------------------------------------------------- normalizing
def _s(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    texts = (re.sub(r"^[-*\u2022]\s+", "", _s(v)).strip() for v in value if not isinstance(v, dict | list))
    return [text for text in texts if text]  # leading bullet marks are dropped; renderers add their own


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def normalize_resume(raw: dict[str, Any]) -> dict[str, Any]:
    experience = []
    for job in _dicts(raw.get("experience")):
        experience.append(
            {
                "title": _s(job.get("title")),
                "company": _s(job.get("company")),
                "location": _s(job.get("location")),
                "dates": _s(job.get("dates")),
                "bullets": _strings(job.get("bullets")),
            }
        )
    projects = [
        {"name": _s(p.get("name")), "description": _s(p.get("description"))} for p in _dicts(raw.get("projects"))
    ]
    education = [
        {"degree": _s(e.get("degree")), "school": _s(e.get("school")), "year": _s(e.get("year"))}
        for e in _dicts(raw.get("education"))
    ]
    return {
        "name": _s(raw.get("name")) or "Candidate",
        "headline": _s(raw.get("headline")),
        "contact": _s(raw.get("contact")),
        "summary": _s(raw.get("summary")),
        "skills": _strings(raw.get("skills")),
        "experience": experience,
        "projects": [p for p in projects if p["name"] or p["description"]],
        "education": [e for e in education if e["degree"] or e["school"]],
        "certifications": _strings(raw.get("certifications")),
        "keywords_matched": _strings(raw.get("keywords_matched")),
        "keywords_missing": _strings(raw.get("keywords_missing")),
        "changes": _strings(raw.get("changes")),
    }


def normalize_questions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for q in _dicts(raw.get("questions")):
        question, answer = _s(q.get("question")), _s(q.get("answer"))
        if question:
            out.append(
                {
                    "category": _s(q.get("category")) or "general",
                    "question": question,
                    "why_asked": _s(q.get("why_asked")),
                    "answer": answer,
                    "follow_ups": _strings(q.get("follow_ups")),
                    "based_on": _s(q.get("based_on")),
                }
            )
    return out


def normalize_talking_points(raw: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for t in _dicts((raw or {}).get("talking_points")):
        points = _strings(t.get("points"))
        if points:
            out.append(
                {
                    "topic": _s(t.get("topic")) or "Talking point",
                    "points": points,
                    "when_to_use": _s(t.get("when_to_use")),
                    "source": _s(t.get("source")),
                }
            )
    return out


def normalize_checklist(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = []
    for c in _dicts(raw.get("checklist")):
        item = _s(c.get("item"))
        if item:
            priority = _s(c.get("priority")).lower()
            hours = c.get("est_hours")
            items.append(
                {
                    "area": _s(c.get("area")) or "General",
                    "item": item,
                    "priority": priority if priority in {"high", "medium", "low"} else "medium",
                    "resource": _s(c.get("resource")),
                    "est_hours": hours if isinstance(hours, int | float) and not isinstance(hours, bool) else None,
                }
            )
    plan = [
        {"day": _s(d.get("day")), "focus": _s(d.get("focus")), "tasks": _strings(d.get("tasks"))}
        for d in _dicts(raw.get("day_plan"))
    ]
    return items, [d for d in plan if d["day"] or d["tasks"]]


# ---------------------------------------------------------------------------------------------- rendering
def render_resume_docx(resume: dict[str, Any], path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(0.6)
        section.left_margin = section.right_margin = Inches(0.75)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)

    def centered(text: str, size: float, bold: bool = False, grey: bool = False) -> None:
        if not text:
            return
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        run.bold = bold
        run.font.size = Pt(size)
        if grey:
            run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    def heading(text: str) -> None:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)
        run = p.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        border = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for key, value in {"w:val": "single", "w:sz": "6", "w:space": "1", "w:color": "1F3A5F"}.items():
            bottom.set(qn(key), value)
        border.append(bottom)
        p._p.get_or_add_pPr().append(border)

    def bullet(text: str) -> None:
        doc.add_paragraph(text, style="List Bullet")

    centered(resume["name"], 20, bold=True)
    centered(resume["headline"], 11.5)
    centered(resume["contact"], 9.5, grey=True)
    if resume["summary"]:
        heading("Summary")
        doc.add_paragraph(resume["summary"])
    if resume["skills"]:
        heading("Skills")
        doc.add_paragraph(" · ".join(resume["skills"]))
    if resume["experience"]:
        heading("Experience")
        for job in resume["experience"]:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(5)
            p.add_run(" — ".join(x for x in (job["title"], job["company"]) if x)).bold = True
            meta = " · ".join(x for x in (job["location"], job["dates"]) if x)
            if meta:
                run = p.add_run(f"   {meta}")
                run.italic = True
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
            for line in job["bullets"]:
                bullet(line)
    if resume["projects"]:
        heading("Projects")
        for project in resume["projects"]:
            p = doc.add_paragraph(style="List Bullet")
            if project["name"]:
                p.add_run(project["name"] + (": " if project["description"] else "")).bold = True
            p.add_run(project["description"])
    if resume["education"]:
        heading("Education")
        for edu in resume["education"]:
            doc.add_paragraph(", ".join(x for x in (edu["degree"], edu["school"], edu["year"]) if x))
    if resume["certifications"]:
        heading("Certifications")
        for cert in resume["certifications"]:
            bullet(cert)
    doc.save(str(path))


_PDF_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2022": "-",
    "\u00b7": "|",
    "\u2026": "...",
    "\u00a0": " ",
    "\u2192": "->",
}


def _latin1(text: str) -> str:
    for old, new in _PDF_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def render_resume_pdf(resume: dict[str, Any], path: Path) -> None:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="Letter")
    pdf.set_margins(18, 15, 18)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_title(_latin1(f"{resume['name']} - Resume"))
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def line(text: str, size: float, style: str = "", align: str = "L", grey: bool = False, h: float = 5) -> None:
        if not text:
            return
        pdf.set_font("Helvetica", style, size)
        pdf.set_text_color(85, 85, 85) if grey else pdf.set_text_color(20, 20, 20)
        pdf.multi_cell(width, h, _latin1(text), align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def heading(text: str) -> None:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(31, 58, 95)
        pdf.cell(width, 6, _latin1(text.upper()), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(31, 58, 95)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(1.5)

    def bullet(text: str) -> None:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(20, 20, 20)
        pdf.set_x(pdf.l_margin + 3)
        pdf.cell(4, 5, "-")
        pdf.multi_cell(width - 7, 5, _latin1(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    line(resume["name"], 20, "B", "C", h=9)
    line(resume["headline"], 11.5, "", "C", h=6)
    line(resume["contact"], 9, "", "C", grey=True)
    if resume["summary"]:
        heading("Summary")
        line(resume["summary"], 10)
    if resume["skills"]:
        heading("Skills")
        line(" | ".join(resume["skills"]), 10)
    if resume["experience"]:
        heading("Experience")
        for job in resume["experience"]:
            pdf.ln(1)
            line(" - ".join(x for x in (job["title"], job["company"]) if x), 10.5, "B")
            line(" | ".join(x for x in (job["location"], job["dates"]) if x), 9, "I", grey=True)
            for text in job["bullets"]:
                bullet(text)
    if resume["projects"]:
        heading("Projects")
        for project in resume["projects"]:
            bullet(": ".join(x for x in (project["name"], project["description"]) if x))
    if resume["education"]:
        heading("Education")
        for edu in resume["education"]:
            line(", ".join(x for x in (edu["degree"], edu["school"], edu["year"]) if x), 10)
    if resume["certifications"]:
        heading("Certifications")
        for cert in resume["certifications"]:
            bullet(cert)
    pdf.output(str(path))


def render_prep_guide(pack: dict[str, Any], videos: list[dict[str, Any]]) -> str:
    out = [
        f"# Interview prep — {pack['target']}",
        "",
        f"*Generated:* {datetime.now().astimezone():%Y-%m-%d %H:%M} · "
        "*Tailored resume:* [resume.docx](resume.docx) · [resume.pdf](resume.pdf)",
        "",
        "## Practice questions",
    ]
    for n, q in enumerate(pack["questions"], 1):
        out += [
            "",
            f"### {n}. {q['question']}",
            "",
            f"*{q['category']}*" + (f" · based on {q['based_on']}" if q["based_on"] else ""),
        ]
        if q["why_asked"]:
            out += ["", f"**Why they ask:** {q['why_asked']}"]
        out += ["", f"**Suggested answer:** {q['answer']}"]
        if q["follow_ups"]:
            out += ["", "**Likely follow-ups:**", *[f"- {f}" for f in q["follow_ups"]]]
    if pack["talking_points"]:
        out += ["", "## Talking points from videos"]
        if videos:
            out += [
                "",
                *[
                    f"- {v['title']} ({v['duration_label']})" + (f" — {v['url']}" if v.get("url") else "")
                    for v in videos
                ],
            ]
        for t in pack["talking_points"]:
            out += ["", f"### {t['topic']}", "", *[f"- {p}" for p in t["points"]]]
            if t["when_to_use"]:
                out += ["", f"*When to use:* {t['when_to_use']}"]
    out += ["", "## Study checklist", ""]
    for c in pack["checklist"]:
        extra = ", ".join(x for x in (c["priority"], f"~{c['est_hours']}h" if c["est_hours"] else "") if x)
        out.append(f"- [ ] **{c['area']}:** {c['item']} ({extra})" + (f" — {c['resource']}" if c["resource"] else ""))
    if pack["day_plan"]:
        out += ["", "## Day-by-day plan"]
        for day in pack["day_plan"]:
            out += ["", f"**{day['day']}** — {day['focus']}", *[f"- [ ] {t}" for t in day["tasks"]]]
    resume = pack["resume"]
    if resume["changes"] or resume["keywords_missing"]:
        out += ["", "## Resume notes"]
        if resume["changes"]:
            out += ["", "**What was changed:**", *[f"- {c}" for c in resume["changes"]]]
        if resume["keywords_matched"]:
            out += ["", "**Job keywords your resume covers:** " + ", ".join(resume["keywords_matched"])]
        if resume["keywords_missing"]:
            out += [
                "",
                "**Job keywords not supported by your resume** (not added — prepare to discuss honestly):",
                *[f"- {k}" for k in resume["keywords_missing"]],
            ]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------- saving
class SavePrepInput(BaseModel):
    resume: dict[str, Any] = Field(description="Tailored resume (structured).")
    questions: dict[str, Any] = Field(description="Scenario questions and answers.")
    checklist: dict[str, Any] = Field(description="Study checklist and day plan.")
    talking_points: dict[str, Any] | None = Field(None, description="Talking points from videos.")
    materials: dict[str, Any] | None = Field(None, description="Output of load_interview_materials.")
    videos: dict[str, Any] | None = Field(None, description="Output of get_video_transcripts.")
    dry_run: bool | None = None


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug or "interview-prep"


class SaveInterviewPrep(Tool):
    name = "save_interview_prep"
    description = (
        "Write the tailored resume as resume.docx and resume.pdf, and prep-guide.md with questions, answers, "
        "talking points and the study checklist. Returns the complete prep pack."
    )
    connector = "local_files"
    Input = SavePrepInput
    risk = RiskLevel.LOW
    side_effects = SideEffect.WRITE
    action_class = "local_file_write"
    permissions = ["tools:invoke", "tools:invoke:write"]
    timeout_seconds = 60.0
    max_output_chars = 200_000
    audit = AuditConfig(log_arguments=False, log_output=False)  # personal data

    def run(self, args: SavePrepInput, ctx: ToolContext) -> dict[str, Any]:
        materials = args.materials or {}
        resume = normalize_resume(args.resume)
        questions = normalize_questions(args.questions)
        if not questions:
            raise ToolError("No interview questions were produced")
        checklist, day_plan = normalize_checklist(args.checklist)
        videos = _dicts((args.videos or {}).get("videos"))
        talking_points = normalize_talking_points(args.talking_points) if videos else []
        target = _s(materials.get("target")) or target_label(_s(materials.get("role")), _s(materials.get("company")))
        assert ctx.settings.sandbox_dir is not None
        base = ctx.settings.sandbox_dir / "interview-prep" if args.dry_run else ctx.settings.interview_prep_dir
        name = f"{datetime.now().astimezone():%Y-%m-%d}-{_slug(target)}"  # derived, never caller-supplied
        if args.dry_run:  # dry runs (and evaluation cases) never overwrite each other
            name += f"-{_slug(ctx.execution_id)[:8]}"
        folder = base / name
        folder.mkdir(parents=True, exist_ok=True)
        files = {
            "resume_docx": str(folder / "resume.docx"),
            "resume_pdf": str(folder / "resume.pdf"),
            "prep_guide": str(folder / "prep-guide.md"),
        }
        pack = {
            "target": target,
            "resume": resume,
            "questions": questions,
            "talking_points": talking_points,
            "checklist": checklist,
            "day_plan": day_plan,
        }
        try:
            render_resume_docx(resume, Path(files["resume_docx"]))
            render_resume_pdf(resume, Path(files["resume_pdf"]))
        except ImportError as exc:
            raise ToolError("Document libraries are missing: run `uv sync`") from exc
        Path(files["prep_guide"]).write_text(render_prep_guide(pack, videos), encoding="utf-8")
        return {**pack, "videos": videos, "files": files, "folder": str(folder), "dry_run": bool(args.dry_run)}

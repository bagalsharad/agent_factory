# Interview Prep

Give it your resume and a job description. It produces:

- **`resume.docx` and `resume.pdf`**: your resume tailored to the job. Facts are never invented: it rephrases,
  reorders and emphasises what your resume already says. Job requirements your resume doesn't show are listed as
  gaps in the prep guide, not added to the resume.
- **`prep-guide.md`**:
  - about 10 practice questions (behavioral, technical, system design, scenario, role fit), each with why it is
    asked, a suggested answer built from your own experience (STAR), and likely follow-ups. Weaknesses named in
    previous interview feedback get their own questions.
  - talking points from any videos you add (YouTube talks, recorded mock interviews, course videos).
  - a study checklist (area, item, priority, resource, estimated hours) and a day-by-day plan.
  - what changed in your resume and which job keywords it does and doesn't cover.

Output folder: `~/Documents/InterviewPrep/<date>-<role-at-company>/` (`AF_INTERVIEW_PREP_DIR`). Everything runs
on your local Ollama model; nothing leaves your Mac except YouTube caption requests.

## Inputs

| Field | Required | What to pass |
|---|---|---|
| `resume` | yes | a `.pdf`, `.docx`, `.md` or `.txt` file, or the text itself |
| `job_description` | yes | a file, or the text itself |
| `interview_feedback` | no | list of files or text: notes from previous interviews |
| `videos` | no | up to 5 YouTube URLs or local video/audio files |
| `company`, `role` | no | used in titles and the folder name |
| `interview_date` | no | e.g. `2026-11-20`; shapes the day-by-day plan |
| `dry_run` | no | `true` saves under `data/sandbox/interview-prep` instead |

Files must be inside an approved folder (`AF_DOCUMENT_DIRS`, default `~/Documents, ~/Downloads, ~/Desktop`).
Videos follow the [Video Summarizer](video-summarizer.md) rules: YouTube needs `www.youtube.com` in
`AF_NETWORK_ALLOWLIST`; local files need `brew install ffmpeg` and `agent-factory media setup`.

## Run it

From the dashboard: **Agent Catalog → Interview Prep → Run**, and paste the input JSON. From the CLI:

```bash
uv run agent-factory agents run interview-prep -i '{
  "resume": "~/Documents/resume.pdf",
  "job_description": "~/Documents/jobs/staff-data-engineer.txt",
  "interview_feedback": ["~/Documents/jobs/feedback-round-1.md"],
  "videos": ["https://www.youtube.com/watch?v=VIDEO_ID"],
  "company": "Example Corp",
  "role": "Staff Data Engineer",
  "interview_date": "2026-11-20"
}'
```

Try it with the bundled samples (fictional candidate and job):

```bash
uv run agent-factory agents run interview-prep -i '{"resume": "fixture:sample-resume",
  "job_description": "fixture:sample-jd", "interview_feedback": ["fixture:sample-feedback"],
  "videos": ["fixture:reliable-agents-talk"], "dry_run": true}'
```

## How it works

1. `load_interview_materials` reads the resume, job description and feedback (approved folders only).
2. `get_video_transcripts` fetches captions or transcribes local files (skipped when there are no videos).
3. Four model steps, each with its own output schema: tailored resume; questions and answers; one note per video
   segment (map) combined into talking points (reduce); study checklist and day plan.
4. `save_interview_prep` writes the Word, PDF and Markdown files to a folder whose name it derives itself.

Documents and transcripts are treated as untrusted content: text inside them cannot instruct the agent, and model
steps cannot call tools.

**Always review the tailored resume before sending it.** The instructions forbid inventing experience, but a local
model can still misread or overstate something.

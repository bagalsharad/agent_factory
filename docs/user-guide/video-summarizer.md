# Video Summarizer & Transcriber

Turns a **YouTube URL** or a **local video/audio file** into:

- `notes.md`: summary, key points, timestamped chapters (clickable `youtu.be?t=` links for YouTube), verbatim
  quotes, action items (checkboxes), study notes (terms, review questions)
- `transcript.md`: the full transcript with `[mm:ss]` timestamps

Output folder: `~/VideoNotes/<date>-<title>/` (`AF_VIDEO_NOTES_DIR`). Summaries run on your local Ollama model.

## YouTube

Uses the video's existing captions (manual preferred, then auto-generated, then a translation into your first
preferred language). Nothing is downloaded. Videos without captions are reported as such. Requires
`www.youtube.com` in `AF_NETWORK_ALLOWLIST` (already added to your `.env`).

```bash
uv run agent-factory agents run video-summarizer -i '{"source": "https://www.youtube.com/watch?v=aircAruvnKk"}'
uv run agent-factory agents run video-summarizer -i '{"source": "https://youtu.be/VIDEO_ID", "languages": ["hi", "en"]}'
```

## Local files (transcribed on your Mac)

One-time setup:

```bash
brew install ffmpeg
uv run agent-factory media setup     # downloads the Whisper large-v3-turbo MLX model (~1.6 GB), once
uv run agent-factory media status
```

Files must be inside an approved folder (`AF_MEDIA_DIRS`, default `~/Movies, ~/Downloads, ~/Desktop, ~/Documents`).
Supported: mp4, mov, m4v, mkv, webm, avi, mp3, m4a, wav, aac, flac, ogg; up to 4 hours.

```bash
uv run agent-factory agents run video-summarizer -i '{"source": "~/Movies/team-meeting.mp4"}'
```

Whisper runs in a separate process on the Apple GPU (MLX) with downloads disabled, and is stopped if it exceeds the
time limit.

## How it works

`transcript` (tool) → `segment_notes` (model, once per ~6,000-character chunk — map) → `synthesize` (model,
reduce over the segment notes only) → `validate` → `save` (deterministic Markdown rendering). Chapter times come
from the segment start times; links are built by code, not by the model.

Speed: an 18-minute video took about 9 minutes with `qwen3:8b` on a 16 GB Mac (5 model calls). To go faster, quit
Docker Desktop to free memory, or use a smaller model (`ollama pull qwen3:4b` and set it in the designer, step 3).
Use `"dry_run": true` to write notes under `data/sandbox/video-notes/` instead.

## Notes

- Fetching captions uses YouTube's caption endpoints through `youtube-transcript-api`; use it for personal study and
  respect creators' rights. YouTube may rate-limit or block caption requests from some networks.
- Transcript text is untrusted: the model has no tools, and instructions spoken in a video cannot trigger actions.
